"""Diarization Error Rate scorer — the metric must be label-invariant (a diarizer's
speaker *names* don't matter, only whether it splits speech the same way) and must
penalise the three DER error types (missed / false-alarm / confusion)."""
import pytest

pytest.importorskip("scipy")
pytest.importorskip("numpy")

from tau2.voice.transcription.benchmark import diarization_error_rate as der

# Two non-overlapping reference turns, ~3s each.
REF = [{"speaker": 0, "start": 0.0, "end": 3.0},
       {"speaker": 1, "start": 3.4, "end": 6.4}]


def test_perfect_is_zero():
    assert der(REF, [{"speaker": 0, "start": 0.0, "end": 3.0},
                     {"speaker": 1, "start": 3.4, "end": 6.4}])["der"] == 0.0


def test_label_invariant():
    # swapped / renamed hyp labels → still perfect
    m = der(REF, [{"speaker": 7, "start": 0.0, "end": 3.0},
                  {"speaker": 3, "start": 3.4, "end": 6.4}])
    assert m["der"] == 0.0 and m["hyp_speakers"] == 2


def test_undersplit_one_speaker_is_confusion():
    # the whole clip called one speaker → the 2nd speaker's ~3s is all confusion
    m = der(REF, [{"speaker": 0, "start": 0.0, "end": 6.4}])
    assert m["hyp_speakers"] == 1
    assert m["speaker_count_err"] == 1
    assert 0.4 < m["der"] < 0.6           # ~3s confused of ~6s reference


def test_missed_speech():
    # hyp only covers the first turn → 2nd turn (~3s) is missed
    m = der(REF, [{"speaker": 0, "start": 0.0, "end": 3.0}])
    assert m["missed_s"] > 2.5


def test_false_alarm():
    # hyp labels a region where the reference is silent
    m = der(REF, [{"speaker": 0, "start": 0.0, "end": 3.0},
                  {"speaker": 1, "start": 3.4, "end": 6.4},
                  {"speaker": 1, "start": 7.0, "end": 9.0}])
    assert m["false_alarm_s"] > 1.5
