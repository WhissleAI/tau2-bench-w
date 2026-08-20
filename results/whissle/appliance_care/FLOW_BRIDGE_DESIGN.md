# ApplianceCare — connecting the saved Whissle flow to tau2 scoring

**Date:** 2026-08-21 · **Status:** implemented and tested locally; nothing published.

## Correction to the previous note

`PREFLIGHT_BLOCKER.md` concluded that tau2 and the saved flow "cannot connect".
That was too broad. The failed probe showed only that a **per-request** `tools`
array on `/chat/turn` is ignored. It said nothing about tools **registered on the
agent**, which is the supported mechanism and the one used here.

The design below is proven by focused tests rather than argued from comments.

## Shape

```
Whissle saved flow (16 states)
  → attached custom HTTP tools (16, bearer via stored connector)
      → POST {TAU_BRIDGE_PUBLIC_URL}/tools/{name}
          → tau2 ToolBridge  →  the exact Environment being scored
```

The bridge executes through `Environment.get_response`, the same entry point the
orchestrator uses, so a call mutates the live tables and calls `sync_tools`
identically. There is no second database.

## The double-execution problem, and what prevents it

By the time Whissle's reply arrives the tools have already run. If the adapter
returned them as `tool_calls`, the orchestrator would route them to the
environment and run every write **twice** — a second support case, a broken DB
hash, a failed task.

So the adapter never returns `tool_calls`. It returns the reply, and hands the
already-executed (call, result) pairs to the orchestrator through
`drain_trajectory_records()`, which splices them into the trajectory *ahead of*
the reply. Both evaluators then see exactly one occurrence of each call:

- `Environment.set_state` pops the `ToolMessage` that must follow each call and
  matches on id, replaying only mutating tools and comparing output strictly.
- `ActionEvaluator.extract_tool_calls` reads `tool_calls` off assistant messages.

`test_double_execution_would_have_been_caught` asserts the failure mode directly:
inject one duplicate write and the task scores 0. Without it, "we prevented double
execution" would be an untested claim.

## Hidden state stays hidden

`EnvironmentServer` is deliberately not reused. It is unauthenticated and serves
`/user_tools/*` — which in this domain *is* the answer key (`smell_check`,
`read_display_code`, `inspect_drain_filter`). The bridge serves the 16 agent tools
only and has no route that can reach `use_user_tool`. Two tests pin this.

## Security

- Bearer token required on every tool route, compared with `secrets.compare_digest`.
- Token read from `TAU_BRIDGE_TOKEN`; never logged, echoed, or serialized into a record.
- Token never written into tool JSON — Whissle holds it in a stored connector,
  referenced by `credential_id`.
- No `/docs`, `/redoc`, or OpenAPI route: the tool list is not a discovery surface.
- `/healthz` is unauthenticated but reveals only the domain name.
- The public URL is configurable (`TAU_BRIDGE_PUBLIC_URL`). Nothing opens a tunnel
  automatically.

## Test results

| Suite | Result |
|---|---|
| `tests/test_bridge/` (new) | **59 passed** |
| `tests/test_domains/test_appliance_care/` | **74 passed** |
| Full suite, excluding pre-existing breakage | **1611 passed**, 120 skipped |

Pre-existing failures, unchanged by this work and confirmed against a clean
checkout of the same commit:

- 5 collection errors — missing optional deps (`gymnasium` and similar)
- ~54 `banking_knowledge` retrieval failures/errors — require live OpenAI embeddings
- 2 `test_run_streaming.py` audio-native failures — **reproduced on the stashed baseline**

## Outstanding backend questions

### 1. Does the TEXT flow runner resolve custom HTTP tools?

Unverified, and it decides whether the text smoke test is meaningful. Evidence
gathered so far, from the live agent:

- The agent has two configured tools: `search_knowledge_base`, `take_message`.
- The live flow's `greet` state carries `allowed_tools: []`.
- The text trace nevertheless reports `tools_gated allowed: ["search_knowledge_base"]`.

So the resolved set is a strict subset of the agent's configured tools. Whether
`take_message` was dropped by tool resolution or excluded deliberately by its
`action_policy: approve` cannot be told apart from outside the backend. The
decisive test is to attach one custom HTTP tool and read `tools_gated` — which
needs the create/attach approval this work stops before.

Real voice is reported to load attached custom HTTP tools; that is the path the
voice smoke test would exercise.

### 2. The transition judge still has no working provider

Re-confirmed on 2026-08-21, unchanged from 2026-08-20:

```
transition_check greet_to_understand → not_satisfied
reason: "judge error: all LLM providers failed; last error: Gemini API (429): ... "Your prepayment ..."
```

Every `llm_condition` transition fails closed, so the flow cannot leave `greet`
and the customer hears "Sorry — I couldn't work that out just now."

**Can the configured OpenAI provider be used instead?** Not from anywhere reachable
here. The judge provider is not exposed on the agent record (which carries only
`stt_provider`, `tts_provider`, `avatar_provider`), not in the flow's `settings`
block (`on_guard_trip`, `fallback_state`, `max_visits_per_state`,
`max_transitions_per_call`), and not in the CLI, where `models` states that "the
platform picks the engine — the model/provider is never exposed." Redirecting the
judge to OpenAI is therefore a **backend or org-credential change**, not a
live-agent edit. No flow-performance claim can be made until it works.

## Sanitisation

No API keys, tokens, personal paths, phone numbers, or signed URLs appear in this
document or in the committed code and tests. Test tokens are literal
non-secrets (`test-token-not-a-real-secret`).
