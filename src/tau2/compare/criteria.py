# Copyright Sierra
"""Deterministic criterion evaluation, with "cannot tell" as a first-class answer.

WHY NO JUDGE LLM
----------------
The obvious way to score "did the agent recover from the mis-heard name?" is to
ask a model. We do not, for the reason ``tau2.health.model_router.is_independent``
exists: in a Whissle-vs-vendor comparison, any judge is either Whissle's own
model (marking its own homework) or the competitor's (marking its competitor's).
Every criterion here is therefore a substring, a regex, or a fact about the tool
record — checkable by hand from the transcript printed in the report.

THE THIRD ANSWER
----------------
:class:`CheckResult.passed` is ``Optional[bool]``. ``None`` means *we cannot
tell*, and it is returned wherever the evidence needed does not exist — a vendor
that publishes no tool record, a run that never happened, a turn index the
conversation never reached. It is never collapsed into ``False``, because a
missing record and a wrong answer are different findings and only one of them is
about the agent.

Crucially, ``None`` is also never collapsed into ``True``. The comparison layer
resolves an unknown to "cannot tell", not to the incumbent's favour.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from tau2.compare.vendors.base import (
    REASON_VENDOR_NO_TOOL_RECORD,
    ScenarioRun,
)

SCOPE_ANY = "any_turn"
SCOPE_ALL = "all_turns"
SCOPE_FINAL = "final_turn"
SCOPE_TURN = "turn"
#: Everything from turn ``index`` onward. The scope most of these scenarios need:
#: "after the caller corrected you" is a range, not a turn, and scoping a
#: correction check to the whole conversation would fail on the pre-correction
#: turn where the agent was still right to be wrong.
SCOPE_FROM = "from_turn"


class CheckSpecError(ValueError):
    """A criterion this evaluator does not know how to run.

    Raised rather than silently returning "cannot tell": an unimplemented check
    that reports as unknown would quietly hollow out a scenario."""


@dataclass
class CheckResult:
    """One criterion's outcome against one run."""

    criterion_id: str
    passed: Optional[bool]
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    critical: bool = False

    @property
    def unknown(self) -> bool:
        return self.passed is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "passed": self.passed,
            "outcome": (
                "cannot_tell"
                if self.passed is None
                else ("pass" if self.passed else "fail")
            ),
            "reason": self.reason,
            "critical": self.critical,
            "evidence": self.evidence,
        }


# ── turn selection ──────────────────────────────────────────────────────────────


def _scoped_turns(run: ScenarioRun, spec: dict[str, Any]):
    """The turns a check applies to, or ``(None, reason)`` when it cannot apply."""
    scope = spec.get("scope", SCOPE_ANY)
    if not run.turns:
        return None, "the run produced no turns"
    if scope in (SCOPE_ANY, SCOPE_ALL):
        return list(run.turns), None
    if scope == SCOPE_FINAL:
        return [run.turns[-1]], None
    if scope in (SCOPE_TURN, SCOPE_FROM):
        idx = spec.get("index")
        if not isinstance(idx, int):
            raise CheckSpecError(
                f"scope {scope!r} requires an integer 'index' (1-based)"
            )
        match = [
            t for t in run.turns
            if (t.index == idx if scope == SCOPE_TURN else t.index >= idx)
        ]
        if not match:
            return None, (
                f"the conversation never reached turn {idx} "
                f"(it ran {len(run.turns)} turn(s))"
            )
        return match, None
    raise CheckSpecError(f"unknown scope {scope!r}")


def _norm(text: str) -> str:
    """Lower-case, collapse whitespace and strip punctuation that varies between
    a spoken confirmation and a written one, so "Tuesday, 10 a.m." and
    "tuesday 10 am" compare equal. Deliberately crude and inspectable."""
    t = (text or "").lower()
    t = t.replace(".", " ").replace(",", " ").replace("-", " ")
    return re.sub(r"\s+", " ", t).strip()


def _contains(haystack: str, needle: str) -> bool:
    return _norm(needle) in _norm(haystack)


# ── the checks ──────────────────────────────────────────────────────────────────


def _reply_text_check(run: ScenarioRun, spec: dict[str, Any], kind: str):
    turns, why = _scoped_turns(run, spec)
    if turns is None:
        return CheckResult("", None, why or "no turns in scope")
    values = [str(v) for v in (spec.get("values") or [])]
    if not values:
        raise CheckSpecError(f"{kind} requires a non-empty 'values' list")
    hits = {
        v: [t.index for t in turns if _contains(t.reply, v)]
        for v in values
    }
    scope_all = spec.get("scope") == SCOPE_ALL
    evidence = {"hits": hits, "turns_considered": [t.index for t in turns]}

    if kind == "reply_contains_any":
        ok = any(hits[v] for v in values)
        return CheckResult(
            "",
            ok,
            (
                f"replies contain {[v for v in values if hits[v]]}"
                if ok
                else f"no reply contained any of {values}"
            ),
            evidence,
        )
    if kind == "reply_contains_all":
        missing = [v for v in values if not hits[v]]
        return CheckResult(
            "",
            not missing,
            "all required phrases present" if not missing
            else f"replies never contained {missing}",
            evidence,
        )
    if kind == "reply_not_contains_any":
        present = [v for v in values if hits[v]]
        return CheckResult(
            "",
            not present,
            "none of the forbidden phrases appeared" if not present
            else f"forbidden phrase(s) {present} appeared at turn(s) "
                 f"{[i for v in present for i in hits[v]]}",
            evidence,
        )
    if kind == "every_reply_contains_any":  # scope_all semantics, explicit
        bad = [t.index for t in turns if not any(_contains(t.reply, v) for v in values)]
        return CheckResult(
            "",
            not bad,
            "every reply in scope matched" if not bad
            else f"turn(s) {bad} matched none of {values}",
            {**evidence, "scope_all": scope_all},
        )
    raise CheckSpecError(f"unknown text check {kind!r}")


def _regex_check(run: ScenarioRun, spec: dict[str, Any], negate: bool):
    turns, why = _scoped_turns(run, spec)
    if turns is None:
        return CheckResult("", None, why or "no turns in scope")
    pattern = spec.get("pattern")
    if not pattern:
        raise CheckSpecError("regex check requires a 'pattern'")
    rx = re.compile(pattern, re.IGNORECASE)
    matched = [t.index for t in turns if rx.search(t.reply or "")]
    ok = (not matched) if negate else bool(matched)
    return CheckResult(
        "",
        ok,
        (
            f"pattern /{pattern}/ matched at turn(s) {matched}"
            if matched
            else f"pattern /{pattern}/ matched no reply"
        ),
        {"pattern": pattern, "matched_turns": matched},
    )


def _tool_calls(run: ScenarioRun, spec: dict[str, Any]):
    """Tool calls in scope, or a ``CheckResult`` explaining why we cannot look."""
    if not run.tools_visible:
        return None, CheckResult("", None, REASON_VENDOR_NO_TOOL_RECORD)
    turns, why = _scoped_turns(run, spec)
    if turns is None:
        return None, CheckResult("", None, why or "no turns in scope")
    calls = [
        {**c, "_turn": t.index}
        for t in turns
        for c in (t.tools or [])
    ]
    name = spec.get("tool") or spec.get("name")
    if name:
        calls = [c for c in calls if str(c.get("name") or c.get("tool")) == name]
    return calls, None


def _arg_names(spec: dict[str, Any], kind: str) -> list[str]:
    """``arg`` accepts a name or a list of aliases.

    Deliberate: we do not control the agent's tool schema, and a check that only
    knows ``date_of_birth`` would silently pass an agent whose field is ``dob``.
    A missing alias must not read as a clean run."""
    arg = spec.get("arg")
    names = [arg] if isinstance(arg, str) else list(arg or [])
    if not names:
        raise CheckSpecError(f"{kind} requires 'arg' (a name or list of aliases)")
    return [str(n) for n in names]


def _arg_value(call: dict[str, Any], arg: str | list[str]) -> Any:
    args = call.get("arguments")
    if not isinstance(args, dict):
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
    names = [arg] if isinstance(arg, str) else list(arg or [])
    for name in names:
        if args.get(name) is not None:
            return args.get(name)
    return None


def evaluate_check(spec: dict[str, Any], run: ScenarioRun) -> CheckResult:
    """Run one check spec against one run. Never raises on data — only on a spec
    this evaluator does not implement."""
    kind = spec.get("kind")
    if not run.runnable:
        return CheckResult(
            "", None,
            run.not_runnable_reason or "the vendor was not runnable",
        )
    if run.error:
        return CheckResult("", None, f"the run errored: {run.error}")

    if kind in (
        "reply_contains_any",
        "reply_contains_all",
        "reply_not_contains_any",
        "every_reply_contains_any",
    ):
        return _reply_text_check(run, spec, kind)
    if kind == "reply_matches_regex":
        return _regex_check(run, spec, negate=False)
    if kind == "reply_not_matches_regex":
        return _regex_check(run, spec, negate=True)

    if kind == "asks_a_question":
        turns, why = _scoped_turns(run, spec)
        if turns is None:
            return CheckResult("", None, why or "no turns in scope")
        asked = [t.index for t in turns if "?" in (t.reply or "")]
        return CheckResult(
            "", bool(asked),
            f"question mark present at turn(s) {asked}" if asked
            else "no reply in scope asked a question",
            {"question_turns": asked},
        )

    if kind in ("tool_called", "tool_not_called", "no_tool_calls"):
        calls, blocked = _tool_calls(run, spec)
        if blocked is not None:
            return blocked
        names = sorted({str(c.get("name") or c.get("tool")) for c in calls})
        if kind == "tool_called":
            return CheckResult(
                "", bool(calls),
                f"{len(calls)} matching call(s): {names}" if calls
                else f"no call to {spec.get('tool')!r} was made",
                {"calls": calls},
            )
        if kind == "tool_not_called":
            return CheckResult(
                "", not calls,
                f"{spec.get('tool')!r} was not called" if not calls
                else f"{spec.get('tool')!r} WAS called at turn(s) "
                     f"{[c['_turn'] for c in calls]}",
                {"calls": calls},
            )
        return CheckResult(
            "", not calls,
            "no tool was called" if not calls else f"tools called: {names}",
            {"calls": calls},
        )

    if kind in ("tool_arg_contains", "tool_arg_not_contains"):
        calls, blocked = _tool_calls(run, spec)
        if blocked is not None:
            return blocked
        names = _arg_names(spec, kind)
        arg = "/".join(names)
        values = [str(v) for v in (spec.get("values") or [])]
        if not values:
            raise CheckSpecError(f"{kind} requires a non-empty 'values'")
        if not calls:
            return CheckResult(
                "", None,
                f"{spec.get('tool')!r} was never called, so its {arg!r} argument "
                "cannot be inspected — this is a missing observation, not a wrong "
                "value",
                {"calls": []},
            )
        observed = [{"turn": c["_turn"], "value": _arg_value(c, names)} for c in calls]
        seen = [o for o in observed if o["value"] is not None]
        if not seen:
            return CheckResult(
                "", None,
                f"{spec.get('tool')!r} was called but carried no {arg!r} argument",
                {"observed": observed},
            )
        matched = [
            o for o in seen
            if any(_contains(str(o["value"]), v) for v in values)
        ]
        if kind == "tool_arg_contains":
            return CheckResult(
                "", bool(matched),
                f"{arg}={[o['value'] for o in matched]} matched {values}" if matched
                else f"{arg} was {[o['value'] for o in seen]}, none matching {values}",
                {"observed": observed},
            )
        return CheckResult(
            "", not matched,
            f"{arg} avoided {values}" if not matched
            else f"{arg}={[o['value'] for o in matched]} contained a forbidden value",
            {"observed": observed},
        )

    if kind == "tool_arg_echoed_in_reply":
        # The said-vs-emitted check: whatever the agent WROTE must appear in what
        # it SAID. MedAgentBench's headline failure is the inverse — an agent that
        # narrates an order it never filed — and this is the mirror that catches a
        # write diverging from the confirmation.
        calls, blocked = _tool_calls(run, spec)
        if blocked is not None:
            return blocked
        names = _arg_names(spec, "tool_arg_echoed_in_reply")
        arg = "/".join(names)
        if not calls:
            return CheckResult(
                "", None,
                f"{spec.get('tool')!r} was never called, so there is no write to "
                "compare against the spoken confirmation",
                {"calls": []},
            )
        rows = []
        for c in calls:
            value = _arg_value(c, names)
            if value is None:
                continue
            later = [t for t in run.turns if t.index >= c["_turn"]]
            echoed = [t.index for t in later if _contains(t.reply, str(value))]
            rows.append({"turn": c["_turn"], "value": value, "echoed_at": echoed})
        if not rows:
            return CheckResult(
                "", None,
                f"no call carried a {arg!r} argument to compare",
                {"calls": calls},
            )
        bad = [r for r in rows if not r["echoed_at"]]
        return CheckResult(
            "", not bad,
            "every written value was echoed in the agent's spoken confirmation"
            if not bad
            else f"written {arg} {[r['value'] for r in bad]} never appeared in any "
                 "reply — the write and the confirmation diverge",
            {"rows": rows},
        )

    if kind == "tool_arg_grounded_in_user":
        # The anti-fabrication check. A field the caller never supplied must not
        # arrive in a tool call with a value. Three cases, and only one is a fail:
        #   * no call carries the field         → PASS  (nothing was invented)
        #   * the value appears in what the caller said → PASS (grounded)
        #   * the value appears nowhere in the caller's words → FAIL (fabricated)
        # Note the asymmetry with tool_arg_contains: "the tool was never called" is
        # a PASS here, not a "cannot tell", because the claim being tested is a
        # negative one — nothing was made up — and an absent write satisfies it.
        calls, blocked = _tool_calls(run, spec)
        if blocked is not None:
            return blocked
        names = _arg_names(spec, kind)
        said = " \n ".join(t.user or "" for t in run.turns)
        carried = []
        for c in calls or []:
            value = _arg_value(c, names)
            if value is None:
                continue
            carried.append({
                "turn": c["_turn"],
                "value": value,
                "grounded": _contains(said, str(value)),
            })
        if not carried:
            return CheckResult(
                "", True,
                f"no tool call carried {'/'.join(names)}, so nothing was invented "
                "for it",
                {"calls_inspected": len(calls or [])},
            )
        ungrounded = [c for c in carried if not c["grounded"]]
        return CheckResult(
            "", not ungrounded,
            "every written value appears in what the caller actually said"
            if not ungrounded
            else f"FABRICATED: {[c['value'] for c in ungrounded]} was written to "
                 f"{'/'.join(names)} but the caller never said it",
            {"carried": carried},
        )

    if kind == "conversation_completed":
        return CheckResult(
            "", bool(run.turns) and bool((run.turns[-1].reply or "").strip()),
            "the agent replied on the final turn"
            if run.turns and (run.turns[-1].reply or "").strip()
            else "the agent produced no reply on the final turn",
            {"n_turns": len(run.turns)},
        )

    raise CheckSpecError(f"unknown check kind {kind!r}")


def evaluate(scenario: Any, run: ScenarioRun) -> list[CheckResult]:
    """Every criterion of a scenario against one vendor's run."""
    results: list[CheckResult] = []
    for criterion in scenario.pass_criteria:
        try:
            res = evaluate_check(criterion.check, run)
        except CheckSpecError as exc:
            res = CheckResult("", None, f"criterion could not be evaluated: {exc}")
        res.criterion_id = criterion.id
        res.critical = criterion.critical
        results.append(res)
    return results


def verdict(results: list[CheckResult]) -> tuple[Optional[bool], str]:
    """Roll criterion results into one scenario outcome.

    ``None`` (cannot tell) wins over a pass and loses to a critical failure:
      * any CRITICAL criterion failing → ``False`` (fabrication / write integrity
        cannot be offset by passes elsewhere);
      * else any unknown → ``None``, because a scenario with an unmeasured
        criterion has not been established;
      * else all-pass → ``True``, any fail → ``False``.
    """
    if not results:
        return None, "no criteria were evaluated"
    critical_fail = [r for r in results if r.critical and r.passed is False]
    if critical_fail:
        return False, (
            "critical criterion failed: "
            + "; ".join(f"{r.criterion_id} ({r.reason})" for r in critical_fail)
        )
    unknown = [r for r in results if r.passed is None]
    if unknown:
        return None, (
            "cannot tell — "
            + "; ".join(f"{r.criterion_id}: {r.reason}" for r in unknown)
        )
    failed = [r for r in results if r.passed is False]
    if failed:
        return False, (
            "failed: " + "; ".join(f"{r.criterion_id} ({r.reason})" for r in failed)
        )
    return True, "all criteria passed"
