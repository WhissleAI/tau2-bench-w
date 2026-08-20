# Copy-ready ticket drafts (NOT filed)

Two separate issues. Both contain only evidence reproduced against the live
backend; neither has been filed.

---

## Ticket A — Flow transition judge has no working LLM provider

**Type:** Bug · **Priority:** Blocker for any flow evaluation

**Summary**
Every `llm_condition` transition fails closed because all configured judge
providers fail. Agents with a saved flow cannot leave their first state.

**Evidence** (agent `26071d02-…`, reproduced 2026-08-20 and again 2026-08-21)

`POST /api/agents/{id}/chat/turn` → `flow.steps`:

```json
{"seq": 1, "kind": "state_enter", "state": "greet", "state_type": "conversation"}
{"seq": 2, "kind": "tools_gated", "state": "greet", "allowed": ["search_knowledge_base"]}
{"seq": 3, "kind": "transition_check", "from": "greet",
 "transition_id": "greet_to_understand", "transition_kind": "llm_condition",
 "condition": "The caller has stated their reason for calling.",
 "result": "not_satisfied",
 "reason": "judge error: all LLM providers failed; last error: Gemini API (429): \"Your prepayment ...\""}
```

User-visible reply: `"Sorry — I couldn't work that out just now. Could you try again?"`

**Impact**
Any agent whose flow uses `llm_condition` transitions is stuck in its start state.
The failure is silent from the caller's perspective — it presents as the agent
being confused, not as an outage.

**Notes**
- The 429 text refers to prepayment, i.e. exhausted credit rather than a rate spike.
- The judge provider is not configurable from outside: it is absent from the agent
  record (`stt_provider` / `tts_provider` / `avatar_provider` only), from the flow
  `settings` block, and from the CLI. Pointing the judge at the org's OpenAI
  provider appears to require a backend change.

**Asks**
1. Restore a working judge provider.
2. Consider falling back to another configured provider before failing closed, and
   surfacing judge-provider failure as an error rather than `not_satisfied`.

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
