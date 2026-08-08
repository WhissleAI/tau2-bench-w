"""Arm definitions, and the guards that keep them a single-variable ablation.

An ablation whose arms differ in more than the variable is worse than no
ablation, because it produces a confident number about nothing. Everything in
this module exists to make that failure impossible rather than unlikely:

* One :class:`ArmSpec` per arm; the *only* field that may differ between the arms
  under comparison is :attr:`ArmSpec.metadata_mode`. :func:`assert_single_variable`
  checks that, over the actual specs, before anything is spent.
* Model, provider, thinking, max_tokens, system prompt and tools are set once in
  :class:`Decoding` and shared by reference.
* Per case, :func:`build_messages` produces both arms' message lists, and the
  runner asserts arm A's and arm B's user content actually *differ* — the check
  that catches "the metadata block was empty, so arm B silently became arm A".
* The model that actually served is read back off the response (PR #664 returns
  it) and compared, so a silent failover to another vendor is caught rather than
  averaged in.

The metadata block
------------------
:func:`speech_analysis_block` reimplements
``pipecat-bot/bot/services_build.py::_MetadataContextMixin._format_field`` and
``_process_context`` exactly — same field order, same
``EMOTION_``/``INTENT_`` prefix stripping, same ``.capitalize()``, same 5%
probability floor, same top-4 truncation, same ``field=Value(NN%)`` rendering,
same ``[User speech analysis: …]`` wrapper. Arm B is not a plausible imitation of
the metadata layer; it is the metadata layer's own formatter, fed the metadata
layer's own output.

One fidelity caveat, stated rather than buried: production adds that string as a
separate ``developer``-role context message. ``/api/bench/agent-turn`` rejects the
``developer`` role (Anthropic accepts only ``user``/``assistant``), so the block is
delivered as the first line of the same user turn. The model sees the identical
characters in the identical position relative to the utterance; it does not see
an identical role tag.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .corpus import ROUTES, SLOT_KEYS, Case

#: Reimplemented from ``_MetadataContextMixin._METADATA_FIELDS``. ``behavior`` /
#: ``role`` / ``eval`` are in production's list but nothing populates them, so
#: they never render; they are kept here so the two lists can be diffed.
METADATA_FIELDS = ["emotion", "intent", "behavior", "role", "eval"]

#: Channels the cascade produces but production never puts in the prompt. Listed
#: so the report can name them rather than imply them.
PRODUCED_BUT_NOT_INJECTED = ("age", "gender", "entity", "hesitation")


# ---------------------------------------------------------------------------
# The production metadata block
# ---------------------------------------------------------------------------


def _format_field(field_name: str, store: dict, probs: dict) -> Optional[str]:
    """Byte-for-byte ``_MetadataContextMixin._format_field``."""
    entries = probs.get(field_name, []) or []
    if entries and len(entries) > 1:
        top = [
            f"{e['token'].replace('EMOTION_', '').replace('INTENT_', '').replace('GENDER_', '').replace('AGE_', '').capitalize()}({int(e['probability'] * 100)}%)"
            for e in entries[:4]
            if e.get("probability", 0) > 0.05
        ]
        if top:
            return f"{field_name}={'/'.join(top)}"
    val = store.get(field_name)
    if val:
        conf = store.get(f"{field_name}_confidence")
        return f"{field_name}={val}({int(conf * 100)}%)" if conf else f"{field_name}={val}"
    return None


def speech_analysis_block(metadata: Optional[dict[str, Any]]) -> str:
    """The exact string production injects, or ``""`` when the head said nothing.

    An empty return is the signal that arm B would collapse into arm A. The
    runner treats it as an excluded case, never as a measurement.
    """
    if not metadata:
        return ""
    probs = metadata.get("probs") or {}
    parts = [p for f in METADATA_FIELDS if (p := _format_field(f, metadata, probs))]
    if not parts:
        return ""
    return f"[User speech analysis: {', '.join(parts)}]"


def oracle_block(affect: str, intent: str = "") -> str:
    """A *perfect* metadata block, for the exploratory headroom arm.

    Same format, gold values, confidence stated as 100%. This is not a measurement
    of Whissle's head — it is the ceiling a perfect head could reach, which is
    what an accuracy figure has to be multiplied against to mean anything.
    """
    parts = [f"emotion={affect.capitalize()}(100%)"]
    if intent:
        parts.append(f"intent={intent.capitalize()}(100%)")
    return f"[User speech analysis: {', '.join(parts)}]"


# ---------------------------------------------------------------------------
# Decoding — set once, shared by every arm
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are the intake brain for a voice agent at Northgate Health. You receive one "
    "caller turn at a time.\n"
    "\n"
    "Reply with EXACTLY ONE JSON object and nothing else — no prose, no code fence:\n"
    '{"route": "<route>", "slots": {"<key>": "<value>", ...}, '
    '"reply": "<one or two sentences you would say back>"}\n'
    "\n"
    f"route must be exactly one of: {', '.join(ROUTES)}\n"
    f"slots keys may only be: {', '.join(SLOT_KEYS)}\n"
    "\n"
    "Rules for slots:\n"
    "- Include a slot ONLY if the caller actually said its value in this call.\n"
    "- Never guess, never complete a partial value, never carry a value over from "
    "your own general knowledge. An omitted slot is always better than an invented one.\n"
    "- member_id, phone: digits only, no spaces or punctuation.\n"
    "- amount: a plain decimal number, no currency symbol (e.g. 142.50).\n"
    "- date: lowercase month name and day number (e.g. march 14).\n"
    "- caller_name: exactly as the caller gave it.\n"
)


@dataclass
class Decoding:
    """Everything that must be identical across arms, in one object."""

    agent_id: str
    model: str = "claude-sonnet-5"
    provider: str = "claude"
    max_tokens: int = 400
    #: Extended thinking off — a variable-length reasoning budget would make
    #: latency and cost per arm incomparable and add a second source of variance.
    thinking: dict[str, Any] = field(default_factory=lambda: {"type": "disabled"})
    system: str = SYSTEM_PROMPT
    tools: list[dict[str, Any]] = field(default_factory=list)

    def body_for(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "messages": messages,
            "tools": self.tools,
            "system": self.system,
            "max_tokens": self.max_tokens,
            "model": self.model,
            "provider": self.provider,
            "thinking": dict(self.thinking),
        }

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["system_sha"] = _sha(self.system)
        return d


def _sha(s: str) -> str:
    import hashlib

    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Arms
# ---------------------------------------------------------------------------

#: ``off``          no metadata block at all
#: ``production``   the real head's output, production format
#: ``oracle``       gold affect at 100% confidence (exploratory ceiling)
#: ``noisy``        gold affect corrupted to a stated accuracy (exploratory)
METADATA_MODES = ("off", "production", "oracle", "noisy")


@dataclass
class ArmSpec:
    key: str
    label: str
    metadata_mode: str
    description: str = ""
    #: Exploratory arms are not part of the pre-declared primary comparison and
    #: are labelled as such everywhere they appear.
    exploratory: bool = False
    #: For ``noisy``: the accuracy of the simulated channel.
    channel_accuracy: Optional[float] = None
    noise_seed: int = 20260808

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ARM_A = ArmSpec(
    key="A",
    label="A — metadata off",
    metadata_mode="off",
    description=(
        "The brain sees the ASR transcript and nothing else. This is also, exactly, "
        "what the live voice path does today: production STT routes to "
        "AssemblyAI/Sarvam/Deepgram, none of which emit a metadata head."
    ),
)

ARM_B = ArmSpec(
    key="B",
    label="B — metadata present (cascade output, production format)",
    metadata_mode="production",
    description=(
        "The transcript, preceded by the real whissle-large metadata head's output "
        "for the same audio, rendered by production's own formatter. NOT a "
        "reconstruction of the signal — the signal itself, obtained from the batch "
        "path, which is the only place the head is currently reachable."
    ),
)

ARM_B_ORACLE = ArmSpec(
    key="B_oracle",
    label="B-oracle — perfect emotion channel (exploratory)",
    metadata_mode="oracle",
    description=(
        "Ceiling probe: the block carries the case's intended affect at 100% "
        "confidence. Answers 'how much is there to win if the head were perfect', "
        "which is the number a head accuracy has to be multiplied against."
    ),
    exploratory=True,
)

ARM_B_NOISY = ArmSpec(
    key="B_noisy",
    label="B-noisy — emotion channel at 63% accuracy (exploratory)",
    metadata_mode="noisy",
    description=(
        "Sign probe: the block carries the intended affect, corrupted on a fixed "
        "seeded schedule to the ~63% low-arousal accuracy our own documentation "
        "reports. A channel that is 63% accurate can hurt if it is blended "
        "confidently; this measures the sign instead of assuming it."
    ),
    exploratory=True,
    channel_accuracy=0.63,
)

PRIMARY_ARMS = (ARM_A, ARM_B)
ALL_ARMS = (ARM_A, ARM_B, ARM_B_ORACLE, ARM_B_NOISY)


def arm_by_key(key: str) -> ArmSpec:
    for a in ALL_ARMS:
        if a.key == key:
            return a
    raise KeyError(f"unknown arm {key!r}")


# ---------------------------------------------------------------------------
# Message construction
# ---------------------------------------------------------------------------

_CORRUPT_TO = {
    "angry": "neutral", "frustrated": "neutral", "sad": "neutral",
    "happy": "neutral", "neutral": "angry",
}


def _noisy_affect(case: Case, spec: ArmSpec) -> str:
    """Deterministic corruption: the same case is corrupted identically in every
    run, so a re-run reproduces the arm rather than resampling it."""
    import random

    rng = random.Random(f"{spec.noise_seed}:{case.case_id}")
    acc = spec.channel_accuracy if spec.channel_accuracy is not None else 1.0
    if rng.random() < acc:
        return case.gold_affect
    return _CORRUPT_TO.get(case.gold_affect, "neutral")


def user_content(case: Case, perception, spec: ArmSpec) -> str:
    """The user turn for one arm. The block, then the transcript."""
    text = perception.asr_text or case.spoken
    if spec.metadata_mode == "off":
        return text
    if spec.metadata_mode == "production":
        block = speech_analysis_block(perception.metadata)
    elif spec.metadata_mode == "oracle":
        block = oracle_block(case.gold_affect)
    elif spec.metadata_mode == "noisy":
        block = oracle_block(_noisy_affect(case, spec))
    else:
        raise ValueError(f"unknown metadata_mode {spec.metadata_mode!r}")
    if not block:
        return text  # caller must treat this as an excluded case, not a measurement
    return f"{block}\n{text}"


#: Anthropic requires the first message to be ``user``, and every case opens with
#: the agent's greeting. This turn carries no task content and is byte-identical
#: in every arm, so it cannot contribute to a delta.
CALL_OPEN = "(call connected)"


def build_messages(case: Case, perception, spec: ArmSpec) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    if case.context and case.context[0].get("role") != "user":
        msgs.append({"role": "user", "content": CALL_OPEN})
    msgs.extend(dict(m) for m in case.context)
    msgs.append({"role": "user", "content": user_content(case, perception, spec)})
    return msgs


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------


class ArmMismatch(RuntimeError):
    """The arms differ in more than the variable. The run is not a valid ablation."""


def assert_single_variable(specs: list[ArmSpec]) -> None:
    """Every field except ``metadata_mode`` (and the labels that describe it) must
    be identical across the arms being compared."""
    if len(specs) < 2:
        return
    ignore = {"key", "label", "metadata_mode", "description", "exploratory",
              "channel_accuracy", "noise_seed"}
    base = {k: v for k, v in specs[0].to_dict().items() if k not in ignore}
    for s in specs[1:]:
        other = {k: v for k, v in s.to_dict().items() if k not in ignore}
        if other != base:
            raise ArmMismatch(
                f"arms {specs[0].key} and {s.key} differ outside metadata_mode: "
                f"{base} vs {other}"
            )
    modes = [s.metadata_mode for s in specs]
    if len(set(modes)) != len(modes):
        raise ArmMismatch(f"two arms share a metadata_mode: {modes}")


def assert_arms_differ(case_id: str, contents: dict[str, str]) -> None:
    """The check that catches the failure mode this whole design exists to avoid.

    If the metadata block was empty — head down, degenerate distribution, a future
    formatter change — arm B's prompt is character-identical to arm A's, the run
    completes, and every delta is exactly zero. That reads as "metadata does not
    help" and means "metadata was not there". Refuse to score it.
    """
    off = contents.get("A")
    for key, content in contents.items():
        if key == "A":
            continue
        if content == off:
            raise ArmMismatch(
                f"{case_id}: arm {key} prompt is identical to arm A — the metadata "
                "block was empty. This case would score a fake zero delta; it must be "
                "excluded, not measured."
            )


def assert_served_model(case_id: str, served: dict[str, Optional[str]], expected: str) -> None:
    """Trust the response, not the request. PR #664 returns the model that actually
    answered; a silent failover to another vendor mid-run would otherwise be
    averaged straight into the delta."""
    bad = {k: v for k, v in served.items() if v != expected}
    if bad:
        raise ArmMismatch(
            f"{case_id}: expected every arm to be served by {expected!r}, got {bad}. "
            "The arms were not matched on model; the case is not comparable."
        )
