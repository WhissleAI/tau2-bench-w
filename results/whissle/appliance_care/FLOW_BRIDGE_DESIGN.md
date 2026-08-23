# ApplianceCare — connecting the saved Whissle flow to tau2 scoring

> **Scope, added 2026-08-24.** Everything in this document is about the
> **saved-flow / product-integration** path. It is **not** about the standard
> benchmark, which needs none of it.
>
> The standard text benchmark runs over `POST /api/bench/agent-turn` with the
> `whissle` agent: tau2 sends its own tool schemas and domain policy on every
> request, Whissle acts purely as the brain and returns `tool_calls`, and
> `Orchestrator._execute_tool_calls` runs them against the tau2 environment that
> is then hashed for scoring. No flow, no custom HTTP tools, no bridge. That path
> works and is unaffected by anything below.
>
> The bridge, the `whissle_flow` adapter, and the custom-tool work exist to measure
> something the standard path deliberately cannot: the agent **as deployed**, with
> its own prompt, its saved flow, and its attached tools.

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

### 2. Platform LLM inference is down (previously mis-scoped as a judge fault)

Calling this a *transition judge* problem was wrong. `POST /api/models/chat` — no
agent, no flow, no judge — returns the identical error, on both the default and
`--fast` engine paths:

```
Gemini API (429): "Your prepayment credits are depleted. Please go to AI Studio
at https://ai.studio/projects to manage your project and billing."
status: RESOURCE_EXHAUSTED
```

The failing credential is a Google AI Studio (Gemini API) project on the Whissle
side. It is **not** this workspace's balance, which is positive with payments
enabled. Non-LLM APIs are healthy. Smallest fix: restore credit on that key.

Full diagnostic, including what the error does *not* prove and what remains
unknown: `LLM_PROVIDER_DIAGNOSTIC.md`.

No flow-performance claim is possible until inference returns.

## Sanitisation

No API keys, tokens, personal paths, phone numbers, or signed URLs appear in this
document or in the committed code and tests. Test tokens are literal
non-secrets (`test-token-not-a-real-secret`).
