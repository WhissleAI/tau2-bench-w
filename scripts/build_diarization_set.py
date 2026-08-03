#!/usr/bin/env python3
"""Build a small, exactly-labelled 2-3 speaker diarization set from LibriSpeech.

Diarizers do NOT reliably separate synthetic TTS voices, so a transcription
round-trip cannot test speaker attribution. The standard fix (LibriCSS / LibriMix)
is to concatenate short utterances from DISTINCT REAL speakers into multi-speaker
"conversations" — real human voices a diarizer can actually separate, with a
ground-truth speaker timeline that is EXACT by construction (we know precisely when
each speaker talks because we assembled it).

Source: LibriSpeech dev-clean (CC BY 4.0), streamed from the Hugging Face hub with
`Audio(decode=False)` so we decode the FLAC bytes with soundfile ourselves (no
torch/torchcodec). Writes 16 kHz mono WAVs + a manifest.jsonl of ground-truth turns
into data/transcription/diarization/. Small (<1 MB) and committed so the benchmark
needs no network.

Run:  python scripts/build_diarization_set.py            (needs: datasets, soundfile, numpy)
"""
from __future__ import annotations

import io
import itertools
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf
from datasets import Audio, load_dataset

OUT_DIR = Path("data/transcription/diarization")
SR = 16000
GAP_S = 0.4                      # silence between turns
MIN_S, MAX_S = 2.5, 7.0         # keep utterances a sensible length
SPEAKERS_WANTED = 5
UTTS_PER_SPEAKER = 2

# Conversation recipes: each is a list of (speaker_index, utterance_index). Speaker
# indices are into the collected-speakers list; utterance indices into that
# speaker's utterances. Designed to cover: A/B, A/B/A return, and a 3-speaker case.
RECIPES = [
    ("2spk-ab-1", [(0, 0), (1, 0)]),
    ("2spk-ab-2", [(2, 0), (3, 0)]),
    ("2spk-aba", [(0, 1), (1, 1), (0, 0)]),
    ("2spk-bcb", [(1, 0), (2, 1), (1, 1)]),
    ("3spk-abc", [(0, 0), (1, 0), (2, 0)]),
    ("3spk-acb", [(3, 0), (0, 1), (4, 0)]),
]


def _load_flac(example) -> tuple[np.ndarray, int]:
    raw = example["audio"]["bytes"]
    data, sr = sf.read(io.BytesIO(raw), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data, sr


def collect_utterances() -> dict[str, list[dict]]:
    ds = load_dataset("openslr/librispeech_asr", "clean", split="validation",
                      streaming=True)
    ds = ds.cast_column("audio", Audio(decode=False))
    by_speaker: dict[str, list[dict]] = defaultdict(list)
    for ex in itertools.islice(ds, 400):
        sid = str(ex.get("speaker_id"))
        if len(by_speaker.get(sid, [])) >= UTTS_PER_SPEAKER:
            continue
        audio, sr = _load_flac(ex)
        dur = len(audio) / sr
        if not (MIN_S <= dur <= MAX_S) or sr != SR:
            continue
        by_speaker[sid].append({"audio": audio, "text": ex["text"].strip()})
        ready = [s for s, u in by_speaker.items() if len(u) >= UTTS_PER_SPEAKER]
        if len(ready) >= SPEAKERS_WANTED:
            break
    return {s: u for s, u in by_speaker.items() if len(u) >= UTTS_PER_SPEAKER}


def build() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    by_speaker = collect_utterances()
    speakers = list(by_speaker)[:SPEAKERS_WANTED]
    if len(speakers) < 3:
        raise SystemExit(f"only {len(speakers)} speakers collected — need ≥3")
    print(f"collected {len(speakers)} speakers: {speakers}")

    gap = np.zeros(int(GAP_S * SR), dtype="float32")
    manifest = []
    for conv_id, recipe in RECIPES:
        # Map the distinct speaker indices used in this recipe → 0-based labels.
        used = sorted({si for si, _ in recipe})
        label_of = {si: i for i, si in enumerate(used)}
        chunks: list[np.ndarray] = []
        turns = []
        t = 0.0
        texts = []
        ok = True
        for si, ui in recipe:
            if si >= len(speakers) or ui >= len(by_speaker[speakers[si]]):
                ok = False
                break
            utt = by_speaker[speakers[si]][ui]
            audio = utt["audio"]
            start = t
            chunks.append(audio)
            chunks.append(gap)
            dur = len(audio) / SR
            end = start + dur
            turns.append({"speaker": label_of[si], "start": round(start, 3),
                          "end": round(end, 3), "text": utt["text"]})
            texts.append(utt["text"])
            t = end + GAP_S
        if not ok:
            print(f"skip {conv_id} (not enough utterances)")
            continue
        wav = np.concatenate(chunks).astype("float32")
        wav_path = OUT_DIR / f"{conv_id}.wav"
        sf.write(wav_path, wav, SR, subtype="PCM_16")
        manifest.append({
            "id": conv_id,
            "language": "en",
            "audio": str(wav_path),
            "num_speakers": len(used),
            "reference": " ".join(texts),
            "turns": turns,
        })
        print(f"wrote {conv_id}: {len(used)} spk, {len(turns)} turns, {t:.1f}s")

    man_path = OUT_DIR / "manifest.jsonl"
    with man_path.open("w", encoding="utf-8") as f:
        for row in manifest:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nmanifest → {man_path}  ({len(manifest)} conversations)")


if __name__ == "__main__":
    build()
