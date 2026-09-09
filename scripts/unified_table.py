#!/usr/bin/env python3
"""Build the unified text-vs-audio benchmark table across domains.

Consumes the per-arm diagnostic JSONs emitted by diagnostic_report.py and produces
one row per domain with text and voice (audio) side by side — the shape the
benchmark page and the report both render.

Usage:
  unified_table.py --out results/whissle/UNIFIED_TABLE.json \
     --text retail=<diag.json> airline=<diag.json> appliances=<diag.json> \
     --voice retail=<diag.json> airline=<diag.json> appliances=<diag.json>
"""
from __future__ import annotations
import argparse, json, os

MODEL = "openai.gpt-oss-120b"
# Published τ²-bench numbers, all measured on claude-haiku-4-5 (the older prod default).
PUBLISHED = {
    "retail":     {"text": 61.4, "voice": 20.0, "text_n": 114, "voice_n": 5},
    "airline":    {"text": 56.0, "voice": None, "text_n": 50,  "voice_n": None},
    "appliances": {"text": None, "voice": None, "text_n": None, "voice_n": None},  # was 0/30 route-artifact
}
LABELS = {"retail": "Retail — order support",
          "airline": "Airline — booking & changes",
          "appliances": "Appliances — washer support"}


def load(path):
    if not path or not os.path.exists(path):
        return None
    return json.load(open(path))


def arm(diag):
    if not diag:
        return None
    r = diag.get("result", {})
    exact = r.get("exact_passes", "0/0")
    passed, n = (exact.split("/") + ["0", "0"])[:2]
    return {"score": r.get("exact_pass_pct"), "passed": int(passed), "n": int(n),
            "model": MODEL,
            "db": r.get("db_component_passes"),
            "action": r.get("action_component_passes")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/whissle/UNIFIED_TABLE.json")
    ap.add_argument("--text", nargs="*", default=[])
    ap.add_argument("--voice", nargs="*", default=[])
    a = ap.parse_args()
    text = {kv.split("=", 1)[0]: kv.split("=", 1)[1] for kv in a.text}
    voice = {kv.split("=", 1)[0]: kv.split("=", 1)[1] for kv in a.voice}

    rows = []
    for dom in ("retail", "airline", "appliances"):
        t = arm(load(text.get(dom)))
        v = arm(load(voice.get(dom)))
        if v:
            v["preliminary"] = v["n"] < 10
        pub = PUBLISHED.get(dom, {})
        rows.append({
            "domain": dom, "label": LABELS[dom], "suite": "τ²/τ³-bench",
            "text": t, "voice": v,
            "published": {"text": pub.get("text"), "voice": pub.get("voice"),
                          "model": "claude-haiku-4-5"},
            "text_delta_vs_published": (round(t["score"] - pub["text"], 1)
                                        if t and pub.get("text") is not None else None),
        })
    out = {"model_under_test": MODEL, "user_sim": "gpt-4o", "metric": "Pass^1",
           "rows": rows}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w"), indent=2)
    # pretty print
    print(f"\nUNIFIED TABLE — {MODEL} — Pass^1\n")
    print(f"{'Domain':<26}{'TEXT':>16}{'AUDIO':>16}{'pub text':>10}{'Δ text':>9}")
    print("-" * 77)
    for r in rows:
        t = r["text"]; v = r["voice"]
        ts = f"{t['score']}% ({t['passed']}/{t['n']})" if t else "—"
        vs = (f"{v['score']}% ({v['passed']}/{v['n']}){'*' if v and v.get('preliminary') else ''}"
              if v else "—")
        pub = r["published"]["text"]
        d = r["text_delta_vs_published"]
        print(f"{r['label']:<26}{ts:>16}{vs:>16}{(str(pub)+'%' if pub else '—'):>10}{(f'{d:+}' if d is not None else '—'):>9}")
    print("\n* preliminary (N<10). Published = claude-haiku-4-5 (older prod default).")
    print(f"written: {a.out}")


if __name__ == "__main__":
    main()
