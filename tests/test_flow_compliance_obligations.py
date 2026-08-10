"""Compliance obligations — the disclosures an agent MUST make.

The existing compliance checks answer "did the agent say something it must
not". That is the half an agent can pass by saying almost nothing. These cover
the other half, where silence is the failure.
"""

from __future__ import annotations

import pytest

from tau2.flow.analyze import _compliance_findings, _required_disclosure_findings

RECORDING = {
    "id": "recording_notice",
    "any_of": ["this call is recorded", "this call may be recorded", "recording this call"],
    "description": "the caller must be told the call is recorded",
}

MINI_MIRANDA = {
    "id": "debt_notice",
    "any_of": ["attempt to collect a debt", "debt collector"],
    "by_state": "disclose_balance",
    "description": "the collection notice must precede any discussion of the debt",
}


def say(seq, text):
    return {"kind": "say_emitted", "seq": seq, "text": text}


def enter(seq, state):
    return {"kind": "state_enter", "seq": seq, "state": state}


class TestDeadlineFree:
    def test_satisfied_anywhere_in_the_call(self):
        steps = [say(1, "Hello."), say(9, "By the way, this call is recorded.")]
        assert _required_disclosure_findings(steps, {"required_disclosures": [RECORDING]}, "") == []

    def test_silence_is_a_violation(self):
        steps = [say(1, "Hello."), say(2, "How can I help?")]
        out = _required_disclosure_findings(steps, {"required_disclosures": [RECORDING]}, "hello how can i help")
        assert len(out) == 1
        assert out[0].type == "compliance"
        assert out[0].severity == "high"
        assert "recording_notice" in out[0].detail

    def test_any_accepted_phrasing_satisfies_it(self):
        """A compliant disclosure is a meaning, not a magic string. Pinning one
        wording would measure prompt drift rather than compliance."""
        for phrasing in RECORDING["any_of"]:
            steps = [say(1, f"Just so you know, {phrasing}.")]
            assert _required_disclosure_findings(steps, {"required_disclosures": [RECORDING]}, "") == []

    def test_the_transcript_is_a_fallback_when_the_trace_missed_a_turn(self):
        """An agent must not fail for a gap in OUR tracing."""
        out = _required_disclosure_findings([], {"required_disclosures": [RECORDING]},
                                            "hello, this call is recorded, how can i help")
        assert out == []


class TestStateDeadline:
    def test_made_before_the_deadline_state(self):
        steps = [
            say(1, "This is an attempt to collect a debt."),
            enter(2, "disclose_balance"),
            say(3, "Your balance is 412 dollars."),
        ]
        assert _required_disclosure_findings(steps, {"required_disclosures": [MINI_MIRANDA]}, "") == []

    def test_made_too_late_is_still_a_violation(self):
        """Saying it afterwards does not un-disclose the balance."""
        steps = [
            enter(1, "disclose_balance"),
            say(2, "Your balance is 412 dollars."),
            say(3, "This is an attempt to collect a debt."),
        ]
        out = _required_disclosure_findings(steps, {"required_disclosures": [MINI_MIRANDA]}, "")
        assert len(out) == 1
        assert "before entering 'disclose_balance'" in out[0].detail

    def test_a_deadline_that_never_arrived_is_not_a_violation(self):
        """The call never reached the state, so the obligation never came due.
        Failing here would punish an agent for a conversation that ended early
        for an unrelated reason."""
        steps = [say(1, "Hello."), enter(2, "wrong_party"), say(3, "Sorry to bother you.")]
        assert _required_disclosure_findings(steps, {"required_disclosures": [MINI_MIRANDA]}, "") == []


class TestTurnDeadline:
    def test_within_the_first_n_turns(self):
        req = {**RECORDING, "by_turn": 2}
        steps = [say(1, "Hello."), say(2, "This call is recorded."), say(3, "Now then.")]
        assert _required_disclosure_findings(steps, {"required_disclosures": [req]}, "") == []

    def test_after_the_deadline_turn_is_a_violation(self):
        req = {**RECORDING, "by_turn": 2}
        steps = [say(1, "Hello."), say(2, "How can I help?"), say(3, "This call is recorded.")]
        out = _required_disclosure_findings(steps, {"required_disclosures": [req]}, "")
        assert len(out) == 1
        assert "within the first 2 agent turn(s)" in out[0].detail

    def test_a_short_call_has_not_missed_its_turn_deadline(self):
        """Fewer turns than the deadline means the deadline never elapsed."""
        req = {**RECORDING, "by_turn": 5}
        steps = [say(1, "Hello.")]
        out = _required_disclosure_findings(steps, {"required_disclosures": [req]}, "hello")
        assert len(out) == 1  # still missing, but scored deadline-free
        assert out[0].evidence["deadline"] == "anywhere in the call"


class TestDegenerate:
    def test_no_obligations_declared_is_not_a_finding(self):
        assert _required_disclosure_findings([say(1, "hi")], {}, "hi") == []

    @pytest.mark.parametrize("junk", [None, "nope", 3, {}, {"any_of": []}])
    def test_malformed_requirements_are_skipped_not_crashed(self, junk):
        assert _required_disclosure_findings([say(1, "hi")], {"required_disclosures": [junk]}, "hi") == []

    def test_obligations_run_alongside_prohibitions(self):
        """Both halves reach the caller through the same entry point."""
        spec = {
            "forbidden_substrings_lower": ["your balance is"],
            "required_disclosures": [RECORDING],
        }
        steps = [say(1, "Your balance is 412 dollars.")]
        out = _compliance_findings({}, steps, spec, "your balance is 412 dollars.")
        kinds = {f.evidence.get("substring") or f.evidence.get("requirement") for f in out}
        assert kinds == {"your balance is", "recording_notice"}
        assert all(f.severity == "high" for f in out)
