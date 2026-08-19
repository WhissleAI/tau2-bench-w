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
    "bosch-wat28400uc-washer.md",
    "bosch-wat28401uc-washer.md",
    "bosch-wat28402uc-washer.md",
    "lg-wt901cw-washer.md",
    "miele-wwb020-washer.md",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_corpus_contains_the_expected_manuals():
    names = {p.name for p in APPLIANCE_CARE_MANUALS_DIR.glob("*.md")}
    assert names == EXPECTED_MANUALS


# The corpus deliberately contains REAL manufacturer content now: benchmark tasks
# grounded in invented specifications cannot tell you whether an agent is right.
# What must hold instead is that every file declares what it is, that only approved
# extracts are present (never a full manual), and that the POLICY stays synthetic —
# a benchmark support policy must never be mistaken for a manufacturer's real one.

APPROVED_BRANDS = {"bosch", "lg", "miele"}


def test_every_manual_declares_its_provenance():
    """A reader must be able to tell, from line 1, what a file is and is not."""
    for path in APPLIANCE_CARE_MANUALS_DIR.glob("*.md"):
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:12])
        if path.name == "appliancecare-support-policy.md":
            assert "SYNTHETIC" in head.upper(), (
                "the support policy must stay clearly labelled synthetic — it is a "
                "benchmark policy, not any manufacturer's real support policy"
            )
        else:
            assert "APPROVED EXTRACT" in head.upper(), f"{path.name} lacks the banner"
            assert "NOT THE FULL MANUAL" in head.upper(), (
                f"{path.name} must state it is an extract, not a full manual"
            )


def test_extracts_disclaim_affiliation():
    """Real brand names are used. Every file must disclaim endorsement."""
    for path in APPLIANCE_CARE_MANUALS_DIR.glob("*.md"):
        if path.name == "appliancecare-support-policy.md":
            continue
        text = path.read_text(encoding="utf-8").lower()
        assert "not affiliated" in text, f"{path.name} lacks an affiliation disclaimer"
        assert "benchmark" in text, (
            f"{path.name} does not identify itself as a benchmark"
        )


def test_only_approved_manufacturers_appear():
    """A brand nobody verified must not drift into the corpus."""
    unapproved = [
        "samsung",
        "whirlpool",
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
        "ge appliances",
        # the invented brands this corpus replaced
        "northwind",
        "larkfield",
        "vantis",
    ]
    for path in APPLIANCE_CARE_MANUALS_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8").lower()
        for brand in unapproved:
            assert brand not in text, f"{path.name} mentions unapproved brand {brand}"


def test_the_support_policy_claims_no_manufacturer():
    """The synthetic policy must not attach itself to a real manufacturer."""
    text = (
        (APPLIANCE_CARE_MANUALS_DIR / "appliancecare-support-policy.md")
        .read_text(encoding="utf-8")
        .lower()
    )
    for brand in APPROVED_BRANDS:
        assert brand not in text, (
            f"the benchmark support policy names {brand}; it must not read as that "
            "manufacturer's real support, warranty, or dispatch policy"
        )


def test_every_model_maps_to_a_real_manual():
    env = get_environment()
    for model in env.tools.db.appliance_models:
        assert env.tools.library.get(model.manual_id) is not None, (
            f"{model.model_id} points at missing manual {model.manual_id}"
        )


def test_the_cross_model_conflicts_survive():
    """The traps are the point of the corpus — assert they are still there.

    The trap changed shape when the corpus moved to real manuals, and the real one
    is stronger. The synthetic corpus used "same code, different meaning" (E24 was a
    drain fault on one model and a door-lock fault on another). Bosch does not do
    that: WAT28400UC, WAT28401UC and WAT28402UC genuinely share E:18/E:32/E:93 with
    identical meanings.

    What Bosch *does* do is publish E:23 in the WAT28402UC manual only. So the
    discriminator between three models whose numbers differ by one digit is the
    PRESENCE of a code, not a disagreement about its meaning — and a customer who
    reads out "E:23" has, by that fact alone, identified their model.
    """
    env = get_environment()
    # Shared across the Bosch family, same meaning — no false conflict.
    for model_id in ("WAT28400UC", "WAT28401UC", "WAT28402UC"):
        assert "Pump is blocked" in env.tools.lookup_error_code(model_id, "E:18")

    # E:23 is documented ONLY on the WAT28402UC, and it is a stop-use instruction.
    e23 = env.tools.lookup_error_code("WAT28402UC", "E:23")
    assert "leaking" in e23.lower()
    assert "after-sales service" in e23.lower()
    for model_id in ("WAT28400UC", "WAT28401UC"):
        codes = next(
            m for m in env.tools.db.appliance_models if m.model_id == model_id
        ).error_codes
        assert "E:23" not in codes, f"{model_id} must not document E:23"
        assert "not a documented code" in env.tools.lookup_error_code(model_id, "E:23")

    # Miele signals faults with indicator lights, not codes at all. A customer
    # quoting any code on a WWB020 has misread it or misidentified the appliance.
    miele_wwb020 = next(
        m for m in env.tools.db.appliance_models if m.model_id == "WWB020"
    )
    assert miele_wwb020.error_codes == {}
    assert miele_wwb020.drain_filter_customer_accessible is False

    # Bosch documents a power-cycle reset; LG and Miele do not.
    resets = {m.model_id: m.reset_supported for m in env.tools.db.appliance_models}
    assert resets == {
        "WAT28400UC": True,
        "WAT28401UC": True,
        "WAT28402UC": True,
        "WT901CW": False,
        "WWB020": False,
    }


def test_search_finds_the_right_section_for_the_canonical_query():
    env = get_environment()
    hits = env.tools.library.search(
        "drain filter cleaning", manual_ids=["boschwat28400ucwasher"]
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
