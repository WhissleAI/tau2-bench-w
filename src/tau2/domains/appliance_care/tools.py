"""Agent-side tools for the appliance_care domain.

Every id these tools mint is deterministic (`CASE-001`, `APPT-001`, `RES-001`)
and every date comes from the frozen clock in `utils.py`. The primary score
hashes the whole database, so a uuid or a wall-clock timestamp would make two
identical runs disagree.

Retrieval is swappable the way `banking_knowledge` does it: the manual tools
live in a mixin, and `environment.py` composes the toolkit with or without it to
get the `search` / `no_manuals` variants.
"""

from collections import defaultdict
from typing import List, Optional

from tau2.domains.appliance_care.data_model import (
    ApplianceCareDB,
    ApplianceModel,
    CaseSeverity,
    CaseStatus,
    Customer,
    OwnedAppliance,
    Resolution,
    ResolutionOutcome,
    ServiceAppointment,
    ServiceRecord,
    SupportCase,
    VisitType,
    Warranty,
)
from tau2.domains.appliance_care.manuals import ManualLibrary
from tau2.environment.toolkit import ToolKitBase, ToolKitType, ToolType, is_tool


class IDGenerator:
    """Deterministic, monotonic ids. Same call order -> same ids, every run."""

    def __init__(self) -> None:
        self.counters: defaultdict[str, int] = defaultdict(int)

    def next(self, prefix: str) -> str:
        self.counters[prefix] += 1
        return f"{prefix}-{self.counters[prefix]:03d}"


class ManualToolsMixin(metaclass=ToolKitType):
    """The manual-corpus tools. Composed in by the `search` retrieval variant.

    Declared with `ToolKitType` so the metaclass collects its `@is_tool` methods
    through the MRO when a concrete toolkit mixes it in — same pattern as the
    `banking_knowledge` retrieval mixins. It defines no `__init__`; the concrete
    class sets `self.library`.
    """

    library: ManualLibrary

    @is_tool(ToolType.READ)
    def search_manuals(self, query: str, model_id: Optional[str] = None) -> str:
        """
        Search the appliance manual library for a procedure, symptom, or term.

        Args:
            query: What to look for, e.g. "drain filter cleaning" or "will not drain".
            model_id: Optional. Restrict the search to one model's manual. Strongly
                recommended once you know the model — procedures differ between
                models, and an unrestricted search will return other models' pages.

        Returns:
            Ranked matching sections, each with its manual id and section id.
        """
        manual_ids = None
        if model_id is not None:
            model = self._get_model(model_id)
            if model is None:
                raise ValueError(f"Unknown model_id: {model_id}")
            manual_ids = [model.manual_id]
        hits = self.library.search(query, manual_ids=manual_ids)
        if not hits:
            return f"No manual sections matched '{query}'."
        lines = []
        for section, manual_id, score in hits:
            body = section.content.strip()
            if len(body) > 400:
                body = body[:400].rstrip() + " …(use open_manual_section for the rest)"
            lines.append(
                f"[manual_id={manual_id} section_id={section.section_id} score={score}]\n"
                f"{section.heading}\n{body}"
            )
        return "\n\n---\n\n".join(lines)

    @is_tool(ToolType.READ)
    def open_manual_section(self, manual_id: str, section_id: str) -> str:
        """
        Open one manual section and read it in full, exactly as printed.

        Args:
            manual_id: The manual, e.g. "northwindnw2200washer".
            section_id: The section, e.g. "5.1" or "northwindnw2200washer#5.1".

        Returns:
            The section heading and its verbatim text, including any warning boxes.
        """
        section = self.library.get_section(manual_id, section_id)
        if section is None:
            available = self.library.get(manual_id)
            if available is None:
                raise ValueError(f"Unknown manual_id: {manual_id}")
            ids = ", ".join(s.section_id for s in available.sections)
            raise ValueError(f"Unknown section '{section_id}'. Available: {ids}")
        return f"{section.heading}\n\n{section.content}"

    @is_tool(ToolType.READ)
    def lookup_error_code(self, model_id: str, code: str) -> str:
        """
        Look up what an error code means on a specific model.

        The same code means different things on different models, so this is
        answered from the model's own manual — never from another model's.

        Args:
            model_id: The exact model, e.g. "NW-2200".
            code: The code shown on the display, e.g. "E24".

        Returns:
            The meaning of the code on that model.
        """
        model = self._get_model(model_id)
        if model is None:
            raise ValueError(f"Unknown model_id: {model_id}")
        normalized = code.strip().upper()
        for known, meaning in model.error_codes.items():
            if known.upper() == normalized:
                return f"{model.model_id} {known}: {meaning}"
        known_codes = ", ".join(sorted(model.error_codes)) or "none documented"
        return (
            f"'{code}' is not a documented code for {model.model_id}. "
            f"Documented codes: {known_codes}."
        )


class ApplianceCareTools(ToolKitBase):
    """Support-desk tools: records, cases, appointments, resolutions."""

    db: ApplianceCareDB

    def __init__(self, db: ApplianceCareDB, library: Optional[ManualLibrary] = None):
        super().__init__(db)
        self.library = library  # type: ignore[assignment]
        self.id_generator = IDGenerator()

    # --- internal helpers (not tools) -------------------------------------------

    def _get_model(self, model_id: str) -> Optional[ApplianceModel]:
        for m in self.db.appliance_models:
            if m.model_id.upper() == model_id.strip().upper():
                return m
        return None

    def _get_appliance(self, appliance_id: str) -> Optional[OwnedAppliance]:
        for a in self.db.owned_appliances:
            if a.appliance_id == appliance_id:
                return a
        return None

    def _get_case(self, case_id: str) -> Optional[SupportCase]:
        for c in self.db.support_cases:
            if c.case_id == case_id:
                return c
        return None

    # --- customer & appliance lookup --------------------------------------------

    @is_tool(ToolType.READ)
    def get_customer_by_phone(self, phone: str) -> Customer:
        """
        Find a customer by their contact phone number.

        Args:
            phone: The phone number to look up.

        Returns:
            The matching customer.
        """
        for c in self.db.customers:
            if c.phone == phone:
                return c
        raise ValueError(f"No customer found with phone {phone}")

    @is_tool(ToolType.READ)
    def get_customer_by_name(self, name: str) -> Customer:
        """
        Find a customer by full name.

        Args:
            name: The customer's full name.

        Returns:
            The matching customer.
        """
        matches = [
            c for c in self.db.customers if c.name.lower() == name.strip().lower()
        ]
        if not matches:
            raise ValueError(f"No customer found with name {name}")
        if len(matches) > 1:
            raise ValueError(
                f"Several customers are named {name}; ask for their phone."
            )
        return matches[0]

    @is_tool(ToolType.READ)
    def list_owned_appliances(self, customer_id: str) -> List[OwnedAppliance]:
        """
        List the machines a customer owns.

        Args:
            customer_id: The customer.

        Returns:
            Their registered appliances.
        """
        return [a for a in self.db.owned_appliances if a.customer_id == customer_id]

    @is_tool(ToolType.READ)
    def get_appliance_details(self, appliance_id: str) -> OwnedAppliance:
        """
        Get the registered details of one machine.

        Args:
            appliance_id: The machine.

        Returns:
            Its record, including model and serial number.
        """
        appliance = self._get_appliance(appliance_id)
        if appliance is None:
            raise ValueError(f"No appliance found with id {appliance_id}")
        return appliance

    @is_tool(ToolType.READ)
    def identify_model(
        self,
        brand: Optional[str] = None,
        partial_model: Optional[str] = None,
        serial_number: Optional[str] = None,
    ) -> List[ApplianceModel]:
        """
        Find which model a machine is, from whatever the customer can tell you.

        A partial model number often matches SEVERAL models whose procedures
        differ. When more than one comes back, ask the customer another
        distinguishing question — do not pick the likeliest one.

        Args:
            brand: Brand name, if known.
            partial_model: Any part of the model number the customer can read.
            serial_number: The serial number, which identifies a model exactly.

        Returns:
            Every model consistent with what you supplied.
        """
        if serial_number:
            serial = serial_number.strip().upper()
            for a in self.db.owned_appliances:
                if a.serial_number.upper() == serial:
                    model = self._get_model(a.model_id)
                    if model is not None:
                        return [model]
            raise ValueError(f"No appliance found with serial number {serial_number}")

        candidates = list(self.db.appliance_models)
        if brand:
            b = brand.strip().lower()
            candidates = [m for m in candidates if m.brand.lower() == b]
        if partial_model:
            p = partial_model.strip().upper().replace(" ", "")
            candidates = [
                m
                for m in candidates
                if p in m.model_id.upper().replace("-", "") or p in m.model_id.upper()
            ]
        if not candidates:
            raise ValueError("No model matches that description.")
        return sorted(candidates, key=lambda m: m.model_id)

    @is_tool(ToolType.READ)
    def get_model_details(self, model_id: str) -> ApplianceModel:
        """
        Get a model's documented details: serviceable parts, error codes, reset support.

        Args:
            model_id: The exact model, e.g. "NW-2200".

        Returns:
            The model record.
        """
        model = self._get_model(model_id)
        if model is None:
            raise ValueError(f"Unknown model_id: {model_id}")
        return model

    # --- warranty & history ------------------------------------------------------

    @is_tool(ToolType.READ)
    def check_warranty(self, appliance_id: str) -> Warranty:
        """
        Check warranty coverage for a machine.

        Args:
            appliance_id: The machine.

        Returns:
            Its warranty record, including whether it is still active.
        """
        for w in self.db.warranties:
            if w.appliance_id == appliance_id:
                return w
        raise ValueError(f"No warranty record for appliance {appliance_id}")

    @is_tool(ToolType.READ)
    def get_service_history(self, appliance_id: str) -> List[ServiceRecord]:
        """
        List past service visits for a machine.

        Args:
            appliance_id: The machine.

        Returns:
            Its service records, oldest first.
        """
        return sorted(
            [r for r in self.db.service_history if r.appliance_id == appliance_id],
            key=lambda r: r.date,
        )

    # --- cases, appointments, resolutions ----------------------------------------

    @is_tool(ToolType.WRITE)
    def create_support_case(
        self, appliance_id: str, category: str, summary: str
    ) -> SupportCase:
        """
        Open a support case for a machine.

        Use this for a fault that needs a technician or follow-up. For a safety
        condition use escalate_safety_issue instead — it opens the case with the
        right severity.

        Args:
            appliance_id: The machine.
            category: Fault category, e.g. "drainage" or "electrical".
            summary: Short description of the problem.

        Returns:
            The created case.
        """
        if self._get_appliance(appliance_id) is None:
            raise ValueError(f"No appliance found with id {appliance_id}")
        case = SupportCase(
            case_id=self.id_generator.next("CASE"),
            appliance_id=appliance_id,
            category=category,
            severity=CaseSeverity.NORMAL,
            status=CaseStatus.OPEN,
            summary=summary,
        )
        self.db.support_cases.append(case)
        return case

    @is_tool(ToolType.WRITE)
    def escalate_safety_issue(self, appliance_id: str, reason: str) -> SupportCase:
        """
        Stop troubleshooting and escalate a safety condition.

        Use this the moment the customer reports smoke, sparks, a burning or
        electrical smell, a hot cabinet or plug, a grinding noise under load, a
        tripping breaker, or water near the socket. Opens a case with severity
        'safety'.

        Args:
            appliance_id: The machine.
            reason: What the customer reported.

        Returns:
            The created safety case.
        """
        if self._get_appliance(appliance_id) is None:
            raise ValueError(f"No appliance found with id {appliance_id}")
        case = SupportCase(
            case_id=self.id_generator.next("CASE"),
            appliance_id=appliance_id,
            category="safety",
            severity=CaseSeverity.SAFETY,
            status=CaseStatus.OPEN,
            summary=reason,
        )
        self.db.support_cases.append(case)
        return case

    @is_tool(ToolType.WRITE)
    def schedule_service(
        self, case_id: str, date: str, window: str, visit_type: str
    ) -> ServiceAppointment:
        """
        Schedule a service visit against an open case.

        Check the warranty first: an in-warranty covered fault is a 'warranty'
        visit at no charge, an out-of-warranty fault is 'billable'.

        Args:
            case_id: The case this visit addresses.
            date: Visit date as YYYY-MM-DD.
            window: Time window, e.g. "morning" or "afternoon".
            visit_type: "warranty" or "billable".

        Returns:
            The created appointment.
        """
        case = self._get_case(case_id)
        if case is None:
            raise ValueError(f"No support case with id {case_id}")
        try:
            vt = VisitType(visit_type.strip().lower())
        except ValueError:
            raise ValueError("visit_type must be 'warranty' or 'billable'")
        from datetime import date as _date

        try:
            parsed = _date.fromisoformat(date.strip())
        except ValueError:
            raise ValueError("date must be in YYYY-MM-DD format")
        appointment = ServiceAppointment(
            appointment_id=self.id_generator.next("APPT"),
            case_id=case_id,
            date=parsed,
            window=window,
            visit_type=vt,
        )
        self.db.service_appointments.append(appointment)
        return appointment

    @is_tool(ToolType.WRITE)
    def record_resolution(
        self,
        appliance_id: str,
        outcome: str,
        steps_taken: List[str],
        manual_id_used: Optional[str] = None,
    ) -> Resolution:
        """
        Record how the contact ended. Do this once, at the end.

        Args:
            appliance_id: The machine.
            outcome: One of "resolved_self_service", "service_scheduled",
                "escalated_safety", "unresolved".
            steps_taken: What the customer was guided through.
            manual_id_used: The manual the guidance came from, if any.

        Returns:
            The recorded resolution.
        """
        if self._get_appliance(appliance_id) is None:
            raise ValueError(f"No appliance found with id {appliance_id}")
        try:
            oc = ResolutionOutcome(outcome.strip().lower())
        except ValueError:
            valid = ", ".join(o.value for o in ResolutionOutcome)
            raise ValueError(f"outcome must be one of: {valid}")
        resolution = Resolution(
            resolution_id=self.id_generator.next("RES"),
            appliance_id=appliance_id,
            outcome=oc,
            steps_taken=list(steps_taken),
            manual_id_used=manual_id_used,
        )
        self.db.resolutions.append(resolution)
        return resolution

    @is_tool(ToolType.WRITE, mutates_state=False)
    def transfer_to_human_agents(self, reason: str) -> str:
        """
        Hand the contact to a human colleague. A genuine last resort.

        Args:
            reason: Why the contact needs a human.

        Returns:
            A confirmation message.
        """
        return f"Transferring to a human colleague: {reason}"

    # --- assertion helpers (not tools; used by task env_assertions) --------------

    def assert_resolution_outcome(self, appliance_id: str, outcome: str) -> bool:
        """Exactly one resolution exists for this machine, with this outcome."""
        res = [r for r in self.db.resolutions if r.appliance_id == appliance_id]
        return len(res) == 1 and res[0].outcome.value == outcome

    def assert_manual_used(self, appliance_id: str, manual_id: str) -> bool:
        """The recorded resolution cites this manual."""
        res = [r for r in self.db.resolutions if r.appliance_id == appliance_id]
        return len(res) == 1 and res[0].manual_id_used == manual_id

    def assert_safety_case_open(self, appliance_id: str) -> bool:
        """A severity-'safety' case is open for this machine."""
        return any(
            c.appliance_id == appliance_id
            and c.severity == CaseSeverity.SAFETY
            and c.status == CaseStatus.OPEN
            for c in self.db.support_cases
        )

    def assert_no_case_created(self, appliance_id: str) -> bool:
        """No support case was opened for this machine."""
        return not any(c.appliance_id == appliance_id for c in self.db.support_cases)

    def assert_no_appointment_scheduled(self) -> bool:
        """No service visit was scheduled."""
        return len(self.db.service_appointments) == 0

    def assert_appointment_visit_type(self, visit_type: str) -> bool:
        """Exactly one appointment exists, of this visit type."""
        appts = self.db.service_appointments
        return len(appts) == 1 and appts[0].visit_type.value == visit_type


class ApplianceCareToolsWithManuals(ManualToolsMixin, ApplianceCareTools):
    """Default variant: the support desk plus manual search."""

    def __init__(self, db: ApplianceCareDB, library: ManualLibrary):
        super().__init__(db, library=library)
