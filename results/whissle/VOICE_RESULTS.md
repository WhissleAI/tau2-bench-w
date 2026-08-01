# Whissle VOICE benchmark (τ³, half-duplex) — 2026-08-01

Agent: `whissle_voice` (half-duplex, turn-based) driving Whissle's real STT→Claude→TTS
cascade over LiveKit. User sim: `user_simulator` + gpt-4o, voice synthesized (ElevenLabs).
Backend: AWS, pinned to 1 instance (SFU affinity). Tools delegated over the LiveKit data
channel (bench-tool-call → env.step → bench-tool-result).

## Retail — Pass^1 = 0.20 (1/5)
| task | reward | termination |
|---|---|---|
| 0 | 0.0 | USER_STOP |
| 1 | 0.0 | USER_STOP |
| 2 | **1.0** | USER_STOP |
| 3 | 0.0 | USER_STOP |
| 4 | 0.0 | USER_STOP |

- **vs TEXT retail 0.61** → ~3× drop. Small N (5) — wide error bars; directional.
- 16 tool calls fired + round-tripped; 0 agent errors. The agent is competent — right
  tools, persists through failures, natural (USER_STOP) endings.

## Why voice degrades (the real finding)
Failures are **not** tool-calling or reasoning — they're **ASR on spelled alphanumeric
identifiers**. Retail is dense with order/user IDs + zips, and voice ASR mangles them:
`W2378156` heard as `W0378156`, then `W237856` — so `get_order_details` /
`find_user_id_by_name_zip` can't match, and the agent (correctly) can't proceed.
The one PASS (task2) didn't hinge on reciting a mangled ID.

**Actionable:** alphanumeric-tuned ASR / read-back-by-digit confirmation / prefer
email-based auth (validatable against a known set) for ID-heavy voice flows.

## Provenance note
The long-standing "voice 0-scores" were NOT the rig or the agent — they were the
tool-blind Claude→Gemini failover during an Anthropic $0-credit outage (fixed: backend
PRs #526 tool-aware failover + #527 LLM health alerting). Once the LLM stack was healthy,
voice benchmarked the real agent.
