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


def get_now() -> datetime:
    """The simulated 'now'. Frozen — see the module docstring."""
    return datetime(2026, 3, 2, 10, 0, 0)


def get_today() -> date:
    """The simulated 'today'. Frozen — see the module docstring."""
    return date(2026, 3, 2)
