"""Honesty rules, enforced in code rather than asked for in a style guide.

A prose convention decays the first time somebody is in a hurry. These are five
executable rules that run over both the :class:`~tau2.reporting.model.RunReport`
and the *rendered* Markdown, and a report that violates one does not get written
unless the caller explicitly passes ``--allow-violations``.

  R1  no headline number without N
  R2  a non-independent judge is disclosed wherever the number appears
  R3  the exclusion rate is shown next to the score, never buried
  R4  anything below the sample-size threshold is labelled preliminary
  R5  the underlying LLM providers are never named in agent-facing text
      (published external baselines are expected, and exempt)

The mechanism for R1–R4 is a single machine-checkable **qualifier**: every place
the headline value is stated, the qualifier must be on the same line. The renderer
cannot emit the value any other way, and the linter re-derives the requirement
from the report rather than trusting the renderer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .model import PRELIMINARY_N_THRESHOLD, RunReport

# Sentinels the renderer wraps exempt spans in. HTML comments: invisible in every
# Markdown viewer, trivially greppable, and they survive a copy-paste of the file.
ALLOW_PROVIDERS_OPEN = "<!-- honesty:allow-providers -->"
ALLOW_PROVIDERS_CLOSE = "<!-- /honesty:allow-providers -->"
ALLOW_CONTEXT_OPEN = "<!-- honesty:allow-context -->"
ALLOW_CONTEXT_CLOSE = "<!-- /honesty:allow-context -->"

#: Vendor/model tokens that must not appear in text describing *our* agent.
#: Superset of the publishing gate in the website repo's ``benchmarkdata.test.ts``
#: so a string that passes here cannot fail there.
PROVIDER_TOKENS: tuple[str, ...] = (
    "gemini",
    "anthropic",
    "openai",
    "gpt",
    "claude",
    "llama",
    "mistral",
    "qwen",
    "gemma",
    "deepseek",
    "o3-mini",
    "elevenlabs",
    "deepgram",
    "sarvam",
    "cartesia",
    "bedrock",
    "vertex ai",
)

# Boundaries are `\w` only, deliberately not `[\w-]`: a hyphen must not shield a
# vendor name. "Claude-powered", "GPT-4o" and "Gemini-1.5 Pro" are all hits, which is
# the point — the marketing forms are exactly the ones that leak.
_PROVIDER_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(t) for t in sorted(PROVIDER_TOKENS, key=len, reverse=True)) + r")(?!\w)",
    re.IGNORECASE,
)


#: What a redacted provider name is replaced with. Visible on purpose — a silent
#: substitution would be a second kind of dishonesty.
REDACTION = "[model provider]"


def redact_providers(text: str) -> str:
    """Strip vendor names out of a string quoted verbatim from a run artifact.

    Error payloads and stack traces name the upstream model provider, and those
    strings are the most valuable evidence in a failure analysis — quoting them is
    right, publishing the vendor name is not. The substitution is marked so a reader
    can see that something was removed rather than wondering whether the log really
    read that way.
    """
    return _PROVIDER_RE.sub(REDACTION, text or "")


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str
    location: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        loc = f" [{self.location}]" if self.location else ""
        return f"{self.rule}: {self.message}{loc}"


# --------------------------------------------------------------------------
# The qualifier: one string that satisfies R1–R4 at once.
# --------------------------------------------------------------------------


def required_tokens(report: RunReport) -> list[str]:
    """The substrings that must accompany every statement of the headline value."""
    toks = [f"N = {report.n_scored}"]
    if report.exclusions.any:
        toks.append(f"{report.exclusions.n_excluded}/{report.exclusions.n_total} excluded")
    if report.judge.needs_disclosure:
        toks.append("judge not independent")
    if report.preliminary:
        toks.append("PRELIMINARY")
    return toks


def qualifier(report: RunReport) -> str:
    """The compliant annotation. Rendered next to every statement of the number."""
    bits = [f"N = {report.n_scored}"]
    if report.exclusions.any:
        bits.append(
            f"{report.exclusions.n_excluded}/{report.exclusions.n_total} excluded "
            f"({report.exclusions.rate_pct:.1f}%)"
        )
    if report.judge.needs_disclosure:
        bits.append("judge not independent")
    if report.preliminary:
        bits.append("PRELIMINARY")
    return " · ".join(bits)


def headline_claim(report: RunReport) -> str:
    """The only sanctioned way to state the headline number in prose."""
    return f"**{report.headline.formatted()}** ({qualifier(report)})"


def headline_pattern(report: RunReport) -> re.Pattern[str]:
    """Matches a bare statement of the headline value.

    Word-bounded so ``4.25`` does not match inside ``14.257``; percent-aware so
    ``54.0%`` and ``54%`` both count as the claim being restated.
    """
    v = report.headline.value
    if v is None:
        return re.compile(r"(?!)")  # matches nothing
    if report.headline.unit == "pct":
        body = rf"{v:.1f}\s*%|{v:.0f}\s*%" if float(v).is_integer() else rf"{v:.1f}\s*%"
    else:
        body = re.escape(f"{v:.2f}")
    return re.compile(rf"(?<![\d.]){body}(?![\d])")


# --------------------------------------------------------------------------
# Span bookkeeping for the exempt regions
# --------------------------------------------------------------------------


def _spans(text: str, open_tag: str, close_tag: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    idx = 0
    while True:
        a = text.find(open_tag, idx)
        if a < 0:
            return out
        b = text.find(close_tag, a)
        if b < 0:
            out.append((a, len(text)))
            return out
        out.append((a, b + len(close_tag)))
        idx = b + len(close_tag)


def _in_span(pos: int, spans: Iterable[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def _line_at(text: str, pos: int) -> tuple[str, int]:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    end = len(text) if end < 0 else end
    return text[start:end], text.count("\n", 0, pos) + 1


# --------------------------------------------------------------------------
# R1–R4: every statement of the headline value carries its qualifiers
# --------------------------------------------------------------------------

_RULE_FOR_TOKEN = {
    "N": "R1_headline_requires_n",
    "excluded": "R3_exclusion_rate_adjacent",
    "judge": "R2_judge_independence_disclosed",
    "PRELIMINARY": "R4_preliminary_labelled",
}


def _rule_for(token: str) -> str:
    if token.startswith("N = "):
        return _RULE_FOR_TOKEN["N"]
    if "excluded" in token:
        return _RULE_FOR_TOKEN["excluded"]
    if token.startswith("judge"):
        return _RULE_FOR_TOKEN["judge"]
    return _RULE_FOR_TOKEN["PRELIMINARY"]


def check_headline_annotations(md: str, report: RunReport) -> list[Violation]:
    pat = headline_pattern(report)
    ctx = _spans(md, ALLOW_CONTEXT_OPEN, ALLOW_CONTEXT_CLOSE)
    needed = required_tokens(report)
    seen = 0
    out: list[Violation] = []
    for m in pat.finditer(md):
        if _in_span(m.start(), ctx):
            continue
        seen += 1
        line, lineno = _line_at(md, m.start())
        for tok in needed:
            if tok not in line:
                out.append(
                    Violation(
                        _rule_for(tok),
                        f"the headline value {report.headline.formatted()!r} is stated "
                        f"without {tok!r} on the same line",
                        f"line {lineno}: {line.strip()[:110]}",
                    )
                )
    if seen == 0 and report.headline.value is not None:
        out.append(
            Violation(
                "R1_headline_requires_n",
                "the rendered report never states its own headline value — a report "
                "that hides its number is not a report",
            )
        )
    return out


# --------------------------------------------------------------------------
# R4 (structural half): the preliminary label exists at all
# --------------------------------------------------------------------------


def check_preliminary_label(md: str, report: RunReport) -> list[Violation]:
    if not report.preliminary:
        return []
    if "PRELIMINARY" not in md:
        return [
            Violation(
                "R4_preliminary_labelled",
                f"N = {report.n_scored} is below the {PRELIMINARY_N_THRESHOLD}-unit "
                "threshold (or the run is partial) but the report is not labelled "
                "PRELIMINARY",
            )
        ]
    return []


# --------------------------------------------------------------------------
# R2 (structural half): a non-independent judge gets a stated caveat
# --------------------------------------------------------------------------


def check_judge_disclosure(md: str, report: RunReport) -> list[Violation]:
    if not report.judge.needs_disclosure:
        return []
    out = []
    if "judge not independent" not in md and "NOT independent" not in md:
        out.append(
            Violation(
                "R2_judge_independence_disclosed",
                "the judge is not independent of the agent's vendor and the report "
                "never says so",
            )
        )
    if not report.judge.note.strip():
        out.append(
            Violation(
                "R2_judge_independence_disclosed",
                "a non-independent judge must carry an explanatory note, not just a "
                "boolean",
            )
        )
    return out


# --------------------------------------------------------------------------
# R5: no provider names in agent-facing text
# --------------------------------------------------------------------------


def check_providers_markdown(md: str, report: RunReport) -> list[Violation]:
    allow = _spans(md, ALLOW_PROVIDERS_OPEN, ALLOW_PROVIDERS_CLOSE)
    out: list[Violation] = []
    for m in _PROVIDER_RE.finditer(md):
        if _in_span(m.start(), allow):
            continue
        line, lineno = _line_at(md, m.start())
        out.append(
            Violation(
                "R5_no_provider_names",
                f"{m.group(0)!r} names an LLM provider outside a published-baseline "
                "span; agent-facing text must describe the Whissle agent, not its "
                "supplier",
                f"line {lineno}: {line.strip()[:110]}",
            )
        )
    return out


#: Report fields whose text reaches a reader as a description of *our* agent.
_AGENT_FACING_FIELDS = (
    "title",
    "what_measured",
    "why_measured",
    "scoring_rule",
    "label",
    "partial_reason",
)


def check_providers_structured(report: RunReport) -> list[Violation]:
    """The same rule at the source, so a bad string is caught before rendering."""
    out: list[Violation] = []

    def scan(text: Optional[str], where: str) -> None:
        if not text:
            return
        m = _PROVIDER_RE.search(text)
        if m:
            out.append(
                Violation(
                    "R5_no_provider_names",
                    f"{m.group(0)!r} names an LLM provider in agent-facing text",
                    where,
                )
            )

    for f in _AGENT_FACING_FIELDS:
        scan(getattr(report, f, None), f"report.{f}")
    for term, detail in report.methodology:
        # The judge row is where naming an alternative *independent* grader is the
        # honest disclosure, not branding — see R5's carve-out in the module docstring.
        if "judge" in term.lower():
            continue
        scan(term, "methodology.term")
        scan(detail, f"methodology[{term}]")
    for cat in report.failures:
        scan(cat.label, f"failures[{cat.key}].label")
        scan(cat.description, f"failures[{cat.key}].description")
    for lim in report.limitations:
        scan(lim.text, f"limitations[{lim.key}]")
    return out


# --------------------------------------------------------------------------
# Structural rules that do not need the rendered text
# --------------------------------------------------------------------------


def check_structure(report: RunReport) -> list[Violation]:
    out: list[Violation] = []
    if report.headline.value is not None and not report.headline.n:
        out.append(
            Violation(
                "R1_headline_requires_n",
                "the headline metric carries no N; a number without a denominator is "
                "not a result",
                "report.headline.n",
            )
        )
    ex = report.exclusions
    if ex.n_total and ex.n_scored + ex.n_excluded != ex.n_total:
        out.append(
            Violation(
                "R3_exclusion_rate_adjacent",
                f"exclusion arithmetic does not close: {ex.n_scored} scored + "
                f"{ex.n_excluded} excluded != {ex.n_total} attempted",
                "report.exclusions",
            )
        )
    if ex.any and not ex.breakdown:
        out.append(
            Violation(
                "R3_exclusion_rate_adjacent",
                f"{ex.n_excluded} units were excluded with no reason breakdown; "
                "'we dropped some' is not an exclusion policy",
                "report.exclusions.breakdown",
            )
        )
    if report.baselines.any and not report.baselines.comparability_note:
        out.append(
            Violation(
                "R6_comparability_stated",
                "published baselines are shown without an explicit statement of what "
                "is and is not comparable",
                "report.baselines.comparability_note",
            )
        )
    return out


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

ALL_RULES = (
    "R1_headline_requires_n",
    "R2_judge_independence_disclosed",
    "R3_exclusion_rate_adjacent",
    "R4_preliminary_labelled",
    "R5_no_provider_names",
    "R6_comparability_stated",
)


def audit(report: RunReport, markdown: Optional[str] = None) -> list[Violation]:
    """Run every rule. ``markdown`` omitted runs only the source-level half."""
    out: list[Violation] = []
    out += check_structure(report)
    out += check_providers_structured(report)
    if markdown is not None:
        out += check_headline_annotations(markdown, report)
        out += check_preliminary_label(markdown, report)
        out += check_judge_disclosure(markdown, report)
        out += check_providers_markdown(markdown, report)
    return out


def compliance_table(report: RunReport, markdown: Optional[str] = None) -> list[list[str]]:
    """Rows for the report's own compliance appendix: rule, verdict, what it checked."""
    viols = audit(report, markdown)
    by_rule: dict[str, int] = {}
    for v in viols:
        by_rule[v.rule] = by_rule.get(v.rule, 0) + 1
    what = {
        "R1_headline_requires_n": f"headline carries N = {report.n_scored} everywhere it is stated",
        "R2_judge_independence_disclosed": (
            "non-independent judge disclosed beside the number"
            if report.judge.needs_disclosure
            else "not applicable — judge is independent or deterministic"
        ),
        "R3_exclusion_rate_adjacent": (
            f"{report.exclusions.n_excluded}/{report.exclusions.n_total} exclusion rate "
            "shown beside the score"
            if report.exclusions.any
            else "not applicable — nothing was excluded"
        ),
        "R4_preliminary_labelled": (
            "labelled PRELIMINARY"
            if report.preliminary
            else f"not applicable — N = {report.n_scored} ≥ {PRELIMINARY_N_THRESHOLD} and the run is complete"
        ),
        "R5_no_provider_names": "no LLM vendor named outside the published-baseline table",
        "R6_comparability_stated": (
            "comparability to published baselines stated explicitly"
            if report.baselines.any
            else "not applicable — no published baseline is registered"
        ),
    }
    return [
        [rule, "FAIL" if by_rule.get(rule) else "pass", what.get(rule, "")]
        for rule in ALL_RULES
    ]
