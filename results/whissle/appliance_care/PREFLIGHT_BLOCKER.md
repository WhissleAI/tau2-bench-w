# ApplianceCare — flow-integration preflight: BLOCKED

> **Scope note, added 2026-08-24.** "BLOCKED" here means the **saved-flow /
> product-integration** path was blocked, not the benchmark. The standard text
> benchmark runs over `/api/bench/agent-turn` and needs neither the flow nor
> custom HTTP tools. Blocker 2 (LLM inference) was resolved on 2026-08-21.
> See `FLOW_BRIDGE_DESIGN.md` for the architecture split.

**Date:** 2026-08-20
**Agent:** ApplianceCare Support (`26071d02-bb4f-45b2-98c4-8fa1cf0c0330`)
**Outcome:** the corrected flow was **not published** and **neither approved smoke test was run.**

Two independent blockers were found during the preflight that gates publication.
Both were reproduced live against the backend; neither is inferred from source
comments alone.

---

## Blocker 1 — τ² tools cannot be injected into the flow runtime

The flow runtime gates tool availability against the tools **configured on the
agent**, server-side. A per-request `tools` array is accepted by the HTTP layer
and then silently ignored.

**Probe.** `POST /api/agents/{id}/chat/turn` with a body carrying both a
`message` and a `tools` array containing one τ² tool (`lookup_error_code`).

**Result** (conversation `e54d0a9c-26a0-402a-87c3-ce0b678696f3`):

```json
{"seq": 2, "kind": "tools_gated", "turn": 1, "state": "greet",
 "allowed": ["search_knowledge_base"]}
```

`tools_used: []`, `tool_events: []`. The injected tool never reached the model —
the runtime gated to the agent's own tool, and the request field was dropped
without an error.

### Why no Tau-side change can fix this

The gate is server-side. Every transport is either stateful-with-own-tools or
stateless-with-injected-tools; none is both:

| Transport | Runs FlowRuntime | Accepts τ²'s 16 tools |
|---|:--:|:--:|
| `POST /api/bench/agent-turn` (current text agent) | **no** — stateless brain call | yes (fully replace the agent's) |
| `POST /api/agents/{id}/chat/turn` | **yes** | **no** — body is `{message, conversation_id}` only |
| `POST /api/bench/voice/start` `{real:false}` (current voice agent) | **no** — bench mode | yes |
| `POST /api/bench/voice/start` `{real:true}` | yes | **no** — agent's own prompt, flow, tools, greeting |

The two approved smoke tests both run the left-hand rows, so as written **neither
would have executed the saved flow** — they drive the brain with τ²'s policy and
tools and no state machine.

### Consequence for the corrected draft

The 16-state draft's `allowed_tools` name exactly the 16 τ² agent tools. The agent
has two: `search_knowledge_base`, `take_message`. Publishing it today would gate
every state to the empty set, and the agent would improvise prose instead of
calling tools — the precise failure the CLI validator warns about. `--allow-unknown-tools`
suppresses that warning; it does not make the tools exist, and it was not treated
as evidence here.

### The narrowest backend change that would close it

The voice bench path already has a tool-delegation bridge — `bench-tool-call` /
`bench-tool-result` over the LiveKit data channel — but only in bench mode, which
turns the flow off. The missing capability is a mode that keeps the FlowRuntime on
**and** delegates tool execution to the harness: either

1. `chat/turn` accepts per-request `tools` and gates `allowed_tools` against them, or
2. `voice/start {real:true}` additionally accepts `tools` and routes calls over the
   existing data-channel bridge.

Either one makes the corrected flow scorable without changing the flow itself.

---

## Blocker 2 — the flow transition judge has no working LLM provider

Independently of the tools question, the live flow cannot currently advance past
its first state. Every `transition_check` fails closed:

```json
{"seq": 3, "kind": "transition_check", "from": "greet",
 "transition_id": "greet_to_understand", "transition_kind": "llm_condition",
 "condition": "The caller has stated their reason for calling.",
 "result": "not_satisfied",
 "reason": "judge error: all LLM providers failed; last error: Gemini API (429): ... \"Your prepayment ..."}
```

The user-visible reply is `"Sorry — I couldn't work that out just now. Could you
try again?"`. Reproduced on two separate turns; the 429 text points at exhausted
prepaid credit rather than a transient rate-limit spike, so a retry is not
expected to clear it.

This is a provider/infrastructure failure on the stop-at-first-failure list, and it
would invalidate any flow measurement taken right now even if Blocker 1 were solved.

---

## Verified state (unchanged by this preflight)

| Item | Value |
|---|---|
| Live published flow | 10 states, 13 transitions (the old generic customer-support flow) |
| Saved draft flow | 16 states, 25 transitions, 16 distinct `allowed_tools` |
| Tools on agent | `search_knowledge_base`, `take_message` |
| Knowledge documents | 6, present and untouched |

Nothing was created, published, deleted, or modified. No agent, knowledge document,
phone number, embed, or call. The draft remains saved and unpublished.

## Cost

Two `chat/turn` requests. Both failed at the transition judge before any agent LLM
completion, so agent-model and TTS usage were nil; the only spend is the backend's
own failed judge calls. No per-request cost figures are exposed by the API, so no
dollar amount can be stated — recorded here as unavailable rather than estimated.
