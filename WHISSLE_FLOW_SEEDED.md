# Whissle SEEDED end-to-end flow validation

The **"true completion"** run: where `WHISSLE_FLOW_SIM.md` drives an LLM
simulated user through a flow-enabled agent whose tools mostly **fail-soft** (no
customer to verify, no fleet to reserve, no ticket to save), this suite wraps each
freshly-created test agent in a **seed → run → teardown** bracket so its tools
operate on **real records** and actually **succeed** — then asserts they did.

It is the payoff of the round-2 backend fixes: with real data behind the tools, the
flows should reach `end`, the action tools should actually be **called** (not faked),
and — the headline — `debt_collection` should disclose a balance **only** after
identity is verified, i.e. **0 pre-verify disclosures**.

## What the seeding layer does (`src/tau2/flow/seed.py`)

A `Seeder`, keyed by `agent_type`, inserts the records that type's tools read, then
tears every one down by exact id (never a pattern-delete):

| type | seeded | drives |
|------|--------|--------|
| `debt_collection` | `POST /api/customers` — a contact **bound to the agent** whose `attributes` (borrower_name, due_amount, days_overdue, due_date, loan_account_number, merchant_name, phone, date_of_birth) become the debt template vars. Fallback: the same values written as **static agent variables** (within `agents:write`). | The sim states its **full name + last-4 of the account (+ DOB)** so `verify_identity → mark_verified` fires; the balance is revealed only in the post-verify `disclose_balance` state. |
| `appointment_scheduling`, `dental_receptionist` | schedule **auto-seeds** (Mon–Fri 09:00–18:00 Asia/Kolkata) — no record. | New booking: sim gives a weekday in-hours. Reschedule/cancel: the sim **books in-flow first** in the same session, then changes it (matched on agent_id + phone). |
| `car_rental` | fleet **auto-seeds on create** (fix #600) — no record. | The sim gives a concrete city + dates and accepts the first vehicle so the flow reaches book→confirm→end with `reserve` actually called. |
| `customer_support` | `POST /api/customers` (verify/save), a **data-lookup credential** (`POST /api/orgs/{org}/credentials`) as the `lookup_record` seam, and a **KB doc** (`POST /api/agents/{id}/kb/ingest`) so `search_knowledge_base` can resolve resolvable cases. | The sim provides account/order ids; a resolvable case should reach `resolve_done`, not over-escalate. |

External tools (`send_sms`, `send_email`, `transfer_call`, `payment_link`) cannot be
seeded — their fail-soft/mocked return is treated as success; the flow still
progresses past them.

**Best-effort by design.** Every seed op records `ok` / `skipped:<scope>` / `error`
and never aborts the session. A key lacking `contacts:write` / `kb:write` degrades
those steps to `skipped` (the tool then fail-softs, exactly as the unseeded run)
while the within-scope steps (credential seam, static-variable fallback, sim-fact
injection) still apply.

## The seeded-run assertions (the "true completion" checks)

Added on top of the deterministic flow analyzer:

- **action tool actually called** — per (type, scenario), the tool a true completion
  must invoke (`book_appointment` / `reschedule_appointment` / `cancel_appointment`
  / `reserve` / `capture_ptp`) must appear in `tools_used`, not just a narrated
  confirmation.
- **debt pre-verify disclosure scan** — an **independent** reply-text scan (separate
  from the analyzer's `compliance` finding): find the engine turn the identity gate
  opened (`identity_verified` set truthy / `mark_verified` entered), then scan every
  agent reply **before** it for a forbidden disclosure substring.
- **CS `resolve_done`** — did the flow reach `mark_resolved` / set a resolved var,
  rather than escalate/transfer.

## Run

```bash
cp .env.example .env         # WHISSLE_API_KEY=wsk_...  (agents:write; contacts/kb for full seeding)
./run_flow_seeded.sh --agent-type debt_collection --sessions 10
./run_flow_seeded.sh all --no-semantic        # 5 core types × 10 seeded sessions
uv run python -m tau2.flow.simulate seeded-report   # before→after table
```

Equivalently: `run_flow_sim.sh --agent-type <type> --sessions 10 --seeded`.

## Reporting

- `results/whissle/flow_sim/<type>/SUMMARY.{md,json}` — now carries a **seeded
  rollup**: action-tool-called rate, debt pre-verify disclosures + verify-fire rate,
  CS resolve_done rate, seed health (no-error sessions, resources tracked,
  teardown-failed).
- `results/whissle/flow_sim/SEEDED_REPORT.md` — the true **before→after** table vs
  the pre-seed baseline, plus the debt-compliance headline.

## Cleanup

Every seeded record (customer / credential / KB doc) is deleted by tracked id in
every exit path; every throwaway agent is deleted with `?confirm=true` (required for
agents that own an auto-seeded KB — e.g. the car_rental fleet — which otherwise 409
and would linger). A run prints `cleanup verified: no flowsim-* agents linger`.
