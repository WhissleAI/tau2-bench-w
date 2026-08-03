# Whissle transcription benchmark (pre-recorded audio)

Measures how faithfully the **Whissle platform** transcribes pre-recorded calls and
meetings across the languages the product exposes — **en, hi, te, hinglish,
tenglish** — reported as **WER** (word error rate) and **CER** (character error
rate). It benchmarks the *product surface* (`POST /api/models/transcribe`), so the
underlying engine is chosen server-side from the language and is never named in the
request, the response, or these results — exactly as a customer sees it.

This is separate from the tau2 agentic domains (airline/retail/telecom): those score
task success (pass^k); this scores transcription fidelity. It reuses the repo's
`.env` creds, `results/whissle/` output, and `jiwer` scoring.

It **drives the `whissle` CLI** ([WhissleAI/whissle-cli](https://github.com/WhissleAI/whissle-cli)) — `models tts` to synthesize and `models transcribe` to transcribe — so it exercises exactly what a customer runs, not a private HTTP path.

## Setup

```bash
# 1. Install the whissle CLI so `whissle` is on PATH (or set WHISSLE_CLI to point at it)
npm i -g @whissle/cli        # or: npm link inside a whissle-cli checkout
#   WHISSLE_CLI="node /path/to/whissle-cli/bin/whissle.mjs"   # alternative

# 2. .env (same file the voice benchmark uses)
WHISSLE_API_KEY=wsk_live_...     # a secret key with models:invoke
# WHISSLE_BASE=...               # optional; defaults to the prod gateway
```

The CLI reads the key from `WHISSLE_API_KEY` (or its own `~/.whissle/config.json`).

## Run

```bash
./run_transcribe.sh               # round-trip, all languages, seed set
./run_transcribe.sh hi            # just Hindi
./run_transcribe.sh all 3         # 3 repeats per case (TTS is nondeterministic)

# real labelled clips instead of round-trip
MODE=corpus MANIFEST=data/transcription/my_corpus.jsonl ./run_transcribe.sh
```

Direct (without the wrapper — needs the `voice` extra, which this module lives under):

```bash
uv sync --extra voice
uv run --extra voice python -m tau2.voice.transcription.benchmark run --language te --repeat 2
```

## Diarization (speaker attribution)

Transcription fidelity is *what* was said; diarization is *who* said it. Because
diarizers can't separate synthetic TTS voices, this is a **corpus-only** test on real
multi-speaker audio, scored with **DER** (Diarization Error Rate) + speaker-count
accuracy — all through the CLI (`whissle models transcribe --diarize --json`).

```bash
python scripts/build_diarization_set.py    # build the clips (needs datasets, soundfile, numpy)
uv run --extra voice python -m tau2.voice.transcription.benchmark diarize
```

The clips (`data/transcription/diarization/`) are built by concatenating distinct
real **LibriSpeech** speakers, so the ground-truth speaker timeline is exact. Drop
your own real 2-speaker call recordings into a manifest (`{id, audio, num_speakers,
turns:[{speaker,start,end,text}]}`) to score production audio.

## Two modes

| mode | audio source | what it proves | use for |
|---|---|---|---|
| **round-trip** (default) | platform TTS synthesizes each `reference` | endpoint works, the right engine is picked per language, native script survives | CI regression guard; caught the STT-*translate* model that mangled Hindi |
| **corpus** | your `audio` file per row | real-world WER/CER | vendor comparison, release gating |

**Round-trip is a fidelity smoke test, not an absolute WER** — the same platform both
speaks and hears, and spoken→written normalization (e.g. "ten" vs "10", "appointment"
vs "अपॉइंटमेंट" in code-mixed) shows up as honest, non-zero error. For a defensible
real-world WER, use **corpus** mode with held-out human-labelled clips (FLEURS,
Common Voice hi/te, or de-identified real calls).

## Manifest format

One JSON object per line (`data/transcription/whissle_roundtrip.jsonl` is the seed):

```json
{"id": "hi-appointment", "language": "hi", "reference": "आपका अपॉइंटमेंट ..."}
{"id": "call-042", "language": "en", "reference": "...", "audio": "data/transcription/clips/call-042.wav"}
```

- `id` — stable case id.
- `language` — one of `en · hi · te · hinglish · tenglish`.
- `reference` — ground-truth transcript (native script).
- `tts_text` — *(round-trip, optional)* text to speak if it should differ from `reference`.
- `audio` — *(corpus)* path to a wav/mp3/… clip.

## Scoring

WER + CER via `jiwer` after a fair, script-agnostic normalization (NFC, casefold,
strip punctuation, collapse whitespace). Devanagari/Telugu pass through unchanged.
Results print a per-language + overall summary and are written to
`results/whissle/transcribe_<mode>_<lang>.json` with every hypothesis for audit.
