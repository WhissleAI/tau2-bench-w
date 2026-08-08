"""PatientAgentBench assistant agents backed by Whissle. TWO DISTINCT MODES.

Conflating these two numbers would be dishonest, so they are separate classes with
separate registry names and the reports label every run with its mode.

``whissle`` — HARNESS-TOOLS mode (default, the number we publish)
    The benchmark's own LangGraph ReAct loop, its own 15 sandbox tools and its own
    system prompt, with ONLY the model swapped for the Whissle agent brain. This is
    apples-to-apples with the published baselines: everything except the brain is
    theirs. It measures the BRAIN.

``whissle-native`` — AGENT-TOOLS mode (product measurement, NOT comparable)
    The deployed Whissle agent answers with its own prompt, its own registered tools
    and its own guardrails; the harness's sandbox tools are not bound. It measures
    the PRODUCT. Its scores must never be quoted against the paper's leaderboard:
    the agent is not solving the same task surface, and dimensions that grade
    sandbox tool use (notably workflow accuracy) are scoring a different substrate.

Both are registered into PatientAgentBench's assistant registry by ``register.py``,
which is why this adapter needs no fork of their (CC-BY-NC, no-PRs) repository.
"""

from __future__ import annotations

import os
from typing import Any, Optional, Sequence

from tau2.health.patientagent.chat_model import (
    WhissleChatModel,
    lc_messages_to_anthropic,
    turn_to_ai_message,
)
from tau2.health.patientagent.client import WhissleInfraError
from tau2.health.patientagent.scoring import INFRA_MARKER

# INFRA_MARKER is defined in scoring.py (the module that consumes it) and re-exported
# here for the agent classes that stamp it onto transport errors.

HARNESS_TOOLS_MODE = "harness_tools"
AGENT_TOOLS_MODE = "agent_tools"


def _base_assistant_agent() -> type:
    """Import PatientAgentBench's ABC lazily.

    The adapter lives in the tau2 tree but runs inside the PatientAgentBench venv
    (their pinned langchain 1.x conflicts with tau2's 0.3.x), so this import must
    not happen at tau2 import time.
    """
    from patient_agent_bench.assistant_agent.base import BaseAssistantAgent

    return BaseAssistantAgent


def _assistant_agent_error() -> type:
    from patient_agent_bench.assistant_agent.default_agent import AssistantAgentError

    return AssistantAgentError


def _make_chat_model(model_config: Any) -> WhissleChatModel:
    """Build the chat model, letting the benchmark config override env defaults.

    A custom model spec in their config JSON can carry ``model_id`` (used as the
    Whissle model override) — everything else comes from ``WHISSLE_*`` env.
    """
    whissle_model = os.getenv("WHISSLE_MODEL") or None
    # Their ModelConfig always has model_id; treat the sentinel "whissle" as "use
    # the agent's own configured model" rather than a real override.
    configured = getattr(model_config, "model_id", None)
    if configured and configured not in ("whissle", "whissle-agent"):
        whissle_model = configured
    return WhissleChatModel(
        whissle_model=whissle_model,
        timeout_s=float(os.getenv("WHISSLE_TIMEOUT_S", "120")),
        max_attempts=int(os.getenv("WHISSLE_MAX_ATTEMPTS", "3")),
    )


def build_agent_classes() -> tuple[type, type]:
    """Construct both agent classes against the (lazily imported) PAB base class."""
    base = _base_assistant_agent()
    agent_error = _assistant_agent_error()

    class WhissleAssistantAgent(base):  # type: ignore[misc,valid-type]
        """HARNESS-TOOLS mode — their ReAct loop, their tools, our brain."""

        NAME = "whissle"
        WHISSLE_MODE = HARNESS_TOOLS_MODE

        def __init__(
            self,
            model_config: Any,
            current_datetime: str,
            tools: Optional[Sequence[Any]] = None,
            prompt_name: Optional[str] = None,
            system_prompt: Optional[str] = None,
            role_arn: Optional[str] = None,
        ) -> None:
            from patient_agent_bench.assistant_agent.default_prompt import (
                SYSTEM_PROMPT as DEFAULT_PROMPT,
            )
            from patient_agent_bench.config import load_prompt
            from patient_agent_bench.tools.registry import create_tool_registry

            if not current_datetime:
                raise ValueError("current_datetime is required")

            self.model_config = model_config
            self.current_datetime = current_datetime
            self.tools = list(tools) if tools is not None else create_tool_registry().get_tools()

            # Same prompt-resolution precedence the default agent uses, so the
            # comparison holds: system_prompt > prompt_name > their default.
            if system_prompt is not None:
                self.system_prompt_template = system_prompt
            elif prompt_name is not None:
                self.system_prompt_template = load_prompt("assistant_agent", prompt_name)
            else:
                self.system_prompt_template = DEFAULT_PROMPT

            self.llm = _make_chat_model(model_config)

        def _create_agent(self, user_profile: str) -> Any:
            from langchain.agents import create_agent
            from patient_agent_bench.config import format_prompt_safe

            system_prompt = format_prompt_safe(
                self.system_prompt_template,
                user_profile=user_profile,
                current_datetime=self.current_datetime,
            )
            return create_agent(model=self.llm, tools=self.tools, system_prompt=system_prompt)

        def invoke(self, messages: list[Any], user_profile: str) -> dict[str, Any]:
            try:
                result = self._create_agent(user_profile).invoke({"messages": messages})
                return dict(result)
            except WhissleInfraError as exc:
                # Transport died -> not an agent-quality signal. Mark for exclusion.
                raise agent_error(f"{INFRA_MARKER} {exc}") from exc
            except Exception as exc:
                raise agent_error(str(exc)) from exc

        def get_tools(self) -> list[Any]:
            return self.tools

    class WhissleNativeAgent(base):  # type: ignore[misc,valid-type]
        """AGENT-TOOLS mode — the deployed Whissle agent, as a product.

        No benchmark system prompt and no benchmark tools are sent, so the agent
        keeps its own prompt, tools and guardrails. The patient record is injected
        as a leading context turn (``PAB_NATIVE_PROFILE=none`` disables it) because
        without any patient identity the agent cannot act at all.
        """

        NAME = "whissle-native"
        WHISSLE_MODE = AGENT_TOOLS_MODE

        def __init__(
            self,
            model_config: Any,
            current_datetime: str,
            tools: Optional[Sequence[Any]] = None,
            prompt_name: Optional[str] = None,
            system_prompt: Optional[str] = None,
            role_arn: Optional[str] = None,
        ) -> None:
            if not current_datetime:
                raise ValueError("current_datetime is required")
            self.model_config = model_config
            self.current_datetime = current_datetime
            # Recorded for the report, never bound to the agent.
            self.tools = list(tools) if tools is not None else []
            self.profile_mode = os.getenv("PAB_NATIVE_PROFILE", "preamble")
            self.llm = _make_chat_model(model_config)

        def invoke(self, messages: list[Any], user_profile: str) -> dict[str, Any]:
            anthropic_messages, _ = lc_messages_to_anthropic(messages)
            if self.profile_mode == "preamble" and user_profile:
                anthropic_messages = [
                    {
                        "role": "user",
                        "content": (
                            "[context] You are speaking with this patient. "
                            f"Current time: {self.current_datetime}.\n{user_profile}"
                        ),
                    },
                    {"role": "assistant", "content": [{"type": "text", "text": "Understood."}]},
                    *anthropic_messages,
                ]
            try:
                # No system, no tools: the agent is fully itself.
                response = self.llm.client.turn(anthropic_messages, system=None, tools=None)
            except WhissleInfraError as exc:
                raise agent_error(f"{INFRA_MARKER} {exc}") from exc
            except Exception as exc:
                raise agent_error(str(exc)) from exc
            # The harness appends everything beyond the input length, so return the
            # full chain with our new turn on the end.
            return {"messages": list(messages) + [turn_to_ai_message(response)]}

        def get_tools(self) -> list[Any]:
            return self.tools

    return WhissleAssistantAgent, WhissleNativeAgent
