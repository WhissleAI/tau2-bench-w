"""Deterministic grading, per channel.

No LLM judge in the primary metrics. Every headline number here is computed from
string comparisons against a gold value that was fixed before the run, because a
judge shared between two arms of an ablation adds a second stochastic process to
a difference that is already small.

Four families, because "which channel earns its place" is the question:

``route_correct``      intent / routing — did the agent pick the right branch
``slot_*``             entities — exact-match slot fill, split into DIGIT slots
                       (member_id, amount, phone) and PROPER-NOUN slots
                       (caller_name), which is where the mechanism predicts gains
                       and where a general WER would hide them
``fabricated_slots``   write integrity — a slot value the caller never said,
                       reaching a payload the agent is asking to have written
``acknowledged``       emotion — did a negative-affect turn get acknowledged
                       before being acted on

Write integrity deserves its definition stated plainly, because it is the metric
most easily faked. A slot is *fabricated* when the value the agent emitted cannot
be recovered from what the caller actually said — checked against BOTH the ASR
transcript the agent saw and the gold spoken text, so an ASR error is scored as a
transcription failure, not as the model inventing data. Only a value that is in
neither is counted as fabrication.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .corpus import DIGIT_SLOTS, NOUN_SLOTS, ROUTES, SLOT_KEYS, Case

_MONTHS = ("january february march april may june july august september "
           "october november december").split()

_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "thirtieth": 30,
}

_ACK_MARKERS = (
    "sorry", "apolog", "understand", "i hear", "i can hear", "frustrat",
    "appreciate", "that sounds", "that must", "i'm sorry", "condolence",
    "sympath", "thank you for your patience", "must be", "difficult",
    "unacceptable", "shouldn't have", "should not have", "let me help",
    "i realise", "i realize", "glad", "wonderful", "great to hear", "pleased",
    "delighted", "happy to hear",
)


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def norm_name(s: str) -> str:
    s = _strip_accents(str(s or "")).lower()
    s = re.sub(r"[^a-z ]+", " ", s)
    return " ".join(s.split())


def norm_digits(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def norm_amount(s: str) -> str:
    d = re.sub(r"[^0-9.]", "", str(s or ""))
    if not d:
        return ""
    try:
        return f"{float(d):.2f}"
    except ValueError:
        return ""


def norm_date(s: str) -> str:
    """'March 14th' / '2026-03-14' / 'march 14' → 'march 14'."""
    s = str(s or "").lower().strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return f"{_MONTHS[int(m.group(2)) - 1]} {int(m.group(3))}"
    month = next((mo for mo in _MONTHS if mo in s), "")
    dm = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", s)
    day = dm.group(1) if dm else ""
    if not day:
        for w, v in _NUM_WORDS.items():
            if re.search(rf"\b{w}\b", s):
                day = str(v)
                break
    return f"{month} {int(day)}".strip() if month and day else s


def normalise(key: str, value: Any) -> str:
    if key == "caller_name":
        return norm_name(value)
    if key == "date":
        return norm_date(value)
    if key == "amount":
        return norm_amount(value)
    if key in ("member_id", "phone"):
        return norm_digits(value)
    return str(value or "").strip().lower()


# ---------------------------------------------------------------------------
# Parsing the agent's reply
# ---------------------------------------------------------------------------


@dataclass
class Parsed:
    ok: bool = False
    route: str = ""
    slots: dict[str, str] = field(default_factory=dict)
    reply: str = ""
    parse_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_reply(text: str) -> Parsed:
    """Pull the JSON action out of the model's reply, tolerantly.

    A parse failure is recorded, never silently coerced into a wrong answer: an
    unparseable reply is scored as incorrect on routing but is excluded from
    fabrication counting, because we cannot see what payload it meant to emit.
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    obj: Optional[dict] = None
    try:
        obj = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        return Parsed(ok=False, parse_error="no JSON object in reply", reply=raw[:400])
    slots = obj.get("slots") or {}
    if not isinstance(slots, dict):
        slots = {}
    return Parsed(
        ok=True,
        route=str(obj.get("route") or "").strip(),
        slots={str(k): ("" if v is None else str(v)) for k, v in slots.items()},
        reply=str(obj.get("reply") or ""),
    )


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


@dataclass
class CaseGrade:
    case_id: str
    slice: str
    arm: str

    parsed_ok: bool = False
    route: str = ""
    route_correct: bool = False
    route_valid: bool = False

    slots_expected: int = 0
    slots_correct: int = 0
    slots_missing: int = 0
    slots_wrong: int = 0
    digit_expected: int = 0
    digit_correct: int = 0
    noun_expected: int = 0
    noun_correct: int = 0

    fabricated_slots: list[str] = field(default_factory=list)
    hallucinated_keys: list[str] = field(default_factory=list)

    acknowledged: Optional[bool] = None
    reply_chars: int = 0

    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def slot_accuracy(self) -> Optional[float]:
        return (self.slots_correct / self.slots_expected) if self.slots_expected else None

    @property
    def fabricated(self) -> bool:
        return bool(self.fabricated_slots)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["slot_accuracy"] = self.slot_accuracy
        d["fabricated"] = self.fabricated
        return d


def _spoken_haystack(case: Case, asr_text: str) -> str:
    """Everything the caller can be said to have provided, normalised for search."""
    return norm_name(f"{asr_text} {case.spoken}") + " " + norm_digits(f"{asr_text} {case.spoken}")


def _value_is_grounded(key: str, value: str, case: Case, asr_text: str) -> bool:
    """Can this emitted value be recovered from what the caller said?

    Checked against the ASR text *and* the gold spoken text so an ASR error is not
    charged to the model as a fabrication.
    """
    v = normalise(key, value)
    if not v:
        return True  # an empty slot cannot be a fabrication
    both = f"{asr_text} {case.spoken}"
    if key in DIGIT_SLOTS:
        return v in norm_digits(both) if v else True
    if key == "caller_name":
        hay = norm_name(both)
        return all(tok in hay for tok in v.split() if len(tok) > 2)
    if key == "date":
        return norm_date(both).startswith(v.split()[0]) if v else True
    return v in norm_name(both)


def grade_case(case: Case, arm: str, reply_text: str, asr_text: str) -> CaseGrade:
    p = parse_reply(reply_text)
    g = CaseGrade(case_id=case.case_id, slice=case.slice, arm=arm)
    g.parsed_ok = p.ok
    g.route = p.route
    g.route_valid = p.route in ROUTES
    g.route_correct = bool(p.ok and p.route == case.gold_route)
    g.reply_chars = len(p.reply)
    g.detail["raw_reply"] = (reply_text or "")[:1200]
    g.detail["parse_error"] = p.parse_error

    # -- slots ------------------------------------------------------------
    for key, gold in case.gold_slots.items():
        g.slots_expected += 1
        is_digit = key in DIGIT_SLOTS
        is_noun = key in NOUN_SLOTS
        if is_digit:
            g.digit_expected += 1
        if is_noun:
            g.noun_expected += 1
        got = p.slots.get(key, "")
        if not got:
            g.slots_missing += 1
            continue
        if normalise(key, got) == normalise(key, gold):
            g.slots_correct += 1
            if is_digit:
                g.digit_correct += 1
            if is_noun:
                g.noun_correct += 1
        else:
            g.slots_wrong += 1

    # -- write integrity --------------------------------------------------
    if p.ok:
        for key, value in p.slots.items():
            if key not in SLOT_KEYS:
                g.hallucinated_keys.append(key)
                continue
            if not _value_is_grounded(key, value, case, asr_text):
                g.fabricated_slots.append(f"{key}={value}")

    # -- emotion ----------------------------------------------------------
    if case.slice == "emotion":
        low = (p.reply or reply_text or "").lower()
        g.acknowledged = any(m in low for m in _ACK_MARKERS)

    return g


# ---------------------------------------------------------------------------
# Cascade-level measurement: what the ASR itself got right
# ---------------------------------------------------------------------------


@dataclass
class AsrGrade:
    """A property of the cascade, not of an arm — measured once per case.

    This is the raw material the entity channel would carry. It bounds what any
    downstream entity consumption could possibly achieve: a slot the ear never
    heard cannot be filled correctly by a consumer of the ear's output.
    """

    case_id: str
    digit_slots: int = 0
    digit_recoverable: int = 0
    noun_slots: int = 0
    noun_recoverable: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def grade_asr(case: Case, asr_text: str) -> AsrGrade:
    a = AsrGrade(case_id=case.case_id)
    hay_digits = norm_digits(asr_text)
    hay_words = norm_name(asr_text)
    misses = []
    for key, gold in case.gold_slots.items():
        v = normalise(key, gold)
        if key in DIGIT_SLOTS:
            a.digit_slots += 1
            if v and v in hay_digits:
                a.digit_recoverable += 1
            else:
                misses.append(f"{key}={gold}")
        elif key in NOUN_SLOTS:
            a.noun_slots += 1
            toks = [t for t in v.split() if len(t) > 2]
            if toks and all(t in hay_words for t in toks):
                a.noun_recoverable += 1
            else:
                misses.append(f"{key}={gold}")
    a.detail["missed"] = misses
    a.detail["asr_text"] = asr_text
    return a
