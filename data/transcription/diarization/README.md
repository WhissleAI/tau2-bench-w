# Diarization test clips

Small multi-speaker clips for the diarization benchmark (`benchmark.py diarize`).

Each `*.wav` is built by **concatenating short utterances from distinct real
speakers** in **LibriSpeech dev-clean**, with a 0.4 s silence between turns — the
standard LibriCSS/LibriMix construction. Real human voices (not TTS, which diarizers
can't separate) with a **ground-truth speaker timeline that is exact by
construction** (`manifest.jsonl` records each turn's speaker/start/end/text).

Regenerate with `python scripts/build_diarization_set.py` (needs `datasets`,
`soundfile`, `numpy`).

## Attribution

Audio derived from **LibriSpeech** (Panayotov et al., 2015), licensed
**CC BY 4.0** — https://www.openslr.org/12. Derivative clips here are redistributed
under the same license.
