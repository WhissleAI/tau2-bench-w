# Copyright Sierra
"""The AgentClinic doctor PROTOCOL, and the translation between it and the shape a
Whissle agent actually speaks.

AgentClinic's doctor has no function-calling surface: every action is a marker
inside free text (``agentclinic.py``, upstream ``DoctorAgent.system_prompt`` /
``main``)::

    "REQUEST TEST: [test]"      -> routed to the measurement agent
    "REQUEST IMAGES"            -> (NEJM only) the case image is attached
    "DIAGNOSIS READY: [dx]"     -> ends the episode; the moderator grades it

Upstream detects these with a plain, case-SENSITIVE substring test
(``if "DIAGNOSIS READY" in doctor_dialogue``). Anything else is a question to the
patient. We keep that rule *exactly* for scoring, because loosening it would make
our number incomparable with every published baseline — a model that writes
"diagnosis ready:" in lowercase genuinely scores zero upstream. We do additionally
record the LENIENT match (``format_deviation``) so a formatting loss is visible
rather than silently folded into "clinically wrong".

Two protocols, one normalized :class:`DoctorAction`:

``markers`` (default, text mode)
    No tools are advertised. Byte-for-byte the upstream doctor contract, so the
    result is directly comparable to the paper's table.

``tools`` (default in voice mode)
    The same three actions advertised as real tools (``request_test``,
    ``request_images``, ``make_diagnosis``) — the surface our product actually
    exposes, and the only workable one over speech (a doctor is not going to *say*
    "REQUEST TEST colon Chest underscore X dash Ray" out loud). Tool calls and
    markers are parsed by the same function, so both protocols produce identical
    downstream records and identical scoring.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ── upstream marker strings (do not "improve" these) ────────────────────────────

TEST_MARKER = "REQUEST TEST"
IMAGE_MARKER = "REQUEST IMAGES"
DIAGNOSIS_MARKER = "DIAGNOSIS READY"

_TEST_RE = re.compile(r"REQUEST\s+TEST\s*:?\s*(.+)", re.I)
_DX_RE = re.compile(r"DIAGNOSIS\s+READY\s*:?\s*(.+)", re.I | re.S)


# ── the doctor's system prompt (verbatim upstream + optional additions) ─────────

def doctor_system_prompt(
    presentation: Any,
    *,
    max_infs: int,
    infs: int,
    bias_prompt: str = "",
    img_request: bool = False,
    protocol: str = "markers",
) -> str:
    """Rebuild the doctor system prompt for the current turn.

    Upstream regenerates this every inference so the running question budget
    ("You are only allowed to ask N questions total ... You have asked M so far")
    is live; we do the same. The base text is copied verbatim from
    ``DoctorAgent.system_prompt`` — including its missing space before "You may
    also request medical images", which is upstream's, not a typo of ours.

    ``protocol="tools"`` appends a short addendum telling the agent the same three
    actions are available as tools. The marker instructions stay in the prompt so a
    model that prefers to write the marker is still understood.
    """
    base = (
        "You are a doctor named Dr. Agent who only responds in the form of dialogue. "
        "You are inspecting a patient who you will ask questions in order to "
        "understand their disease. You are only allowed to ask {} questions total "
        "before you must make a decision. You have asked {} questions so far. You "
        'can request test results using the format "REQUEST TEST: [test]". For '
        'example, "REQUEST TEST: Chest_X-Ray". Your dialogue will only be 1-3 '
        "sentences in length. Once you have decided to make a diagnosis please type "
        '"DIAGNOSIS READY: [diagnosis here]"'.format(max_infs, infs)
        + (
            'You may also request medical images related to the disease to be '
            'returned with "REQUEST IMAGES".'
            if img_request
            else ""
        )
    )
    presentation_txt = (
        "\n\nBelow is all of the information you have. {}. \n\n Remember, you must "
        "discover their disease by asking them questions. You are also able to "
        "provide exams.".format(presentation)
    )
    out = base + bias_prompt + presentation_txt
    if protocol == "tools":
        out += (
            "\n\n<tools>\nThe same three actions are also available to you as tools: "
            "`request_test` (order a test or measurement), `request_images` (ask for "
            "the case imaging), and `make_diagnosis` (commit to your final diagnosis "
            "and end the consultation). Prefer the tools; the text markers above are "
            "accepted as a fallback. Calling `make_diagnosis` is how you commit — "
            "describing a diagnosis in prose without calling it does not count."
            "\n</tools>"
        )
    return out


# ── tool schemas (Anthropic input_schema shape, what /api/bench takes) ───────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "request_test",
        "description": (
            "Order a diagnostic test, lab panel, imaging study, or physical "
            "measurement for this patient and receive its result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "test": {
                    "type": "string",
                    "description": "The test to order, e.g. 'Chest X-Ray', 'CBC', "
                                   "'Acetylcholine receptor antibodies'.",
                }
            },
            "required": ["test"],
        },
    },
    {
        "name": "request_images",
        "description": "Request the medical images associated with this case.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "make_diagnosis",
        "description": (
            "Commit to the single most likely diagnosis for this patient. This ends "
            "the consultation and is what the case is graded on."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "diagnosis": {
                    "type": "string",
                    "description": "The most likely diagnosis, as a disease name.",
                }
            },
            "required": ["diagnosis"],
        },
    },
]


def tool_schemas(*, img_request: bool = False) -> list[dict[str, Any]]:
    """The doctor's tools. ``request_images`` is only advertised when the case is
    an image case run in request mode (mirrors upstream's prompt gating)."""
    return [
        t for t in TOOL_SCHEMAS
        if img_request or t["name"] != "request_images"
    ]


# ── the normalized action ───────────────────────────────────────────────────────

@dataclass
class DoctorAction:
    """One doctor turn, normalized across marker-text and tool-call protocols.

    ``text`` is what goes into the transcript and, for a diagnosis, what is handed
    to the moderator — upstream passes the WHOLE doctor utterance to the moderator,
    not just the extracted disease name, so we do too.
    """

    kind: str                      # "question"|"test"|"images"|"look"|"diagnosis"
    text: str                      # the doctor's utterance as transcribed
    payload: Optional[str] = None  # test name / diagnosis string
    tool_calls: list[dict] = field(default_factory=list)
    # True when the action was only recognizable case-insensitively — i.e. upstream's
    # exact substring rule would have MISSED it. Scored as upstream scores it, but
    # counted so a formatting loss never masquerades as a clinical loss.
    format_deviation: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.kind == "diagnosis"


def _strict(text: str, marker: str) -> bool:
    """Upstream's detector: case-sensitive substring."""
    return marker in text


def parse_doctor_output(
    reply: str,
    tool_calls: Optional[list[dict]] = None,
    *,
    lenient: bool = True,
) -> DoctorAction:
    """Whissle's turn (free text and/or tool calls) → one :class:`DoctorAction`.

    Precedence mirrors upstream's ``main`` loop: a diagnosis ends the episode, else
    a test request is routed to the measurement agent, else the turn is a question
    to the patient. Tool calls win over markers when both are present (an agent that
    calls ``make_diagnosis`` has committed, whatever its prose says).

    ``lenient`` controls only whether a case-mismatched marker is RECOGNIZED at all;
    when it is recognized that way the action carries ``format_deviation=True`` so
    the report can separate "wrong medicine" from "wrong formatting".
    """
    reply = reply or ""
    calls = list(tool_calls or [])
    by_name = {c.get("name"): c for c in calls if isinstance(c, dict)}

    if "make_diagnosis" in by_name:
        c = by_name["make_diagnosis"]
        dx = str((c.get("arguments") or {}).get("diagnosis") or "").strip()
        rendered = f"{DIAGNOSIS_MARKER}: {dx}" if dx else DIAGNOSIS_MARKER
        text = f"{reply.strip()} {rendered}".strip() if reply.strip() else rendered
        return DoctorAction("diagnosis", text, dx, calls, raw={"via": "tool"})

    if "request_test" in by_name:
        c = by_name["request_test"]
        test = str((c.get("arguments") or {}).get("test") or "").strip()
        rendered = f"{TEST_MARKER}: {test}" if test else TEST_MARKER
        text = f"{reply.strip()} {rendered}".strip() if reply.strip() else rendered
        return DoctorAction("test", text, test, calls, raw={"via": "tool"})

    if "request_images" in by_name:
        text = f"{reply.strip()} {IMAGE_MARKER}".strip() if reply.strip() else IMAGE_MARKER
        return DoctorAction("images", text, None, calls, raw={"via": "tool"})

    if "analyze_image" in by_name:
        # The vision tool from the image-input contract: a READ of the case image,
        # not one of AgentClinic's three actions. Kept distinct so it never consumes
        # a "REQUEST IMAGES" slot or reads as a question to the patient.
        c = by_name["analyze_image"]
        q = str((c.get("arguments") or {}).get("question") or "").strip()
        text = (f"{reply.strip()} [looks at the case image: {q}]".strip()
                if reply.strip() else f"[looks at the case image: {q}]")
        return DoctorAction("look", text, q, calls, raw={"via": "tool"})

    # Marker protocol (also the fallback when a tools-mode agent writes prose).
    if _strict(reply, DIAGNOSIS_MARKER):
        m = _DX_RE.search(reply)
        return DoctorAction("diagnosis", reply, (m.group(1).strip() if m else None),
                            calls, raw={"via": "marker"})
    if _strict(reply, TEST_MARKER):
        m = _TEST_RE.search(reply)
        return DoctorAction("test", reply, (m.group(1).strip() if m else None),
                            calls, raw={"via": "marker"})
    if _strict(reply, IMAGE_MARKER):
        return DoctorAction("images", reply, None, calls, raw={"via": "marker"})

    if lenient:
        m = _DX_RE.search(reply)
        if m:
            return DoctorAction("diagnosis", reply, m.group(1).strip(), calls,
                                format_deviation=True, raw={"via": "marker_lenient"})
        m = _TEST_RE.search(reply)
        if m:
            return DoctorAction("test", reply, m.group(1).strip(), calls,
                                format_deviation=True, raw={"via": "marker_lenient"})
        if re.search(r"REQUEST\s+IMAGES", reply, re.I):
            return DoctorAction("images", reply, None, calls,
                                format_deviation=True, raw={"via": "marker_lenient"})

    return DoctorAction("question", reply, None, calls, raw={"via": "text"})


# ── refusal / non-commitment detection (the safety-boundary measurement) ────────

# Phrases a deliberately non-diagnosing assistant uses. Kept deterministic and
# conservative: these only COUNT when the episode produced no diagnosis at all, so
# a doctor who both diagnoses and says "please see a specialist" is never mislabeled.
_REFUSAL_PATTERNS = [
    r"\b(can(?:'|no)?t|cannot|unable to|not able to|won'?t be able to)\s+"
    r"(?:provide|give|offer|make|offer you|render)?\s*(?:a|an|any)?\s*"
    r"(?:medical\s+)?(?:diagnos\w+|medical advice)",
    r"\b(?:can(?:'|no)?t|cannot|am not able to|not able to|not qualified to|"
    r"not in a position to)\s+diagnos\w+",
    r"\bi(?:'m| am)\s+not\s+(?:a\s+)?(?:doctor|physician|clinician|medical "
    r"professional|licensed|qualified)",
    r"\bnot\s+a\s+substitute\s+for\s+(?:professional\s+)?medical",
    r"\bthis\s+is\s+not\s+medical\s+advice\b",
    r"\b(?:gathering|collecting)\s+information[^.]{0,40}not\s+diagnos",
    r"\bi\s+don'?t\s+diagnose\b",
    r"\bonly\s+a\s+(?:licensed\s+)?(?:doctor|clinician|physician|healthcare provider)"
    r"\s+can\s+diagnos",
    r"\b(?:please\s+)?(?:consult|see|speak (?:to|with)|contact)\s+(?:a|an|your)\s+"
    r"(?:licensed\s+|qualified\s+|healthcare\s+|medical\s+)*"
    r"(?:doctor|physician|clinician|provider|professional|specialist)"
    r"[^.]{0,60}\bfor\s+(?:a\s+)?(?:proper\s+|formal\s+|accurate\s+)?diagnos",
]
_REFUSAL_RE = [re.compile(p, re.I) for p in _REFUSAL_PATTERNS]


def looks_like_refusal(text: str) -> Optional[str]:
    """Return the refusal pattern that matched (evidence), or None.

    This is the deliberate-product-boundary detector: our agents are built to
    *decline to diagnose*, while this benchmark scores diagnostic accuracy. We
    measure that collision instead of prompting it away.
    """
    for rx in _REFUSAL_RE:
        m = rx.search(text or "")
        if m:
            return m.group(0)[:120]
    return None
