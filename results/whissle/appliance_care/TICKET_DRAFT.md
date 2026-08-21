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

## Ticket B — Confirm whether the TEXT flow runner resolves custom HTTP tools

**Type:** Question / possible bug · **Priority:** Blocks text-channel tool evaluation

**Summary**
Real voice is understood to load attached custom HTTP tools. It is unconfirmed
whether the **text** runner (`/api/agents/{id}/chat/turn`) does the same, or
resolves only built-in tools.

**Evidence available so far** (agent `26071d02-…`)

| Fact | Value |
|---|---|
| Tools configured on the agent | `search_knowledge_base`, `take_message` |
| Live flow `greet` state `allowed_tools` | `[]` |
| `tools_gated` reported in the text trace | `["search_knowledge_base"]` |

The resolved set is a strict subset of the agent's configured tools. From outside
the backend it is not possible to distinguish "tool resolution dropped
`take_message`" from "`take_message` was excluded because its `action_policy` is
`approve`".

**Separately established:** a per-request `tools` array on `/chat/turn` is accepted
by the HTTP layer and then silently ignored — the trace shows the runtime gating to
the agent's own tools instead. Silent-drop rather than a 4xx is itself worth a look.

**Ask**
Confirm whether the text flow runner runs the custom-tool augmentation step before
resolving a state's `allowed_tools`. If it does not, the suggested narrow fix is to
run `augment_custom_tools` before tool resolution in the text runner, matching voice.

**How to verify**
Attach one read-only custom HTTP tool to an agent and drive one text turn in a
state that allows it. If the tool appears in `tools_gated`, text supports custom
tools; if it is absent while voice loads the same tool, the gap is confirmed.
