"""Manual corpus for the appliance_care domain.

Follows the `banking_knowledge` idea — a document corpus the agent must search —
but keeps retrieval **offline and dependency-free** so the proof of concept runs
under a plain `uv sync`. Scoring is a deterministic term-overlap ranking, not an
embedding model: identical queries always return identical results, which the
primary score depends on.

Manuals are markdown, split into sections on their `##` / `###` headings, so
`open_manual_section` can hand back one procedure verbatim — warning boxes,
tables, and all — rather than a flattened blob.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

# Words that carry no signal for ranking appliance manuals.
_STOPWORDS = frozenset(
    """a an the and or but if then than that this these those is are was were be been
    being do does did doing have has had having i you he she it we they my your of to
    in on at for with from by as it's its not no can cannot will would should could
    what when where which who whom how why there here about into out up down over under
    machine appliance please help""".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]*")


def _tokenize(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


class ManualSection(BaseModel):
    """One `##`/`###` section of a manual, kept verbatim."""

    section_id: str = Field(description="Stable id, e.g. 'nw2200#5.1'")
    heading: str = Field(description="The section heading as printed")
    content: str = Field(description="The section body, verbatim markdown")


class Manual(BaseModel):
    manual_id: str = Field(description="Stable id, e.g. 'nw2200'")
    title: str = Field(description="Manual title (the H1)")
    path: str = Field(description="Source filename")
    sections: List[ManualSection] = Field(default_factory=list)

    def full_text(self) -> str:
        return "\n\n".join(f"{s.heading}\n{s.content}" for s in self.sections)


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)")


def _parse_manual(path: Path) -> Manual:
    """Split one markdown manual into sections on its headings."""
    text = path.read_text(encoding="utf-8")
    manual_id = path.stem.replace("-", "").replace("_", "")
    title = path.stem
    sections: List[ManualSection] = []
    heading: Optional[str] = None
    buf: List[str] = []
    preamble: List[str] = []

    def flush() -> None:
        if heading is None:
            return
        num = _SECTION_NUM_RE.match(heading.lstrip("# ").strip())
        suffix = num.group(1) if num else str(len(sections) + 1)
        sections.append(
            ManualSection(
                section_id=f"{manual_id}#{suffix}",
                heading=heading.lstrip("# ").strip(),
                content="\n".join(buf).strip(),
            )
        )

    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            level, htext = len(m.group(1)), m.group(2).strip()
            if level == 1:
                title = htext
                continue
            flush()
            heading, buf = htext, []
        elif heading is None:
            preamble.append(line)
        else:
            buf.append(line)
    flush()

    # Keep the preamble (synthetic-data banner, model coverage, the "do not apply
    # another model's instructions" callouts) searchable as section 0 — for the
    # near-model tasks it is the most important text in the file.
    pre = "\n".join(preamble).strip()
    if pre:
        sections.insert(
            0,
            ManualSection(
                section_id=f"{manual_id}#0",
                heading="Model coverage and important notices",
                content=pre,
            ),
        )
    return Manual(manual_id=manual_id, title=title, path=path.name, sections=sections)


class ManualLibrary(BaseModel):
    """The manual corpus, plus deterministic offline search over it."""

    manuals: Dict[str, Manual] = Field(default_factory=dict)

    @classmethod
    def load(cls, manuals_dir: str | Path) -> "ManualLibrary":
        d = Path(manuals_dir)
        if not d.is_dir():
            raise FileNotFoundError(f"Manual corpus not found: {d}")
        manuals = {}
        for path in sorted(d.glob("*.md")):
            manual = _parse_manual(path)
            manuals[manual.manual_id] = manual
        if not manuals:
            raise FileNotFoundError(f"No manuals (*.md) found in {d}")
        return cls(manuals=manuals)

    def get(self, manual_id: str) -> Optional[Manual]:
        return self.manuals.get(manual_id)

    def get_section(self, manual_id: str, section_id: str) -> Optional[ManualSection]:
        manual = self.manuals.get(manual_id)
        if manual is None:
            return None
        for s in manual.sections:
            # Accept either the full id ('nw2200#5.1') or the bare number ('5.1').
            if s.section_id == section_id or s.section_id.endswith(f"#{section_id}"):
                return s
        return None

    def search(
        self,
        query: str,
        manual_ids: Optional[List[str]] = None,
        top_k: int = 5,
    ) -> List[tuple[ManualSection, str, float]]:
        """Rank sections by term overlap with the query.

        Ties break on (manual_id, section_id) so the ordering is total and stable.
        Returns (section, manual_id, score), best first.
        """
        terms = _tokenize(query)
        if not terms:
            return []
        wanted = set(manual_ids) if manual_ids else None
        scored: List[tuple[float, str, str, ManualSection]] = []
        for manual_id, manual in self.manuals.items():
            if wanted is not None and manual_id not in wanted:
                continue
            for section in manual.sections:
                hay = _tokenize(f"{section.heading} {section.content}")
                if not hay:
                    continue
                counts = {t: hay.count(t) for t in set(terms)}
                matched = sum(1 for t in set(terms) if counts[t] > 0)
                if matched == 0:
                    continue
                # Coverage dominates raw frequency: a section mentioning every
                # query term once beats one repeating a single term ten times.
                score = matched * 10.0 + sum(counts.values()) / len(hay) * 10.0
                scored.append((score, manual_id, section.section_id, section))
        scored.sort(key=lambda r: (-r[0], r[1], r[2]))
        return [(s, mid, round(sc, 4)) for sc, mid, _sid, s in scored[:top_k]]
