# Whissle conversation-flow — test results & remaining gaps

Results of testing the in-call **conversation-flow state machine** on real Whissle agents with
the flow-sim harness in this repo. The harness drives agents over the **text channel**
(`/api/agents/{id}/chat/turn`); the flow runtime is shared with the LiveKit/Pipecat voice bot
(same `FlowRuntime`, transitions, gates), so this validates the state-machine **logic** that the
voice bot also executes — it does **not** exercise voice transport specifics (audio/barge-in).

Raw per-session logs (event `.jsonl` + transcript `.json` + `SUMMARY.md`) are under
`results/whissle/flow_sim/<type>/`.

## Suites (see WHISSLE_FLOW_OVERVIEW.md)
| suite | what it checks |
|---|---|
| authored (`flow/`) | marker / multi-tool / guarded-loop canaries drive turn-by-turn |
| default-coverage (`flow_defaults/`) | all 15 seeded types auto-attach a working default flow — **15/15 pass** |
| flow-sim (`flow_sim/`) | LLM user-sim × N sessions + a deterministic rule-analyzer (bug finder) |
| seeded | real records seeded so tools succeed (true completion) |

## Bug-finding audit → fixes (5 agents × 10 sessions)
State tracking itself was **clean throughout** — 0 illegal transitions, teleports, or desyncs.
The bugs were in transition-judging, gate-variable wiring, and generation-gating.

**Found & fixed (backend PRs #580–#610):**
- **`llm_condition` judge saw only the last exchange** → forward gates fired ~1/10 → flows stalled. Fixed: 6-turn judge window.
- **Unsettable / deadlocked expression gates** (dental `callback_number`, appt ordering) → completion arms unreachable. Fixed: re-authored + a `validate_flow` rejecter so the class can't ship again.
- **Tool-gating leakage** — mostly a harness attribution artifact (fixed in the analyzer: attribute a tool to the gate live at call-time); genuine `allowed_tools` gaps closed.
- **Fake confirmations** — a `book` state that said "you're all set" without calling `book_appointment`. Fixed: `type:"tool"` states force the call.
- **Template leak** — `₹{{due_amount}}` reached the caller. Fixed: render + strip.
- **debt_collection compliance** — disclosed the debt before identity was verified (≥4/10). Fixed across three rounds (see below).

## Before → after (coverage is the clearest signal)
| agent | states before→after | transitions | key result |
|---|---|---|---|
| customer_support | 6→**10** | 5→**11** | full resolve + escalate chains fire (were dead) |
| dental | 6→**8** | 5→**8** | booking + cancel arms revived |
| car_rental | 4→**6** | 3→**5** | `capture` stall gone (`capture_to_search` 1/11 → **7/10**) |
| appointment | 6→**7** | 6→**7** | `book` reached; leak closed |
| debt_collection | →**9/10** | →10/15 | figure leak gone; verify now deterministic |

## debt_collection compliance — the headline
- **R1/R2:** removed the balance figures from the base prompt and put them only in the post-verify `disclose_balance` state (gated on `identity_verified`). Killed the *figure* leak. But `verify→mark_verified` was an `llm_condition` that under-fired, so the agent lingered pre-verify and *generically acknowledged* the debt → **2/10** residual.
- **R3 (deployed):** the verify gate is now **deterministic** — a server-side `verify_identity` tool compares what the caller said against the bound record and, only on a real match, sets `identity_verified` (via `SUCCESS_MIRROR_TOOLS`); `disclose_balance`'s *only* entry is the `identity_verified == true` expression. The LLM structurally **cannot** open the gate; it fails closed with no record. Pre-verify goals forbid acknowledging any debt.

## Remaining gaps
1. **Voice/audio acceptance** — text-channel harness validates flow logic (shared runtime); audio, barge-in, `voice_override`/turn-taking need a real device/call QA pass. **Not yet done.**
2. **Empirical compliance→0 with a bound debtor** — the deterministic gate is structurally proven + unit-tested; the *happy-path* run (verify succeeds → disclose post-verify → 0 leaks) needs a seeded debtor record. Blocked on seeding scope (below); planned via the SSM/DB seed path.
3. **Seeding scope** — the studio secret key lacked `contacts:write`/`kb:write`, so the seeded run couldn't create the debtor/KB records (tools failed → "reached-end" under-measured). A key-scope fix is pending sign-off.
4. **Existing-agent propagation of R3** — new agents get the deterministic flow + prompt + `verify_identity` tool from the blueprint; existing agents need the flow re-upgrade + prompt-strip apply-run + the tool in their stored set.
5. **`loan_account_number` prompt hygiene** — still rendered into the debt prompt; an adversarial model could echo the record's own last-4 to the verify tool. Separate hardening.

## Reproduce
```
cp .env.example .env      # WHISSLE_API_KEY (wsk_, needs contacts:write/kb:write for seeding) + WHISSLE_BASE
./run_flow_sim.sh --agent-type debt_collection --sessions 10     # bug-finder
./run_flow_defaults.sh                                           # 15-type coverage
./run_flow_seeded.sh --agent-type debt_collection --sessions 10  # seeded (needs the write scopes)
```
