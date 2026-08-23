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
APPLIANCE_CARE_VERSION = "v4"


def get_now() -> datetime:
    """The simulated 'now'. Frozen — see the module docstring."""
    return datetime(2026, 3, 2, 10, 0, 0)


def get_today() -> date:
    """The simulated 'today'. Frozen — see the module docstring."""
    return date(2026, 3, 2)
