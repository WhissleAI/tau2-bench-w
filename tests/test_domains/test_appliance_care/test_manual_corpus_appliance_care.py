"""Manual-corpus tests for the appliance_care domain.

Includes the drift check against the Whissle CLI agent package. The two copies
must stay byte-identical: the CLI agent ingests them as its knowledge base and
this domain scores against them, so if they diverge the benchmark and the agent
are describing different machines.

The drift check skips when the CLI repository is not checked out beside this one,
so the domain stays self-contained.
"""

import hashlib
import re
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
    PRESENCE of a code, not a disagreement about its meaning.

    That makes E:23 a strong hint about WHICH MANUAL documents it. It is not proof of
    which machine the customer owns — see test_identification_appliance_care.py, which
    pins the requirement that the appliance_id is resolved via list_owned_appliances
    regardless of any code the customer reads out.
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
    # The WWB 020 DOES have a customer drain-filter procedure — verified against
    # Miele document M.-Nr. 10 980 030, which gives the full ten-step sequence.
    # The corpus previously claimed the opposite; that was a fabrication, not a
    # trap, and the task built on it has been rewritten (ac_05b_procedure_filed_oddly).
    # The real difficulty is where the procedure is FILED, which the next test pins.
    assert miele_wwb020.customer_drain_maintenance_supported is True

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
    """An agent searching Bosch for a "filter" must still land on the pump.

    Bosch never uses the word "filter" for the drain path, but a customer and an
    agent both will. Retrieval has to bridge that gap, or the correct procedure is
    unreachable by the words people actually type.
    """
    env = get_environment()
    for query in ("drain filter cleaning", "clean the drain pump", "will not drain"):
        hits = env.tools.library.search(query, manual_ids=["boschwat28400ucwasher"])
        assert hits, f"no hits for {query!r}"
        assert "drain pump" in hits[0][0].heading.lower(), (
            f"{query!r} landed on {hits[0][0].heading!r}, not the pump procedure"
        )


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


def test_the_miele_drain_procedure_exists_but_is_filed_oddly():
    """The WWB 020 trap is retrieval difficulty, not absence.

    Miele document M.-Nr. 10 980 030 documents the customer drain-filter clean in
    full, but files it under "Opening the door in the event of a blocked drain
    outlet and/or power outage" — no "filter" in the heading. The corpus once
    claimed this model had no customer procedure at all, which was simply untrue;
    this test exists so that fabrication cannot come back.
    """
    env = get_environment()
    manual = env.tools.library.get("mielewwb020washer")
    # Collapse whitespace: the source is hard-wrapped, so a phrase can straddle a
    # line break and a naive substring check would miss text that is really there.
    body = re.sub(r"\s+", " ", " ".join(s.content for s in manual.sections)).lower()

    # The procedure is present, with the manufacturer's own steps.
    assert "drain pump flap" in body
    assert "unscrew the drain filter" in body
    assert "turn the impellers by hand" in body

    # And the hazards that ride with it.
    assert "scalding" in body
    assert "water damage" in body

    # The heading it hides under is named, so an agent can actually find it.
    assert "opening the door in the event of a blocked drain outlet" in body

    # The old fabrication must not reappear in any form.
    assert "no customer drain-filter cleaning procedure" not in body


def test_the_bosch_extracts_document_the_drain_pump_procedure():
    """v5: all three previously denied a procedure their manuals document in full.

    Verified against the official PDFs (9001002399_H / 9001002426_I / 9001427986_A,
    pages 28 / 29 / 31). This test exists so the denial cannot come back.
    """
    env = get_environment()
    for manual_id, page in (
        ("boschwat28400ucwasher", 28),
        ("boschwat28401ucwasher", 29),
        ("boschwat28402ucwasher", 31),
    ):
        manual = env.tools.library.get(manual_id)
        body = re.sub(r"\s+", " ", " ".join(s.content for s in manual.sections)).lower()

        assert "cleaning the drain pump" in body, manual_id
        assert f"page {page}" in body, f"{manual_id} must cite its own page"
        assert "service cover" in body and "protective film" in body, manual_id
        assert "pump cover counterclockwise" in body, manual_id
        assert "handle must be vertical" in body, manual_id
        assert "impeller" in body, manual_id
        assert "scalding" in body, f"{manual_id} drops the manufacturer's warning"

        # The v5 fabrications, in every form they took.
        assert "no separate pull-out drain filter cartridge" not in body, manual_id
        assert "no customer procedure for opening the pump housing" not in body, (
            manual_id
        )


def test_bosch_never_calls_the_drain_path_a_filter():
    """The word does not appear in any Bosch manual; the corpus must match."""
    for name in (
        "bosch-wat28400uc-washer.md",
        "bosch-wat28401uc-washer.md",
        "bosch-wat28402uc-washer.md",
    ):
        text = (APPLIANCE_CARE_MANUALS_DIR / name).read_text(encoding="utf-8").lower()
        # Saying the model has NO drain filter is correct and useful; calling its
        # drain path one is the error. Strip the negation before checking.
        text = text.replace("no drain filter", "")
        assert "drain filter" not in text, (
            f"{name} calls the Bosch drain path a filter; the manual says drain pump"
        )


def test_the_lg_extract_documents_two_in_drum_lint_filters():
    env = get_environment()
    manual = env.tools.library.get("lgwt901cwwasher")
    body = re.sub(r"\s+", " ", " ".join(s.content for s in manual.sections)).lower()
    assert "two lint filters inside the drum" in body
    assert "drum wall" in body
    assert "both tabs are locked" in body
    # And that a drain complaint is NOT a filter problem on this model.
    assert "kinked drain hose" in body or "no higher than 8 ft" in body


def test_no_invented_model_or_part_survives_anywhere():
    """The retired synthetic corpus must not leak back through a task or a part."""
    import json

    from tau2.domains.appliance_care.utils import APPLIANCE_CARE_TASK_SET_PATH

    blob = json.dumps(json.load(open(APPLIANCE_CARE_TASK_SET_PATH))).lower()
    # "TWO filters" is legitimate for the LG, whose manual says exactly that. The
    # invented claim was that a *Bosch* model had two, with a lint filter first.
    for token in (
        "northwind",
        "larkfield",
        "vantis",
        "nw-22",
        "nw22",
        "nw-2200",
        "nw-2400",
        "lf-w70",
        "vt-500",
        "kick-panel",
        "lint filter must come out first",
        "filter cartridge",
    ):
        assert token not in blob, f"tasks still reference the invented {token!r}"
