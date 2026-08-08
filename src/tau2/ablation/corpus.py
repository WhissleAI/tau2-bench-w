"""The frozen, pre-declared case list.

The corpus is **declared before the run and never edited after seeing results**.
It is generated deterministically from :func:`build_corpus` (fixed pools, fixed
templates, fixed seed) and frozen to ``data/ablation/metadata_ablation_v1.json``;
the runner loads the frozen file and refuses a corpus whose digest does not match
what the run recorded. Regenerating and re-freezing is a new corpus version, not
an edit to this one — which is the only way "no cherry-picked task subsets" can be
a property of the artifact rather than a promise in prose.

Three slices, because the question is *which channel* earns its place and a blended
score cannot answer it:

``entity``   utterances carrying names, dates, IDs, amounts and phone numbers.
             This is where the cascade's mechanism predicts gains, and where a
             general word-error rate would dilute them to invisibility.
``intent``   utterances whose correct routing is unambiguous to a human but whose
             surface form does not name the action ("I don't think I can make
             Thursday" → reschedule). If the intent head carries anything, it
             carries it here.
``emotion``  affect-laden utterances with an intended affect and a required
             acknowledgement. Measures the *sign* of the emotion channel, not its
             assumed benefit.

Each case is one caller turn against a fixed one-turn context, deliberately: a
multi-turn conversation would let turn-to-turn variance swamp a per-turn signal,
and would stop the two arms being the same experiment run twice.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

CORPUS_VERSION = "metadata_ablation_v1"
CORPUS_PATH = Path("data/ablation/metadata_ablation_v1.json")

#: The routing label set the agent must choose from. Fixed and closed — an open
#: label set turns routing accuracy into a string-matching exercise.
ROUTES = (
    "book_appointment",
    "reschedule_appointment",
    "cancel_appointment",
    "billing_question",
    "update_contact_details",
    "prescription_refill",
    "escalate_to_human",
    "other",
)

#: The slot keys the agent may fill. ``member_id``/``amount``/``phone`` are the
#: DIGIT slots and ``caller_name`` the PROPER-NOUN slot; those two families are
#: scored separately because they are where the mechanism predicts an effect.
SLOT_KEYS = ("caller_name", "member_id", "date", "amount", "phone")
DIGIT_SLOTS = ("member_id", "amount", "phone")
NOUN_SLOTS = ("caller_name",)

AFFECTS = ("neutral", "angry", "frustrated", "sad", "happy")

GREETING = "Thanks for calling Northgate Health. How can I help you today?"


@dataclass
class Case:
    case_id: str
    slice: str
    spoken: str
    gold_route: str
    gold_slots: dict[str, str]
    gold_affect: str = "neutral"
    #: Negative affect cases require the reply to acknowledge the caller's state
    #: before acting. This is the deterministic half of "did emotion help?".
    requires_acknowledgement: bool = False
    context: list[dict[str, str]] = field(default_factory=lambda: [
        {"role": "assistant", "content": GREETING}
    ])
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Case":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# ---------------------------------------------------------------------------
# Pools. Names are chosen to span scripts and syllable counts an English ASR
# finds easy and hard; a corpus of Smiths would report a proper-noun error rate
# of zero and prove nothing.
# ---------------------------------------------------------------------------

NAMES = [
    "Priya Raghunathan", "Michael Okonkwo", "Sarah Whitfield", "Arjun Balasubramanian",
    "Elena Kowalczyk", "James Carter", "Fatima Al-Rashid", "Wei Zhang",
    "Nkechi Adeyemi", "Thomas Bergström", "Ana Sofia Delgado", "Rahul Venkataraman",
    "Grace Liu", "Daniel O'Sullivan", "Yuki Tanaka", "Ingrid Halvorsen",
    "Omar Haddad", "Charlotte Ashworth", "Kwame Mensah", "Isabella Rossi",
]

DATES = [
    ("March fourteenth", "march 14"), ("April second", "april 2"),
    ("June twenty third", "june 23"), ("the fifth of May", "may 5"),
    ("October thirty first", "october 31"), ("February ninth", "february 9"),
    ("July eighteenth", "july 18"), ("December first", "december 1"),
    ("August twenty seventh", "august 27"), ("November sixth", "november 6"),
]

MEMBER_IDS = [
    "482917", "730155", "6041289", "915374", "228806",
    "5573401", "864220", "199548", "3082617", "740913",
]

AMOUNTS = [
    ("one hundred and forty two dollars and fifty cents", "142.50"),
    ("eighty nine dollars", "89.00"),
    ("three hundred and seven dollars", "307.00"),
    ("twelve dollars and ninety nine cents", "12.99"),
    ("two thousand four hundred and sixty dollars", "2460.00"),
    ("fifty five dollars and twenty cents", "55.20"),
    ("six hundred and eighteen dollars", "618.00"),
    ("nine hundred and ninety dollars", "990.00"),
]

PHONES = [
    ("four one five, five five five, zero one nine two", "4155550192"),
    ("two zero two, five five five, eight three four one", "2025558341"),
    ("six five zero, five five five, seven seven two six", "6505557726"),
    ("three one two, five five five, four nine zero eight", "3125554908"),
    ("nine one seven, five five five, one six three five", "9175551635"),
]


def _spell(digits: str) -> str:
    """'482917' → 'four eight two nine one seven'. Spoken digit strings are the
    hard case for an ASR and the whole point of the digit-slot family."""
    words = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
             "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}
    return " ".join(words[d] for d in digits)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

ENTITY_TEMPLATES: list[tuple[str, str, tuple[str, ...]]] = [
    ("Hi, this is {name}, member ID {id_spoken}, and I need to move my appointment to {date_spoken}.",
     "reschedule_appointment", ("caller_name", "member_id", "date")),
    ("Good morning — {name} speaking, my member number is {id_spoken}. I'd like to book something for {date_spoken}.",
     "book_appointment", ("caller_name", "member_id", "date")),
    ("This is {name}. Please cancel the {date_spoken} appointment under member {id_spoken}.",
     "cancel_appointment", ("caller_name", "member_id", "date")),
    ("My name's {name} and I was charged {amount_spoken} — member ID {id_spoken}. Can you explain that?",
     "billing_question", ("caller_name", "member_id", "amount")),
    ("Hello, {name} here. My new number is {phone_spoken}, member {id_spoken}.",
     "update_contact_details", ("caller_name", "member_id", "phone")),
    ("It's {name}, member {id_spoken}. I need my prescription refilled before {date_spoken}.",
     "prescription_refill", ("caller_name", "member_id", "date")),
    ("{name} calling. The invoice says {amount_spoken} but I paid on {date_spoken}.",
     "billing_question", ("caller_name", "amount", "date")),
    ("Hi — {name}. Reach me on {phone_spoken} about the {date_spoken} slot.",
     "update_contact_details", ("caller_name", "phone", "date")),
]

#: Intent-slice utterances: the correct route is unambiguous to a human, but the
#: surface form never names it. Paired with a distractor route that a text-only
#: reading plausibly picks.
INTENT_CASES: list[tuple[str, str, str]] = [
    ("I don't think I'm going to be able to make Thursday after all.", "reschedule_appointment", "surface says nothing about rescheduling"),
    ("Something's come up at work and Tuesday just isn't going to happen.", "reschedule_appointment", "implied reschedule"),
    ("Actually, forget it — I won't be needing that slot at all any more.", "cancel_appointment", "implied cancel, not reschedule"),
    ("I've moved house, so everything you've got on file for me is out of date.", "update_contact_details", "implied contact update"),
    ("There's a number on my statement I really don't recognise.", "billing_question", "implied billing"),
    ("I'm down to my last two tablets.", "prescription_refill", "implied refill"),
    ("Is there someone more senior I could talk to about this?", "escalate_to_human", "explicit escalation"),
    ("Nothing you've suggested is going to work for me, honestly.", "escalate_to_human", "implied escalation"),
    ("I've got a free morning next week if anything's going.", "book_appointment", "implied booking"),
    ("What's the earliest you could fit me in?", "book_appointment", "implied booking"),
    ("The card on file expired last month.", "update_contact_details", "implied details update"),
    ("I already paid this, twice actually.", "billing_question", "implied billing dispute"),
    ("My repeat ran out and the pharmacy turned me away.", "prescription_refill", "implied refill"),
    ("Put me down for whenever the doctor's back from leave.", "book_appointment", "implied booking"),
    ("I'd rather not come in at all, if that's still an option.", "cancel_appointment", "implied cancel"),
]

#: Emotion-slice utterances. ``requires_acknowledgement`` is True for the negative
#: affects: the correct behaviour is to acknowledge before acting, which is a
#: deterministic property of the reply text, not a judge's opinion.
EMOTION_CASES: list[tuple[str, str, str, bool]] = [
    ("This is the third time I've called about this and nobody has done anything.", "angry", "escalate_to_human", True),
    ("I have been on hold for forty minutes. Forty minutes.", "angry", "escalate_to_human", True),
    ("Honestly this is completely unacceptable and I want it sorted out now.", "angry", "escalate_to_human", True),
    ("You people charged me twice and then told me it was my fault.", "angry", "billing_question", True),
    ("I'm really not happy about how this has been handled.", "angry", "escalate_to_human", True),
    ("I keep getting bounced around and nobody can tell me anything useful.", "frustrated", "escalate_to_human", True),
    ("I've tried the website four times and it just doesn't work.", "frustrated", "escalate_to_human", True),
    ("Look, I just want to know when my appointment is. That's all.", "frustrated", "other", True),
    ("Every time I ring I get a different answer.", "frustrated", "escalate_to_human", True),
    ("I don't understand why this has to be so complicated.", "frustrated", "other", True),
    ("My mother passed away last week, so I need to cancel her appointment.", "sad", "cancel_appointment", True),
    ("I've not been coping very well since the diagnosis, to be honest.", "sad", "escalate_to_human", True),
    ("I'm a bit frightened about what the results are going to say.", "sad", "other", True),
    ("It's been a really hard month and I've fallen behind on everything.", "sad", "billing_question", True),
    ("I'm just very tired and I don't really know what to do next.", "sad", "escalate_to_human", True),
    ("That's brilliant news, thank you so much for sorting it out.", "happy", "other", False),
    ("Oh wonderful, that works perfectly for me.", "happy", "other", False),
    ("You've been incredibly helpful, I really appreciate it.", "happy", "other", False),
    ("Great, then let's get the next one in the diary.", "happy", "book_appointment", False),
    ("Perfect. Same time next month would be ideal.", "happy", "book_appointment", False),
    ("I'd like to check what time my appointment is on Friday.", "neutral", "other", False),
    ("Could you tell me whether my prescription has gone through?", "neutral", "prescription_refill", False),
    ("I need to update the phone number you have for me.", "neutral", "update_contact_details", False),
    ("I'd like to book a follow-up, please.", "neutral", "book_appointment", False),
    ("Can you confirm the balance on my account?", "neutral", "billing_question", False),
]


def build_corpus(seed: int = 20260808) -> list[Case]:
    """Deterministically construct the corpus. Same seed → byte-identical file."""
    rng = random.Random(seed)
    cases: list[Case] = []

    # ---- entity slice ---------------------------------------------------
    for i in range(40):
        tpl, route, keys = ENTITY_TEMPLATES[i % len(ENTITY_TEMPLATES)]
        name = NAMES[rng.randrange(len(NAMES))]
        mid = MEMBER_IDS[rng.randrange(len(MEMBER_IDS))]
        date_spoken, date_norm = DATES[rng.randrange(len(DATES))]
        amt_spoken, amt_norm = AMOUNTS[rng.randrange(len(AMOUNTS))]
        ph_spoken, ph_norm = PHONES[rng.randrange(len(PHONES))]
        spoken = tpl.format(
            name=name, id_spoken=_spell(mid), date_spoken=date_spoken,
            amount_spoken=amt_spoken, phone_spoken=ph_spoken,
        )
        gold = {}
        for k in keys:
            gold[k] = {
                "caller_name": name, "member_id": mid, "date": date_norm,
                "amount": amt_norm, "phone": ph_norm,
            }[k]
        cases.append(Case(
            case_id=f"ent_{i + 1:03d}", slice="entity", spoken=spoken,
            gold_route=route, gold_slots=gold, gold_affect="neutral",
            note="slot-fill on names / dates / IDs / amounts",
        ))

    # ---- intent slice ---------------------------------------------------
    for i in range(30):
        spoken, route, why = INTENT_CASES[i % len(INTENT_CASES)]
        # Second pass over the fixture varies the opening turn rather than the
        # utterance, so the pair is a different context, not a duplicate case.
        ctx = [{"role": "assistant", "content": GREETING}]
        if i >= len(INTENT_CASES):
            ctx = [{"role": "assistant",
                    "content": "Northgate Health, this is the appointments line. What can I do for you?"}]
        cases.append(Case(
            case_id=f"int_{i + 1:03d}", slice="intent", spoken=spoken,
            gold_route=route, gold_slots={}, gold_affect="neutral",
            context=ctx, note=why,
        ))

    # ---- emotion slice --------------------------------------------------
    for i in range(30):
        spoken, affect, route, ack = EMOTION_CASES[i % len(EMOTION_CASES)]
        ctx = [{"role": "assistant", "content": GREETING}]
        if i >= len(EMOTION_CASES):
            ctx = [{"role": "assistant",
                    "content": "Northgate Health, this is the appointments line. What can I do for you?"}]
        cases.append(Case(
            case_id=f"emo_{i + 1:03d}", slice="emotion", spoken=spoken,
            gold_route=route, gold_slots={}, gold_affect=affect,
            requires_acknowledgement=ack, context=ctx,
            note=f"intended affect: {affect}",
        ))

    return cases


def corpus_digest(cases: list[Case]) -> str:
    """Content hash of the corpus. Recorded on the run so a later reader can prove
    the arms saw the same task list and that the list was not edited afterwards."""
    payload = json.dumps([c.to_dict() for c in cases], sort_keys=True,
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def freeze(path: Path = CORPUS_PATH, seed: int = 20260808) -> Path:
    cases = build_corpus(seed)
    doc = {
        "version": CORPUS_VERSION,
        "seed": seed,
        "digest": corpus_digest(cases),
        "routes": list(ROUTES),
        "slot_keys": list(SLOT_KEYS),
        "n": len(cases),
        "slices": {s: sum(1 for c in cases if c.slice == s)
                   for s in ("entity", "intent", "emotion")},
        "cases": [c.to_dict() for c in cases],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load(path: Path = CORPUS_PATH, slices: Optional[list[str]] = None) -> tuple[list[Case], dict]:
    """Load the frozen corpus and verify its digest.

    A digest mismatch means the file was hand-edited after freezing, which would
    silently invalidate every comparison made against it. It is an error, not a
    warning."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [Case.from_dict(c) for c in doc["cases"]]
    actual = corpus_digest(cases)
    if actual != doc.get("digest"):
        raise ValueError(
            f"corpus digest mismatch: file says {doc.get('digest')}, content is {actual}. "
            "The frozen corpus was edited after it was declared — regenerate it as a "
            "new version rather than reusing this one."
        )
    if slices:
        cases = [c for c in cases if c.slice in slices]
    meta = {k: v for k, v in doc.items() if k != "cases"}
    return cases, meta


if __name__ == "__main__":  # pragma: no cover
    p = freeze()
    cs, meta = load(p)
    print(f"froze {len(cs)} cases → {p}  digest={meta['digest']}  slices={meta['slices']}")
