# Whissle agent — tau²/τ³-bench results

Agent under test: the Whissle platform agent (production brain: prompt + model +
guardrails), driven over the API via `--agent whissle` (see
`src/tau2/agent/whissle_agent.py` → `POST {WHISSLE_BASE}/api/bench/agent-turn`).
tau2 owns the tools, task DB, and scoring. User simulator: `gpt-4o`. Concurrency 2
(higher concurrency causes LLM-provider rate-limiting that pollutes the score).

| Domain  | Pass^1        | Leaderboard (GPT-4o / Claude-3.5-Sonnet) | Notes |
|---------|---------------|------------------------------------------|-------|
| Retail  | 0.61 (70/114) | 0.60 / 0.69                              | in the band |
| Airline | 0.56 (28/50)  | ~0.42 / ~0.46                            | beats both |

Raw trajectories: `retail_run1.json`, `airline_run1.json`.

## Reproduce
```bash
uv sync
export WHISSLE_BASE=... WHISSLE_AGENT_ID=... WHISSLE_API_KEY=... OPENAI_API_KEY=...
uv run tau2 run --domain retail  --agent whissle --agent-llm whissle --user-llm gpt-4o --max-concurrency 2
uv run tau2 run --domain airline --agent whissle --agent-llm whissle --user-llm gpt-4o --max-concurrency 2
```
