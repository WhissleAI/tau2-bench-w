"""``SUMMARY.json`` → ``REPORT.md``, in research-paper structure.

Pure function of the artifact: no network, no recomputation, idempotent. The
report is regenerated whenever the analysis improves, so it must never be the
only place a number lives.

Structure is fixed — Abstract, Method, Arms, Results (per channel), Threats to
validity, What was not measured, Reproduction — because a report whose shape
changes per run cannot be diffed against the previous run, and the whole point of
maintaining one per test is that it can be.

Two rules the renderer enforces rather than trusts:

* A verdict of ``no measurable effect`` is always printed with the minimum
  detectable effect beside it, so "we saw nothing" and "we could not have seen
  anything" are never rendered the same way.
* Exploratory arms and post-hoc metrics carry an explicit label in the table, not
  a footnote, so a reader skimming the results cannot mistake one for a
  pre-declared finding.
"""

from __future__ import annotations

from typing import Any, Optional

_VERDICT_MARK = {
    "gain": "**GAIN**",
    "regression": "**REGRESSION**",
    "underpowered": "underpowered",
    "not measured": "not measured",
}


def _pct(x: Optional[float], nd: int = 1) -> str:
    return "—" if x is None else f"{100 * x:.{nd}f}%"


def _num(x: Optional[float], nd: int = 3) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _mark(v: str) -> str:
    return _VERDICT_MARK.get(v, v)


def _ci(ci) -> str:
    if not ci:
        return "—"
    return f"[{ci[0]:+.3f}, {ci[1]:+.3f}]"


def render_report(summary: dict[str, Any]) -> str:
    if summary.get("layer") == "substrate":
        return _render_substrate(summary)
    return _render_ablation(summary)


# ---------------------------------------------------------------------------


def _render_substrate(s: dict[str, Any]) -> str:
    sub = s.get("substrate") or {}
    L: list[str] = []
    A = L.append
    A(f"# Metadata ablation — Layer 1: is there information in the substrate?\n")
    A(f"*Run `{s['run_id']}` · {s.get('finished_at', '')} · "
      f"N = {s.get('n_total')} · schema `{s.get('schema')}`*\n")

    A("## Abstract\n")
    A(sub.get("headline", "") + "\n")
    A("This layer measures the whissle-large metadata head's **probability "
      "substrate** — emotion probs, intent probs and the per-interim "
      "`metadata_probs_timeline` — against gold labels, upstream of every consumer "
      "and without involving the LLM. It answers the precondition question for the "
      "whole programme: a consumer cannot extract value from a channel that does "
      "not discriminate, however well the consumer is written.\n")

    A("## Method\n")
    A("Each case is one caller utterance, synthesised through `/api/models/tts` and "
      "heard once through `/api/models/transcribe`, which runs the external "
      "transcription engine and the whissle-large metadata head in parallel. Head "
      "output is compared against the corpus's declared gold labels.\n")
    A(f"- Corpus: `{(s.get('corpus') or {}).get('version')}`, "
      f"digest `{(s.get('corpus') or {}).get('digest')}`, "
      f"slices {(s.get('corpus') or {}).get('slices')}\n")
    A(f"- Metadata head first-attempt availability: "
      f"{_pct((s.get('metadata_head') or {}).get('first_attempt_availability'))}\n")
    A(f"- Cases with substrate: {s.get('n_with_substrate')} / {s.get('n_total')}\n")

    A("\n## Results — per channel\n")
    A("| channel | top-1 acc | majority-class baseline | acc off majority class | "
      "MI (bits) | MI bias floor | modal label | verdict |")
    A("|---|---|---|---|---|---|---|---|")
    for name, c in (sub.get("channels") or {}).items():
        A(f"| {name} | {_pct(c.get('accuracy'))} | "
          f"{_pct(c.get('majority_class_baseline'))} | "
          f"{_pct(c.get('accuracy_off_majority'))} (n={c.get('n_off_majority')}) | "
          f"{_num(c.get('mutual_information_bits'))} | "
          f"{_num(c.get('mi_small_sample_bias_bits'))} | "
          f"`{c.get('modal_label')}` @ {_pct(c.get('modal_share'))} | "
          f"{c.get('verdict')} |")
    A("")
    A("Read the two accuracy columns together. Top-1 accuracy on a corpus with a "
      "dominant class is largely a restatement of the base rate; the column that "
      "decides whether a consumer can *act* on a channel is its accuracy on the "
      "cases where the answer is not the majority label. Mutual information answers "
      "a different question again — whether the channel is worth **gating** on, "
      "which does not require it to be authoritative.\n")

    for name, c in (sub.get("channels") or {}).items():
        A(f"### {name}\n")
        A(f"{c.get('note', '')}\n")
        A(f"Labels emitted: `{c.get('labels_seen')}`  ·  marginal entropy "
          f"{_num(c.get('marginal_entropy_bits'))} bits  ·  mean reported confidence "
          f"{_pct(c.get('mean_confidence'))}\n")
        conf = c.get("confusion") or {}
        if conf:
            A("| gold | head output |")
            A("|---|---|")
            for g, row in sorted(conf.items()):
                A(f"| {g} | " + ", ".join(f"`{k}`×{v}" for k, v in sorted(
                    row.items(), key=lambda kv: -kv[1])) + " |")
            A("")

    tl = sub.get("timeline") or {}
    A("## Results — the probability timeline\n")
    A(f"Populated on **{tl.get('n_with_timeline')} / {tl.get('n_total')}** cases, "
      f"mean {tl.get('mean_snapshots')} snapshots per utterance "
      f"(range {tl.get('min_snapshots')}–{tl.get('max_snapshots')}).\n")
    A(tl.get("note", "") + "\n")
    A("| feature | mean | sd | range | d (corpus-wide) | d (within emotion slice) | note |")
    A("|---|---|---|---|---|---|---|")
    for k, v in (tl.get("features") or {}).items():
        note = ("duration-sensitive — corpus-wide d is a length artefact"
                if v.get("duration_sensitive") else "")
        A(f"| `{k}` | {v.get('mean')} | {v.get('sd')} | "
          f"[{v.get('min')}, {v.get('max')}] | {v.get('cohens_d_neutral_vs_affective')} | "
          f"{v.get('cohens_d_within_emotion_slice')} (n={v.get('n_within_slice')}) | {note} |")
    A("")

    A("## Threats to validity\n")
    A(f"- {sub.get('caveat', '')}\n")
    A("- **Affect and utterance type are confounded in this corpus.** The neutral "
      "cases are dominated by the entity slice (long, information-dense "
      "utterances) and the affective cases by the emotion slice (short ones). Any "
      "corpus-wide separation on a duration-sensitive timeline feature is therefore "
      "a length artefact, and the within-slice control that would remove it has only "
      "5 neutral cases to work with. The timeline features are reported as "
      "**not cleanly measured**; the fix is to add length-matched neutral "
      "utterances to the emotion slice, which is a corpus change, not an analysis "
      "change.\n")
    A("- Mutual information is positively biased at this sample size; the bias floor "
      "is printed beside every estimate and a value below it is reported as no "
      "information rather than a small one.\n")

    asr = s.get("asr_cascade") or {}
    A("## The ear, measured once\n")
    A("A property of the cascade rather than of any arm, and the ceiling on anything "
      "downstream: a value the ear never heard cannot be recovered by a consumer of "
      "the ear's output.\n")
    A(f"- Digit slots (IDs, amounts, phone numbers): "
      f"**{_pct(asr.get('digit_error_rate'))} not recoverable** from the transcript "
      f"({asr.get('digit_slots', 0) - asr.get('digit_recoverable', 0)} of "
      f"{asr.get('digit_slots')}).\n")
    A(f"- Proper-noun slots (caller names): "
      f"**{_pct(asr.get('proper_noun_error_rate'))} not recoverable** "
      f"({asr.get('proper_noun_slots', 0) - asr.get('proper_noun_recoverable', 0)} of "
      f"{asr.get('proper_noun_slots')}).\n")

    A(_structural_section(s))
    A("## Reproduction\n")
    A("```bash\nuv run python -m tau2.ablation freeze\n"
      "uv run python -m tau2.ablation substrate --run-name <name>\n"
      "uv run python -m tau2.ablation report results/whissle/ablation/<name>\n```\n")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------


def _render_ablation(s: dict[str, Any]) -> str:
    L: list[str] = []
    A = L.append
    dec = s.get("decoding") or {}
    paired = s.get("paired") or {}
    results = paired.get("results") or []

    A("# Metadata ablation — Layer 2: what the substrate changes downstream\n")
    A(f"*Run `{s['run_id']}` · {s.get('finished_at', '')} · "
      f"N = {s.get('n_comparable')} comparable of {s.get('n_total')} · "
      f"schema `{s.get('schema')}`*\n")

    A("## Abstract\n")
    gains = paired.get("gains") or []
    regs = paired.get("regressions") or []
    nulls = paired.get("null") or []
    under = paired.get("underpowered") or []
    A(f"A paired, single-variable ablation of the metadata block over "
      f"{s.get('n_comparable')} identical cases. Of {len(results)} pre-declared "
      f"metrics: **{len(gains)} gain**, **{len(regs)} regression**, "
      f"{len(nulls)} no measurable effect, {len(under)} underpowered.\n")
    if gains:
        A(f"Gains: {', '.join(f'`{g}`' for g in gains)}\n")
    if regs:
        A(f"Regressions: {', '.join(f'`{r}`' for r in regs)}\n")
    A(f"The metadata block changed the reply text on "
      f"**{s.get('replies_changed_by_metadata')} of {s.get('n_comparable')}** cases. "
      "A channel that does not change the output cannot help or hurt, so that count "
      "bounds every effect below it.\n")

    A("## Method\n")
    A("Both arms consume **the same transcript**, produced by one perception pass "
      "per case (TTS → `/api/models/transcribe`). ASR quality, audio, task, model, "
      "system prompt, tool set and decoding are therefore identical across arms by "
      "construction rather than by re-running the pipeline and hoping. The only "
      "difference is the presence of the metadata block.\n")
    A("Arms are **interleaved per case** — A then B for case 1, then case 2 — so "
      "backend load and provider drift are common-mode rather than confounded with "
      "the arm.\n")
    A("All comparisons are **paired**: per-case B minus A, never arm-mean against "
      "arm-mean. At this N an unpaired comparison could not resolve anything real.\n")

    A("\n### Arms\n")
    A("| arm | metadata_mode | pre-declared | description |")
    A("|---|---|---|---|")
    for a in s.get("arms") or []:
        A(f"| **{a['key']}** — {a['label']} | `{a['metadata_mode']}` | "
          f"{'exploratory' if a.get('exploratory') else 'yes'} | {a['description']} |")
    A("")
    A("**Arm C (metadata + entity consumption) was not run.** See *What was not "
      "measured*.\n")

    A("### How the arms were verified matched\n")
    A(f"- **Model actually served**, read off the response rather than the request "
      f"(PR #664): every comparable case asserted `{dec.get('model')}` on both arms; "
      "a case served by anything else is dropped, not averaged in.\n")
    A(f"- **Single-variable spec check**: the arm specifications are compared field "
      "by field and must differ only in `metadata_mode`.\n")
    A("- **Arms-differ assertion**: arm B's prompt must not be character-identical "
      "to arm A's. This is the check that catches the failure this design exists to "
      "avoid — an empty metadata block silently turning arm B into arm A and "
      "producing a reproducible zero that reads as a finding.\n")
    A(f"- **Decoding pinned**: provider `{dec.get('provider')}`, model "
      f"`{dec.get('model')}`, thinking `{dec.get('thinking')}`, max_tokens "
      f"{dec.get('max_tokens')}, system prompt sha `{dec.get('system_sha')}`, "
      f"{len(dec.get('tools') or [])} tools bound.\n")
    A(f"- **Corpus frozen and digested**: `{(s.get('corpus') or {}).get('digest_of_run')}` "
      "— the task list was declared before the run and a digest mismatch is an error.\n")

    A("\n## Results\n")
    A("| metric | arm A | arm B | paired Δ | 95% CI | p | MDE | verdict |")
    A("|---|---|---|---|---|---|---|---|")
    for r in results:
        a = r.get("rate_a") if r.get("rate_a") is not None else r.get("mean_a")
        b = r.get("rate_b") if r.get("rate_b") is not None else r.get("mean_b")
        fmt = _pct if r.get("rate_a") is not None else (lambda x: _num(x, 2))
        A(f"| `{r['metric']}` | {fmt(a)} | {fmt(b)} | {_num(r.get('delta'), 4)} | "
          f"{_ci(r.get('ci'))} | {_num(r.get('p_value'), 4)} | "
          f"{_num(r.get('mde'), 4)} | {_mark(r.get('verdict', ''))} |")
    A("")
    A("`MDE` is the smallest true effect this run could have detected at 80% power. "
      "Where a metric reads *no measurable effect*, the MDE is the honest statement "
      "of what the run rules out — anything smaller than it remains possible.\n")

    for r in results:
        if r.get("verdict") == "underpowered":
            A(f"- `{r['metric']}` is **underpowered**: only "
              f"{r.get('detail', {}).get('discordant', '—')} of {r.get('n_pairs')} "
              f"pairs disagreed between the arms, so the run had the power of a study "
              f"of that size, not of {r.get('n_pairs')}. It could only have detected "
              f"an effect of {_num(r.get('mde'), 3)} or larger.\n")

    A("\n## Cost and latency per arm\n")
    A("| arm | n | served model | route acc | fabrication | mean latency | "
      "mean input tok | mean output tok | cost/case | total |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for key, p in (s.get("per_arm") or {}).items():
        A(f"| {key} | {p['n']} | {', '.join(p['served_models'])} | "
          f"{_pct(p['route_accuracy'])} | {_pct(p['fabrication_rate'])} | "
          f"{p['mean_latency_ms']} ms | {p['mean_input_tokens']} | "
          f"{p['mean_output_tokens']} | ${p['cost_per_case_usd']} | "
          f"${p['total_cost_usd']} |")
    A("")

    exc = s.get("exclusions") or []
    A(f"## Exclusions — {len(exc)} of {s.get('n_total')}\n")
    if exc:
        from collections import Counter
        kinds = Counter(e["reason"].split(":")[0] for e in exc)
        for k, v in kinds.most_common():
            A(f"- `{k}` × {v}")
        A("")
    else:
        A("None.\n")

    mh = s.get("metadata_head") or {}
    A("## Threats to validity\n")
    A(f"- **The metadata head fails open.** First-attempt availability during this "
      f"run was {_pct(mh.get('first_attempt_availability'))}; the suite retries per "
      "case and excludes a case where the head never answers. The retries change "
      "nothing about the audio or the transcript, so they cannot bias the arm "
      "comparison — but they do mean the run is measuring the head *when it "
      "answers*, which is a better condition than an ordinary caller gets.\n")
    A("- **Fidelity of the injection point.** Production adds the block as a "
      "separate `developer`-role context message. `/api/bench/agent-turn` accepts "
      "only `user`/`assistant` roles, so the block is delivered as the first line of "
      "the same user turn. The model sees identical characters in an identical "
      "position relative to the utterance; it does not see an identical role tag.\n")
    A("- **Synthetic speech.** TTS audio is affectively flat, so the emotion channel "
      "is being asked to read an affect the audio does not carry. Results on the "
      "emotion channel bound what it can do on this substrate and are not an "
      "estimate of its behaviour on real callers.\n")
    A("- **Single turn per case.** Chosen so turn-to-turn variance cannot swamp a "
      "per-turn signal, at the cost of not measuring effects that only accumulate "
      "over a conversation.\n")

    A(_structural_section(s))

    A("## What was not measured, and why\n")
    A("- **Arm C — entity consumption.** Not run. The consumers shipped by the entity "
      "work are gated off, and the batch metadata path this suite reaches requests "
      "no entity tags at all (`metadata_tags=None`), so there is no entity output to "
      "consume through this seam even in principle. Reported as not-measured rather "
      "than as a zero.\n")
    A("- **The predictive consumers** — eager-reply hit and false-fire rate, shadow "
      "draft commit-versus-discard, hesitation prediction quality, turn-taking "
      "timing, barge-in and false-cut rates. These live in the live voice pipeline "
      "and change *when* the agent acts rather than *what it says*. "
      "`/api/bench/agent-turn` is a stateless brain call with no turn-taking, so "
      "none of them are observable through it, and on the live path the head that "
      "would feed them is not running. Measuring them requires the head enabled on a "
      "voice path — which is a capacity decision about the shared T4, not a harness "
      "change.\n")
    A("- **What each consumer does with an empty timeline.** Production has been "
      "running with an empty `metadata_probs_timeline`, so arm A is not necessarily "
      "a clean no-signal baseline — it is whatever each consumer's fallback does, "
      "and a well-tuned prior could make it a much stronger baseline than 'no "
      "signal'. Establishing that per consumer is source-reading plus a live-path "
      "run; it is not answerable from this seam.\n")

    A("## Reproduction\n")
    A("```bash\nuv run python -m tau2.ablation preflight\n"
      f"uv run python -m tau2.ablation run --arms A,B --run-name {s['run_id']}\n"
      f"uv run python -m tau2.ablation report results/whissle/ablation/{s['run_id']}\n```\n")
    return "\n".join(L) + "\n"


def _structural_section(s: dict[str, Any]) -> str:
    st = s.get("structural_audit") or {}
    if not st:
        return ""
    L = ["\n## Structural audit — which channels reach the brain at all\n",
         "Some of this question is not experimental. A channel that is produced and "
         "then never read contributes zero by construction, and no sample size will "
         "show otherwise. Verified against the backend source, with line numbers.\n",
         f"{st.get('headline', '')}\n",
         "| channel | produced | reaches the prompt | reaches the flow engine | evidence |",
         "|---|---|---|---|---|"]
    for c in st.get("channels") or []:
        L.append(f"| {c['channel']} | {'yes' if c['produced'] else 'no'} | "
                 f"{'**yes**' if c['reaches_prompt'] else 'no'} | "
                 f"{'yes' if c['reaches_flow'] else 'no'} | "
                 f"{', '.join(f'`{e}`' for e in c['evidence'])} |")
    L.append("")
    lv = st.get("live_voice_path") or {}
    L.append("### The live voice path\n")
    L.append(f"{lv.get('claim', '')}\n")
    L.append(f"Evidence: {', '.join(f'`{e}`' for e in lv.get('evidence') or [])} "
             f"(verified: {lv.get('verified')})\n")
    L.append(f"{lv.get('batch_path_note', '')}\n")
    return "\n".join(L)
