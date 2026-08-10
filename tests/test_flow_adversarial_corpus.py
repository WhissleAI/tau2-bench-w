"""The adversarial corpus is data, so it is validated as data.

A malformed persona or a compliance spec that can never fire is invisible at
run time — the suite simply reports nothing found, which reads identically to a
pass. These checks make that distinguishable before anyone spends a call on it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau2.flow.analyze import DEFAULT_SEVERITY, _compliance_findings

CORPUS = Path(__file__).resolve().parents[1] / "data" / "flow" / "adversarial.json"
DATA = json.loads(CORPUS.read_text())
PERSONAS = DATA["personas"]
SPECS = DATA["compliance_specs"]


class TestPersonas:
    def test_ids_are_unique(self):
        ids = [p["id"] for p in PERSONAS]
        assert len(set(ids)) == len(ids)

    @pytest.mark.parametrize("p", PERSONAS, ids=lambda p: p["id"])
    def test_every_persona_is_complete(self, p):
        assert p["persona"].strip()
        assert p["goal"].strip()
        assert p["expect"].strip()
        assert p["provokes"], f"{p['id']} names no finding it should provoke"

    @pytest.mark.parametrize("p", PERSONAS, ids=lambda p: p["id"])
    def test_provoked_findings_are_real_types(self, p):
        """A persona pointed at a finding type that does not exist can never
        pass or fail — it just silently measures nothing."""
        for t in p["provokes"]:
            assert t in DEFAULT_SEVERITY, f"{p['id']} provokes unknown finding {t!r}"

    @pytest.mark.parametrize("p", PERSONAS, ids=lambda p: p["id"])
    def test_every_persona_targets_an_agent_type_with_a_spec(self, p):
        assert p["agent_type"] in SPECS, f"{p['id']} has no compliance spec"

    def test_the_suite_is_not_only_about_refusing(self):
        """A suite that rewarded refusal would train an agent to stonewall.

        At least a third of the personas must expect the agent to CONTINUE
        helping — the correct answer to a rude caller is service, not a wall.
        """
        helps = [p for p in PERSONAS if any(
            w in p["expect"].lower()
            for w in ("continues", "still books", "completes the call", "still gets", "still disclosed", "skips ahead", "retained")
        )]
        assert len(helps) >= len(PERSONAS) // 3

    def test_more_than_compliance_is_exercised(self):
        """A compliance suite that only ever fires compliance findings is not
        exercising the analyzer, and would not notice if the rest of it broke."""
        provoked = {t for p in PERSONAS for t in p["provokes"]}
        assert provoked - {"compliance"}, "no non-compliance findings are exercised"


class TestComplianceSpecs:
    @pytest.mark.parametrize("name", sorted(SPECS))
    def test_every_spec_has_both_halves(self, name):
        """Prohibitions alone can be passed by an agent that says nothing."""
        spec = SPECS[name]
        assert spec.get("required_disclosures"), f"{name} declares no obligations"
        assert spec.get("forbidden_substrings_lower") or spec.get("disclosure_states"), (
            f"{name} declares no prohibitions"
        )

    @pytest.mark.parametrize("name", sorted(SPECS))
    def test_obligations_are_well_formed(self, name):
        for req in SPECS[name]["required_disclosures"]:
            assert req["id"]
            assert req["any_of"], f"{name}/{req['id']} accepts no phrasing"
            assert req["description"]
            assert all(p == p.lower() for p in req["any_of"]), (
                f"{name}/{req['id']} has a non-lowercase phrasing; matching is lowercased"
            )

    @pytest.mark.parametrize("name", sorted(SPECS))
    def test_a_silent_agent_fails_every_obligation(self, name):
        """The property that makes obligations worth having. If a spec can be
        satisfied by an agent that says nothing, it is not testing anything."""
        spec = {k: v for k, v in SPECS[name].items() if k == "required_disclosures"}
        out = _compliance_findings({}, [], spec, "")
        # by_state obligations whose state was never entered are correctly skipped.
        due = [r for r in SPECS[name]["required_disclosures"] if not r.get("by_state")]
        assert len(out) == len(due), f"{name}: a silent agent escaped an obligation"

    @pytest.mark.parametrize("name", sorted(SPECS))
    def test_a_compliant_agent_passes_every_obligation(self, name):
        """The other direction: a spec nothing can satisfy is equally useless."""
        steps = []
        seq = 0
        for req in SPECS[name]["required_disclosures"]:
            seq += 1
            steps.append({"kind": "say_emitted", "seq": seq, "text": req["any_of"][0]})
        # Enter any deadline states last, so the disclosures precede them.
        for req in SPECS[name]["required_disclosures"]:
            if req.get("by_state"):
                seq += 1
                steps.append({"kind": "state_enter", "seq": seq, "state": req["by_state"]})
        spec = {k: v for k, v in SPECS[name].items() if k == "required_disclosures"}
        assert _compliance_findings({}, steps, spec, "") == []

    def test_forbidden_substrings_are_lowercase(self):
        for name, spec in SPECS.items():
            for w in spec.get("forbidden_substrings_lower") or []:
                assert w == w.lower(), f"{name}: {w!r} will never match"

    def test_debt_collection_gates_on_a_variable_not_a_state(self):
        """The start state is itself a verification state, so gating on state
        entry would open the gate at turn zero and mask every real violation.
        The analyzer documents this; the spec has to honour it."""
        assert SPECS["debt_collection"].get("gate_variable")
