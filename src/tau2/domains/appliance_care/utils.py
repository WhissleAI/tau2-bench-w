"""Data paths and the frozen clock for the appliance_care domain.

The clock is frozen on purpose. The primary score is a hash over the whole
support database plus the hidden appliance state, so a wall-clock timestamp
written into any record would make two otherwise-identical runs disagree.
"""

from datetime import date, datetime

from tau2.utils.utils import DATA_DIR

APPLIANCE_CARE_DATA_DIR = DATA_DIR / "tau2" / "domains" / "appliance_care"
APPLIANCE_CARE_DB_PATH = APPLIANCE_CARE_DATA_DIR / "db.toml"
APPLIANCE_CARE_USER_DB_PATH = APPLIANCE_CARE_DATA_DIR / "user_db.toml"
APPLIANCE_CARE_POLICY_PATH = APPLIANCE_CARE_DATA_DIR / "policy.md"
APPLIANCE_CARE_MANUALS_DIR = APPLIANCE_CARE_DATA_DIR / "manuals"
APPLIANCE_CARE_TASK_SET_PATH = APPLIANCE_CARE_DATA_DIR / "tasks.json"

# ---------------------------------------------------------------------------
# Benchmark version
# ---------------------------------------------------------------------------
# Bumped whenever a change could move a score: the manual corpus, the task set,
# the database, the policy, or the TOOL DESCRIPTIONS. Tool descriptions count
# because they are part of the prompt every agent sees — clarifying one changes
# what the agent is being asked, so a run before and a run after are not
# measuring quite the same thing.
#
# Scores are comparable ONLY between runs carrying the same version. When
# reporting a number, report the version beside it.
#
#   v1  2026-08-19  invented brands (Northwind / Larkfield / Vantis).
#   v2  2026-08-20  real Bosch, LG and Miele manual extracts replace v1.
#   v3  2026-08-21  ac_05b corrected: the Miele WWB 020 does document a customer
#                   drain-filter clean, so the task no longer rewards booking an
#                   engineer for it.
#   v4  2026-08-24  appliance_id contract stated in the tool descriptions and in
#                   the lookup errors (model number and serial named as such,
#                   recovery path given). No task, database or manual changed.
#   v5  2026-08-25  source-grounding correction. All three Bosch extracts denied a
#                   customer drain-pump procedure their manuals document in full
#                   (p.28/29/31), so ac_01a punished an agent for obeying the
#                   manual. Bosch never uses the word "filter" for the drain path;
#                   the generic filter tools are replaced by model-specific
#                   sequences (Bosch pump / LG two in-drum lint filters / Miele
#                   screw-in filter). ac_02a/ac_02b rebuilt on the real E:23
#                   discriminator after the invented two-filter distinction and the
#                   "Bosch NW-22" label were removed. Policy gains a narrow service-
#                   access exception. Corpus, policy, tools, tasks and scoring all
#                   changed: no v4 or earlier number is comparable.
#   v6  2026-08-25  scoring audit after the first valid v5 run. Three defects,
#                   all found by that run rather than by reasoning about it:
#                   a correct final plug-in was scored as "never unplugged", a
#                   post-repair drain check satisfied the manual's first step
#                   because ACTION ignores order, and a materially correct
#                   resolution took DB 0 because its free-text wording differed
#                   from the reference. Ordering is now recorded explicitly, the
#                   DB hash excludes agent prose and live power position, and
#                   gold actions compare only decidable arguments. Scoring
#                   behaviour changed, so v5 numbers are not comparable.
#   v7  2026-08-25  the v6 run followed the manual exactly, passed all eleven
#                   assertions and produced a byte-identical support database -
#                   and scored zero, because it had read the error code, glanced
#                   at the drain hose, and restarted once to confirm the fix. A
#                   false negative. The hidden database is now split: the final
#                   condition and the required steps decide the score; harmless
#                   observations and diagnostic history are recorded and reported
#                   but do not fail a task. `get_appliance_details` is no longer
#                   required, since `list_owned_appliances` already returns the
#                   whole record. Rejected calls the agent recovers from are an
#                   efficiency cost, not a failure. ACTION is kept, because it is
#                   the only thing that catches an agent guessing the appliance_id
#                   without ever identifying the customer.
#   v8  2026-08-26  benchmark-defect pass after the v7 baseline. Three defects,
#                   all of which failed agents for things they had done right.
#                   A power cycle now counts as the documented reset on models
#                   that publish one, because "turn it off and on again" IS that
#                   procedure and only one of two equivalent tools was wired to
#                   it. Isolating a hazardous machine now records the stop-use
#                   instruction, so the signal no longer depends on whether the
#                   simulator reached for acknowledge_stop_using. A safety
#                   escalation no longer has to cite a manual, since you stop
#                   before consulting one. `severity` and `manual_id_used` moved
#                   out of the DB hash into named assertions. Scoring changed:
#                   v7 numbers are not comparable.
APPLIANCE_CARE_VERSION = "v8"


def get_now() -> datetime:
    """The simulated 'now'. Frozen — see the module docstring."""
    return datetime(2026, 3, 2, 10, 0, 0)


def get_today() -> date:
    """The simulated 'today'. Frozen — see the module docstring."""
    return date(2026, 3, 2)
