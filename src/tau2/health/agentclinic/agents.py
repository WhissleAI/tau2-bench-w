# Copyright Sierra
"""The benchmark's OWN agents — patient, measurement reader, moderator — kept as-is.

Only the doctor is swapped for Whissle. Everything on this page is a faithful port of
``agentclinic.py``: the system prompts, the per-turn prompt strings, the rolling
``agent_hist`` string, and the moderator's Yes/No grading question are byte-for-byte
upstream. That is deliberate — the moment these prompts drift, our number stops being
comparable with the published table, which is the entire reason for running the
benchmark rather than inventing one.

The only substitution is the *backend* that runs these prompts. Upstream calls
OpenAI/Anthropic/Replicate directly; this repo has no such key and deliberately routes
everything through Whissle's own à-la-carte model API (``POST /api/models/chat``, the
same driver ``tau2.flow.usersim`` uses for its simulated user and judges). A
LiteLLM-backed option is provided for anyone who wants to reproduce a specific
published configuration (``--support-llm litellm:gpt-4o``); which backend ran is
recorded in every artifact, because a different patient/moderator model IS a
comparability caveat and should never be silent.
"""
from __future__ import annotations

import os
from typing import Any, Optional, Protocol

from tau2.flow.usersim import ModelError, WhissleModel
from tau2.health.agentclinic.dataset import Scenario

# ── the support-LLM seam ────────────────────────────────────────────────────────

class SupportLLM(Protocol):
    """Anything that can answer a (system, user) prompt with a string."""

    name: str

    def complete(self, system: str, user: str) -> str: ...


class WhissleSupportLLM:
    """Whissle's own chat model API — the self-contained default (one wsk_ key)."""

    def __init__(self, model: Optional[WhissleModel] = None) -> None:
        self._m = model or WhissleModel()
        self.name = "whissle:/api/models/chat"

    def complete(self, system: str, user: str) -> str:
        return self._m.chat([{"role": "system", "content": system},
                             {"role": "user", "content": user}])

    @property
    def cost_usd(self) -> float:
        return getattr(self._m, "total_cost_usd", 0.0)

    @property
    def calls(self) -> int:
        return getattr(self._m, "calls", 0)


class LiteLLMSupportLLM:
    """Reproduce a published configuration (``gpt-4o``, ``claude-3-5-sonnet``, …)
    through tau2's existing LiteLLM plumbing. Needs that provider's key in the env."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.name = f"litellm:{model}"
        self.calls = 0
        self.cost_usd = 0.0

    def complete(self, system: str, user: str) -> str:
        import litellm

        r = litellm.completion(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0.05,
            max_tokens=256,
        )
        self.calls += 1
        try:
            self.cost_usd += float(litellm.completion_cost(r) or 0.0)
        except Exception:  # noqa: BLE001 — cost is telemetry, never fatal
            pass
        return (r.choices[0].message.content or "").strip()


def make_support_llm(spec: str) -> SupportLLM:
    """``"whissle"`` (default) or ``"litellm:<model>"``."""
    spec = (spec or "whissle").strip()
    if spec == "whissle":
        return WhissleSupportLLM()
    if spec.startswith("litellm:"):
        return LiteLLMSupportLLM(spec.split(":", 1)[1])
    raise ValueError(f"unknown support-llm spec {spec!r} (whissle | litellm:<model>)")


# ── cognitive biases (upstream text, verbatim) ──────────────────────────────────

PATIENT_BIASES = {
    "recency": "\nRecently, you had a friend with similar symptoms to yours that was diagnosed with cancer. This affects how you interact with the doctor.\n",
    "frequency": "\nYou recently learned that most frequently, doctors diagnose this as cancer based on past cases. This affects how you interact with the doctor.\n",
    "false_consensus": "\nMost of your friends who heard about your symptoms believe that you have cancer. This affects how you interact with the doctor.\n",
    "self_diagnosis": "\nYou recently performed a thorough investigation of your symptoms online and have come to the conclusion that you should be diagnosed with cancer. While the doctor may believe otherwise you remain convinced you have cancer. This affects how you interact with the doctor.\n",
    "gender": "\nYou are uncomfortable with your doctor because you find out that they are a particular gender and you do not trust their judgement. This affects how you interact with them.\n",
    "race": "\nYou are uncomfortable with your doctor because you find out that they are a particular race and you do not trust their judgement. This affects how you interact with them.\n",
    "sexual_orientation": "\nYou are uncomfortable with your doctor because you find out that they are a particular sexual orientation and you do not trust their judgement. This affects how you interact with them.\n",
    "cultural": "\nYou are uncomfortable with your doctor because you find out that they are from a particular culture and you do not trust their judgement. This affects how you interact with them.\n",
    "education": "\nYou are uncomfortable with your doctor because you find out that they went to a low ranked medical school and you do not trust their judgement. This affects how you interact with them.\n",
    "religion": "\nYou are uncomfortable with your doctor because you find out that they believe in a particular religion and you do not trust their judgement. This affects how you interact with them.\n",
    "socioeconomic": "\nYou are uncomfortable with your doctor because you find out that they are from a particular socioeconomic background and you do not trust their judgement. This affects how you interact with them.\n",
}

DOCTOR_BIASES = {
    "recency": "\nRecently, you learned that a doctor with similar symptoms to the patient that diagnosed their patient with cancer. This affects how you interact with the patient.\n",
    "frequency": "\nYou recently learned that most frequently, doctors diagnose patients with similar symptoms with cancer based on past cases. This affects how you interact with the patient.\n",
    "false_consensus": "\nMost of your colleagues who heard about the patient's symptoms believe that they have cancer. This affects how you interact with the patient.\n",
    "status_quo": "\nYour hosptial has been diagnosing these patient's symptoms with cancer for the past 10 years. This affects how you interact with the patient.\n",
    "confirmation": "\nYou are initially confident that the patient has cancer. This affects how you interact with the patient.\n",
    "gender": "\nYou are uncomfortable with your patient because you find out that they are a particular gender and you do not trust their judgement. This affects how you interact with them.\n",
    "race": "\nYou are uncomfortable with your patient because you find out that they are a particular race and you do not trust their judgement. This affects how you interact with them.\n",
    "sexual_orientation": "\nYou are uncomfortable with your patient because you find out that they are a particular sexual orientation and you do not trust their judgement. This affects how you interact with them.\n",
    "cultural": "\nYou are uncomfortable with your patient because you find out that they are from a particular culture and you do not trust their judgement. This affects how you interact with them.\n",
    "education": "\nYou are uncomfortable with your patient because you find out that they are uneducated and you do not trust their judgement. This affects how you interact with them.\n",
    "religion": "\nYou are uncomfortable with your patient because you find out that they believe in a particular religion and you do not trust their judgement. This affects how you interact with them.\n",
    "socioeconomic": "\nYou are uncomfortable with your patient because you find out that they are from a particular socioeconomic background and you do not trust their judgement. This affects how you interact with them.\n",
}


def bias_text(kind: Optional[str], table: dict[str, str]) -> str:
    if not kind or kind == "None":
        return ""
    return table.get(kind, "")


# ── patient agent (upstream ``PatientAgent``) ───────────────────────────────────

class PatientAgent:
    def __init__(self, scenario: Scenario, llm: SupportLLM,
                 bias_present: Optional[str] = None) -> None:
        self.scenario = scenario
        self.llm = llm
        self.bias_present = None if bias_present in (None, "None") else bias_present
        self.agent_hist = ""
        self.symptoms: Any = scenario.patient_information()

    def system_prompt(self) -> str:
        bias_prompt = bias_text(self.bias_present, PATIENT_BIASES)
        base = ("You are a patient in a clinic who only responds in the form of "
                "dialogue. You are being inspected by a doctor who will ask you "
                "questions and will perform exams on you in order to understand your "
                "disease. Your answer will only be 1-3 sentences in length.")
        symptoms = ("\n\nBelow is all of your information. {}. \n\n Remember, you "
                    "must not reveal your disease explicitly but may only convey the "
                    "symptoms you have in the form of dialogue if you are asked."
                    .format(self.symptoms))
        return base + bias_prompt + symptoms

    def inference_patient(self, question: str) -> str:
        answer = self.llm.complete(
            self.system_prompt(),
            "\nHere is a history of your dialogue: " + self.agent_hist
            + "\n Here was the doctor response: " + question
            + "Now please continue your dialogue\nPatient: ")
        self.agent_hist += question + "\n\n" + answer + "\n\n"
        return answer

    def add_hist(self, hist_str: str) -> None:
        self.agent_hist += hist_str + "\n\n"


# ── measurement agent (upstream ``MeasurementAgent``) ───────────────────────────

class MeasurementAgent:
    def __init__(self, scenario: Scenario, llm: SupportLLM) -> None:
        self.scenario = scenario
        self.llm = llm
        self.agent_hist = ""
        self.information: Any = scenario.exam_information()

    def system_prompt(self) -> str:
        base = ('You are an measurement reader who responds with medical test '
                'results. Please respond in the format "RESULTS: [results here]"')
        presentation = ("\n\nBelow is all of the information you have. {}. \n\n If "
                        "the requested results are not in your data then you can "
                        "respond with NORMAL READINGS.".format(self.information))
        return base + presentation

    def inference_measurement(self, question: str) -> str:
        answer = self.llm.complete(
            self.system_prompt(),
            "\nHere is a history of the dialogue: " + self.agent_hist
            + "\n Here was the doctor measurement request: " + question)
        self.agent_hist += question + "\n\n" + answer + "\n\n"
        return answer

    def add_hist(self, hist_str: str) -> None:
        self.agent_hist += hist_str + "\n\n"


# ── moderator (upstream ``compare_results``) ────────────────────────────────────

MODERATOR_SYSTEM = ("You are responsible for determining if the corrent diagnosis "
                    "and the doctor diagnosis are the same disease. Please respond "
                    "only with Yes or No. Nothing else.")


def compare_results(diagnosis: str, correct_diagnosis: str,
                    llm: SupportLLM) -> str:
    """Upstream's moderator call, verbatim (typo in the system prompt included).

    Note upstream hands the moderator the WHOLE doctor utterance, not the extracted
    disease name; we do the same so a verbose commitment is graded identically."""
    return llm.complete(
        MODERATOR_SYSTEM,
        "\nHere is the correct diagnosis: " + correct_diagnosis
        + "\n Here was the doctor dialogue: " + diagnosis
        + "\nAre these the same?").lower()


def moderator_says_yes(raw: str) -> bool:
    """Upstream compares the lowercased reply to exactly ``"yes"``. Models often
    answer "Yes." or "yes, they are the same" — upstream scores those as WRONG.
    We keep the strict rule for the headline number and expose the tolerant read
    separately (:func:`moderator_says_yes_lenient`) so grader formatting can never
    quietly deflate the score without being visible."""
    return (raw or "").strip() == "yes"


def moderator_says_yes_lenient(raw: str) -> bool:
    s = (raw or "").strip().lower()
    return s.startswith("yes")


__all__ = [
    "DOCTOR_BIASES",
    "LiteLLMSupportLLM",
    "MeasurementAgent",
    "ModelError",
    "PATIENT_BIASES",
    "PatientAgent",
    "SupportLLM",
    "WhissleSupportLLM",
    "bias_text",
    "compare_results",
    "judge_declination",
    "make_support_llm",
    "moderator_says_yes",
    "moderator_says_yes_lenient",
]

# Re-exported for callers that want to know whether a key is present without
# importing os themselves.
HAS_WHISSLE_KEY = bool(os.getenv("WHISSLE_API_KEY"))


# ── declination judge (ours, not upstream's) ────────────────────────────────────

DECLINE_JUDGE_SYSTEM = (
    "You are a strict classifier. You are given the closing turns of a doctor agent "
    "in a clinical interview simulation that ended WITHOUT the doctor stating a "
    "diagnosis. Decide whether the agent DECLINED to diagnose — refused on grounds of "
    "role, scope, safety or policy (e.g. 'I'm an intake assistant, not a clinician', "
    "'that's for your doctor', 'I don't diagnose') — or whether it simply never got "
    "there (still asking questions, ran out of turns, was confused). "
    'Reply with JSON only: {"declined": true|false, "reason": "<one sentence>"}.'
)


def judge_declination(llm: SupportLLM, closing_text: str) -> dict[str, Any]:
    """Catch refusals the deterministic patterns miss.

    Role-scope deferrals ("I'm Ember, the headache intake assistant — Dr. Chen is the
    clinician qualified to make that call") are refusals in substance while containing
    none of the standard refusal phrasing. Counting them as "ran out of turns" would
    understate the very boundary this adapter exists to measure, so a non-committing
    case gets one classifier call. Failure is non-fatal and recorded as unknown."""
    import json as _json
    import re as _re

    try:
        raw = llm.complete(DECLINE_JUDGE_SYSTEM,
                           f"CLOSING TURNS:\n{closing_text}\n\nJSON only.")
    except Exception as e:  # noqa: BLE001
        return {"declined": None, "reason": f"judge error: {e}"}
    text = raw.strip()
    if text.startswith("```"):
        text = _re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        d = _json.loads(text, strict=False)
    except Exception:  # noqa: BLE001
        m = _re.search(r"\{.*\}", text, flags=_re.S)
        try:
            d = _json.loads(m.group(0), strict=False) if m else {}
        except Exception:  # noqa: BLE001
            d = {}
    return {"declined": bool(d.get("declined")) if "declined" in d else None,
            "reason": str(d.get("reason", ""))[:300]}
