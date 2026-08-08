"""Layer 1 — is there signal in the probability substrate at all?

The thing under test is not entity strings. It is the **probability substrate**
the whissle-large metadata head emits: emotion probs, intent probs, age/gender,
and the per-interim ``metadata_probs_timeline`` that hesitation is derived from.
No transcription vendor emits any of it, so it has no third-party substitute —
which is exactly why it is worth knowing whether it carries information.

This layer is deliberately upstream of every consumer, and deliberately free of
the LLM. Its question is the precondition for all the others:

    Does the substrate discriminate between states we care about?

If the emotion head returns ``Neutral`` at high confidence on an angry caller and
a happy one alike, then no downstream consumer — eager reply, hesitation gating,
turn completeness, shadow commit — can extract value from it, however well that
consumer is written. Conversely a *noisy* signal is not automatically useless: a
predictive feature does not have to be authoritative, only informative, and the
right test for it is mutual information against the state, not top-1 accuracy
against a label. Both are reported, because they answer different questions and
the wrong one has been used to justify the layer before.

Measured per channel:

``accuracy``          top-1 against the gold label. The bar for an *authoritative*
                      consumer (one that overrides or corrects).
``mutual_information``  bits the channel's distribution carries about the gold
                      label. The bar for a *predictive* consumer (one that gates
                      or prioritises). A channel can score ~0 accuracy and still
                      be worth gating on, and vice versa.
``degeneracy``        share of cases taking the modal value, and the entropy of
                      the marginal. A head that always says the same thing has
                      zero information no matter what its confidence says.
``discrimination``    for the continuous hesitation features: the separation
                      between gold classes, as a standardised mean difference.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

#: Coarse mapping from the corpus's intended affect to the emotion head's label
#: space. The head emits Neutral/Happy/Angry/Sad/Fear/Surprise-style tokens; the
#: corpus declares five intended affects. ``frustrated`` maps onto ``Angry``
#: because the head has no separate token for it — stated here rather than
#: silently scored as a miss.
AFFECT_TO_HEAD = {
    "neutral": {"neutral"},
    "angry": {"angry", "disgust", "frustrated"},
    "frustrated": {"angry", "disgust", "frustrated"},
    "sad": {"sad", "fear"},
    "happy": {"happy", "surprise"},
}

#: The corpus's routing labels grouped onto the intent head's label space.
ROUTE_TO_INTENT = {
    "book_appointment": {"request", "propose", "announce"},
    "reschedule_appointment": {"request", "propose", "inform"},
    "cancel_appointment": {"request", "reject", "inform"},
    "billing_question": {"question", "request", "inform"},
    "update_contact_details": {"inform", "announce", "request"},
    "prescription_refill": {"request", "inform"},
    "escalate_to_human": {"request", "complain", "reject"},
    "other": set(),
}


def _norm(tok: str) -> str:
    return (tok or "").replace("EMOTION_", "").replace("INTENT_", "") \
        .replace("AGE_", "").replace("GENDER_", "").strip().lower()


def entropy(probs: Sequence[float]) -> float:
    tot = sum(p for p in probs if p > 0)
    if tot <= 0:
        return 0.0
    return -sum((p / tot) * math.log2(p / tot) for p in probs if p > 0)


def _marginal_entropy(labels: Sequence[str]) -> float:
    c = Counter(labels)
    n = sum(c.values())
    return entropy([v / n for v in c.values()]) if n else 0.0


def mutual_information(gold: Sequence[str], pred: Sequence[str]) -> float:
    """I(gold; pred) in bits, from the empirical joint.

    Positively biased at small n — with 100 cases and a handful of labels the
    bias is on the order of (|G|-1)(|P|-1)/(2 n ln2) bits, which is reported
    alongside so a small positive number is not read as a small real effect.
    """
    n = len(gold)
    if n == 0 or n != len(pred):
        return 0.0
    joint: Counter = Counter(zip(gold, pred))
    pg, pp = Counter(gold), Counter(pred)
    mi = 0.0
    for (g, p), c in joint.items():
        pxy = c / n
        mi += pxy * math.log2(pxy / ((pg[g] / n) * (pp[p] / n)))
    return max(0.0, mi)


def mi_bias(gold: Sequence[str], pred: Sequence[str]) -> float:
    n = len(gold) or 1
    return ((len(set(gold)) - 1) * (len(set(pred)) - 1)) / (2 * n * math.log(2))


def cohens_d(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    pooled = math.sqrt(((len(a) - 1) * va + (len(b) - 1) * vb) / (len(a) + len(b) - 2))
    return round((mb - ma) / pooled, 4) if pooled else None


@dataclass
class ChannelReport:
    channel: str
    n: int = 0
    labels_seen: dict[str, int] = field(default_factory=dict)
    modal_label: str = ""
    modal_share: Optional[float] = None
    marginal_entropy_bits: Optional[float] = None
    accuracy: Optional[float] = None
    accuracy_note: str = ""
    majority_class_baseline: Optional[float] = None
    accuracy_off_majority: Optional[float] = None
    n_off_majority: Optional[int] = None
    accuracy_off_majority_note: str = ""
    mutual_information_bits: Optional[float] = None
    mi_small_sample_bias_bits: Optional[float] = None
    mi_above_bias: Optional[bool] = None
    mean_confidence: Optional[float] = None
    verdict: str = ""
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TimelineReport:
    n_with_timeline: int = 0
    n_total: int = 0
    mean_snapshots: Optional[float] = None
    min_snapshots: Optional[int] = None
    max_snapshots: Optional[int] = None
    features: dict[str, dict[str, Any]] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyse_channel(channel: str, gold: list[str], top: list[str],
                    confidences: list[float], hit_sets: dict[str, set]) -> ChannelReport:
    r = ChannelReport(channel=channel, n=len(gold))
    if not gold:
        return r
    counts = Counter(top)
    r.labels_seen = dict(counts)
    r.modal_label, modal_n = counts.most_common(1)[0]
    r.modal_share = round(modal_n / len(top), 4)
    r.marginal_entropy_bits = round(_marginal_entropy(top), 4)
    hits = sum(1 for g, p in zip(gold, top) if p in hit_sets.get(g, set()))
    r.accuracy = round(hits / len(gold), 4)
    r.accuracy_note = (
        "top-1 against the gold label, scored through the documented label mapping; "
        "a head label counts as correct if it is in the gold label's accepted set"
    )

    # Overall accuracy on a corpus with a dominant class is mostly a report of the
    # base rate. The number that decides whether a consumer can act on the channel
    # is its accuracy on the cases where the answer is NOT the majority label, and
    # the majority-class baseline it has to beat.
    gold_counts = Counter(gold)
    majority_gold, majority_n = gold_counts.most_common(1)[0]
    r.majority_class_baseline = round(majority_n / len(gold), 4)
    off_idx = [i for i, g in enumerate(gold) if g != majority_gold]
    if off_idx:
        off_hits = sum(1 for i in off_idx if top[i] in hit_sets.get(gold[i], set()))
        r.accuracy_off_majority = round(off_hits / len(off_idx), 4)
        r.n_off_majority = len(off_idx)
        r.accuracy_off_majority_note = (
            f"accuracy on the {len(off_idx)} cases whose gold label is not the "
            f"majority class ({majority_gold!r}). This is the number a consumer that "
            "acts on the channel actually depends on: overall accuracy on a corpus "
            f"that is {100 * r.majority_class_baseline:.0f}% one class is mostly a "
            "restatement of the base rate."
        )
    r.mutual_information_bits = round(mutual_information(gold, top), 4)
    r.mi_small_sample_bias_bits = round(mi_bias(gold, top), 4)
    r.mi_above_bias = (r.mutual_information_bits > r.mi_small_sample_bias_bits)
    if confidences:
        r.mean_confidence = round(sum(confidences) / len(confidences), 4)
    conf: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for g, p in zip(gold, top):
        conf[g][p] += 1
    r.confusion = {k: dict(v) for k, v in conf.items()}

    if r.modal_share is not None and r.modal_share >= 0.95:
        r.verdict = "degenerate — the head returns one label almost always"
        r.note = (
            f"{100 * r.modal_share:.0f}% of cases returned {r.modal_label!r}. A channel "
            "with no variance carries no information regardless of the confidence it "
            "reports, and no downstream consumer can gate on it."
        )
    elif not r.mi_above_bias:
        r.verdict = "no information above small-sample bias"
        r.note = (
            f"MI = {r.mutual_information_bits} bits against a small-sample bias floor "
            f"of {r.mi_small_sample_bias_bits} bits at n = {len(gold)}. Not "
            "distinguishable from noise at this sample size."
        )
    else:
        r.verdict = "informative"
        r.note = (
            f"MI = {r.mutual_information_bits} bits, above the {r.mi_small_sample_bias_bits}-bit "
            "bias floor. Informative enough to gate on even where top-1 accuracy is low — "
            "a predictive consumer does not need an authoritative channel."
        )
    return r


def analyse(records: list[dict[str, Any]]) -> dict[str, Any]:
    """``records`` are the per-case dicts written by the runner (or the perception
    cache joined to the corpus)."""
    gold_affect, top_emotion, emo_conf = [], [], []
    gold_route, top_intent, int_conf = [], [], []
    timelines: dict[str, list[float]] = defaultdict(list)
    by_affect: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    within_slice: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    n_with_tl = 0
    snaps: list[int] = []

    for rec in records:
        meta = (rec.get("perception") or {}).get("metadata")
        if not meta:
            continue
        gold_affect.append(rec.get("gold_affect") or "neutral")
        top_emotion.append(_norm(str(meta.get("emotion") or "")))
        emo_conf.append(float(meta.get("emotion_confidence") or 0.0))
        gold_route.append(rec.get("gold_route") or "other")
        top_intent.append(_norm(str(meta.get("intent") or "")))
        int_conf.append(float(meta.get("intent_confidence") or 0.0))

        s = meta.get("hesitation_emotion_snapshots")
        if isinstance(s, (int, float)) and s > 0:
            n_with_tl += 1
            snaps.append(int(s))
        for k, v in meta.items():
            if k.startswith("hesitation_") and isinstance(v, (int, float)):
                timelines[k].append(float(v))
                affect = rec.get("gold_affect") or "neutral"
                by_affect[k][affect].append(float(v))
                if rec.get("slice") == "emotion":
                    within_slice[k][affect].append(float(v))

    emotion = analyse_channel("emotion", gold_affect, top_emotion, emo_conf, AFFECT_TO_HEAD)
    intent = analyse_channel("intent", gold_route, top_intent, int_conf, ROUTE_TO_INTENT)

    tl = TimelineReport(n_with_timeline=n_with_tl, n_total=len(gold_affect))
    if snaps:
        tl.mean_snapshots = round(sum(snaps) / len(snaps), 2)
        tl.min_snapshots, tl.max_snapshots = min(snaps), max(snaps)
    tl.note = (
        "metadata_probs_timeline IS populated on the batch path — the per-interim "
        "snapshots hesitation is derived from exist here, which is what makes this "
        "layer measurable at all. On the live voice path the same timeline is empty, "
        "because the head that fills it is not running there."
    )
    for k, vals in sorted(timelines.items()):
        mean = sum(vals) / len(vals)
        var = sum((x - mean) ** 2 for x in vals) / max(1, len(vals) - 1)
        neg = [x for a, xs in by_affect[k].items() if a != "neutral" for x in xs]
        neu = by_affect[k].get("neutral", [])
        # WITHIN-SLICE control. Across the whole corpus, "neutral" is dominated by
        # the entity slice, whose utterances are long, and "affective" by the
        # emotion slice, whose utterances are short — so a corpus-wide separation
        # on any duration-sensitive feature (span_s, snapshots) is a length
        # artefact wearing an effect size. Recomputing inside the emotion slice,
        # where both groups are the same kind of short utterance, removes it.
        wn = within_slice[k].get("neutral", [])
        wa = [x for a, xs in within_slice[k].items() if a != "neutral" for x in xs]
        tl.features[k] = {
            "n": len(vals),
            "mean": round(mean, 4),
            "sd": round(math.sqrt(var), 4),
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
            "constant": var == 0,
            "cohens_d_neutral_vs_affective": cohens_d(neu, neg),
            "cohens_d_within_emotion_slice": cohens_d(wn, wa),
            "n_within_slice": [len(wn), len(wa)],
            "duration_sensitive": k in ("hesitation_span_s", "hesitation_emotion_snapshots"),
        }

    informative = [c.channel for c in (emotion, intent) if c.verdict == "informative"]
    return {
        "n_cases_with_substrate": len(gold_affect),
        "channels": {"emotion": emotion.to_dict(), "intent": intent.to_dict()},
        "timeline": tl.to_dict(),
        "informative_channels": informative,
        "headline": (
            f"Of the two probability channels that reach the model, "
            f"{len(informative)} carry information about the state they are supposed "
            f"to describe ({', '.join(informative) or 'none'}) at n = {len(gold_affect)}."
        ),
        "caveat": (
            "Measured on TTS-synthesised speech. Synthetic audio is affectively flat, "
            "so the emotion channel is being asked to read an affect the audio does "
            "not carry: a degenerate result here bounds what the channel can do on "
            "THIS substrate and is NOT an estimate of its accuracy on real callers. "
            "The intent, age/gender and timeline channels do not depend on affective "
            "prosody in the same way and are less compromised by it."
        ),
    }
