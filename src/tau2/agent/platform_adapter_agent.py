"""Tau half-duplex agent backed by an ApplianceCare platform adapter session.

The platform chooses text and tool calls. Tau's ordinary orchestrator executes
every returned tool and owns the database and score. The adapter implementation
is loaded only when this agent is selected, so ordinary Tau runs have no dependency
on the benchmark repository.
"""

from __future__ import annotations

import copy
import importlib
import json
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from tau2.agent.base_agent import HalfDuplexAgent, ValidAgentInputMessage
from tau2.data_model.message import (
    AssistantMessage,
    Message,
    MultiToolMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import Tool

BENCHMARK_ROOT_ENV = "APPLIANCE_PLATFORM_BENCHMARK_ROOT"
SESSION_FACTORY_ENV = "APPLIANCE_PLATFORM_SESSION_FACTORY"
EVIDENCE_DIR_ENV = "APPLIANCE_PLATFORM_EVIDENCE_DIR"
PLATFORM_ID_ENV = "APPLIANCE_PLATFORM_ID"
DEFAULT_SESSION_FACTORY = "platform_tau_bridge:create_session"
WITHHELD_MANUAL_TOOLS = frozenset(
    {"search_manuals", "open_manual_section", "lookup_error_code"}
)
_SAFE_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{1,63}")

SessionFactory = Callable[..., Any]


class PlatformAdapterState(BaseModel):
    """Vendor-neutral conversation history sent through the adapter."""

    messages: list[dict[str, Any]] = Field(default_factory=list)


def _build_brief(domain_policy: str) -> str:
    return (
        "You are the customer-support agent being evaluated. Follow the supplied "
        "support policy. Use the platform knowledge base for manufacturer manual "
        "facts. In each turn, either reply to the customer or request tools, never "
        "both. Tau executes the tools and returns their results.\n\n"
        f"<policy>\n{domain_policy}\n</policy>"
    )


def _load_session_factory() -> SessionFactory:
    root_value = os.getenv(BENCHMARK_ROOT_ENV)
    if not root_value:
        raise ValueError(f"{BENCHMARK_ROOT_ENV} is required")
    root = Path(root_value).expanduser().resolve()
    scripts = root / "scripts"
    if not (root / "benchmark" / "v9").is_dir() or not scripts.is_dir():
        raise ValueError(f"{BENCHMARK_ROOT_ENV} is not ApplianceCare-Bench")
    scripts_text = str(scripts)
    if scripts_text not in sys.path:
        sys.path.insert(0, scripts_text)

    spec = os.getenv(SESSION_FACTORY_ENV, DEFAULT_SESSION_FACTORY)
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"{SESSION_FACTORY_ENV} must be module:function")
    factory = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(factory):
        raise ValueError(f"session factory is not callable: {spec}")
    return factory


def _normalise_message(message: Message) -> list[dict[str, Any]]:
    if isinstance(message, MultiToolMessage):
        items: list[dict[str, Any]] = []
        for tool_message in message.tool_messages:
            items.extend(_normalise_message(tool_message))
        return items
    if isinstance(message, UserMessage):
        return [{"role": "user", "content": message.content or ""}]
    if isinstance(message, ToolMessage):
        return [
            {
                "role": "tool",
                "tool_call_id": message.id,
                "content": message.content or "",
                "error": message.error,
            }
        ]
    if isinstance(message, AssistantMessage):
        payload: dict[str, Any] = {"role": "assistant"}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": copy.deepcopy(call.arguments),
                }
                for call in message.tool_calls
            ]
        else:
            payload["content"] = message.content or ""
        return [payload]
    raise TypeError(f"unsupported Tau message: {type(message).__name__}")


class PlatformAdapterAgent(HalfDuplexAgent[PlatformAdapterState]):
    """Translate Tau messages to one vendor-neutral platform session."""

    strict_cleanup = True

    def __init__(
        self,
        tools: list[Tool],
        domain_policy: str,
        *,
        task=None,
        session_factory: SessionFactory | None = None,
        platform_id: str | None = None,
        evidence_dir: Path | None = None,
    ):
        super().__init__(tools=tools, domain_policy=domain_policy)
        self.task = task
        self.platform_id = platform_id or os.getenv(PLATFORM_ID_ENV)
        if not self.platform_id or not _SAFE_ID.fullmatch(self.platform_id):
            raise ValueError(
                f"{PLATFORM_ID_ENV} must be a safe 2-64 character identifier"
            )
        self.session_id = str(uuid.uuid4())
        self.evidence_dir = evidence_dir
        if self.evidence_dir is None and os.getenv(EVIDENCE_DIR_ENV):
            self.evidence_dir = (
                Path(os.environ[EVIDENCE_DIR_ENV]).expanduser().resolve()
            )
        schemas = [
            copy.deepcopy(tool.openai_schema)
            for tool in tools
            if tool.name not in WITHHELD_MANUAL_TOOLS
        ]
        factory = session_factory or _load_session_factory()
        self.session = factory(
            brief=_build_brief(domain_policy),
            tool_schemas=schemas,
            task=task,
        )
        self._opened = False
        self._stopped = False
        self._last_assistant_message: AssistantMessage | None = None

    def get_init_state(
        self, message_history: Optional[list[Message]] = None
    ) -> PlatformAdapterState:
        if self._opened:
            raise RuntimeError("platform adapter agent was initialized twice")
        self.session.open()
        self._opened = True
        state = PlatformAdapterState()
        for message in message_history or []:
            state.messages.extend(_normalise_message(message))
        return state

    def generate_next_message(
        self, message: ValidAgentInputMessage, state: PlatformAdapterState
    ) -> tuple[AssistantMessage, PlatformAdapterState]:
        if not self._opened or self._stopped:
            raise RuntimeError("platform adapter agent is not active")
        state.messages.extend(_normalise_message(message))
        result = self.session.turn(state.messages)
        turn_evidence = copy.deepcopy(self.session.turns[-1])
        raw_data = {
            "served_model": result.served_model,
            "platform_id": self.platform_id,
            "platform_session_id": self.session_id,
            "platform_turn": turn_evidence,
        }
        if result.tool_calls:
            assistant = AssistantMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id=call.id,
                        name=call.name,
                        arguments=copy.deepcopy(call.arguments),
                        requestor="assistant",
                    )
                    for call in result.tool_calls
                ],
                usage=result.usage,
                cost=result.cost,
                raw_data=raw_data,
                generation_time_seconds=result.latency_ms / 1000,
            )
        else:
            assistant = AssistantMessage(
                role="assistant",
                content=result.message,
                usage=result.usage,
                cost=result.cost,
                raw_data=raw_data,
                generation_time_seconds=result.latency_ms / 1000,
            )
        state.messages.extend(_normalise_message(assistant))
        self._last_assistant_message = assistant
        return assistant, state

    def stop(
        self,
        message: Optional[ValidAgentInputMessage] = None,
        state: Optional[PlatformAdapterState] = None,
    ) -> None:
        if self._stopped or not self._opened:
            return
        teardown = self.session.close()
        evidence = {
            "platform_id": self.platform_id,
            "platform_session_id": self.session_id,
            "task_id": getattr(self.task, "id", None),
            **self.session.evidence(),
        }
        if self._last_assistant_message is not None:
            raw_data = self._last_assistant_message.raw_data or {}
            raw_data["platform_teardown"] = {
                "agent_deleted": teardown.agent_deleted,
                "knowledge_base_deleted": teardown.knowledge_base_deleted,
                "errors": list(teardown.errors),
            }
            self._last_assistant_message.raw_data = raw_data
        if self.evidence_dir is not None:
            self._write_evidence(evidence)
        self._stopped = True

    def _write_evidence(self, evidence: dict[str, Any]) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        task_id = getattr(self.task, "id", None) or "unknown-task"
        destination = self.evidence_dir / f"{task_id}__{self.session_id}.json"
        if destination.exists():
            raise RuntimeError(
                f"refusing to overwrite platform evidence: {destination}"
            )
        payload = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.evidence_dir,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            temp_path.replace(destination)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()


def create_platform_adapter_agent(tools, domain_policy, **kwargs):
    """Factory registered as ``platform_adapter`` in Tau."""
    return PlatformAdapterAgent(
        tools=tools,
        domain_policy=domain_policy,
        task=kwargs.get("task"),
    )
