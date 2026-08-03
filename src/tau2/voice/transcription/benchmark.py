#!/usr/bin/env python3
"""Multilingual transcription benchmark, driven through the `whissle` CLI.

Measures transcription fidelity (WER + CER) across the languages the product exposes
— en, hi, te, hinglish, tenglish — by DOGFOODING the `whissle` CLI (`models tts` +
`models transcribe`), exactly what a customer runs. The engine/voice/provider is
chosen and hidden by the platform; this harness never names it or calls HTTP itself.

Two modes, both driven by a JSONL manifest of {id, language, reference, ...}:

  round-trip (default)  Synthesize each `reference` (or its `tts_text`) with the
                        platform TTS, transcribe it back, score vs the reference.
                        Fully self-contained — no external corpus — so it runs in
                        CI and catches endpoint / language-routing regressions
                        (e.g. an STT-translate model mangling native script). It is
                        a *fidelity smoke test*, not an absolute real-world WER: the
                        same platform both speaks and hears.

  corpus                Score real labelled clips: each row carries `audio` (a path
                        to a wav/mp3/… file) with its ground-truth `reference`. This
                        is the real WER number; drop FLEURS / Common Voice / held-out
                        call clips into the manifest.

Usage:
    # round-trip over the seeded set, all languages
    python -m tau2.voice.transcription.benchmark run

    # one language, more repeats, JSON out
    python -m tau2.voice.transcription.benchmark run --language hi --repeat 3

    # real labelled clips
    python -m tau2.voice.transcription.benchmark run \
        --manifest data/transcription/my_corpus.jsonl --mode corpus

Requires the `whissle` CLI on PATH (or set WHISSLE_CLI, e.g.
`WHISSLE_CLI="node /path/to/whissle-cli/bin/whissle.mjs"`). Auth/base URL come from
the CLI's own config or WHISSLE_API_KEY / WHISSLE_BASE_URL (see run_transcribe.sh).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional

import jiwer
from dotenv import load_dotenv
from rich.console import Console
from typer import Option, Typer

load_dotenv()

app = Typer(add_completion=False)
console = Console()

DEFAULT_MANIFEST = "data/transcription/whissle_roundtrip.jsonl"
LANGUAGES = ["en", "hi", "te", "hinglish", "tenglish"]


# ── the whissle CLI (dogfood the product's own client, not raw HTTP) ───────────
# The benchmark drives the `whissle` CLI end to end — `models tts` to synthesize,
# `models transcribe --json` to transcribe — so it exercises exactly what a customer
# using the CLI hits, engine/voice/provider chosen and hidden by the platform. Auth
# and base URL come from the CLI's own config (~/.whissle/config.json) or the
# WHISSLE_API_KEY / WHISSLE_BASE_URL env. Override the binary with WHISSLE_CLI, e.g.
# `WHISSLE_CLI="node /path/to/whissle-cli/bin/whissle.mjs"`.

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _cli_argv() -> list[str]:
    override = os.getenv("WHISSLE_CLI")
    return override.split() if override else ["whissle"]


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    # The CLI reads WHISSLE_BASE_URL; bridge WHISSLE_BASE (what run_*.sh set).
    base = os.getenv("WHISSLE_BASE_URL") or os.getenv("WHISSLE_BASE")
    if base:
        env["WHISSLE_BASE_URL"] = base
    return env


def _run_cli(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        _cli_argv() + args, capture_output=True, text=True,
        env=_cli_env(), timeout=timeout,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"whissle {' '.join(args[:2])} -> exit {proc.returncode}: {_ANSI.sub('', detail)[:200]}"
        )
    return proc


def synthesize_to_file(text: str, language: str, out_path: str) -> None:
    """`whissle models tts` → writes an audio file. Omit --language for English (the
    platform auto-detects script); pass it for hi/te/hinglish/tenglish."""
    args = ["models", "tts", text, "--out", out_path]
    if language and language != "en":
        args += ["--language", language]
    _run_cli(args)


def transcribe_file(path: str, language: str, diarize: bool = False) -> dict[str, Any]:
    """`whissle models transcribe --json` → {text, segments, duration_seconds, cost_usd}."""
    args = ["models", "transcribe", path, "--language", language, "--json"]
    if diarize:
        args.append("--diarize")
    return json.loads(_ANSI.sub("", _run_cli(args).stdout).strip())


# ── scoring ────────────────────────────────────────────────────────────────

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Fair, script-agnostic normalization for WER/CER: NFC, casefold, strip
    punctuation, collapse whitespace. Devanagari/Telugu pass through unchanged
    (casefold is a no-op there); Latin + code-mixed get lowercased."""
    text = unicodedata.normalize("NFC", text or "")
    text = _PUNCT.sub(" ", text)
    text = _WS.sub(" ", text).strip().casefold()
    return text


def score(reference: str, hypothesis: str) -> dict[str, float]:
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return {"wer": 1.0, "cer": 1.0}
    # jiwer raises on empty hypothesis for WER; guard to a full-miss score.
    wer = 1.0 if not hyp else float(jiwer.wer(ref, hyp))
    cer = 1.0 if not hyp else float(jiwer.cer(ref, hyp))
    return {"wer": round(min(wer, 1.0), 4), "cer": round(min(cer, 1.0), 4)}


# ── run one manifest row ─────────────────────────────────────────────────────

def _run_case(row: dict[str, Any], mode: str, repeat: int) -> dict[str, Any]:
    language = row.get("language", "en")
    reference = row["reference"]
    results: list[dict[str, Any]] = []

    for i in range(repeat):
        t0 = time.time()
        tmp_path: Optional[str] = None
        try:
            if mode == "corpus":
                audio_path = str(Path(row["audio"]))
            else:  # round-trip: synthesize the reference (or its tts_text) via the CLI
                fd, tmp_path = tempfile.mkstemp(suffix=".mp3", prefix=f"{row['id']}_")
                os.close(fd)
                synthesize_to_file(row.get("tts_text") or reference, language, tmp_path)
                audio_path = tmp_path
            resp = transcribe_file(audio_path, language)
            hyp = resp.get("text", "")
            s = score(reference, hyp)
            results.append({
                "hypothesis": hyp,
                **s,
                "duration_s": resp.get("duration_seconds"),
                "cost_usd": resp.get("cost_usd"),
                "latency_s": round(time.time() - t0, 2),
            })
        except Exception as e:  # noqa: BLE001 — one bad row must not sink the run
            results.append({"error": str(e), "wer": 1.0, "cer": 1.0,
                            "latency_s": round(time.time() - t0, 2)})
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    ok = [r for r in results if "error" not in r]
    agg = {
        "wer": round(sum(r["wer"] for r in results) / len(results), 4),
        "cer": round(sum(r["cer"] for r in results) / len(results), 4),
        "errors": len(results) - len(ok),
    }
    return {
        "id": row["id"], "language": language, "reference": reference,
        "runs": results, **agg,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

@app.command()
def languages() -> None:
    """List the language codes the platform transcription endpoint accepts."""
    console.print("supported languages: " + " · ".join(LANGUAGES))


@app.command()
def run(
    manifest: str = Option(DEFAULT_MANIFEST, help="JSONL of {id, language, reference, tts_text?, audio?}"),
    mode: str = Option("round-trip", help="round-trip | corpus"),
    language: Optional[str] = Option(None, help="filter to one language code"),
    repeat: int = Option(1, help="repeats per case (round-trip TTS is nondeterministic)"),
    limit: Optional[int] = Option(None, help="cap number of cases"),
    save_to: Optional[str] = Option(None, help="write full JSON results here"),
) -> None:
    """Run the transcription benchmark and print a per-language WER/CER summary."""
    rows = [
        json.loads(line)
        for line in Path(manifest).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if language:
        rows = [r for r in rows if r.get("language") == language]
    if limit:
        rows = rows[:limit]
    if not rows:
        console.print("[red]no cases match[/red]")
        raise SystemExit(1)

    console.print(
        f"[bold]transcription benchmark[/bold]  mode={mode}  cases={len(rows)}  "
        f"repeat={repeat}  via `{' '.join(_cli_argv())}`"
    )
    cases = []
    for row in rows:
        c = _run_case(row, mode, repeat)
        flag = "[red]✗[/red]" if c["errors"] else ("[yellow]•[/yellow]" if c["wer"] > 0.15 else "[green]✓[/green]")
        console.print(
            f"  {flag} {c['id']:<22} {c['language']:<9} "
            f"WER {c['wer']:.3f}  CER {c['cer']:.3f}"
        )
        cases.append(c)

    # per-language + overall aggregates
    from collections import defaultdict
    by_lang: dict[str, list] = defaultdict(list)
    for c in cases:
        by_lang[c["language"]].append(c)

    console.print("\n[bold]Summary (mean WER / CER by language)[/bold]")
    for lang in sorted(by_lang):
        cs = by_lang[lang]
        wer = sum(c["wer"] for c in cs) / len(cs)
        cer = sum(c["cer"] for c in cs) / len(cs)
        errs = sum(c["errors"] for c in cs)
        console.print(
            f"  {lang:<10} n={len(cs):<3} WER {wer:.3f}  CER {cer:.3f}"
            + (f"  [red]{errs} error(s)[/red]" if errs else "")
        )
    overall_wer = sum(c["wer"] for c in cases) / len(cases)
    overall_cer = sum(c["cer"] for c in cases) / len(cases)
    console.print(f"  [bold]{'ALL':<10} n={len(cases):<3} WER {overall_wer:.3f}  CER {overall_cer:.3f}[/bold]")

    out = save_to or f"results/whissle/transcribe_{mode}_{language or 'all'}.json"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({
        "mode": mode, "manifest": manifest, "repeat": repeat,
        "overall": {"wer": round(overall_wer, 4), "cer": round(overall_cer, 4)},
        "by_language": {
            lang: {
                "n": len(cs),
                "wer": round(sum(c["wer"] for c in cs) / len(cs), 4),
                "cer": round(sum(c["cer"] for c in cs) / len(cs), 4),
            } for lang, cs in by_lang.items()
        },
        "cases": cases,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"\nsaved → {out}")


if __name__ == "__main__":
    app()
