# Model sweep — which brain runs voice and text

Production runs `ANTHROPIC_MODEL=claude-haiku-4-5` for the text-agent brain, the
voice path, call summaries, metadata extraction and the shadow LLM, and every
benchmark number we have was measured on it. This is the tooling that tests
whether something better is worth its cost and its latency.

## The four pieces

| file | what it answers |
|---|---|
| `run_arms.sh` | drives MedAgentBench / AgentClinic / PatientAgentBench across every arm, same cases, same order, judge pinned |
| `ttft_probe.py` | **time to first spoken token**, streaming, direct against each vendor — the number no harness records |
| `collect.py` | pulls every arm's scores and latencies out of the three results trees into one table |
| `cost_model.py` | cost per case per arm (estimated — see the caveat below) |

## Why the probe is separate

The harnesses record end-to-end completion time. A voice pipeline does not feel
that. Our TTS starts speaking on the first sentence, so a model that streams its
opening clause in 400 ms feels instant even when the whole turn takes four
seconds — and a model that thinks for two seconds before emitting anything is
dead air however fast it finishes. `ttft_probe.py` measures first *visible*
token (thinking tokens are generated first and are not spoken), first sentence
boundary, total time, and tokens/cost from each vendor's own usage block.

It also covers configurations the benchmarks cannot reach through the deployed
endpoint: `effort`, `thinking: {type: disabled}`, `speed: "fast"`, and Gemini's
`thinkingLevel`.

## Running it

```bash
# scores — one benchmark, all arms (or name arms: haiku opus5 g35fl)
./run_arms.sh medagent
./run_arms.sh agentclinic
./run_arms.sh pab

# latency + cost per model/effort config
ANTHROPIC_API_KEY=… GEMINI_API_KEY=… python3 ttft_probe.py --reps 5

python3 collect.py       # score table
python3 cost_model.py    # cost table
```

Prerequisites beyond the normal bench setup: a MedAgentBench FHIR container
(`docker run -d --rm -p 8090:8080 jyxsu6/medagentbench:latest`, and
`MEDAGENTBENCH_FHIR_BASE` must match its port and keep its trailing slash), and
PatientAgentBench's own venv, which pins langchain 1.x against tau2's 0.3.x.

## Two things to get right, or you measure the wrong thing

**Pin the judge.** On the default `--judge-provider whissle` route the grader is
whatever `/api/models/chat` happens to route to. It is not nameable and it can
move between arms, and a judge that moves makes every score incomparable. Every
arm here runs `--judge-provider anthropic` with an explicit model. Note
PatientAgentBench wants an upstream *registry key* (`claude-sonnet-5-api`), not
a raw model id — a raw id fails the config parse before the run starts.

**Costs here are estimated, and the estimate is structural.** The deployed
`/api/bench/agent-turn` returns no `usage` block, so no harness can record what a
case cost — AgentClinic is the only one that even looks, and it writes `null` on
every turn. `cost_model.py` composes measured turns/case with measured
tokens/turn on a matched workload shape. Absolute dollars run low, because the
probe's history is shorter than an accumulated benchmark case. The **ratios**
between arms are the signal, and they are the thing the decision turns on.
Backend PR whissle_gateway_backend#664 adds the `usage` block; once that ships,
replace this file with the real per-case numbers.
