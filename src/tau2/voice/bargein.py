# Copyright Sierra
"""Barge-in and turn-taking — scoring what happens when a caller interrupts.

STATUS: SCORER ONLY. The transport cannot yet produce these events; see
"What is missing" at the bottom. This module defines the measurement so the
capture work has a target, and so the metric is settled before there is a
number to be tempted by.

WHY THIS IS A SEPARATE BENCHMARK
--------------------------------
Every other voice number here is measured half-duplex: one party talks at a
time, by construction. That is the right way to measure task success, because it
removes a variable. It also means the entire suite is blind to the single most
common complaint about voice agents — that you cannot interrupt them, or that
they stop the moment you breathe.

Those are opposite failures with the same cause (a threshold), and an agent
tuned to avoid one walks straight into the other. Any honest measurement has to
report both together, which is why nothing here is a single "barge-in score".

THE FOUR OUTCOMES
-----------------
When a caller speaks while the agent is speaking, exactly one of these happens:

  respected   the agent stopped promptly and listened.               (correct)
  ignored     the agent talked over the caller to the end of its turn.
  over_eager  the agent stopped for something that was not an interruption —
              a backchannel ("mhm", "right"), a cough, background speech.
  lost        the agent stopped, but the caller's words never made it into the
              transcript. The worst outcome, and the one that looks like a
              success from the agent's side: it yielded, and heard nothing.

`lost` is called out because a naive implementation counts it as `respected` —
the agent did stop — and a metric that rewards yielding without listening would
push exactly the wrong way.

CUT-IN LATENCY IS DIAGNOSTIC, NEVER THE HEADLINE
------------------------------------------------
How fast the agent stops is measurable and interesting, and it is not the
result. An agent that stops in 80ms for every cough is worse than one that takes
400ms and stops for the right things. Latency is reported for the interruptions
that were correctly respected, and the page's standing rule applies: this is
table stakes, not a moat.

Pure and I/O-free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: What the caller did. `backchannel` and `noise` are NOT interruptions — the
#: correct agent behaviour is to keep talking — and scoring them as though they
#: were is how a suite ends up rewarding an agent that stops for everything.
INTENT = ("interrupt", "backchannel", "noise")

OUTCOMES = ("respected", "ignored", "over_eager", "lost")


@dataclass
class BargeEvent:
    """One moment where the caller spoke while the agent was speaking."""

    id: str
    #: What the caller was actually doing.
    intent: str
    #: What the caller said, for the report.
    utterance: str = ""
    #: Milliseconds into the agent's turn that the caller started.
    offset_ms: Optional[int] = None
    #: Did the agent's audio stop within the observation window?
    agent_stopped: bool = False
    #: Milliseconds from caller-speech-onset to agent-audio-stop.
    cut_in_ms: Optional[int] = None
    #: Did the caller's words reach the agent's transcript?
    caller_heard: bool = False
    #: Set when the session could not be measured at all.
    infra_error: Optional[str] = None

    @property
    def scorable(self) -> bool:
        return not self.infra_error

    @property
    def outcome(self) -> Optional[str]:
        if not self.scorable:
            return None

        if self.intent == "interrupt":
            if not self.agent_stopped:
                return "ignored"
            # It stopped. Whether that is a success depends entirely on whether
            # it then heard anything — see the module docstring.
            return "respected" if self.caller_heard else "lost"

        # A backchannel or a noise is not an interruption. Continuing to speak
        # is correct; stopping is the failure.
        if self.agent_stopped:
            return "over_eager"
        return "respected"


@dataclass
class BargeReport:
    scored: int = 0
    excluded: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    #: Cut-in times for interruptions that were correctly respected.
    cut_in_ms: list[int] = field(default_factory=list)
    #: Counts split by what the caller was doing.
    by_intent: dict[str, dict[str, int]] = field(default_factory=dict)

    def _rate(self, n: int, d: int) -> Optional[float]:
        return None if not d else 100.0 * n / d

    @property
    def real_interruptions(self) -> int:
        return self.by_intent.get("interrupt", {}).get("total", 0)

    @property
    def non_interruptions(self) -> int:
        return sum(
            v.get("total", 0) for k, v in self.by_intent.items() if k != "interrupt"
        )

    @property
    def respected_rate(self) -> Optional[float]:
        """Of GENUINE interruptions, how many the agent both stopped for and
        heard. Denominator is real interruptions only — mixing backchannels in
        would let an agent improve this by ignoring more coughs."""
        d = self.by_intent.get("interrupt", {})
        return self._rate(d.get("respected", 0), d.get("total", 0))

    @property
    def false_barge_rate(self) -> Optional[float]:
        """Of things that were NOT interruptions, how often the agent stopped
        anyway. The counterweight — an agent cannot improve both by moving one
        threshold, which is the entire point of publishing them together."""
        total = self.non_interruptions
        stopped = sum(
            v.get("over_eager", 0) for k, v in self.by_intent.items() if k != "interrupt"
        )
        return self._rate(stopped, total)

    @property
    def lost_rate(self) -> Optional[float]:
        """Of genuine interruptions, how often the agent yielded and heard
        nothing. Counted separately because it masquerades as success."""
        d = self.by_intent.get("interrupt", {})
        return self._rate(d.get("lost", 0), d.get("total", 0))

    @property
    def median_cut_in_ms(self) -> Optional[int]:
        if not self.cut_in_ms:
            return None
        s = sorted(self.cut_in_ms)
        return s[len(s) // 2]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scored": self.scored,
            "excluded": self.excluded,
            "outcomes": dict(self.outcomes),
            "real_interruptions": self.real_interruptions,
            "non_interruptions": self.non_interruptions,
            "respected_rate_pct": self.respected_rate,
            "false_barge_rate_pct": self.false_barge_rate,
            "lost_rate_pct": self.lost_rate,
            "median_cut_in_ms": self.median_cut_in_ms,
            "by_intent": {k: dict(v) for k, v in sorted(self.by_intent.items())},
            "reading": REPORT_READING,
        }


#: Printed with every report, because the two headline rates move against each
#: other and quoting either alone is misleading.
REPORT_READING = (
    "Respected rate and false-barge rate trade off against one another: both are "
    "governed by how sensitive the endpointer is, so an agent can improve either "
    "one by moving a single threshold, at the other's expense. Neither number "
    "means anything on its own and this report never publishes one without the "
    "other. The lost rate is separate and is not a trade-off — an agent that "
    "stops and hears nothing is failing at both."
)


def score_events(events: Iterable[BargeEvent]) -> BargeReport:
    rep = BargeReport()
    for e in events:
        if not isinstance(e, BargeEvent):
            continue
        if not e.scorable:
            rep.excluded += 1
            continue
        outcome = e.outcome
        if outcome is None:
            rep.excluded += 1
            continue

        rep.scored += 1
        rep.outcomes[outcome] = rep.outcomes.get(outcome, 0) + 1
        bucket = rep.by_intent.setdefault(
            e.intent, {"total": 0, **{o: 0 for o in OUTCOMES}}
        )
        bucket["total"] += 1
        bucket[outcome] = bucket.get(outcome, 0) + 1

        if e.intent == "interrupt" and outcome == "respected" and e.cut_in_ms is not None:
            rep.cut_in_ms.append(e.cut_in_ms)
    return rep


# ---------------------------------------------------------------------------
# WHAT IS MISSING, AND WHERE IT GOES
# ---------------------------------------------------------------------------
#
# The scorer above is complete and tested. Nothing can feed it yet, because the
# harness has never deliberately spoken over the agent — `voice_transport.py`
# does the opposite by design: its turn detector is careful NOT to treat a
# mid-turn pause as a barge-in, since for every other suite an accidental
# interruption is a measurement error.
#
# Three pieces are needed, in this order:
#
# 1. PUBLISH WHILE THE BOT IS SPEAKING. `VoiceTransport` currently synthesises
#    and publishes the caller's turn after the bot's turn ends. Barge-in needs a
#    publish scheduled at a chosen offset INTO the bot's turn — the natural seam
#    is beside the existing `synth`/publish path, driven by the same
#    `agent_speech_last_t()` clock the detector already reads.
#
# 2. A STOP TIMESTAMP, NOT A STOP FLAG. `cut_in_ms` needs the moment the agent's
#    audio actually stopped. `bot_stopped_total()` is a counter and
#    `agent_speech_last_t()` is the last speech sample — the latter is the
#    honest anchor and is already used elsewhere for exactly this reason, so
#    cut-in is (last speech sample after the interrupt) minus (caller onset).
#
# 3. DID IT HEAR US. `caller_heard` is the field that decides `respected` from
#    `lost`, and it cannot be inferred from the audio — it needs the interrupt
#    utterance to appear in the agent's transcript for that turn. The data
#    channel already carries the agent's view of what the caller said.
#
# Until all three exist, `score_events` has no producer, and no barge-in number
# should appear anywhere. A partial implementation would most likely report a
# `respected` for every case where the agent went quiet, which is the one
# failure mode this design exists to prevent.
