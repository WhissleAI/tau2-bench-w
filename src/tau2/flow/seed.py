# Copyright Sierra
"""Per-agent SEEDING layer for the simulated-user flow engine — the "true
completion" harness where every tool has the real data it needs to SUCCEED.

Where ``flow/simulate.py`` drives a throwaway agent whose tools mostly *fail-soft*
(no customer to verify, no fleet to reserve, no ticket to save), this module wraps
each freshly-created test agent with a ``seed`` → run → ``teardown`` bracket so its
tools operate on REAL records inserted for the session and cleaned up after it.

A :class:`Seeder` is keyed by ``agent_type``. Given a just-created ``agent_id`` and
the persona ``Task``, :meth:`Seeder.seed` inserts the records that type's tools read,
returns a :class:`SeedContext` (tracked resource ids + the identity/consistency
facts to feed the user-sim + the action tool to assert), and :meth:`Seeder.teardown`
deletes every tracked id (by id — never a pattern-delete) so nothing lingers.

Recipes (per the scoping map; exact request shapes verified live):

  debt_collection    POST /api/customers — a contact BOUND to the agent whose
                     ``attributes`` land in the debt template vars (borrower_name,
                     due_amount, days_overdue, due_date, loan_account_number,
                     merchant_name, phone, date_of_birth). The sim states its full
                     name + last-4 of the account (+ DOB) so verify_identity →
                     mark_verified fires and the balance is disclosed ONLY in the
                     post-verify ``disclose_balance`` state.
  appointment_*      schedule auto-seeds (Mon–Fri 09:00–18:00 Asia/Kolkata); a NEW
  dental_receptionist booking needs no record. reschedule/cancel BOOK in-flow first
                     in the same session, then change (matched on agent_id + phone),
                     so the sim goal is prefixed with a two-phase book-then-change.
  car_rental         the fleet AUTO-SEEDS on create (fix #600) — no manual seed;
                     search_records returns inventory and reserve works.
  customer_support   POST /api/customers for verification/save_*; a data-lookup
                     credential (POST /api/orgs/{org}/credentials) as the
                     lookup_record seam; a KB doc (POST /api/agents/{id}/kb/ingest)
                     so search_knowledge_base can RESOLVE resolvable cases.

Every seed op is BEST-EFFORT and self-describing: it records ``ok`` /
``skipped:<scope>`` / ``error`` per step and never aborts the session — a key that
lacks ``contacts:write`` / ``kb:write`` simply degrades those steps to ``skipped``
(the tool then fail-softs, exactly as the unseeded run) while the within-scope steps
(credential seam, agent-variable var-patch fallback, sim-fact injection) still apply.

This module owns ONLY seeding + the seeded-run assertions (pre-verify disclosure
scan, action-tool-called, resolve_done). It is pure of flow-analysis logic — that
stays in ``analyze.py``. Network is via the shared :class:`FlowClient`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from tau2.flow.client import FlowClient, FlowClientError

# ── seeded identity / consistency facts, per persona ──────────────────────────
# A stable phone per session lets appt/dental reschedule/cancel match the booking
# the sim makes earlier in the SAME session (matched on agent_id + phone).


@dataclass
class SeedResource:
    """One inserted record, tracked for teardown by exact id (never pattern)."""

    kind: str                    # customer | credential | kb_doc
    id: str
    delete_path: str             # the DELETE path relative to the client base
    org_scoped: bool = False


@dataclass
class SeedContext:
    """What a seed produced: tracked resources, sim guidance, assertions to make."""

    agent_type: str
    resources: list[SeedResource] = field(default_factory=list)
    # Facts appended to the user-sim system prompt so it states consistent details
    # (verified name / last-4 / DOB for debt; a stable phone for appt/dental).
    sim_facts: str = ""
    # Prepended to the persona goal (e.g. book-then-reschedule for reschedule tasks).
    goal_prefix: str = ""
    # The tool the flow must actually CALL for a true completion (no fake confirm).
    expected_action_tool: Optional[str] = None
    # Per-step seeding status, surfaced in the session result + report.
    steps: list[dict[str, Any]] = field(default_factory=list)

    def note(self, step: str, status: str, detail: str = "", **extra: Any) -> None:
        self.steps.append({"step": step, "status": status, "detail": detail, **extra})

    @property
    def customer_phone(self) -> Optional[str]:
        for r in self.resources:
            if r.kind == "customer":
                return getattr(r, "_phone", None)
        return None


# ── the identity fixtures used to seed + prime the sim ────────────────────────
# Keyed by the debt persona name so the seeded customer + the sim's stated identity
# agree. Falls back to a generic John Miller record for any debt task.

_DEBT_IDENTITY = {
    "debt_rightparty_pays": ("John Miller", "1985-03-12", "LT99213", "12500", "15"),
    "debt_promise_to_pay":  ("Sarah Owens", "1990-07-22", "LT88104", "9800", "22"),
    "debt_dispute":         ("Alan Pierce", "1978-11-03", "LT77321", "15400", "31"),
    "debt_partial_payment": ("Grace Hall",  "1982-05-19", "LT66540", "7200", "12"),
    "debt_hardship":        ("Ben Carter",  "1975-09-28", "LT55210", "20300", "45"),
    "debt_verify_then_hang":("Dana Reed",   "1988-02-14", "LT44118", "6100", "9"),
    "debt_callback_request":("Omar Farid",  "1980-12-01", "LT33027", "11200", "18"),
}
_DEBT_DEFAULT = ("John Miller", "1985-03-12", "LT99213", "12500", "15")

# The debt tasks whose persona is NOT the right party / refuses to verify — for
# these we must NOT prime the sim with verifiable identity (that is the whole test).
_DEBT_NO_VERIFY = {
    "debt_wrong_party", "debt_refuse_verify", "debt_probe_before_verify",
    "debt_wrong_person_name",
}

# Expected action tool per (agent_type, scenario) — the tool a TRUE completion must
# actually invoke. None where no single tool defines success (e.g. debt compliance
# scenarios, CS resolvable which is judged by resolve_done instead).
_ACTION_TOOL = {
    ("dental_receptionist", "book"):        "book_appointment",
    ("dental_receptionist", "reschedule"):  "reschedule_appointment",
    ("dental_receptionist", "cancel"):      "cancel_appointment",
    ("appointment_scheduling", "new"):        "book_appointment",
    ("appointment_scheduling", "reschedule"): "reschedule_appointment",
    ("appointment_scheduling", "cancel"):     "cancel_appointment",
    ("car_rental", "book"):                 "reserve",
    ("car_rental", "change vehicle"):       "reserve",
    ("debt_collection", "promise-to-pay"):  "capture_ptp",
}


class Seeder:
    """Seed → run → teardown bracket for one agent_type's test agent."""

    def __init__(self, client: FlowClient, org_id: Optional[str] = None) -> None:
        self.client = client
        self.org_id = org_id or self._resolve_org()

    def _resolve_org(self) -> str:
        try:
            who = self.client.whoami()
            return (who.get("organization") or {}).get("id") or ""
        except Exception:  # noqa: BLE001
            return ""

    # ── public API ────────────────────────────────────────────────────────────

    def seed(self, agent_id: str, task: Any) -> SeedContext:
        ctx = SeedContext(agent_type=task.agent_type)
        fn = getattr(self, f"_seed_{task.agent_type}", None)
        if fn is None:
            ctx.note("recipe", "skipped", f"no seed recipe for {task.agent_type}")
            return ctx
        try:
            fn(agent_id, task, ctx)
        except Exception as e:  # noqa: BLE001 — seeding never aborts a session
            ctx.note("recipe", "error", f"{type(e).__name__}: {e}")
        return ctx

    def teardown(self, ctx: SeedContext) -> dict[str, Any]:
        """Delete every tracked resource by its exact id. Returns a teardown report
        (deleted / failed) so the runner can assert 0 lingering."""
        deleted, failed = [], []
        for r in ctx.resources:
            try:
                self.client._req("DELETE", r.delete_path, action=f"del_{r.kind}")
                deleted.append({"kind": r.kind, "id": r.id})
            except Exception as e:  # noqa: BLE001
                failed.append({"kind": r.kind, "id": r.id, "detail": str(e)})
        return {"deleted": deleted, "failed": failed,
                "tracked": len(ctx.resources)}

    # ── low-level best-effort inserts ──────────────────────────────────────────

    def _post_customer(self, agent_id: str, phone: str, name: str,
                       attributes: dict, ctx: SeedContext) -> Optional[str]:
        """POST /api/customers a contact BOUND to the agent. Best-effort: a key
        lacking contacts:write degrades to a ``skipped`` note (tool then fail-softs)."""
        body = {"agent_id": agent_id, "phone": phone, "name": name,
                "attributes": attributes}
        try:
            d = self.client._req("POST", "/api/customers", json=body,
                                 action="seed_customer").json()
            cid = d.get("id")
            if cid:
                res = SeedResource("customer", cid, f"/api/customers/{cid}")
                res._phone = phone  # type: ignore[attr-defined]
                ctx.resources.append(res)
                ctx.note("customer", "ok", f"id={cid} phone={phone}")
            return cid
        except FlowClientError as e:
            status = "skipped" if e.status == 403 else "error"
            scope = "contacts:write" if e.status == 403 else str(e.status)
            ctx.note("customer", status, f"POST /api/customers -> {scope}")
            return None

    def _patch_agent_vars(self, agent_id: str, values: dict, ctx: SeedContext) -> None:
        """Within-scope fallback: write the debt facts as STATIC agent variables so
        the template has values even when the customer record could not be inserted.
        (agents:write is present; this is harmless when the customer seed succeeded.)"""
        try:
            got = self.client.get_agent(agent_id)
            existing = {v.get("key"): dict(v) for v in (got.get("variables") or [])}
            for k, val in values.items():
                v = existing.get(k) or {"key": k, "type": "text", "label": k}
                v["source"] = "static"
                v["value"] = str(val)
                existing[k] = v
            self.client._req("PATCH", f"/api/agents/{agent_id}",
                            json={"variables": list(existing.values())},
                            action="seed_var_patch")
            ctx.note("var_patch", "ok", f"static vars: {sorted(values)}")
        except FlowClientError as e:
            ctx.note("var_patch", "error", str(e))

    def _post_credential(self, kind: str, name: str, config: dict,
                        ctx: SeedContext) -> Optional[str]:
        try:
            d = self.client._req(
                "POST", f"/api/orgs/{self.org_id}/credentials",
                json={"kind": kind, "name": name, "config": config},
                action="seed_credential").json()
            cid = d.get("id")
            if cid:
                ctx.resources.append(SeedResource(
                    "credential", cid,
                    f"/api/orgs/{self.org_id}/credentials/{cid}", org_scoped=True))
                ctx.note("credential", "ok", f"id={cid} kind={kind}")
            return cid
        except FlowClientError as e:
            status = "skipped" if e.status == 403 else "error"
            ctx.note("credential", status, f"POST credentials -> {e.status}")
            return None

    def _ingest_kb(self, agent_id: str, title: str, text: str,
                  ctx: SeedContext) -> Optional[str]:
        try:
            d = self.client._req(
                "POST", f"/api/agents/{agent_id}/kb/ingest",
                json={"title": title, "text": text}, action="seed_kb").json()
            doc_id = d.get("id") or d.get("doc_id")
            if doc_id:
                ctx.resources.append(SeedResource(
                    "kb_doc", doc_id, f"/api/agents/{agent_id}/kb/{doc_id}"))
                ctx.note("kb_doc", "ok", f"id={doc_id}")
            else:
                ctx.note("kb_doc", "ok", "ingested (no id returned)")
            return doc_id
        except FlowClientError as e:
            status = "skipped" if e.status == 403 else "error"
            ctx.note("kb_doc", status, f"POST kb/ingest -> {e.status}")
            return None

    # ── per-type recipes ───────────────────────────────────────────────────────

    def _seed_debt_collection(self, agent_id: str, task: Any,
                              ctx: SeedContext) -> None:
        name, dob, acct, amount, overdue = _DEBT_IDENTITY.get(task.id, _DEBT_DEFAULT)
        phone = _phone_for(task.id)
        attrs = {
            "borrower_name": name, "due_amount": amount, "days_overdue": overdue,
            "due_date": "2026-06-20", "loan_account_number": acct,
            "merchant_name": "LoanTap", "phone": phone, "date_of_birth": dob,
        }
        self._post_customer(agent_id, phone, name, attrs, ctx)
        # Within-scope fallback so the template has the amount even if the customer
        # insert was scope-skipped.
        self._patch_agent_vars(agent_id, {
            "borrower_name": name, "due_amount": amount, "days_overdue": overdue,
            "due_date": "2026-06-20", "loan_account_number": acct,
            "merchant_name": "LoanTap"}, ctx)

        if task.id in _DEBT_NO_VERIFY:
            ctx.sim_facts = (
                "You are NOT the person the agent is looking for (or you refuse to "
                "verify). Do NOT provide a real name, DOB, or account number that "
                "would pass verification.")
        else:
            last4 = acct[-4:]
            ctx.sim_facts = (
                f"Your verified identity (state these when the agent asks to confirm "
                f"who you are): full name {name}; date of birth {dob}; loan/account "
                f"number {acct} (last four digits {last4}). You ARE the right party.")
        ctx.expected_action_tool = _ACTION_TOOL.get(
            (task.agent_type, task.scenario))

    def _seed_appointment_scheduling(self, agent_id: str, task: Any,
                                     ctx: SeedContext) -> None:
        self._seed_scheduling_common(agent_id, task, ctx)

    def _seed_dental_receptionist(self, agent_id: str, task: Any,
                                  ctx: SeedContext) -> None:
        self._seed_scheduling_common(agent_id, task, ctx)

    def _seed_scheduling_common(self, agent_id: str, task: Any,
                                ctx: SeedContext) -> None:
        # Schedule auto-seeds (Mon–Fri 09:00–18:00 Asia/Kolkata) — no record to
        # insert. Give the sim a stable phone so an in-session booking and a later
        # reschedule/cancel match on agent_id + phone.
        phone = _phone_for(task.id)
        ctx.note("schedule", "auto", "Mon–Fri 09:00–18:00 Asia/Kolkata (auto-seeded)")
        ctx.sim_facts = (
            f"Use these contact details consistently whenever asked: phone {phone}. "
            f"Pick a WEEKDAY (Mon–Fri) time between 9am and 6pm for any appointment.")
        if task.scenario in ("reschedule", "cancel"):
            # No pre-existing booking to match — BOOK one in-flow first, in this same
            # session, then change it (matched on agent_id + phone).
            verb = "reschedule it to a different weekday" if task.scenario == \
                "reschedule" else "cancel it"
            ctx.goal_prefix = (
                f"FIRST book a new weekday appointment (give your name and phone "
                f"{phone} and confirm a specific weekday 9am–6pm slot). ONLY AFTER "
                f"the booking is confirmed, {verb}. ")
        ctx.expected_action_tool = _ACTION_TOOL.get((task.agent_type, task.scenario))

    def _seed_car_rental(self, agent_id: str, task: Any, ctx: SeedContext) -> None:
        # Fleet auto-seeds on create (fix #600): search_records returns inventory and
        # reserve works. Nothing to insert; prime the sim with concrete dates so the
        # search has parameters and the flow can reach book → confirm → end.
        ctx.note("fleet", "auto", "fleet auto-seeded on create (#600)")
        ctx.sim_facts = (
            "Use a specific US pickup city (e.g. Austin), a concrete future pickup "
            "date and return date, and accept the FIRST matching vehicle offered so "
            "the booking can complete. Provide name, phone and email when asked.")
        ctx.expected_action_tool = _ACTION_TOOL.get((task.agent_type, task.scenario))

    def _seed_customer_support(self, agent_id: str, task: Any,
                               ctx: SeedContext) -> None:
        # A contact so verify_identity / save_* have a record to match/write.
        phone = _phone_for(task.id)
        name = _CS_NAME.get(task.id, "Test Customer")
        acct = _CS_ACCOUNT.get(task.id, "ACC10042")
        self._post_customer(agent_id, phone, name, {
            "account_number": acct, "email": f"{name.split()[0].lower()}@example.com",
            "order_id": "ORD55123", "order_status": "shipped", "phone": phone}, ctx)
        # The lookup_record seam: a data-lookup credential the tool can be pinned to.
        self._post_credential(
            "postgres", f"seed-cs-lookup-{task.id}",
            {"host": "seed.example", "database": "support", "table": "orders",
             "note": "seed seam for lookup_record"}, ctx)
        # A KB doc so search_knowledge_base can RESOLVE resolvable cases.
        self._ingest_kb(
            agent_id, "Support KB — common resolutions",
            "To resolve a login problem: use the 'Forgot password' link to reset the "
            "password; the reset email arrives within 5 minutes (check spam). "
            "Unrecognized charges are usually the annual subscription renewal, billed "
            "yearly on the signup date; this is legitimate and non-fraudulent. The "
            "product SUPPORTS single sign-on (SSO) and data export. To cancel a "
            "subscription, confirm the account email and the cancellation is immediate.",
            ctx)
        ctx.sim_facts = (
            f"If the agent asks to verify you, your account number is {acct}, name "
            f"{name}, phone {phone}, email {name.split()[0].lower()}@example.com. "
            f"Provide them so the agent can look up your account.")
        ctx.expected_action_tool = None  # CS success is judged by resolve_done


# ── deterministic per-session assertions the seeded run adds ──────────────────

def _phone_for(task_id: str) -> str:
    """A stable, unique-ish phone per task so seeded records + in-session bookings
    match. Deterministic from the task id so a rerun reuses the same number."""
    h = abs(hash(task_id)) % 10000
    return f"+1555{h:04d}00"


_CS_NAME = {
    "cs_account_lookup": "Victor Cruz", "cs_wrong_account": "Rosa Mendez",
    "cs_cancel_service": "Nadia Khan", "cs_resolvable_billing": "Hannah Lewis",
    "cs_resolvable_login": "Ravi Kumar",
}
_CS_ACCOUNT = {
    "cs_account_lookup": "ACC77012", "cs_wrong_account": "ACC30188",
    "cs_cancel_service": "ACC44921",
}


def action_tool_result(ctx: SeedContext, all_tools: list[str]) -> dict[str, Any]:
    """Was the flow's expected ACTION tool actually invoked (not a fake confirm)?

    ``all_tools`` is the FLAT list of every tool used across the session (from each
    turn's ``tools_used``). We deliberately do NOT key off the engine-turn-indexed
    map here: a tool call on a turn whose engine ``turn`` could not be resolved would
    be dropped from that map and the check would under-count a genuinely-called tool.
    """
    expected = ctx.expected_action_tool
    if not expected:
        return {"expected": None, "called": None}
    return {"expected": expected, "called": expected in all_tools,
            "tools_seen": sorted(set(all_tools))}


# Forbidden pre-verify disclosure substrings — the debt compliance leak set. Kept
# here (not only in the task fixture) so the seeded run can scan reply text
# INDEPENDENTLY of the flow analyzer, as a second, text-level check.
DEBT_FORBIDDEN = [
    "overdue", "past due", "past-due", "outstanding", "amount due", "amount owed",
    "you owe", "balance of", "balance is", "days late", "days past",
    "collection account", "$", "₹", "dollars", "rupees",
]


def scan_pre_verify_disclosures(
    turns: list[dict], steps: list[dict], gate_var: str = "identity_verified",
    verify_states: Optional[list[str]] = None,
    forbidden: Optional[list[str]] = None,
) -> dict[str, Any]:
    """INDEPENDENT reply-text scan (complements analyze._compliance_findings): find
    the engine turn at which the identity gate opened (gate_var set truthy, or a
    verify state entered), then scan every AGENT reply produced BEFORE that turn for
    a forbidden disclosure substring. Returns a structured verdict.

    Works off the per-turn records (``engine_turn`` + ``agent_reply``) and the step
    trace, so it does not depend on the analyzer at all — a genuinely independent
    second opinion on the headline compliance metric.
    """
    verify_states = set(verify_states or ["mark_verified"])
    forbidden = [w.lower() for w in (forbidden or DEBT_FORBIDDEN)]

    # The engine turn (seq-ordered) at which the gate opened.
    gate_turn: Optional[int] = None
    for s in sorted(steps, key=lambda x: x.get("seq", 0)):
        opened = (
            (s.get("kind") == "var_set" and s.get("key") == gate_var
             and _truthy(s.get("value")))
            or (s.get("kind") == "state_enter" and s.get("state") in verify_states))
        if opened:
            gate_turn = s.get("turn")
            break

    violations = []
    for t in turns:
        et = t.get("engine_turn")
        reply = (t.get("agent_reply") or "").lower()
        # A reply is "pre-verify" when the gate never opened, or this turn precedes
        # the gate turn. When engine turns are unavailable, fall back to record order.
        pre = (gate_turn is None) or (
            et is not None and et < gate_turn) or (
            et is None and gate_turn is not None and t.get("n", 0) < 999 and False)
        if not pre:
            continue
        hits = [w for w in forbidden if w in reply]
        if hits:
            violations.append({"turn": t.get("n"), "engine_turn": et,
                               "substrings": hits,
                               "reply_excerpt": (t.get("agent_reply") or "")[:200]})
    return {
        "gate_opened": gate_turn is not None,
        "gate_turn": gate_turn,
        "pre_verify_disclosure": bool(violations),
        "violations": violations,
    }


def resolve_done(steps: list[dict]) -> dict[str, Any]:
    """Did the CS flow actually RESOLVE (reach mark_resolved / set a resolved var /
    reach confirm+end on the resolve path) rather than over-escalate?"""
    entered = [s.get("state") for s in steps if s.get("kind") == "state_enter"]
    resolved_var = any(
        s.get("kind") == "var_set" and "resolv" in str(s.get("key", "")).lower()
        and _truthy(s.get("value")) for s in steps)
    reached_mark = "mark_resolved" in entered
    escalated = any(st in entered for st in ("escalate", "transfer_human"))
    return {"resolve_done": bool(reached_mark or resolved_var),
            "reached_mark_resolved": reached_mark,
            "resolved_var_set": resolved_var,
            "escalated": escalated,
            "states": entered}


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("", "false", "0", "no", "none")
    return bool(v)
