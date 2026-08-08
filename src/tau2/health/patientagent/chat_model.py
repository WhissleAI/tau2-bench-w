"""``WhissleChatModel`` — the Whissle agent brain behind a LangChain chat interface.

PatientAgentBench's assistant is a LangGraph ReAct agent built by
``langchain.agents.create_agent(model=..., tools=..., system_prompt=...)``. Anything
satisfying ``BaseChatModel`` + ``bind_tools`` can be the ``model``. That is the
apples-to-apples seam: swap ONLY the model and the benchmark's own ReAct loop, its
own 15 sandbox tools, its own system prompt and its own conversation runner are all
preserved, so the resulting number is comparable to the published baselines.

Translation is the whole job, in both directions:

  LangChain messages  ->  Anthropic-style ``messages`` + ``system``
  LangChain tools     ->  ``[{name, description, input_schema}]``
  turn response       ->  ``AIMessage(content=..., tool_calls=[...])``

The subtle part is tool results. Anthropic requires every ``tool_result`` answering a
single assistant turn's parallel ``tool_use`` blocks to arrive in ONE user message.
LangGraph emits them as N separate ``ToolMessage``s, so consecutive tool messages are
coalesced — without that, parallel tool calls (which this endpoint really does emit)
desynchronize the history and the agent loses the thread.
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Optional, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field, PrivateAttr

from tau2.health.patientagent.client import (
    TurnResponse,
    WhissleBenchClient,
    WhissleConfig,
)


def to_whissle_tool(tool: Any) -> dict[str, Any]:
    """Convert any LangChain-acceptable tool spec to the bench endpoint's schema.

    Routes through ``convert_to_openai_tool`` so StructuredTool, plain functions,
    Pydantic models and raw dicts all normalize identically.
    """
    if isinstance(tool, dict) and "input_schema" in tool and "name" in tool:
        return tool  # already in endpoint shape
    openai_tool = convert_to_openai_tool(tool)
    fn = openai_tool.get("function", openai_tool)
    return {
        "name": fn["name"],
        "description": fn.get("description", ""),
        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
    }


def _stringify(content: Any) -> str:
    """Flatten LangChain's str-or-block-list content into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return "" if content is None else str(content)


def lc_messages_to_anthropic(
    messages: Sequence[BaseMessage],
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Translate LangChain messages to (anthropic_messages, system_prompt).

    System messages are hoisted into the endpoint's dedicated ``system`` field and
    joined if there are several. Consecutive ToolMessages coalesce into one user
    message carrying several ``tool_result`` blocks (see module docstring).
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    for message in messages:
        if isinstance(message, SystemMessage):
            text = _stringify(message.content)
            if text:
                system_parts.append(text)
            continue

        if isinstance(message, ToolMessage):
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": _stringify(message.content),
            }
            # Anthropic requires the status flag on failures so the model can react.
            if getattr(message, "status", None) == "error":
                block["is_error"] = True
            # Coalesce onto the previous user turn when it is already tool results.
            if (
                out
                and out[-1]["role"] == "user"
                and isinstance(out[-1]["content"], list)
                and all(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in out[-1]["content"]
                )
            ):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue

        if isinstance(message, AIMessage):
            blocks: list[dict[str, Any]] = []
            text = _stringify(message.content)
            if text:
                blocks.append({"type": "text", "text": text})
            for call in message.tool_calls or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id"),
                        "name": call.get("name"),
                        "input": call.get("args") or {},
                    }
                )
            # An assistant turn with neither text nor calls would be an invalid
            # empty block list; keep the turn but make it explicit.
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            continue

        if isinstance(message, HumanMessage):
            out.append({"role": "user", "content": _stringify(message.content)})
            continue

        # Unknown subclass: fall back on its declared role-ish type.
        role = "assistant" if getattr(message, "type", "") == "ai" else "user"
        out.append({"role": role, "content": _stringify(message.content)})

    return out, ("\n\n".join(system_parts) if system_parts else None)


def turn_to_ai_message(response: TurnResponse) -> AIMessage:
    """Translate a decoded bench turn into a LangChain ``AIMessage``.

    LangChain names tool arguments ``args`` (Anthropic uses ``input``, our endpoint
    uses ``arguments``) — getting this wrong silently produces empty tool inputs.
    """
    tool_calls = [
        {"name": call["name"], "args": call["arguments"], "id": call["id"], "type": "tool_call"}
        for call in response.tool_calls
    ]
    return AIMessage(
        content=response.text,
        tool_calls=tool_calls,
        response_metadata={"stop_reason": response.stop_reason, "provider": "whissle"},
    )


class WhissleChatModel(BaseChatModel):
    """A LangChain chat model whose completions come from a real Whissle agent.

    Not an LLM wrapper: each call runs a turn of the deployed Whissle agent (its
    model, guardrails and org config) with the benchmark's system prompt and tool
    schema supplied per call.
    """

    agent_id: Optional[str] = None
    api_key: Optional[str] = None
    base: Optional[str] = None
    whissle_model: Optional[str] = None
    timeout_s: float = 120.0
    max_attempts: int = 3

    # Tools bound by ``bind_tools``; carried on the instance so ``create_agent``'s
    # bound copy keeps them.
    whissle_tools: list[dict[str, Any]] = Field(default_factory=list)

    _client: Optional[WhissleBenchClient] = PrivateAttr(default=None)

    @property
    def _llm_type(self) -> str:
        return "whissle-bench-agent"

    @property
    def client(self) -> WhissleBenchClient:
        if self._client is None:
            config = WhissleConfig(timeout_s=self.timeout_s, max_attempts=self.max_attempts)
            if self.base:
                config.base = self.base.rstrip("/")
            if self.agent_id:
                config.agent_id = self.agent_id
            if self.api_key:
                config.api_key = self.api_key
            if self.whissle_model:
                config.model = self.whissle_model
            self._client = WhissleBenchClient(config)
        return self._client

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "WhissleChatModel":
        """Return a copy carrying the converted tool schemas.

        ``create_agent`` calls this with the benchmark's 15 sandbox tools.
        """
        converted = [to_whissle_tool(tool) for tool in tools]
        # Copy rather than mutate: LangGraph may bind different tool subsets.
        clone = self.model_copy(update={"whissle_tools": converted})
        clone._client = self._client
        return clone

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        anthropic_messages, system = lc_messages_to_anthropic(messages)
        tools = kwargs.get("tools") or self.whissle_tools
        response = self.client.turn(
            anthropic_messages,
            system=system,
            tools=[to_whissle_tool(t) for t in tools] if tools else None,
        )
        message = turn_to_ai_message(response)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        raise NotImplementedError(
            "The bench turn endpoint is request/response; PatientAgentBench does not "
            "stream assistant turns."
        )

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "model": self.whissle_model, "base": self.base}


def whissle_tools_from_registry(tools: Sequence[BaseTool]) -> list[dict[str, Any]]:
    """Convenience: convert a PatientAgentBench tool registry's tools."""
    return [to_whissle_tool(tool) for tool in tools]


def dumps_tool_schema(tools: Sequence[Any]) -> str:
    """Stable JSON of the bound tool schemas — recorded in run artifacts so a
    published number can be traced to the exact tool surface that produced it."""
    return json.dumps([to_whissle_tool(t) for t in tools], sort_keys=True, indent=2)
