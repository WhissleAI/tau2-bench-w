# Diagnostic: the "transition judge" failure is not a transition-judge failure

> **RESOLVED 2026-08-21.** Credit was restored on the backend's Gemini key and every
> LLM path recovered immediately. `POST /api/models/chat` returns normally
> (`"OK"`, 724 ms), and the live agent's flow now advances: `greet → understand_issue`
> with `transition_check … "result": "fired"`. The diagnosis below held — it was
> platform-wide inference, not the judge, and a billing top-up was the whole fix.
> Kept as the record of how it was found, and of what stayed unknown.

**Date:** 2026-08-21 · **Method:** read-only; 2 paid requests, both unbilled (they failed).

## Correction

Two earlier notes in this folder called this a *flow transition judge* problem.
That was wrong — it was inferred from where the error first appeared, not from
where it originates. The same error occurs on a plain platform LLM call with no
agent, no flow, and no judge involved. **Platform LLM inference is down for this
workspace; the judge is merely the first place it became visible.**

## Verified facts

| # | Fact | How established |
|---|---|---|
| 1 | The full error is `Gemini API (429) … "Your prepayment credits are depleted. Please go to AI Studio at https://ai.studio/projects to manage your project and billing." status: RESOURCE_EXHAUSTED` | Returned in full by `POST /api/models/chat` |
| 2 | The trace's `reason` field is truncated **server-side at 133 characters**, which is why earlier reads stopped at "Your prepayment " | Measured `len(reason)` on the stored trace |
| 3 | `POST /api/models/chat` fails with the identical error — no agent, no flow, no judge | Paid request 1 |
| 4 | The `--fast` engine path fails identically | Paid request 2 |
| 5 | Non-LLM APIs are healthy: `GET /api/models/voices` returns 11 voices, `default_engine: cartesia` | Free read |
| 6 | Auth, org resolution, agent reads, flow reads, and KB reads all work | Free reads |
| 7 | Workspace balance is **$9.6373**, `low_balance: false`, `payments_enabled: true` | `GET` usage |
| 8 | Failed LLM calls are **not** billed — balance identical before and after both paid requests | usage before/after |
| 9 | Successful `chat/turn` requests **were** billed at $0.01 (`"Agent chat (API)"`) even while the judge failed | usage ledger |
| 10 | The error text is `all LLM providers failed; last error: Gemini …` — multiple providers were attempted | Error body, both endpoints |

## What the raw error proves

- **Gemini's prepaid credit is exhausted.** `RESOURCE_EXHAUSTED` plus "prepayment
  credits are depleted" is credit exhaustion, not throttling. A rate limit would
  reference a per-minute or per-day quota metric and typically carry a retry hint;
  this references a billing page.
- **The credential is a Google AI Studio project.** The remediation URL is
  `ai.studio/projects` — an AI Studio (Gemini API) project's own billing, reached
  by whoever owns that project.
- **It is not my workspace balance.** Balance is positive, `low_balance` is false,
  payments are enabled, and Whissle bills in USD through its own ledger. The two
  systems are unrelated: Whissle's ledger debited $0.01 for turns during which
  Gemini was already failing.
- **The fault is not in the flow layer.** `/api/models/chat` shares the failure and
  touches no flow code.
- **It is not confined to one engine tier** — the default and `--fast` paths both fail.

## What the raw error does not prove

- **That Gemini is the configured or primary provider.** The wording is "all LLM
  providers failed; **last** error: Gemini". Gemini is the last one attempted. It
  could be primary, a fallback, or the only one actually configured. Nothing
  reachable from the API distinguishes these.
- **Why the other providers failed.** Only the last error is surfaced. The others'
  identities and failure reasons are not exposed on any endpoint reachable here —
  not in the 502 body, not in the flow trace, not in usage.
- **That the provider chain is correctly configured.** "All providers failed" is
  equally consistent with a healthy chain whose members are all genuinely down, and
  with a chain where only one provider is really wired up and the rest are
  misconfigured. Topping up Gemini would restore service under either reading, and
  would also hide the second one.
- **That no other fault exists behind it.** Once inference returns, the judge could
  still fail for unrelated reasons. It has never been observed working.

## Which component owns the failing credential

A **Google AI Studio (Gemini API) project belonging to the Whissle backend or its
provider configuration** — not this workspace, and not any org-level credential
visible through the CLI or API.

**The specific owning account cannot be identified from here, and I am not going to
guess.** The API surfaces no provider configuration: the agent record exposes only
`stt_provider`, `tts_provider`, and `avatar_provider`; the flow `settings` block
carries only `on_guard_trip`, `fallback_state`, `max_visits_per_state`, and
`max_transitions_per_call`; the CLI states that "the platform picks the engine — the
model/provider is never exposed"; and `/openapi.json` returns 401. No backend source
is present in this workspace, so the configuration path that selects the judge could
not be read — only whoever holds the backend's provider config can name the project.

## Smallest required fix

**Restore credit on the Gemini API key the backend uses** (AI Studio → the project in
question → billing). That is one billing action, no code and no configuration change,
and it unblocks every LLM path at once.

Two things worth doing alongside it, neither blocking:

1. **Report all provider failures, not just the last.** The current message hides
   whether the fallback chain is real. One line of logging would have made this
   diagnosis a single call instead of six.
2. **Raise the 133-character cap on the trace `reason` field.** The truncation cut
   the message exactly before the words that identified the cause, which is what sent
   the first read down the wrong path.

## Unknown

- Which account/project owns the Gemini credential.
- Which other providers are configured, and why each failed.
- Whether the fallback chain is functioning as designed.
- Whether the judge works once inference is restored — never yet observed.
- Whether `chat/turn` billing at $0.01 for a turn that produced only the fallback
  message is intended. Flagged as an observation, not a claim.

## Sanitisation

No keys, tokens, personal paths, phone numbers, or signed URLs. The org id and
agent id appear as non-secret identifiers already used throughout this folder.
