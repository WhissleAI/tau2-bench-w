#!/usr/bin/env python3
"""Model-level latency probe: time-to-first-SPOKEN-token, not time-to-last.

Why this exists separately from the benchmarks: none of the three health
harnesses record what a voice pipeline actually feels. They record end-to-end
completion time, and our TTS starts speaking on the first sentence — so a model
that streams its opening clause in 400 ms feels instant even when the full turn
takes four seconds, and a model that thinks for two seconds before emitting
anything is dead air no matter how fast it finishes.

So we measure, per arm, streaming, against the vendor directly:

  ttft_ms       first VISIBLE text token. Thinking tokens are generated before
                any visible output, so on a thinking model this is the number
                that moves — and it is the one nothing else captures.
  ttfs_ms       first sentence boundary — when TTS can actually start speaking.
  total_ms      last token.
  out_tps       visible output tokens per second after the first one.
  tokens/cost   from the vendor's own usage block, priced per the published
                rate card. Thinking tokens bill at the OUTPUT rate on both
                vendors and Gemini does not include them in candidatesTokenCount,
                so they are added explicitly rather than inferred.

Run:  python3 ttft_probe.py --reps 5 --out results.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import statistics
import sys
import time

import httpx

# ── pricing, $ per 1M tokens (input, output) ─────────────────────────────────
# Anthropic: platform.claude.com/docs/en/pricing. Google: ai.google.dev/gemini-api/docs/pricing
# Sonnet 5 carries introductory pricing ($2/$10) through 2026-08-31; both are
# recorded so the report can show what the arm costs after it expires.
PRICING = {
    "claude-haiku-4-5":       (1.00,  5.00),
    "claude-sonnet-5":        (2.00, 10.00),   # intro; list is 3.00/15.00
    "claude-sonnet-5@list":   (3.00, 15.00),
    "claude-opus-5":          (5.00, 25.00),
    "claude-opus-5@fast":     (10.00, 50.00),
    "claude-fable-5":         (10.00, 50.00),
    "gemini-3.5-flash":       (1.50,  9.00),
    "gemini-3.5-flash-lite":  (0.30,  2.50),
    "gemini-3-flash-preview": (1.50,  9.00),   # priced as 3.x Flash
    "gemini-3.1-pro-preview": (2.00, 12.00),
}

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:streamGenerateContent?alt=sse&key={key}")

SENTENCE_END = re.compile(r"[.!?](\s|$)|\n")

# ── the workload ─────────────────────────────────────────────────────────────
# Three shapes that between them cover what a voice turn actually is. Deliberately
# NOT toy prompts: the system prompt is long enough to be realistic, because
# prompt length is part of what determines time-to-first-token.
SYSTEM = (
    "You are Ember, the voice assistant for Northside Family Clinic. You are speaking "
    "to a caller on the telephone, so your replies are spoken aloud by a text-to-speech "
    "engine. Keep every reply to one or two short sentences. Never use markdown, bullet "
    "points, numbered lists, emoji, or any formatting a person cannot hear. Spell out "
    "numbers as words when they are short. You may book, reschedule and cancel "
    "appointments, take a message for a clinician, and answer questions about clinic "
    "hours and location. You do not give medical advice of any kind, including for "
    "symptoms the caller describes as minor; when a caller asks for medical guidance, "
    "say plainly that you cannot advise and offer to take a message for their clinician "
    "or to book them a visit. If the caller mentions chest pain, difficulty breathing, "
    "sudden weakness, confusion, or uncontrolled bleeding, stop the conversation and "
    "tell them to hang up and call emergency services immediately. Confirm every "
    "appointment detail back to the caller before you write it. Never claim to have "
    "completed an action you have not actually taken with a tool. The clinic is open "
    "Monday to Friday, eight in the morning until six in the evening, and Saturday "
    "from nine until one. It is closed on Sundays and public holidays."
)

TOOLS_ANTHROPIC = [
    {
        "name": "find_appointment_slots",
        "description": "Find open appointment slots for a clinician or department.",
        "input_schema": {
            "type": "object",
            "properties": {
                "department": {"type": "string", "description": "e.g. family medicine"},
                "earliest_date": {"type": "string", "description": "ISO date"},
                "preferred_time_of_day": {"type": "string", "enum": ["morning", "afternoon", "any"]},
            },
            "required": ["department", "earliest_date"],
        },
    },
    {
        "name": "book_appointment",
        "description": "Book a specific slot for a patient. Only call after confirming details aloud.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slot_id": {"type": "string"},
                "patient_name": {"type": "string"},
                "patient_dob": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["slot_id", "patient_name", "patient_dob"],
        },
    },
    {
        "name": "take_message",
        "description": "Leave a message for a clinician to review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "clinician": {"type": "string"},
                "patient_name": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["patient_name", "message"],
        },
    },
]

CASES = {
    # 1. Plain conversational turn — the commonest thing a voice agent does.
    "chitchat": {
        "messages": [
            {"role": "user", "content": "Hi there, um, are you open on Saturdays?"},
        ],
        "tools": False,
    },
    # 2. A refusal boundary that still has to offer a path forward. Tests whether
    #    the model stalls before speaking on a turn requiring judgement.
    "refusal_boundary": {
        "messages": [
            {"role": "user", "content": "I've had a headache for two days. What should I take for it?"},
        ],
        "tools": False,
    },
    # 3. Tool-selection turn with a full schema set and prior history — the shape
    #    where thinking models spend the most time before saying anything.
    "tool_turn": {
        "messages": [
            {"role": "user", "content": "Hi, I need to see someone about my knee."},
            {"role": "assistant", "content": "Of course. Are you looking for family medicine, or should I check orthopaedics?"},
            {"role": "user", "content": "Family medicine is fine. Anytime next week in the morning would work, my name is Dana Whitfield, born third of March nineteen eighty-one."},
        ],
        "tools": True,
    },
}

# ── arms ─────────────────────────────────────────────────────────────────────
# (name, vendor, model, extra request config)
ARMS: list[tuple[str, str, str, dict]] = [
    # Production baseline
    ("haiku-4-5 (prod)",            "anthropic", "claude-haiku-4-5",       {}),
    # Plain ladder, default config
    ("sonnet-5 default",            "anthropic", "claude-sonnet-5",        {}),
    ("opus-5 default",              "anthropic", "claude-opus-5",          {}),
    ("fable-5 default",             "anthropic", "claude-fable-5",         {}),
    # Voice candidates: effort is the latency lever, not model tier
    ("opus-5 low",                  "anthropic", "claude-opus-5",          {"effort": "low"}),
    ("opus-5 low no-think",         "anthropic", "claude-opus-5",          {"effort": "low", "thinking": {"type": "disabled"}}),
    ("opus-5 medium",               "anthropic", "claude-opus-5",          {"effort": "medium"}),
    # Fast mode: request shape verified correct (beta flag + top-level `speed`),
    # but the org is provisioned 0 fast-mode input tokens/min, so these 429 on
    # every call. Left in place so the report can say "blocked", not "untested".
    ("opus-5 low FAST",             "anthropic", "claude-opus-5",          {"effort": "low", "speed": "fast"}),
    ("sonnet-5 low",                "anthropic", "claude-sonnet-5",        {"effort": "low"}),
    ("sonnet-5 low no-think",       "anthropic", "claude-sonnet-5",        {"effort": "low", "thinking": {"type": "disabled"}}),
    # Gemini — the brain inside our cascade, not the Live API
    ("g3.5-flash default(med)",     "gemini",    "gemini-3.5-flash",       {}),
    ("g3.5-flash minimal",          "gemini",    "gemini-3.5-flash",       {"thinking_level": "minimal"}),
    ("g3.5-flash low",              "gemini",    "gemini-3.5-flash",       {"thinking_level": "low"}),
    ("g3.5-flash-lite minimal",     "gemini",    "gemini-3.5-flash-lite",  {"thinking_level": "minimal"}),
    ("g3-flash-preview (prod fo)",  "gemini",    "gemini-3-flash-preview", {}),
]


def _anthropic_body(model: str, case: dict, cfg: dict, max_tokens: int) -> dict:
    body: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM,
        "messages": case["messages"],
        "stream": True,
    }
    if case["tools"]:
        body["tools"] = TOOLS_ANTHROPIC
    if cfg.get("effort"):
        body["output_config"] = {"effort": cfg["effort"]}
    if cfg.get("thinking"):
        body["thinking"] = cfg["thinking"]
    if cfg.get("speed"):
        body["speed"] = cfg["speed"]
    return body


def _gemini_body(case: dict, cfg: dict, max_tokens: int) -> dict:
    contents = []
    for m in case["messages"]:
        contents.append({
            "role": "model" if m["role"] == "assistant" else "user",
            "parts": [{"text": m["content"]}],
        })
    body: dict = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if cfg.get("thinking_level"):
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": cfg["thinking_level"]}
    if case["tools"]:
        body["tools"] = [{"function_declarations": [
            {"name": t["name"], "description": t["description"],
             "parameters": {k: v for k, v in t["input_schema"].items()}}
            for t in TOOLS_ANTHROPIC
        ]}]
    return body


async def run_anthropic(client, model, case, cfg, max_tokens) -> dict:
    key = os.environ["ANTHROPIC_API_KEY"]
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    if cfg.get("speed"):
        headers["anthropic-beta"] = "fast-mode-2026-02-01"
    body = _anthropic_body(model, case, cfg, max_tokens)

    t0 = time.perf_counter()
    ttft = ttfs = None
    text = ""
    usage: dict = {}
    stop_reason = None
    served_speed = None
    saw_tool = False
    async with client.stream("POST", ANTHROPIC_URL, headers=headers, json=body) as r:
        if r.status_code >= 300:
            raw = await r.aread()
            return {"error": f"HTTP {r.status_code}: {raw.decode()[:300]}"}
        async for line in r.aiter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            t = ev.get("type")
            if t == "content_block_start" and ev.get("content_block", {}).get("type") == "tool_use":
                saw_tool = True
                if ttft is None:
                    ttft = (time.perf_counter() - t0) * 1000
            if t == "content_block_delta":
                d = ev.get("delta", {})
                if d.get("type") == "text_delta":
                    if ttft is None:
                        ttft = (time.perf_counter() - t0) * 1000
                    text += d["text"]
                    if ttfs is None and SENTENCE_END.search(text):
                        ttfs = (time.perf_counter() - t0) * 1000
            elif t == "message_start":
                u = ev.get("message", {}).get("usage") or {}
                usage.update(u)
                served_speed = u.get("speed") or served_speed
            elif t == "message_delta":
                usage.update(ev.get("usage") or {})
                stop_reason = (ev.get("delta") or {}).get("stop_reason") or stop_reason
                served_speed = (ev.get("usage") or {}).get("speed") or served_speed
    total = (time.perf_counter() - t0) * 1000
    out_tok = int(usage.get("output_tokens") or 0)
    # Anthropic folds thinking into output_tokens (so pricing is already right)
    # but breaks it out here — which is what tells us WHERE the TTFT went.
    think = int((usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
    return {
        "ttft_ms": ttft, "ttfs_ms": ttfs if ttfs is not None else ttft, "total_ms": total,
        "in_tokens": int(usage.get("input_tokens") or 0),
        "out_tokens": out_tok,
        "thinking_tokens": think,
        "stop_reason": stop_reason, "served_speed": served_speed,
        "tool_call": saw_tool, "chars": len(text), "text": text[:400],
    }


async def run_gemini(client, model, case, cfg, max_tokens) -> dict:
    key = os.environ["GEMINI_API_KEY"]
    url = GEMINI_URL.format(model=model, key=key)
    body = _gemini_body(case, cfg, max_tokens)

    t0 = time.perf_counter()
    ttft = ttfs = None
    text = ""
    um: dict = {}
    finish = None
    saw_tool = False
    async with client.stream("POST", url, json=body,
                             headers={"content-type": "application/json"}) as r:
        if r.status_code >= 300:
            raw = await r.aread()
            return {"error": f"HTTP {r.status_code}: {raw.decode()[:300]}"}
        async for line in r.aiter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            um = ev.get("usageMetadata") or um
            for cand in ev.get("candidates") or []:
                finish = cand.get("finishReason") or finish
                for part in (cand.get("content") or {}).get("parts") or []:
                    # A thought part is flagged; it is NOT what the caller hears.
                    if part.get("thought"):
                        continue
                    if part.get("functionCall"):
                        saw_tool = True
                        if ttft is None:
                            ttft = (time.perf_counter() - t0) * 1000
                    if part.get("text"):
                        if ttft is None:
                            ttft = (time.perf_counter() - t0) * 1000
                        text += part["text"]
                        if ttfs is None and SENTENCE_END.search(text):
                            ttfs = (time.perf_counter() - t0) * 1000
    total = (time.perf_counter() - t0) * 1000
    thoughts = int(um.get("thoughtsTokenCount") or 0)
    return {
        "ttft_ms": ttft, "ttfs_ms": ttfs if ttfs is not None else ttft, "total_ms": total,
        "in_tokens": int(um.get("promptTokenCount") or 0),
        # thinking bills at the output rate and is excluded from candidatesTokenCount
        "out_tokens": int(um.get("candidatesTokenCount") or 0) + thoughts,
        "thinking_tokens": thoughts,
        "stop_reason": finish, "served_speed": None,
        "tool_call": saw_tool, "chars": len(text), "text": text[:400],
    }


def price_key(model: str, cfg: dict) -> str:
    if cfg.get("speed") == "fast" and model == "claude-opus-5":
        return "claude-opus-5@fast"
    return model


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--out", default="ttft_results.json")
    ap.add_argument("--only", default=None, help="substring filter on arm name")
    args = ap.parse_args()

    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        if not os.environ.get(k):
            sys.exit(f"{k} is not set")

    arms = [a for a in ARMS if not args.only or args.only in a[0]]
    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=180.0) as client:
        for name, vendor, model, cfg in arms:
            for case_name, case in CASES.items():
                for rep in range(args.reps):
                    fn = run_anthropic if vendor == "anthropic" else run_gemini
                    try:
                        r = await fn(client, model, case, cfg, args.max_tokens)
                    except Exception as exc:  # noqa: BLE001
                        r = {"error": f"{type(exc).__name__}: {exc}"}
                    r.update(arm=name, vendor=vendor, model=model, cfg=cfg,
                             case=case_name, rep=rep)
                    if not r.get("error"):
                        pin, pout = PRICING[price_key(model, cfg)]
                        r["cost_usd"] = (r["in_tokens"] / 1e6 * pin
                                         + r["out_tokens"] / 1e6 * pout)
                    rows.append(r)
                    tag = "ERR" if r.get("error") else (
                        f"ttft={r['ttft_ms']:.0f}ms total={r['total_ms']:.0f}ms "
                        f"out={r['out_tokens']}"
                    )
                    print(f"{name:28s} {case_name:17s} r{rep} {tag}", flush=True)
                    if r.get("error"):
                        print(f"    {r['error'][:220]}", flush=True)
                        break  # a config that errors errors every time
    pathlib.Path(args.out).write_text(json.dumps(rows, indent=1))

    # ── summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 118)
    hdr = (f"{'arm':28s} {'ttft p50':>9s} {'ttft p95':>9s} {'ttfs p50':>9s} "
           f"{'total p50':>10s} {'out tok':>8s} {'$/1k turns':>11s} {'errors':>7s}")
    print(hdr)
    print("-" * 118)
    for name, _, _, _ in arms:
        ok = [r for r in rows if r["arm"] == name and not r.get("error")
              and r.get("ttft_ms")]
        bad = [r for r in rows if r["arm"] == name and r.get("error")]
        if not ok:
            print(f"{name:28s} {'—':>9s} {'—':>9s} {'—':>9s} {'—':>10s} "
                  f"{'—':>8s} {'—':>11s} {len(bad):>7d}")
            continue
        q = lambda vals, p: (sorted(vals)[min(len(vals) - 1, int(len(vals) * p))])  # noqa: E731
        ttft = [r["ttft_ms"] for r in ok]
        ttfs = [r["ttfs_ms"] for r in ok if r["ttfs_ms"]]
        tot = [r["total_ms"] for r in ok]
        cost = statistics.mean(r["cost_usd"] for r in ok) * 1000
        print(f"{name:28s} {q(ttft,.5):9.0f} {q(ttft,.95):9.0f} "
              f"{(q(ttfs,.5) if ttfs else 0):9.0f} {q(tot,.5):10.0f} "
              f"{statistics.mean(r['out_tokens'] for r in ok):8.0f} "
              f"{cost:11.2f} {len(bad):7d}")
    print("=" * 118)
    print(f"\nraw -> {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
