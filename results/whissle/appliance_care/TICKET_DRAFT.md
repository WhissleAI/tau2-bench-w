# Copy-ready ticket drafts (NOT filed)

Two separate issues. Both contain only evidence reproduced against the live
backend; neither has been filed.

---

## Ticket A — Platform LLM inference failing: Gemini prepaid credit exhausted

**Type:** Bug · **Priority:** Blocker — all LLM-backed features
**Status: RESOLVED 2026-08-21** by a credit top-up on the backend's Gemini key.
File only if the two follow-ups below are wanted; the outage itself is closed.

**Summary**
Every platform LLM call fails. The provider chain ends on a Gemini API key whose
prepaid credit is exhausted. This is NOT specific to the flow transition judge —
that is only where it was first noticed.

**Evidence** (reproduced 2026-08-20 and 2026-08-21)

`POST /api/models/chat` — no agent, no flow, no judge — returns HTTP 502:

```
LLM call failed: all LLM providers failed; last error: Gemini API (429):
{"error": {"code": 429,
  "message": "Your prepayment credits are depleted. Please go to AI Studio at
              https://ai.studio/projects to manage your project and billing.",
  "status": "RESOURCE_EXHAUSTED"}}
```

The `--fast` engine path fails identically. Flow agents surface the same error as
`transition_check → not_satisfied`, so every `llm_condition` fails closed and an
agent cannot leave its start state; the caller just hears "Sorry — I couldn't work
that out just now."

**Scope**
- Non-LLM APIs are healthy (`GET /api/models/voices` → 11 voices).
- Auth, agent/flow/KB reads all work.
- The reporting workspace's balance is positive with payments enabled, so this is
  not customer credit. Failed LLM calls are not billed.
- Note: `chat/turn` still debited $0.01 per turn while returning only the fallback
  message. Flagged for confirmation, not asserted as a bug.

**Fix**
Restore credit on the Gemini API key the backend uses (AI Studio → project → billing).

**Two follow-ups worth considering**
1. Surface **all** provider failures, not just the last. "all LLM providers failed;
   last error: …" makes it impossible to tell from outside whether the fallback
   chain is real or whether only one provider is genuinely wired up.
2. The flow trace's `reason` field is truncated server-side at 133 characters,
   which cut this message immediately before the words identifying the cause and
   sent the first investigation down the wrong path.

**Cannot be determined externally**
Which account owns the Gemini project, which other providers are configured, and
why each failed. None of that is exposed on any reachable endpoint.

---

## Ticket B — A custom HTTP tool attached to an agent was not exposed to that agent's flow on the text channel

**Type:** Question / possible bug · **Priority:** Blocks saved-flow / product-integration testing only
**Status:** not filed. One reproduction, described below.

### Summary (written by hand — the raw evidence is in `evidence/`)

On a single text-channel test, a custom HTTP tool that was attached and enabled on
an agent, and explicitly named in the `allowed_tools` of the flow state the
conversation was in, did not appear in that state's `tools_gated` set and was never
called. The bridge it points at received **zero requests**, and the benchmark
recorded **zero tool executions**.

The test was run on a throwaway agent created for the purpose and deleted
afterwards. No production agent was involved or modified.

**What this observation is.** One text conversation, one agent, one tool. It shows
that in this configuration an attached, allowed custom tool was not exposed to the
flow.

**What this observation is not.** It is not a measurement of how custom tools behave
generally, on other channels, on other agent types, or with other tool
configurations. Only one tool was tested and only once. We have not inspected the
backend, so we are not naming a cause or a code path — establishing why is the
purpose of this ticket, not its premise.

### Why it matters to us — and what it does NOT affect

**Scope: this blocks testing the saved Whissle flow end to end. It does not block
the standard benchmark.**

The standard text benchmark does not use custom HTTP tools or the saved flow at
all. It runs over `POST /api/bench/agent-turn`: the benchmark sends its own tool
schemas and policy on each request, Whissle acts purely as the brain and returns
`tool_calls`, and the benchmark executes those against its own database and scores
the result. Whissle's own prompt, flow, and attached tools are deliberately bypassed
on that path. It is unaffected by this ticket and can run today.

What this ticket blocks is the *other* thing we want to measure: the agent as it is
actually deployed — its saved flow, its own prompt, its attached tools. That is the
product-integration path, and it needs custom HTTP tools to reach the flow.

The voice channel is untested either way.

### Request

Could someone with backend access confirm whether the text flow runner is expected
to expose custom HTTP tools to a state's `allowed_tools`, and if so, what would
prevent it here? If the behaviour differs between the text and voice runners, that
difference is what we most need to know.

### How to reproduce

1. Create an agent with **one** custom HTTP tool attached and enabled, and no
   knowledge base.
2. Give it a live flow with one conversation state whose `allowed_tools` is exactly
   `["<that tool>"]`, plus a reachable `end` state.
3. Drive one fresh text conversation (`POST /api/agents/{id}/chat/turn` with a new
   `session_id`) with a message that requires the tool.
4. Read `flow.steps` on the response, and check whether the tool's endpoint received
   a request.

### Reproduction result — raw

_Benchmark version v4. Observed on the saved-flow path only._

Agent configuration at test time, read back from the API:

```json
"tools":  [{"name": "lookup_error_code", "config": {}, "enabled": true}]
"knowledge_documents": 0
"flow":   [{"id": "lookup", "allowed_tools": ["lookup_error_code"]},
           {"id": "end", "type": "end"}]
```

Observed `flow.steps` for the turn:

```json
{"seq": 0, "kind": "state_enter",      "turn": 1, "state": "lookup"}
{"seq": 1, "kind": "tools_gated",      "turn": 1, "state": "lookup",
 "allowed": ["search_knowledge_base"]}
{"seq": 2, "kind": "transition_check", "turn": 1, "from": "lookup",
 "transition_id": "lookup_to_end", "result": "not_satisfied"}
```

`tools_used: ["search_knowledge_base"]`.

Agent reply: *"I don't have information about that error code in my system. I'm
unable to look up the specific meaning of E:23 for model WAT28402UC."* — which is
the behaviour its prompt specifies when the tool is unavailable.

Counts on our side:

| Measurement | Value |
|---|---|
| Requests received by the tool's HTTP endpoint | **0** |
| Requests logged by the tunnel for that path | **0** |
| Tool executions recorded by the benchmark | **0** |

One detail we cannot explain from outside: the gated set contains
`search_knowledge_base`, which was **not** attached to this agent, and the agent had
no knowledge documents. We report it as observed rather than interpreting it.

Separately, and previously observed: a per-request `tools` array sent to
`/chat/turn` is accepted by the HTTP layer and then ignored, with no 4xx. That may
be intended, but the silent acceptance made it hard to diagnose.

### Evidence files

- `evidence/temp_agent_flow_trace.json` — `GET /api/agents/{id}/flow/trace`,
  retrieved read-only *after* the agent was deleted
- `evidence/temp_agent_session.json` — `GET /api/sessions/{conversation_id}`,
  the two-turn transcript
- `CUSTOM_TOOL_TEXT_TEST.json` — the test's own record

Conversation id `db493f1c-53d0-4ef9-9746-59e7cd73330e`. All files sanitized: no
keys, tokens, tunnel hostnames, session keys, or local paths.

### Cleanup

The temporary agent, tool, and connector were deleted and their absence confirmed
by read-only checks. The production ApplianceCare agent carries only
`search_knowledge_base` and `take_message`, with its live and draft flows unchanged.
