# Copyright Sierra
"""Spelled-identifier robustness — scoring the failure that costs us voice.

WHY THIS EXISTS
---------------
The published gap between our text and voice retail runs is 61.4% → 20.0%, and
the diagnosis in the trajectories is specific: the agent loses on identifiers a
caller says out loud. An order id, a postcode, an email, and — in the run that
made this undeniable — a customer's own name. The τ² voice suite proves the gap
exists but cannot isolate it, because every task also involves tools, policy and
a multi-turn conversation, so a failure could be any of them.

This measures the one thing. A caller says an identifier; the agent has to end
up holding that exact string. Nothing else is being tested, so a number here
moves when recognition moves and not when the prompt changes.

THE SCORING RULE, AND WHY IT IS BRUTAL
--------------------------------------
Exact match after normalisation. No partial credit in the headline.

That is deliberate and it is not conservatism for its own sake: an identifier is
a database key. `yusuf_rossi_9620` heard as "Youssef Rossi" does not return a
worse customer, it returns NO customer, and the call fails. A metric that gave
half marks for getting half the characters would report steady improvement while
the product kept failing exactly as often. Character error rate is computed too,
but as a DIAGNOSTIC — it tells you how close the miss was, which is what decides
whether a retry could plausibly recover it — and never as the headline.

WHAT NORMALISATION MAY AND MAY NOT DO
-------------------------------------
It may absorb differences in how a machine formats a string it heard correctly:
case, surrounding whitespace, the separators in a phone number, a spelled-out
"at" in an email. Those are transcription conventions, not hearing failures, and
penalising them would measure our formatter rather than our recognition.

It may NOT absorb a different string. Homophones stay wrong. "Yusuf" and
"Youssef" normalise to different values and always will, because the lookup they
produce is different. Every rule below is written to fail closed: if it is not
certain two forms are the same identifier, they are different.

Pure and I/O-free.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: The kinds of identifier a caller is asked to say. Each scores separately,
#: because the failure rates are wildly different and one blended number would
#: hide which one to go and fix.
CATEGORIES = ("person_name", "order_id", "postcode", "email", "phone", "reference")

#: Spoken forms of characters that carry no information in the identifier
#: itself. Only ever applied inside the category that defines them.
_EMAIL_SPOKEN = (
    (r"\s+at\s+", "@"),
    (r"\s+dot\s+", "."),
    (r"\s+underscore\s+", "_"),
    (r"\s+dash\s+", "-"),
    (r"\s+hyphen\s+", "-"),
)


def _strip_accents(s: str) -> str:
    """Fold accents, because a synthesiser and a recogniser disagree about them
    far more often than a human would, and "José"/"Jose" is the same lookup in
    every system we integrate with."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def normalize(value: str, category: str = "reference") -> str:
    """Canonical form for comparison. Fails closed — see the module docstring.

    Category-specific because the same transformation is right in one place and
    wrong in another: stripping every non-alphanumeric character is correct for
    a phone number and destroys an email address.
    """
    if value is None:
        return ""
    s = _strip_accents(str(value)).strip().lower()
    s = re.sub(r"\s+", " ", s)

    if category == "email":
        for pat, rep in _EMAIL_SPOKEN:
            s = re.sub(pat, rep, s)
        return s.replace(" ", "")

    if category == "phone":
        # Separators carry no information; a leading country code does.
        return re.sub(r"[^\d+]", "", s)

    if category == "postcode":
        return re.sub(r"[^a-z0-9]", "", s)

    if category in ("order_id", "reference"):
        # Underscores and hyphens are part of the key in some schemes and
        # formatting in others, so they are kept. Whitespace is not.
        return s.replace(" ", "")

    if category == "person_name":
        # Punctuation between name parts varies; the parts themselves do not.
        s = re.sub(r"[^a-z0-9 ]", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    return s


def char_error_rate(truth: str, heard: str) -> float:
    """Levenshtein distance over the length of the truth. Diagnostic only.

    Returns 0.0 for an exact match and can exceed 1.0 when the heard string is
    much longer than the truth — which is a real outcome (the agent captures a
    whole phrase instead of the identifier) and must not be silently clipped, or
    the worst failures would look like the moderate ones.
    """
    t, h = truth or "", heard or ""
    if not t:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, tc in enumerate(t, 1):
        cur = [i]
        for j, hc in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (tc != hc)))
        prev = cur
    return prev[-1] / len(t)


@dataclass
class IdentifierAttempt:
    """One capture of one identifier, as the agent ended up holding it."""

    #: What the agent captured — the tool-call argument, not its prose.
    captured: Optional[str]
    #: 1 for the first capture, 2+ after the agent re-asked.
    attempt: int = 1
    #: True when the agent spelled or read the value back to the caller.
    readback: bool = False
    #: True when the agent asked again rather than proceeding on a bad value.
    reasked: bool = False


@dataclass
class IdentifierCase:
    """One identifier a caller was asked to say, and everything that happened."""

    id: str
    category: str
    #: The ground truth, exactly as the database holds it.
    truth: str
    #: How the caller was told to say it, kept for the report.
    spoken_as: str = ""
    attempts: list[IdentifierAttempt] = field(default_factory=list)
    #: Set when the session never ran; excluded rather than scored as a miss.
    infra_error: Optional[str] = None

    # ── verdicts ──────────────────────────────────────────────────────────────

    @property
    def scorable(self) -> bool:
        return not self.infra_error and bool(self.attempts)

    def _matches(self, a: IdentifierAttempt) -> bool:
        return bool(a.captured) and normalize(a.captured, self.category) == normalize(
            self.truth, self.category
        )

    @property
    def first_pass(self) -> Optional[bool]:
        """Heard correctly on the first attempt — the number that matters most,
        because a caller who has to repeat their own name has already had a bad
        experience even if the call ultimately succeeds."""
        if not self.scorable:
            return None
        return self._matches(self.attempts[0])

    @property
    def final_pass(self) -> Optional[bool]:
        """Correct by the end, however many attempts it took."""
        if not self.scorable:
            return None
        return any(self._matches(a) for a in self.attempts)

    @property
    def recovered(self) -> Optional[bool]:
        """Wrong first, right later. The measure of whether retrying works.

        None when it was right first time — a case that never needed recovery
        is not evidence about recovery, and averaging it in as a success would
        make the retry strategy look better the more often it was unnecessary.
        """
        if not self.scorable or self.first_pass:
            return None
        return self.final_pass

    @property
    def best_cer(self) -> Optional[float]:
        if not self.scorable:
            return None
        t = normalize(self.truth, self.category)
        return min(char_error_rate(t, normalize(a.captured or "", self.category))
                   for a in self.attempts)

    @property
    def had_readback(self) -> Optional[bool]:
        if not self.scorable:
            return None
        return any(a.readback for a in self.attempts)


@dataclass
class IdentifierReport:
    """Scored result over a set of identifier cases."""

    scored: int = 0
    excluded: int = 0
    exclusion_reasons: dict[str, int] = field(default_factory=dict)
    first_pass: int = 0
    final_pass: int = 0
    needed_recovery: int = 0
    recovered: int = 0
    readbacks: int = 0
    cers: list[float] = field(default_factory=list)
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)

    def _rate(self, n: int, d: int) -> Optional[float]:
        return None if not d else 100.0 * n / d

    @property
    def first_pass_rate(self) -> Optional[float]:
        """THE HEADLINE."""
        return self._rate(self.first_pass, self.scored)

    @property
    def final_pass_rate(self) -> Optional[float]:
        return self._rate(self.final_pass, self.scored)

    @property
    def recovery_rate(self) -> Optional[float]:
        """Of the ones heard wrong, how many were fixed. Denominator is the
        cases that actually needed recovering, never the whole set."""
        return self._rate(self.recovered, self.needed_recovery)

    @property
    def readback_rate(self) -> Optional[float]:
        return self._rate(self.readbacks, self.scored)

    @property
    def mean_cer(self) -> Optional[float]:
        return None if not self.cers else sum(self.cers) / len(self.cers)

    def category_rate(self, category: str) -> Optional[float]:
        c = self.by_category.get(category)
        if not c or not c.get("scored"):
            return None
        return 100.0 * c.get("first_pass", 0) / c["scored"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scored": self.scored,
            "excluded": self.excluded,
            "exclusion_reasons": dict(self.exclusion_reasons),
            "first_pass": self.first_pass,
            "first_pass_rate_pct": self.first_pass_rate,
            "final_pass": self.final_pass,
            "final_pass_rate_pct": self.final_pass_rate,
            "needed_recovery": self.needed_recovery,
            "recovered": self.recovered,
            "recovery_rate_pct": self.recovery_rate,
            "readback_rate_pct": self.readback_rate,
            "mean_cer": self.mean_cer,
            "by_category": {
                k: {**v, "first_pass_rate_pct": self.category_rate(k)}
                for k, v in sorted(self.by_category.items())
            },
        }


def score_cases(cases: Iterable[IdentifierCase]) -> IdentifierReport:
    rep = IdentifierReport()
    for c in cases:
        if not c.scorable:
            rep.excluded += 1
            key = (c.infra_error or "no attempt recorded").split(":")[0][:60]
            rep.exclusion_reasons[key] = rep.exclusion_reasons.get(key, 0) + 1
            continue

        rep.scored += 1
        bucket = rep.by_category.setdefault(
            c.category, {"scored": 0, "first_pass": 0, "final_pass": 0}
        )
        bucket["scored"] += 1

        if c.first_pass:
            rep.first_pass += 1
            bucket["first_pass"] += 1
        else:
            rep.needed_recovery += 1
            if c.recovered:
                rep.recovered += 1
        if c.final_pass:
            rep.final_pass += 1
            bucket["final_pass"] += 1
        if c.had_readback:
            rep.readbacks += 1
        cer = c.best_cer
        if cer is not None:
            rep.cers.append(cer)
    return rep
