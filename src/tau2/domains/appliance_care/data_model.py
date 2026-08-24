"""Support-side database for the appliance_care domain.

This is what the agent's tools can read and write: customers, the appliance
models ApplianceCare supports, the machines customers own, warranty records,
service history, support cases, service appointments, and recorded resolutions.

The customer's *physical* machine — whether the filter is actually blocked,
whether there is a burning smell — lives in `user_data_model.py` and is never
readable from here. The agent learns it only by asking the customer.
"""

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field

from tau2.environment.db import DB
from tau2.utils.pydantic_utils import BaseModelNoExtra, get_pydantic_hash


class Customer(BaseModelNoExtra):
    customer_id: str = Field(description="Unique identifier for the customer")
    name: str = Field(description="Customer's full name")
    phone: str = Field(description="Customer's contact phone number")
    email: Optional[str] = Field(None, description="Customer's email address")


class ApplianceCategory(str, Enum):
    WASHING_MACHINE = "washing_machine"


class ApplianceModel(BaseModelNoExtra):
    """A model ApplianceCare supports. `manual_id` links it to the corpus."""

    model_id: str = Field(description="Model identifier, e.g. WAT28400UC")
    brand: str = Field(description="Brand name")
    category: ApplianceCategory = Field(description="Appliance category")
    manual_id: str = Field(description="Identifier of this model's manual")
    display_name: str = Field(description="Human-readable model name")
    manual_document_code: Optional[str] = Field(
        default=None,
        description="Document/revision code printed in the manufacturer's official "
        "manual for this model (e.g. Bosch '9001002399_H', LG 'MFL68485601_06'). "
        "Provenance for the extract in manuals/ — see MANUAL_SOURCES.md.",
    )
    user_serviceable_parts: List[str] = Field(
        default_factory=list,
        description="Parts the manual permits a customer to service",
    )
    error_codes: Dict[str, str] = Field(
        default_factory=dict,
        description="Error code -> meaning, as printed in THIS model's manual. "
        "The same code can mean different things on different models.",
    )
    reset_supported: bool = Field(
        False, description="Whether the manual documents a customer reset procedure"
    )
    customer_drain_maintenance_supported: bool = Field(
        True,
        description=(
            "Whether this model's manual documents a customer procedure for the "
            "DRAIN PATH. What that procedure is differs by manufacturer: Bosch "
            "documents cleaning the drain PUMP behind a service cover, Miele a "
            "screw-in drain FILTER behind a pump flap. False where the manual "
            "gives the customer no drain-path route at all - LG's two lint "
            "filters are a laundry-lint part and do not count."
        ),
    )


class OwnedAppliance(BaseModelNoExtra):
    appliance_id: str = Field(description="Unique identifier for this machine")
    customer_id: str = Field(description="Owner")
    model_id: str = Field(description="Which model this machine is")
    serial_number: str = Field(description="Serial number on the rating label")
    purchase_date: datetime.date = Field(description="Date of purchase (YYYY-MM-DD)")
    install_date: Optional[datetime.date] = Field(
        None, description="Date of installation (YYYY-MM-DD)"
    )


class WarrantyStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"


class Warranty(BaseModelNoExtra):
    warranty_id: str = Field(description="Unique identifier for the warranty record")
    appliance_id: str = Field(description="Machine this warranty covers")
    status: WarrantyStatus = Field(description="Whether the warranty is still active")
    expires_on: datetime.date = Field(description="Expiry date (YYYY-MM-DD)")
    covers: List[str] = Field(
        default_factory=list, description="Fault categories the warranty covers"
    )


class ServiceRecord(BaseModelNoExtra):
    record_id: str = Field(description="Unique identifier for the service record")
    appliance_id: str = Field(description="Machine that was serviced")
    date: datetime.date = Field(description="Date of the visit (YYYY-MM-DD)")
    summary: str = Field(description="What was done")


class CaseSeverity(str, Enum):
    NORMAL = "normal"
    SAFETY = "safety"


class CaseStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class SupportCase(BaseModelNoExtra):
    case_id: str = Field(description="Unique identifier for the support case")
    appliance_id: str = Field(description="Machine the case concerns")
    category: str = Field(description="Fault category, e.g. 'drainage'")
    severity: CaseSeverity = Field(
        CaseSeverity.NORMAL,
        description="'safety' marks a case where troubleshooting had to stop",
    )
    status: CaseStatus = Field(CaseStatus.OPEN, description="Case status")
    summary: str = Field(description="Short description of the problem")


class VisitType(str, Enum):
    WARRANTY = "warranty"
    BILLABLE = "billable"


class ServiceAppointment(BaseModelNoExtra):
    appointment_id: str = Field(description="Unique identifier for the appointment")
    case_id: str = Field(description="Case this visit addresses")
    date: datetime.date = Field(description="Visit date (YYYY-MM-DD)")
    window: str = Field(description="Time window, e.g. 'morning'")
    visit_type: VisitType = Field(
        description="'warranty' (no charge) or 'billable' (chargeable)"
    )


class ResolutionOutcome(str, Enum):
    RESOLVED_SELF_SERVICE = "resolved_self_service"
    SERVICE_SCHEDULED = "service_scheduled"
    ESCALATED_SAFETY = "escalated_safety"
    UNRESOLVED = "unresolved"


class Resolution(BaseModelNoExtra):
    resolution_id: str = Field(description="Unique identifier for the resolution")
    appliance_id: str = Field(description="Machine the contact concerned")
    outcome: ResolutionOutcome = Field(description="How the contact ended")
    steps_taken: List[str] = Field(
        default_factory=list, description="What the customer was guided through"
    )
    manual_id_used: Optional[str] = Field(
        None,
        description="Which manual the guidance came from. Recording this makes "
        "'did the agent use the right manual' a checkable fact rather than a "
        "judgement about the transcript.",
    )


class ApplianceCareDB(DB):
    """Support-side database for the appliance_care domain."""

    customers: List[Customer] = Field(default_factory=list)
    appliance_models: List[ApplianceModel] = Field(default_factory=list)
    owned_appliances: List[OwnedAppliance] = Field(default_factory=list)
    warranties: List[Warranty] = Field(default_factory=list)
    service_history: List[ServiceRecord] = Field(default_factory=list)
    support_cases: List[SupportCase] = Field(default_factory=list)
    service_appointments: List[ServiceAppointment] = Field(default_factory=list)
    resolutions: List[Resolution] = Field(default_factory=list)

    # Free-text fields the agent writes in its own words. They are stored,
    # returned to the agent, and readable in any transcript - but they are NOT
    # hashed, because a support case is not wrong for describing the same fault
    # in different English. Hashing them made the primary score a string-match on
    # prose: a v5 run recorded a materially correct resolution against the right
    # appliance with the right outcome and the right manual, and still took DB 0
    # because its `steps_taken` wording differed from the reference.
    #
    # What still counts: which records exist, for which appliance, with which
    # outcome, severity, visit type, dates and manual. Everything decidable.
    _UNHASHED_PROSE = {
        "support_cases": {"__all__": {"summary"}},
        "resolutions": {"__all__": {"steps_taken"}},
    }

    def get_hash(self) -> str:
        """Hash the decidable state, not the agent's phrasing."""
        return get_pydantic_hash(self, exclude=self._UNHASHED_PROSE)

    def get_statistics(self) -> Dict[str, Any]:
        return {
            "num_customers": len(self.customers),
            "num_appliance_models": len(self.appliance_models),
            "num_owned_appliances": len(self.owned_appliances),
            "num_warranties": len(self.warranties),
            "num_service_records": len(self.service_history),
            "num_support_cases": len(self.support_cases),
            "num_service_appointments": len(self.service_appointments),
            "num_resolutions": len(self.resolutions),
        }
