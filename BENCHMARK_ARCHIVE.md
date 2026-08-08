# Benchmark archive

Every benchmark run lands in a self-describing folder outside the repo, automatically,
for every suite.

```
$TAU2_ARCHIVE_DIR/                     # default: ~/Downloads/whissle_benchmarks
  INDEX.md                             every run, newest first
  index.json
  <suite>/<YYYYMMDD_HHMMSS>_<arm>/
    MANIFEST.md    what ran, when, why, the exact reproduce command, harness + backend SHAs
    manifest.json  the same facts, machine-readable, schema `whissle.benchmark.archive/v1`
    config.json    model, provider, effort, thinking, N, seeds, judge, task set
    summary.json   headline metrics + CIs + N + exclusions
    REPORT.md      the research-paper writeup
    serving.json   every model call: served model, tokens, stop reason, per-case cost
    cases/         one file per case
    logs/          runner output + archive.log (what this archive skipped)
    raw/           the harness's own output, unmodified
```

## The test this is written against

Hand the folder to someone who was not in the room, months from now, with no access to
the conversation that produced it. Can they tell what ran, against what, how honest the
number is, and how to run it again?

## Four fields a run cannot omit

Three of these exist because we got them wrong once each, in a way that survived into
something published.

| Field | Why |
|---|---|
| **`modality`** | Two runs were published as "Voice" on the public page having been driven entirely over **text**. The artifacts were ambiguous about it, so the error was invisible until someone checked the transport by hand. It now has no default: `export_run` raises without it, and a runner's `--mode` is mapped strictly (an unrecognised mode raises rather than falling back to text). |
| **`served`** | `/api/bench/agent-turn` returns the model that actually answered (backend #664). Provider failover means the *requested* model is a request, not evidence — an arm labelled `opus5` whose turns were served by haiku is a mislabelled result, not a slow one. Recorded as a per-model count, so a run served 80/20 by two models shows both. |
| **`metadata_head`** | The whissle-large metadata head is **off in production** — STT routes to a third party and `WHISSLE_STT_TRANSPORT` is unset — so every run to date measured our cascade *without* its distinguishing layer. Captured from the environment and the backend's own agent config, never hand-asserted. |
| **`date` + provenance** | Read from the artifact's own recorded timestamp, never from file mtime. mtime is known-wrong here: `retail_run1.json` carries an mtime of 4 Aug for a run its contents date to 31 Jul. Where no timestamp exists, the date is `"not recorded"`, never inferred. |

## Rules the writer enforces

- **`raw/` is never edited.** Everything derived is computed from it and stored beside
  it, so a bad analysis can always be redone. `MANIFEST.md` is derived too — improving
  the renderer is `python -m tau2.archive rerender`, not a re-run.
- **Nothing is silently overwritten.** A collision gets `__2`, and both runs survive.
- **Nothing is silently dropped.** Every skipped file, degraded section and
  unrecoverable fact lands in `logs/archive.log` and in the manifest's `notes`.
- **"not recorded" is a value.** Never `null`, never `0`, never a plausible guess.

## Using it from a suite

```python
from tau2 import archive

ledger = archive.ServingLedger()          # 1. one line at the turn-taker
# ledger.record_response(resp, case_id=..., requested_model=...)

archive.export_run(                        # 2. one call at end of run
    suite="medagentbench",
    arm=run_name,
    modality=archive.MODALITY_TEXT,        # required — no default
    run_dir=out_dir,
    config=cfg, summary=summary, cases=records,
    ledger=ledger,
    reproduce_command="python -m tau2.health.medagent.run run --...",
)
```

Per-suite bindings live in `tau2/archive/suites.py`, so a runner's diff is one call.
Archiving never raises into a finished run: a benchmark that has produced its numbers
must not lose them to a copy step.

## CLI

```sh
python -m tau2.archive where                # print the archive root
python -m tau2.archive backfill --dry-run   # what would be archived
python -m tau2.archive backfill             # archive everything under results/
python -m tau2.archive index                # rebuild INDEX.md from disk
python -m tau2.archive rerender             # rebuild MANIFEST.md from manifest.json
python -m tau2.archive verify               # check every archived run is complete
```

## Configuration

| Variable | Effect |
|---|---|
| `TAU2_ARCHIVE_DIR` | Archive root. Defaults to `~/Downloads/whissle_benchmarks`; set it in CI or anywhere a user's home directory is the wrong answer. |
| `TAU2_ARCHIVE_MAX_RAW_FILE_MB` | Optional per-file ceiling for `raw/`. **Unset by default** — the point of the archive is that nothing lives only inside the repo. Set it where copying flow-sim's 7.4 GB of session audio is not viable; every skipped file is listed by path and size in `logs/archive.log` and counted in the manifest, and the source directory is recorded so the bytes stay findable. |
| `WHISSLE_BACKEND_SHA` | The backend revision. The deployment serves no version endpoint (`GET /health` returns only `{"ok": true}`), so without this the backend SHA is `"not recorded"` rather than guessed from a deploy time. |

## Which suites are wired

| Suite | Wired | Records served model + cost |
|---|---|---|
| MedAgentBench | ✅ | ✅ |
| AgentClinic | ✅ | ✅ |
| PatientAgentBench | ✅ | ✅ (via the process-wide ledger) |
| tau2 flow-sim | ✅ | user-simulator + judge spend only |
| flow-mutation | ✅ | — |
| flow-defaults | ✅ | — |
| model sweep | ✅ (backfill) | — |
| voice_qa | ❌ | **Does not exist in this repo.** No runner, results directory, or reference to it on any local or remote ref. If it exists it is in another repository, and it cannot be wired from here. |
