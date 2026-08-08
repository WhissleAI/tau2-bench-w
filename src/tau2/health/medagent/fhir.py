"""FHIR client for the MedAgentBench virtual EHR.

Two responsibilities:

1. `send_get_request` reproduces upstream's read path *bit for bit*, including
   the quirk that matters: the HAPI server answers `application/fhir+json`,
   which is not `application/json`, so upstream returns `response.text` — the
   agent is shown a raw JSON **string**, not a parsed object. Changing that
   would change the task difficulty, so we keep it.

2. The write-verification path upstream does not have. Upstream *never sends
   the POST*: it parses the payload, replies "POST request accepted and
   executed successfully", and grades the payload string out of the
   conversation. This module can additionally ask the real EHR whether it
   would accept the write (`$validate`) or actually perform it and read the
   resource back (`execute`). See `integrity.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

import requests


class FhirInfraError(RuntimeError):
    """The virtual EHR could not be reached — an infra failure, not an agent
    failure. Callers must classify the session `infra_fail` and exclude it."""


def send_get_request(
    url: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Upstream-identical GET.

    Returns `{"status_code": int, "data": str|dict}` on success or
    `{"error": str}` on any failure — upstream swallows the exception and
    hands the error text to the agent, so we do too.
    """
    try:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
        return {
            "status_code": response.status_code,
            "data": (
                response.json()
                if response.headers.get("Content-Type") == "application/json"
                else response.text
            ),
        }
    except Exception as e:  # noqa: BLE001 — upstream parity
        return {"error": str(e)}


def verify_fhir_server(fhir_api_base: str) -> bool:
    """True when the virtual EHR answers its capability statement."""
    res = send_get_request(f"{fhir_api_base}metadata")
    return res.get("status_code", 0) == 200


@dataclass
class WriteAttempt:
    """One POST the agent emitted, and what the real EHR made of it.

    `claimed` is always true — the agent emitted this POST and the harness told
    it the write succeeded. Everything else records what the EHR *actually*
    did, which upstream never asks.
    """

    url: str
    resource_type: str
    payload: dict[str, Any]
    mode: str = "none"  # none | validate | execute
    accepted: Optional[bool] = None  # did the EHR accept the resource?
    created_id: Optional[str] = None  # id of the resource that now exists
    read_back: bool = False  # confirmed present by a follow-up GET
    status_code: Optional[int] = None
    error: Optional[str] = None
    issues: list[str] = field(default_factory=list)

    # Conformance is a SEPARATE question from whether the write landed, and the
    # two genuinely disagree. Observed on task8: the payload MedAgentBench's
    # grader requires (`note` as an object) is not valid FHIR R4 — strict
    # `$validate` rejects it — yet HAPI's create endpoint leniently coerces it
    # to `note: [{...}]` and stores it. Collapsing these into one boolean would
    # report either a false failure or a false success depending on the mode.
    conformant: Optional[bool] = None
    conformance_issues: list[str] = field(default_factory=list)

    @property
    def verified_write(self) -> bool:
        """The resource demonstrably exists in the EHR."""
        return bool(self.read_back and self.created_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "resource_type": self.resource_type,
            "payload": self.payload,
            "mode": self.mode,
            "accepted": self.accepted,
            "created_id": self.created_id,
            "read_back": self.read_back,
            "verified_write": self.verified_write,
            "conformant": self.conformant,
            "conformance_issues": self.conformance_issues,
            "status_code": self.status_code,
            "error": self.error,
            "issues": self.issues,
        }


class FhirWriter:
    """Performs (or dry-runs) the writes the upstream harness only pretends to.

    `mode`:
      * `none`     — pure upstream parity; record the payload, ask nothing.
      * `validate` — POST to `{Resource}/$validate`. Non-mutating: strict FHIR R4
                     structural validation. Answers "is this conformant?", which
                     is NOT the same as "would it be stored" — HAPI's create
                     endpoint is more lenient than its validator. Safe to run
                     against the server being read for grading, hence default.
      * `execute`  — run `$validate` for conformance AND really POST, then GET
                     the created resource back. Proves the write landed. Mutates
                     the EHR, so it requires explicit opt-in and a disposable
                     container.
    """

    def __init__(self, api_base: str, mode: str = "validate", timeout: float = 60.0):
        if mode not in ("none", "validate", "execute"):
            raise ValueError(f"unknown write-check mode: {mode!r}")
        self.api_base = api_base if api_base.endswith("/") else api_base + "/"
        self.mode = mode
        self.timeout = timeout

    def check(self, url: str, payload: dict[str, Any]) -> WriteAttempt:
        resource_type = _resource_type(url, payload, self.api_base)
        attempt = WriteAttempt(
            url=url, resource_type=resource_type, payload=payload, mode=self.mode
        )
        if self.mode == "none":
            return attempt
        if not resource_type:
            attempt.accepted = False
            attempt.error = "could not determine resource type from URL or payload"
            return attempt
        try:
            if self.mode == "validate":
                self._validate(attempt)
            else:
                self._execute(attempt)
        except requests.RequestException as e:
            # Transport failure against the virtual EHR is infra, not agent
            # behaviour. Surface it so the episode is classified `infra_fail`.
            raise FhirInfraError(f"FHIR write-check transport failure: {e}") from e
        return attempt

    def _conformance(self, attempt: WriteAttempt) -> None:
        """Strict FHIR R4 validation. Sets `conformant`, never `accepted`."""
        r = requests.post(
            f"{self.api_base}{attempt.resource_type}/$validate",
            json=attempt.payload,
            headers={"Content-Type": "application/fhir+json"},
            timeout=self.timeout,
        )
        outcome = _safe_json(r)
        attempt.conformance_issues = _error_issues(outcome)
        # HAPI answers 200 with an OperationOutcome; the resource is conformant
        # when no issue has severity error/fatal.
        attempt.conformant = r.status_code < 300 and not attempt.conformance_issues

    def _validate(self, attempt: WriteAttempt) -> None:
        self._conformance(attempt)
        # In validate mode conformance is the only evidence available, so it
        # stands in for acceptance — but the two are reported separately so a
        # reader can tell which question was actually asked.
        attempt.accepted = attempt.conformant
        attempt.issues = list(attempt.conformance_issues)

    def _execute(self, attempt: WriteAttempt) -> None:
        # Ask both questions: is it conformant, and does it actually land?
        try:
            self._conformance(attempt)
        except requests.RequestException:
            attempt.conformant = None
        r = requests.post(
            f"{self.api_base}{attempt.resource_type}",
            json=attempt.payload,
            headers={"Content-Type": "application/fhir+json"},
            timeout=self.timeout,
        )
        attempt.status_code = r.status_code
        if r.status_code >= 300:
            attempt.accepted = False
            outcome = _safe_json(r)
            attempt.issues = _error_issues(outcome)
            attempt.error = f"HTTP {r.status_code}: {r.text[:300]}"
            return
        attempt.accepted = True
        body = _safe_json(r) or {}
        attempt.created_id = body.get("id") or _id_from_location(
            r.headers.get("Location") or r.headers.get("Content-Location") or ""
        )
        if not attempt.created_id:
            attempt.error = "EHR accepted the POST but returned no resource id"
            return
        # The write only counts once we can read it back out of the chart.
        got = send_get_request(
            f"{self.api_base}{attempt.resource_type}/{attempt.created_id}?_format=json",
            timeout=self.timeout,
        )
        attempt.read_back = got.get("status_code") == 200

    def cleanup(self, attempts: list[WriteAttempt]) -> int:
        """Delete resources created by `execute` mode. Best-effort."""
        deleted = 0
        for a in attempts:
            if not a.created_id:
                continue
            try:
                r = requests.delete(
                    f"{self.api_base}{a.resource_type}/{a.created_id}",
                    timeout=self.timeout,
                )
                if r.status_code < 300:
                    deleted += 1
            except requests.RequestException:
                pass
        return deleted


def _resource_type(url: str, payload: dict[str, Any], api_base: str) -> str:
    """Prefer the payload's own `resourceType`; fall back to the URL tail.

    Using the payload first matters for the integrity story: if the agent posts
    a MedicationRequest body to the Observation endpoint, we want the EHR asked
    about the body it actually sent.
    """
    rt = payload.get("resourceType")
    if isinstance(rt, str) and rt.strip():
        return rt.strip()
    tail = url.split("?")[0].rstrip("/")
    if tail.startswith(api_base):
        tail = tail[len(api_base) :]
    seg = tail.rsplit("/", 1)[-1]
    return seg if seg.isalpha() else ""


def _safe_json(r: requests.Response) -> Optional[dict[str, Any]]:
    try:
        return r.json()
    except (ValueError, json.JSONDecodeError):
        return None


def _error_issues(outcome: Optional[dict[str, Any]]) -> list[str]:
    """Extract error/fatal diagnostics from a FHIR OperationOutcome."""
    if not isinstance(outcome, dict):
        return []
    issues = []
    for issue in outcome.get("issue") or []:
        if not isinstance(issue, dict):
            continue
        if issue.get("severity") in ("error", "fatal"):
            issues.append(
                str(issue.get("diagnostics") or issue.get("code") or "error")[:300]
            )
    return issues


def _id_from_location(location: str) -> Optional[str]:
    """`.../fhir/Observation/123/_history/1` -> `123`."""
    if not location:
        return None
    parts = [p for p in location.split("?")[0].split("/") if p]
    if "_history" in parts:
        parts = parts[: parts.index("_history")]
    return parts[-1] if parts else None
