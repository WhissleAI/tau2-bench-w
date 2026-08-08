# Copyright Sierra
"""Tests for the benchmark archive writer.

Every test here corresponds to a rule the archive claims to enforce. A rule that
cannot fail is decoration, so each one tampers with a passing case and asserts the
guard fires — the same discipline ``tests/test_reporting.py`` applies to the honesty
rules.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tau2.archive import backfill, cost, index, schema, serving, suites, writer


# ── modality: the field a run cannot omit ──────────────────────────────────────


def test_modality_is_required():
    """A live run with no modality is refused, not defaulted."""
    with pytest.raises(schema.ArchiveError, match="modality is required"):
        schema.require_modality(None)
    with pytest.raises(schema.ArchiveError):
        schema.require_modality("")


def test_unknown_modality_is_refused():
    with pytest.raises(schema.ArchiveError, match="unknown modality"):
        schema.require_modality("telepathy")


def test_not_recorded_modality_is_backfill_only():
    """"not recorded" is a legitimate answer for history and never for a live run —
    the runner is the one caller that cannot possibly not know."""
    assert schema.require_modality("not recorded", backfill=True) == schema.MODALITY_NOT_RECORDED
    with pytest.raises(schema.ArchiveError, match="only when backfilling"):
        schema.require_modality("not recorded", backfill=False)


def test_export_run_refuses_a_run_with_no_modality(tmp_path):
    with pytest.raises(schema.ArchiveError):
        writer.export_run(suite="x", modality=None, root=tmp_path, probe_backend=False)


def test_voice_is_distinguished_from_text():
    assert schema.is_voice(schema.MODALITY_VOICE)
    assert schema.is_voice(schema.MODALITY_VOICE_VISION)
    assert not schema.is_voice(schema.MODALITY_TEXT)
    assert not schema.is_voice(schema.MODALITY_TEXT_VISION)


def test_runner_mode_never_silently_defaults_to_text():
    """The mapping from a runner's --mode to a modality is strict. A default here is
    exactly how a voice run gets archived as text."""
    assert suites.modality_for("voice") == schema.MODALITY_VOICE
    assert suites.modality_for("text") == schema.MODALITY_TEXT
    assert suites.modality_for("text", vision="block") == schema.MODALITY_TEXT_VISION
    assert suites.modality_for("voice", vision="block") == schema.MODALITY_VOICE_VISION
    with pytest.raises(schema.ArchiveError, match="cannot map runner mode"):
        suites.modality_for("carrier-pigeon")


# ── the metadata head ──────────────────────────────────────────────────────────


def test_metadata_head_is_off_for_a_text_run():
    block = schema.MODALITY_TEXT
    from tau2.archive.env import metadata_head

    head = metadata_head(modality=block, probe_backend=False)
    assert head["in_path"] is False
    assert "text" in head["reason"]
    assert head["captured_from"].startswith("environment")


def test_metadata_head_is_off_when_transport_env_is_unset(monkeypatch):
    from tau2.archive.env import STT_TRANSPORT_ENV, metadata_head

    monkeypatch.delenv(STT_TRANSPORT_ENV, raising=False)
    head = metadata_head(modality=schema.MODALITY_VOICE, probe_backend=False)
    assert head["in_path"] is False
    assert STT_TRANSPORT_ENV in head["reason"]
    assert head["stt_transport_env"]["set"] is False


def test_metadata_head_is_off_when_the_agent_routes_to_a_third_party(monkeypatch):
    """Two independent conditions gate the head, and either one alone disables it —
    a whissle transport does not help if the agent is pinned to Deepgram."""
    from tau2.archive import env as env_mod

    monkeypatch.setenv(env_mod.STT_TRANSPORT_ENV, "grpc")
    monkeypatch.setattr(
        env_mod, "agent_stt_provider",
        lambda *a, **k: {"agent_id": "x", "stt_provider": "deepgram",
                         "tts_provider": "deepgram", "source": "test",
                         "fetch_error": None},
    )
    head = env_mod.metadata_head(modality=schema.MODALITY_VOICE, base_url="http://x",
                                 api_key="k", agent_id="a")
    assert head["in_path"] is False
    assert "deepgram" in head["reason"]
    assert head["third_party_stt"] is True


def test_metadata_head_is_on_only_when_both_conditions_hold(monkeypatch):
    from tau2.archive import env as env_mod

    monkeypatch.setenv(env_mod.STT_TRANSPORT_ENV, "grpc")
    monkeypatch.setattr(
        env_mod, "agent_stt_provider",
        lambda *a, **k: {"agent_id": "x", "stt_provider": "whissle",
                         "tts_provider": "x", "source": "test", "fetch_error": None},
    )
    head = env_mod.metadata_head(modality=schema.MODALITY_VOICE, base_url="http://x",
                                 api_key="k", agent_id="a")
    assert head["in_path"] is True


# ── the serving ledger ─────────────────────────────────────────────────────────


BENCH_RESPONSE = {
    "reply": "OK",
    "stop_reason": "end_turn",
    "stop_details": None,
    "model": "claude-haiku-4-5-20251001",
    "usage": {"input_tokens": 477, "output_tokens": 4,
              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
}


def test_ledger_records_the_served_model_not_the_requested_one():
    led = serving.ServingLedger()
    led.record_response(BENCH_RESPONSE, case_id="t1", requested_model="claude-opus-5")
    served = led.served()
    assert served.dominant == "claude-haiku-4-5-20251001"
    assert served.turns == 1
    # The requested model was opus; haiku answered. That IS the finding.
    assert led.failovers()[0]["requested_model"] == "claude-opus-5"


def test_ledger_flags_failover_only_when_models_differ():
    led = serving.ServingLedger()
    led.record_response(BENCH_RESPONSE, requested_model="claude-haiku-4-5")
    assert led.failovers() == []
    assert led.served().failover_observed is False


def test_ledger_records_a_turn_even_with_no_model_field():
    """An older backend returned no `model`. The turn still counts — losing it would
    make an un-instrumented run look like a run with no turns."""
    led = serving.ServingLedger()
    led.record_response({"reply": "hi"})
    assert led.served().turns == 1
    assert led.served().dominant == schema.NOT_RECORDED


def test_ledger_never_raises_on_garbage():
    led = serving.ServingLedger()
    for junk in (None, "string", 42, [], {"usage": "not-a-dict"}):
        led.record_response(junk)
    assert len(led) >= 1  # recorded, not crashed


def test_ledger_attributes_cost_per_case():
    led = serving.ServingLedger()
    led.record_response(BENCH_RESPONSE, case_id="t1")
    led.record_response(BENCH_RESPONSE, case_id="t2")
    by_case = led.by_case()
    assert set(by_case) == {"t1", "t2"}
    assert by_case["t1"]["usage"]["input_tokens"] == 477
    assert by_case["t1"]["cost_usd"] > 0


def test_default_ledger_reset_prevents_cross_run_contamination():
    """Two runs in one process must not share a ledger, or the second inherits the
    first's turns and doubles its cost."""
    first = serving.reset_default()
    first.record_response(BENCH_RESPONSE)
    assert len(serving.default_ledger()) == 1
    second = serving.reset_default()
    assert second is not first
    assert len(serving.default_ledger()) == 0


# ── cost ───────────────────────────────────────────────────────────────────────


def test_price_matches_a_dated_snapshot_to_its_family():
    assert cost.price_for("claude-haiku-4-5-20251001").input_per_mtok == 1.0
    assert cost.price_for("claude-opus-5").output_per_mtok == 25.0


def test_unpriced_model_is_excluded_not_zeroed():
    """A zero would be summed with real figures and silently understate the run."""
    block = cost.compute({"some-unknown-model": cost.Usage(1000, 1000)})
    assert block["agent_usd"] == 0.0          # nothing priced
    assert block["unpriced_models"] == ["some-unknown-model"]
    assert block["by_model"][0]["cost_usd"] is None
    assert "EXCLUDED" in block["by_model"][0]["unpriced_reason"]


def test_cost_reports_the_input_output_ratio():
    block = cost.compute({"claude-haiku-4-5": cost.Usage(4770, 40)})
    assert "input:output" in block["input_dominates_output"]
    assert block["usage_total"]["input_output_ratio"] == pytest.approx(119.2, abs=0.5)


def test_cached_reads_are_priced_separately_from_fresh_input():
    fresh = cost.usd(cost.Usage(input_tokens=1_000_000), "claude-opus-5")
    cached = cost.usd(cost.Usage(cache_read_input_tokens=1_000_000), "claude-opus-5")
    assert fresh == pytest.approx(5.0)
    assert cached == pytest.approx(0.5)       # ~0.1x


def test_unavailable_cost_says_so_rather_than_reporting_zero():
    block = cost.compute({})
    assert block["available"] is False
    assert block["total_usd"] is None
    assert "unrecoverable" in block["reason"]


# ── dates: never from mtime ────────────────────────────────────────────────────


def test_date_is_read_from_the_artifact_not_the_filesystem(tmp_path):
    """The concrete case: retail_run1.json has an mtime of 4 Aug and an internal
    timestamp of 31 Jul. The internal one is the run."""
    run = tmp_path / "run"
    run.mkdir()
    payload = {"generated_at": "2026-07-31T00:53:08.324295+00:00"}
    date, moment = backfill._date_from(payload, ("generated_at",), "SUMMARY.json", run)
    assert date.date == "2026-07-31"
    assert date.source == schema.DATE_FROM_ARTIFACT
    assert "SUMMARY.json:generated_at" in date.source_detail
    assert moment.year == 2026


def test_date_falls_back_to_the_directory_name_then_gives_up(tmp_path):
    stamped = tmp_path / "20260808T092952Z-run"
    stamped.mkdir()
    date, _ = backfill._date_from({}, ("nope",), "SUMMARY.json", stamped)
    assert date.date == "2026-08-08"
    assert date.source == schema.DATE_FROM_DIRNAME

    plain = tmp_path / "no-stamp-here"
    plain.mkdir()
    date, moment = backfill._date_from({}, ("nope",), "SUMMARY.json", plain)
    assert date.date == schema.NOT_RECORDED
    assert date.recovered is False
    assert moment is None


def test_date_provenance_always_carries_the_mtime_warning():
    assert "mtime" in schema.DateProvenance().to_dict()["mtime_note"]
    assert "retail_run1.json" in schema.MTIME_IS_NOT_A_DATE


# ── the writer ─────────────────────────────────────────────────────────────────


def _export(tmp_path, **kw):
    defaults = dict(
        suite="testsuite", arm="arm-a", modality=schema.MODALITY_TEXT,
        root=tmp_path, probe_backend=False, update_index=False,
        started_at=datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(kw)
    return writer.export_run(**defaults)


def test_export_writes_every_required_file(tmp_path):
    result = _export(tmp_path, cases=[{"case_id": "c1", "score": 1}])
    for name in ("MANIFEST.md", "manifest.json", "config.json", "summary.json",
                 "REPORT.md", "cases", "logs", "raw"):
        assert (result.path / name).exists(), name
    assert (result.path / "cases" / "c1.json").exists()
    assert (result.path / "logs" / "archive.log").exists()


def test_export_directory_is_timestamp_then_arm(tmp_path):
    result = _export(tmp_path)
    assert result.path.name == "20260808_120000_arm-a"
    assert result.path.parent.name == "testsuite"


def test_export_never_overwrites_an_existing_run(tmp_path):
    first = _export(tmp_path)
    second = _export(tmp_path)
    assert first.path != second.path
    assert second.collided is True
    assert second.path.name.endswith("__2")
    # Both survive, and the second says what happened.
    assert first.path.exists() and second.path.exists()
    assert any("already exists" in n for n in second.notes)


def test_raw_is_a_verbatim_copy_and_is_never_edited(tmp_path):
    source = tmp_path / "src"
    (source / "nested").mkdir(parents=True)
    (source / "SUMMARY.json").write_text('{"a": 1}')
    (source / "nested" / "case.json").write_text('{"b": 2}')
    (source / ".DS_Store").write_text("noise")

    result = _export(tmp_path, run_dir=source)
    assert (result.path / "raw" / "SUMMARY.json").read_text() == '{"a": 1}'
    assert (result.path / "raw" / "nested" / "case.json").read_text() == '{"b": 2}'
    # Noise is skipped, and nothing was rewritten.
    assert not (result.path / "raw" / ".DS_Store").exists()


def test_missing_raw_source_is_logged_not_swallowed(tmp_path):
    result = _export(tmp_path, run_dir=tmp_path / "does-not-exist")
    log = (result.path / "logs" / "archive.log").read_text()
    assert "does not exist" in log
    assert any("does not exist" in n for n in result.notes)


def test_duplicate_case_ids_do_not_collapse_into_one_file(tmp_path):
    result = _export(tmp_path, cases=[{"case_id": "dup", "n": 1},
                                      {"case_id": "dup", "n": 2}])
    written = sorted(p.name for p in (result.path / "cases").glob("*.json"))
    assert written == ["dup.json", "dup__2.json"]
    assert any("duplicate case id" in n for n in result.notes)


def test_manifest_carries_the_three_load_bearing_facts(tmp_path):
    result = _export(tmp_path)
    record = json.loads((result.path / "manifest.json").read_text())
    assert record["schema"] == schema.SCHEMA
    assert record["modality"] == schema.MODALITY_TEXT
    assert record["environment"]["metadata_head"]["in_path"] is False
    assert record["served"]["available"] is False

    md = (result.path / "MANIFEST.md").read_text()
    assert "driven over TEXT" in md
    assert "metadata head" in md
    assert "Requested vs served" in md


def test_manifest_states_the_text_caveat_in_prose(tmp_path):
    """A JSON field can be misread; a sentence cannot."""
    result = _export(tmp_path, modality=schema.MODALITY_TEXT)
    md = (result.path / "MANIFEST.md").read_text()
    assert "Presenting it as a voice result would be false" in md


def test_voice_run_does_not_carry_the_text_caveat(tmp_path):
    result = _export(tmp_path, modality=schema.MODALITY_VOICE)
    md = (result.path / "MANIFEST.md").read_text()
    assert "driven over a real audio transport" in md
    assert "Presenting it as a voice result would be false" not in md


def test_served_model_reaches_the_manifest(tmp_path):
    led = serving.ServingLedger()
    led.record_response(BENCH_RESPONSE, case_id="c1", requested_model="claude-opus-5")
    result = _export(tmp_path, ledger=led,
                     requested=schema.RequestedRecord(model="claude-opus-5"))
    record = json.loads((result.path / "manifest.json").read_text())
    assert record["served"]["dominant"] == "claude-haiku-4-5-20251001"
    assert record["requested"]["model"] == "claude-opus-5"
    assert record["cost"]["total_usd"] is not None
    assert (result.path / "serving.json").exists()


def test_secrets_are_fingerprinted_not_recorded(monkeypatch, tmp_path):
    monkeypatch.setenv("WHISSLE_API_KEY", "wsk_live_supersecret")
    result = _export(tmp_path)
    record = json.loads((result.path / "manifest.json").read_text())
    value = record["environment"]["env_vars"]["WHISSLE_API_KEY"]
    assert value.startswith("sha256:")
    assert "supersecret" not in (result.path / "MANIFEST.md").read_text()


def test_archive_root_is_configurable(monkeypatch, tmp_path):
    """CI and other machines must not be forced into a user's home directory."""
    monkeypatch.setenv(writer.ARCHIVE_DIR_ENV, str(tmp_path / "elsewhere"))
    assert writer.archive_root() == tmp_path / "elsewhere"
    monkeypatch.delenv(writer.ARCHIVE_DIR_ENV)
    assert writer.archive_root() == writer.DEFAULT_ARCHIVE_DIR
    # An explicit argument always wins.
    assert writer.archive_root(tmp_path / "given") == tmp_path / "given"


def test_export_hook_never_raises_into_a_finished_run(tmp_path, capsys):
    """A benchmark that has produced its numbers must not lose them to a copy step."""
    hook = suites.export_hook("brokensuite", schema.MODALITY_TEXT)
    # An unmappable modality would raise inside export_run.
    assert hook(modality="carrier-pigeon", root=tmp_path) is None
    assert "export failed" in capsys.readouterr().err


# ── the index ──────────────────────────────────────────────────────────────────


def test_index_lists_runs_newest_first_with_undated_last(tmp_path):
    _export(tmp_path, arm="old",
            started_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    _export(tmp_path, arm="new",
            started_at=datetime(2026, 8, 9, tzinfo=timezone.utc))
    _export(tmp_path, arm="undated", backfill=True,
            started_at=None, date=schema.DateProvenance())

    rows = index.scan(tmp_path)
    assert [r["arm"] for r in rows] == ["new", "old", "undated"]

    built = index.rebuild(tmp_path)
    assert built["n_runs"] == 3
    md = (tmp_path / "INDEX.md").read_text()
    assert "| Date | Suite | Arm | Modality | Head |" in md
    # The two caveats are columns, not footnotes.
    assert "were driven over **text**" in md
    assert "distinguishing layer" in md


def test_index_marks_failover_runs(tmp_path):
    led = serving.ServingLedger()
    led.record_response({**BENCH_RESPONSE, "model": "a"})
    led.record_response({**BENCH_RESPONSE, "model": "b"})
    _export(tmp_path, ledger=led)
    index.rebuild(tmp_path)
    assert "⚠" in (tmp_path / "INDEX.md").read_text()


# ── backfill readers ───────────────────────────────────────────────────────────


def test_backfill_reads_a_medagentbench_run(tmp_path):
    run = tmp_path / "brain-parity_smoke"
    (run / "tasks").mkdir(parents=True)
    (run / "SUMMARY.json").write_text(json.dumps({
        "suite": "medagentbench", "mode": "brain-parity",
        "generated_at": "2026-08-08T09:36:34+00:00",
        "run": {"model": "(agent default)", "agent_id": "a1",
                "base": "http://x", "filters": {"limit": 100}},
        "overall": {"n": 100, "correct": 54, "success_rate_pct": 54.0},
    }))
    (run / "tasks" / "t1.json").write_text('{"task_id": "t1"}')

    kwargs = backfill.read_medagentbench(run)
    assert kwargs["suite"] == "medagentbench"
    assert kwargs["modality"] == schema.MODALITY_TEXT
    assert kwargs["date"].date == "2026-08-08"
    assert kwargs["headline"]["value"] == 54.0
    assert len(kwargs["cases"]) == 1


def test_backfill_reads_agentclinic_modality_from_its_own_mode_field(tmp_path):
    for mode, vision, expected in (
        ("text", "off", schema.MODALITY_TEXT),
        ("text", "block", schema.MODALITY_TEXT_VISION),
        ("voice", "off", schema.MODALITY_VOICE),
        ("", "off", schema.MODALITY_NOT_RECORDED),
    ):
        run = tmp_path / f"20260808T092952Z-{mode or 'blank'}-{vision}"
        (run / "cases").mkdir(parents=True)
        (run / "SUMMARY.json").write_text(json.dumps({
            "accuracy": 0.75, "ts": "20260808T092952Z", "mode": mode,
            "vision": vision, "n_cases_scored": 100,
        }))
        assert backfill.read_agentclinic(run)["modality"] == expected


def test_backfill_resolves_the_agentclinic_arm_model_from_the_sweep(tmp_path):
    """AgentClinic never wrote the agent's model. The sweep aggregation did."""
    run = tmp_path / "20260808T185622Z-sweep25_haiku"
    (run / "cases").mkdir(parents=True)
    (run / "SUMMARY.json").write_text(json.dumps({
        "accuracy": 0.84, "ts": "20260808T185622Z", "mode": "text",
        "n_cases_scored": 25,
    }))
    resolved = backfill.read_agentclinic(run, {"haiku": "claude-haiku-4-5"})
    assert resolved["requested"].model == "claude-haiku-4-5"
    assert "cross-referencing" in resolved["requested"].extra["model_note"]

    unresolved = backfill.read_agentclinic(run, {})
    assert unresolved["requested"].model == schema.NOT_RECORDED
    assert "not recorded" in unresolved["requested"].extra["model_note"]


def test_backfill_flags_a_run_that_scored_nothing(tmp_path):
    run = tmp_path / "20260808T201248Z-sweep25m_opus5"
    (run / "cases").mkdir(parents=True)
    (run / "SUMMARY.json").write_text(json.dumps({
        "accuracy": None, "ts": "20260808T201248Z", "mode": "text",
        "n_cases_total": 25, "n_cases_scored": 0,
    }))
    kwargs = backfill.read_agentclinic(run)
    assert any("produced no scored cases" in n for n in kwargs["notes"])


def test_backfill_reads_a_flow_sim_run_without_a_summary(tmp_path):
    """A directory with sessions but no SUMMARY.json is a run missing a rollup, not
    a missing run. The first version of this reader dropped it silently."""
    run = tmp_path / "flow_sim_baseline" / "customer_support"
    run.mkdir(parents=True)
    (run / "cs_login_20260805T181604Z.session.json").write_text(json.dumps(
        {"task_id": "cs_login", "mode": "voice", "outcome": {"task_success": True}}))
    (run / "cs_login_20260805T181604Z.mix.wav").write_bytes(b"RIFF")

    kwargs = backfill.read_flow_sim(run)
    assert kwargs is not None
    assert kwargs["modality"] == schema.MODALITY_VOICE
    assert kwargs["date"].date == "2026-08-05"
    assert "reconstructed" in kwargs["summary"]["_reconstructed"]


def test_backfill_reads_a_tau2_core_run_and_its_real_spend(tmp_path):
    path = tmp_path / "retail_run1.json"
    path.write_text(json.dumps({
        "timestamp": "2026-07-31T00:53:08.324295",
        "info": {"git_commit": "121283b8", "agent_info": {"llm": "whissle"},
                 "user_info": {"llm": "gpt-4o"}},
        "tasks": [{}],
        "simulations": [
            {"agent_cost": 0.01, "user_cost": 0.002,
             "reward_info": {"reward": 1.0}},
            {"agent_cost": 0.02, "user_cost": 0.003,
             "reward_info": {"reward": 0.0}},
        ],
    }))
    kwargs = backfill.read_tau2_core(path)
    assert kwargs["date"].date == "2026-07-31"      # NOT the file's mtime
    assert kwargs["modality"] == schema.MODALITY_TEXT
    assert kwargs["headline"]["value"] == 0.5
    spend = kwargs["ledger"].extra_usd()
    assert spend["agent"] == pytest.approx(0.03)
    assert spend["user_simulator"] == pytest.approx(0.005)


def test_backfill_reads_the_model_sweep_aggregation(tmp_path):
    path = tmp_path / "collected.json"
    path.write_text(json.dumps({
        "haiku": {"model": "claude-haiku-4-5", "medagent": {"overall": 68.0}},
        "opus5": {"model": "claude-opus-5", "medagent": {"overall": 72.0}},
    }))
    kwargs = backfill.read_modelsweep(path)
    assert kwargs["suite"] == "modelsweep"
    assert kwargs["config"]["arms"] == ["haiku", "opus5"]
    # The aggregation carries no timestamp of its own.
    assert kwargs["date"].date == schema.NOT_RECORDED
    assert backfill.arm_model_map(path) == {
        "haiku": "claude-haiku-4-5", "opus5": "claude-opus-5"}


def test_backfill_never_writes_into_the_source_tree(tmp_path):
    """`raw/` is a copy; the original results must come back byte-identical."""
    results = tmp_path / "results" / "whissle"
    run = results / "medagentbench" / "brain-parity_x"
    (run / "tasks").mkdir(parents=True)
    summary = {"suite": "medagentbench", "mode": "brain-parity",
               "generated_at": "2026-08-08T09:36:34+00:00",
               "run": {"model": "m"}, "overall": {"n": 1, "success_rate_pct": 100.0}}
    (run / "SUMMARY.json").write_text(json.dumps(summary))
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}

    backfill.run(results, root=tmp_path / "archive", include_modelsweep=False)

    after = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    assert before == after


def test_oversized_raw_files_are_skipped_loudly_and_never_by_default(monkeypatch, tmp_path):
    """The default is to copy everything — the archive's whole point is that nothing
    lives only inside the repo. A configured ceiling skips media, and says exactly
    which files and where they still are."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "small.json").write_text('{"a": 1}')
    (source / "big.wav").write_bytes(b"\0" * (3 * 1024 * 1024))

    # Default: nothing is skipped.
    monkeypatch.delenv(writer.MAX_RAW_FILE_MB_ENV, raising=False)
    full = _export(tmp_path, run_dir=source)
    assert (full.path / "raw" / "big.wav").exists()
    assert json.loads((full.path / "manifest.json").read_text())["counts"]["raw_complete"]

    # With a ceiling: the media is skipped, the artifact is not, and the log names it.
    monkeypatch.setenv(writer.MAX_RAW_FILE_MB_ENV, "1")
    capped = _export(tmp_path, run_dir=source, arm="capped")
    assert (capped.path / "raw" / "small.json").exists()
    assert not (capped.path / "raw" / "big.wav").exists()

    counts = json.loads((capped.path / "manifest.json").read_text())["counts"]
    assert counts["raw_complete"] is False
    assert counts["raw_oversized_skipped"][0]["path"] == "big.wav"

    log = (capped.path / "logs" / "archive.log").read_text()
    assert "big.wav" in log and "INCOMPLETE" in log
    assert "incomplete by configuration" in (capped.path / "MANIFEST.md").read_text()
