# ApplianceCare native Whissle preflight handoff

ApplianceCare benchmark definitions and replication scripts are maintained in
[the canonical benchmark repository](https://github.com/colinsohn/appliance-care-bench).
Tau remains the customer simulator, synthetic environment, tool execution bridge
and scorer. Do not duplicate the benchmark scripts here or replace Whissle's saved
prompt, private KB and orchestration with direct-model controls.

The latest bounded native-route check returned the same conversation ID for two
requests that omitted `conversation_id`. HTTP requests succeeded, but fresh-session
isolation did not. This is a preflight failure, not a platform score. Karan's reported
fix needs its deployed endpoint and fresh-session request contract confirmed before
another attempt. Backend source access is not required if the deployed API works.

The canonical review branch `fix/native-conversation-preflight` contains:

- `scripts/whissle_conversation_probe.py`: three-message, no-retry check with explicit
  live confirmation and private evidence output; credentials stay in the environment.
- `scripts/whissle_native_text_benchmark.py`: validates recorded fresh/continuation IDs
  and forces the native Studio mode for official launches.
- `results/probes/WHISSLE_NATIVE_ISOLATION_2026-09-10.json`: the failed check evidence.
- `docs/WHISSLE_NATIVE_STATUS.md`: remaining gates and reproduction instructions.

After contract confirmation, verify fresh sessions and continuation together with
saved prompt, exact five PDFs, traceable flow, 13 support tools, authenticated Tau
callbacks and actual served model. Independent reviews and retention sign-off are
separate requirements before freezing v9 and approving the official 30-trial run.
Do not run another hybrid, direct-model or voice test to work around isolation.

This documentation-only change starts from main commit `9f6632f`. Future Tau code
changes should likewise use a new branch from current main and a pull request.
It does not deploy backend code, change scoring or claim a passing live benchmark.
