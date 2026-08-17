"""Manual-corpus tests for the appliance_care domain.

Includes the drift check against the Whissle CLI agent package. The two copies
must stay byte-identical: the CLI agent ingests them as its knowledge base and
this domain scores against them, so if they diverge the benchmark and the agent
are describing different machines.

The drift check skips when the CLI repository is not checked out beside this one,
so the domain stays self-contained.
"""

import hashlib
from pathlib import Path

import pytest

from tau2.domains.appliance_care.environment import get_environment
from tau2.domains.appliance_care.manuals import ManualLibrary
from tau2.domains.appliance_care.utils import APPLIANCE_CARE_MANUALS_DIR

# The CLI package, if the sibling repository is present.
CLI_KNOWLEDGE_DIR = (
    Path(__file__).resolve().parents[3].parent
    / "whissle-cli"
    / "examples"
    / "agents"
    / "appliance-care"
    / "knowledge"
)

EXPECTED_MANUALS = {
    "appliancecare-support-policy.md",
    "northwind-nw2200-washer.md",
    "northwind-nw2200x-washer.md",
    "northwind-nw2400-washer.md",
    "larkfield-lfw70-washer.md",
    "vantis-vt500-washer.md",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_corpus_contains_the_expected_manuals():
    names = {p.name for p in APPLIANCE_CARE_MANUALS_DIR.glob("*.md")}
    assert names == EXPECTED_MANUALS


def test_every_manual_is_labelled_synthetic():
    """No real manufacturer content: every file says so on line 1."""
    for path in APPLIANCE_CARE_MANUALS_DIR.glob("*.md"):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert "SYNTHETIC SAMPLE DATA" in first, f"{path.name} lacks the banner"


def test_no_real_brand_names():
    real_brands = [
        "samsung",
        "whirlpool",
        "bosch",
        "miele",
        "electrolux",
        "maytag",
        "kenmore",
        "hotpoint",
        "beko",
        "haier",
        "siemens",
        "indesit",
        "frigidaire",
        "panasonic",
        "hisense",
        "zanussi",
    ]
    for path in APPLIANCE_CARE_MANUALS_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8").lower()
        for brand in real_brands:
            assert brand not in text, f"{path.name} mentions {brand}"


def test_every_model_maps_to_a_real_manual():
    env = get_environment()
    for model in env.tools.db.appliance_models:
        assert env.tools.library.get(model.manual_id) is not None, (
            f"{model.model_id} points at missing manual {model.manual_id}"
        )


def test_the_cross_model_conflicts_survive():
    """The traps are the point of the corpus — assert they are still there."""
    env = get_environment()
    assert "Drain fault" in env.tools.lookup_error_code("NW-2200", "E24")
    assert "Door lock fault" in env.tools.lookup_error_code("NW-2400", "E24")
    assert "Drain fault" in env.tools.lookup_error_code("NW-2400", "E31")
    # The VT-500 has no customer-accessible drain filter.
    vt500 = next(m for m in env.tools.db.appliance_models if m.model_id == "VT-500")
    assert vt500.drain_filter_customer_accessible is False
    # Only the NW-2200 family documents a customer reset.
    resets = {m.model_id: m.reset_supported for m in env.tools.db.appliance_models}
    assert resets == {
        "NW-2200": True,
        "NW-2200X": True,
        "NW-2400": False,
        "LF-W70": False,
        "VT-500": False,
    }


def test_search_finds_the_right_section_for_the_canonical_query():
    env = get_environment()
    hits = env.tools.library.search(
        "drain filter cleaning", manual_ids=["northwindnw2200washer"]
    )
    assert hits, "no hits for the canonical drain-filter query"
    assert "drain filter" in hits[0][0].heading.lower()


def test_manual_library_rejects_an_empty_corpus(tmp_path):
    with pytest.raises(FileNotFoundError):
        ManualLibrary.load(tmp_path)


@pytest.mark.skipif(
    not CLI_KNOWLEDGE_DIR.is_dir(),
    reason="whissle-cli is not checked out beside this repository",
)
def test_no_drift_against_the_cli_agent_package():
    """The tau corpus and the CLI agent's knowledge must be byte-identical.

    If this fails, one side was edited without the other. Re-sync with:
        cp <whissle-cli>/examples/agents/appliance-care/knowledge/*.md \\
           data/tau2/domains/appliance_care/manuals/
    """
    drift = []
    for name in sorted(EXPECTED_MANUALS):
        ours = APPLIANCE_CARE_MANUALS_DIR / name
        theirs = CLI_KNOWLEDGE_DIR / name
        if not theirs.exists():
            drift.append(f"{name}: missing from the CLI package")
            continue
        if _sha256(ours) != _sha256(theirs):
            drift.append(f"{name}: contents differ")
    extra = {p.name for p in CLI_KNOWLEDGE_DIR.glob("*.md")} - EXPECTED_MANUALS
    for name in sorted(extra):
        drift.append(f"{name}: present in the CLI package but not in the tau corpus")
    assert not drift, "manual corpus has drifted:\n  " + "\n  ".join(drift)
