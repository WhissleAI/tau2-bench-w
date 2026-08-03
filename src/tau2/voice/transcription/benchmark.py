#!/usr/bin/env python3
"""Multilingual transcription benchmark for the Whissle platform endpoint.

Measures transcription fidelity (WER + CER) of `POST /api/models/transcribe` across
the languages the product exposes — en, hi, te, hinglish, tenglish — WITHOUT ever
naming the underlying engine (that is chosen server-side from the language).

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

Reads WHISSLE_BASE + WHISSLE_API_KEY from the environment (see run_transcribe.sh).
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional

import jiwer
import requests
from dotenv import load_dotenv
from rich.console import Console
from typer import Option, Typer

load_dotenv()

app = Typer(add_completion=False)
console = Console()

DEFAULT_MANIFEST = "data/transcription/whissle_roundtrip.jsonl"
LANGUAGES = ["en", "hi", "te", "hinglish", "tenglish"]


# ── platform HTTP (engine stays hidden) ───────────────────────────────────────

def _base() -> str:
    return os.getenv(
        "WHISSLE_BASE", "https://aws-gateway-backend.whissle.ai/bot"
    ).rstrip("/")


def _auth() -> dict[str, str]:
    key = os.getenv("WHISSLE_API_KEY")
    if not key:
        raise RuntimeError("WHISSLE_API_KEY not set (see run_transcribe.sh / .env)")
    return {"Authorization": f"Bearer {key}"}


def synthesize(text: str, voice: Optional[str] = None) -> bytes:
    """Platform TTS → audio bytes (mp3). Engine hidden."""
    body: dict[str, Any] = {"text": text}
    if voice:
        body["voice"] = voice
    r = requests.post(
        f"{_base()}/api/models/tts", headers=_auth(), json=body, timeout=120
    )
    r.raise_for_status()
    return r.content


def transcribe(
    audio: bytes, filename: str, language: str, diarize: bool = False
) -> dict[str, Any]:
    """Platform STT → {text, segments, duration_seconds, cost_usd}. Engine hidden."""
    r = requests.post(
        f"{_base()}/api/models/transcribe",
        headers=_auth(),
        files={"file": (filename, audio, "application/octet-stream")},
        data={"language": language, "diarize": str(diarize).lower()},
        timeout=180,
    )
    if r.status_code != 200:
        raise RuntimeError(f"transcribe {r.status_code}: {r.text[:200]}")
    return r.json()


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
        try:
            if mode == "corpus":
                path = Path(row["audio"])
                audio = path.read_bytes()
                filename = path.name
            else:  # round-trip: synthesize the reference (or its tts_text)
                audio = synthesize(row.get("tts_text") or reference, row.get("voice"))
                filename = f"{row['id']}.mp3"
            resp = transcribe(audio, filename, language, diarize=False)
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
        f"repeat={repeat}  base={_base()}"
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
