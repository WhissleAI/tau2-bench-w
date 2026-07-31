"""Whissle agent for tau2-bench (τ³).

A half-duplex agent whose brain is the real Whissle platform agent, reached over the
API (POST {WHISSLE_BASE}/api/bench/agent-turn). Whissle decides each turn's action
(text or tool calls) with its real prompt + model + guardrails; tau2 owns the tools,
task DB, and scoring. Text modality; the voice modality reuses the same agent over
the voice pipeline.

Env:
  WHISSLE_BASE      default https://aws-gateway-backend.whissle.ai/bot
  WHISSLE_AGENT_ID  a configured agent in your org (prompt overridden with the domain policy)
  WHISSLE_API_KEY   a wsk_ key for that org
  WHISSLE_MODEL     optional real model override (else the agent's configured model)
"""
import json
import os
import time
from typing import List, Optional

import requests
from loguru import logger
from pydantic import BaseModel

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

AGENT_INSTRUCTION = (
    "You are a customer service agent that helps the user according to the <policy> "
    "provided below. In each turn you can EITHER send a message to the user OR make "
    "tool calls — never both. Always follow the policy."
)


class WhissleState(BaseModel):
    # Anthropic-style history for /api/bench (the endpoint supplies nothing stateful).
    messages: list = []


class WhissleAgent(HalfDuplexAgent[WhissleState]):
    def __init__(self, tools: List[Tool], domain_policy: str):
        super().__init__(tools=tools, domain_policy=domain_policy)
        self.base = (os.getenv("WHISSLE_BASE") or "https://aws-gateway-backend.whissle.ai/bot").rstrip("/")
        self.agent_id = os.getenv("WHISSLE_AGENT_ID")
        self.api_key = os.getenv("WHISSLE_API_KEY")
        self.model = os.getenv("WHISSLE_MODEL") or None
        if not self.agent_id or not self.api_key:
            raise ValueError("WHISSLE_AGENT_ID and WHISSLE_API_KEY are required")
        self._tools = [self._to_anthropic(t) for t in tools]
        self._system = (
            f"<instructions>\n{AGENT_INSTRUCTION}\n</instructions>\n"
            f"<policy>\n{domain_policy}\n</policy>"
        )

    @staticmethod
    def _to_anthropic(t: Tool) -> dict:
        s = t.openai_schema
        fn = s.get("function", s)
        return {
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        }

    def get_init_state(self, message_history: Optional[list[Message]] = None) -> WhissleState:
        st = WhissleState(messages=[])
        for m in message_history or []:
            self._append_incoming(st, m)
        return st

    def _append_incoming(self, st: WhissleState, message) -> None:
        if isinstance(message, MultiToolMessage):
            for tm in message.tool_messages:
                self._append_incoming(st, tm)
        elif isinstance(message, UserMessage):
            st.messages.append({"role": "user", "content": message.content or ""})
        elif isinstance(message, ToolMessage):
            st.messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": message.id, "content": message.content or ""}],
            })
        elif isinstance(message, AssistantMessage):
            blocks = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for tc in message.tool_calls or []:
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            st.messages.append({"role": "assistant", "content": blocks or (message.content or "")})

    def generate_next_message(
        self, message: ValidAgentInputMessage, state: WhissleState
    ) -> tuple[AssistantMessage, WhissleState]:
        self._append_incoming(state, message)
        resp = self._turn(state.messages)
        blocks = resp.get("content") or []
        tool_calls = resp.get("tool_calls") or []
        text = (resp.get("reply") or "").strip()
        # Record the assistant turn verbatim so the next call has faithful history.
        state.messages.append({"role": "assistant", "content": blocks or text})
        # τ³ rule: an AssistantMessage has content OR tool_calls, never both.
        if tool_calls:
            am = AssistantMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(id=tc["id"], name=tc["name"], arguments=tc.get("arguments") or {}, requestor="assistant")
                    for tc in tool_calls
                ],
            )
        else:
            am = AssistantMessage(role="assistant", content=text or "I'm sorry, could you rephrase that?")
        return am, state

    def _turn(self, messages: list) -> dict:
        body = {"agent_id": self.agent_id, "messages": messages, "tools": self._tools, "system": self._system}
        if self.model:
            body["model"] = self.model
        last = "unknown"
        for attempt in range(3):
            try:
                r = requests.post(
                    f"{self.base}/api/bench/agent-turn",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    data=json.dumps(body),
                    timeout=120,
                )
                if r.status_code >= 500:
                    last = f"{r.status_code} {r.text[:120]}"
                    time.sleep(2 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except requests.exceptions.RequestException as e:
                last = str(e)
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"bench agent-turn failed after retries: {last}")


def create_whissle_agent(tools, domain_policy, **kwargs):
    return WhissleAgent(tools=tools, domain_policy=domain_policy)
