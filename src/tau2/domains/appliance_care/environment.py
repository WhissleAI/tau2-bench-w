"""Environment for the appliance_care domain.

Couples the two databases the way `telecom` does: after every tool call,
`sync_tools()` recomputes what the customer can observe from the machine's real
hidden condition. That is what makes "the customer cleaned the filter, so the
machine drains now, so the problem is gone" happen in state rather than in the
transcript — and it is why the primary score can be a hash comparison.

Retrieval variants (the `banking_knowledge` idea, offline):
  manual_access="search"      the agent gets search_manuals / open_manual_section /
                              lookup_error_code over the whole corpus  (default)
  manual_access="no_manuals"  those tools are withheld — the ablation that shows
                              how much of the score comes from the manuals
"""

from pathlib import Path
from typing import Optional

from tau2.data_model.tasks import Task
from tau2.domains.appliance_care.data_model import ApplianceCareDB
from tau2.domains.appliance_care.manuals import ManualLibrary
from tau2.domains.appliance_care.tools import (
    ApplianceCareTools,
    ApplianceCareToolsWithManuals,
)
from tau2.domains.appliance_care.user_data_model import ApplianceCareUserDB
from tau2.domains.appliance_care.user_tools import ApplianceCareUserTools
from tau2.domains.appliance_care.utils import (
    APPLIANCE_CARE_DB_PATH,
    APPLIANCE_CARE_MANUALS_DIR,
    APPLIANCE_CARE_POLICY_PATH,
    APPLIANCE_CARE_TASK_SET_PATH,
    APPLIANCE_CARE_USER_DB_PATH,
    APPLIANCE_CARE_VERSION,
)
from tau2.environment.environment import Environment
from tau2.utils import load_file

MANUAL_ACCESS_VARIANTS = ("search", "no_manuals")


class ApplianceCareEnvironment(Environment):
    tools: ApplianceCareTools
    user_tools: ApplianceCareUserTools

    def __init__(
        self,
        domain_name: str,
        policy: str,
        tools: ApplianceCareTools,
        user_tools: ApplianceCareUserTools,
    ):
        super().__init__(domain_name, policy, tools, user_tools)

    def get_benchmark_version(self) -> str:
        """Stamp every run with the version it was measured against.

        This is why the constant exists rather than living only in a report: a
        result file written months from now carries its own version, so nobody
        has to reconstruct which corpus, task set and tool descriptions produced
        the number.
        """
        return APPLIANCE_CARE_VERSION

    def sync_tools(self):
        """Keep the two sides consistent after every tool call.

        Deliberately writes nothing. Whether the machine drains, and whether the
        customer's complaint remains, are *computed* from the hidden fault state
        (`ApplianceCondition.drains_normally`, `ApplianceCareUserDB.problem_still_present`)
        rather than stored.

        That is not a stylistic choice. The gold environment reaches its end
        state through `Environment.make_tool_call`, which by contract does not
        call `sync_tools`, while the predicted environment is synced during
        replay. Anything this method wrote into the database would be stale on
        one side and fresh on the other, and the DB hashes would never match —
        even for a perfect trajectory. Any future sync here must therefore either
        be idempotent from the primitive state, or not enter the database at all.
        """
        return


def _build_policy() -> str:
    return load_file(APPLIANCE_CARE_POLICY_PATH)


def get_environment(
    db: Optional[ApplianceCareDB] = None,
    user_db: Optional[ApplianceCareUserDB] = None,
    solo_mode: bool = False,
    manual_access: str = "search",
    manuals_dir: Optional[str | Path] = None,
) -> ApplianceCareEnvironment:
    """Build the appliance_care environment.

    Args:
        db: Support-side database. Loaded from data/ when omitted.
        user_db: Customer-side hidden state. Loaded from data/ when omitted.
        solo_mode: Not supported — the whole domain rests on a customer who
            performs physical actions, so there is nothing to solo.
        manual_access: "search" (default) or "no_manuals" (ablation).
        manuals_dir: Override the manual corpus directory (used by tests).
    """
    if solo_mode:
        raise ValueError("Solo mode not supported for appliance_care")
    if manual_access not in MANUAL_ACCESS_VARIANTS:
        raise ValueError(
            f"Invalid manual_access: {manual_access}. "
            f"Valid: {', '.join(MANUAL_ACCESS_VARIANTS)}"
        )
    if db is None:
        db = ApplianceCareDB.load(APPLIANCE_CARE_DB_PATH)
    if user_db is None:
        user_db = ApplianceCareUserDB.load(APPLIANCE_CARE_USER_DB_PATH)

    if manual_access == "search":
        library = ManualLibrary.load(manuals_dir or APPLIANCE_CARE_MANUALS_DIR)
        tools: ApplianceCareTools = ApplianceCareToolsWithManuals(db, library=library)
    else:
        tools = ApplianceCareTools(db)

    user_tools = ApplianceCareUserTools(user_db)
    domain_name = (
        "appliance_care"
        if manual_access == "search"
        else f"appliance_care-{manual_access}"
    )
    return ApplianceCareEnvironment(
        domain_name=domain_name,
        policy=_build_policy(),
        tools=tools,
        user_tools=user_tools,
    )


def get_environment_no_manuals(**kwargs) -> ApplianceCareEnvironment:
    """The no-manual ablation, as a registrable domain constructor."""
    kwargs["manual_access"] = "no_manuals"
    return get_environment(**kwargs)


def load_tasks(path) -> list[Task]:
    tasks = load_file(path)
    if isinstance(tasks, dict) and "tasks" in tasks:
        tasks = tasks["tasks"]
    return [Task.model_validate(task) for task in tasks]


def load_tasks_split(path) -> Optional[dict[str, list[str]]]:
    split_file = Path(path).parent / f"split_{Path(path).stem}.json"
    if split_file.exists():
        return load_file(split_file)
    return None


def get_tasks(task_split_name: Optional[str] = "base") -> list[Task]:
    tasks = load_tasks(APPLIANCE_CARE_TASK_SET_PATH)
    if task_split_name is None:
        return tasks
    splits = get_tasks_split()
    if splits is None:
        return tasks
    if task_split_name not in splits:
        raise ValueError(
            f"Invalid task split name: {task_split_name}. Valid: {list(splits)}"
        )
    return [t for t in tasks if t.id in splits[task_split_name]]


def get_tasks_split() -> Optional[dict[str, list[str]]]:
    return load_tasks_split(APPLIANCE_CARE_TASK_SET_PATH)


if __name__ == "__main__":
    env = get_environment()
    print("agent tools:", [t.name for t in env.get_tools()])
    print("user tools: ", [t.name for t in env.get_user_tools()])
