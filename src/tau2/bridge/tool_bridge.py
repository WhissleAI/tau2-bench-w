"""An authenticated HTTP surface over ONE live tau2 :class:`Environment`.

WHY THIS EXISTS
---------------
A Whissle agent's saved flow gates tools against what is *attached to the agent*.
Per-request tool injection is silently dropped by the flow runtime, so the only
way a saved flow can drive tau2's tools is for those tools to exist on the
Whissle side as custom HTTP tools that call back into the benchmark. This module
is the callee.

The distinction that matters for scoring: the bridge executes each tool against
**the same Environment instance the runner is scoring**, not a copy. There is no
second database and no replay at call time — `get_response` mutates the live
tables and syncs, exactly as the orchestrator would.

WHY NOT :class:`tau2.environment.server.EnvironmentServer`
----------------------------------------------------------
That server is unauthenticated and, more importantly, publishes ``/user_tools/*``
alongside the agent tools. In this domain the user tools ARE the hidden state —
``read_display_code``, ``smell_check``, ``open_pump_cover``. Exposing them
to the agent under test would hand it the answer key. This bridge serves agent
tools only and has no route that can reach ``use_user_tool``.

TRUST BOUNDARY
--------------
Every request needs ``Authorization: Bearer <token>``, compared with
:func:`secrets.compare_digest`. The token comes from the environment
(``TAU_BRIDGE_TOKEN``) and is never logged, echoed, or serialized into a record.
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

from tau2.data_model.message import AssistantMessage, Message, ToolCall, ToolMessage
from tau2.environment.environment import Environment

BRIDGE_TOKEN_ENV = "TAU_BRIDGE_TOKEN"
BRIDGE_URL_ENV = "TAU_BRIDGE_PUBLIC_URL"

_JSON_TYPES: dict[str, tuple] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


@dataclass
class BridgeCallRecord:
    """One executed tool call, in the order the bridge executed it.

    ``seq`` is the bridge's own monotonic counter, and it is the ordering
    authority: Whissle may pipeline calls, so arrival order is the only order
    tau2 can honestly claim to have observed.
    """

    seq: int
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    content: str
    error: bool
    turn_hint: Optional[int] = None

    def to_messages(self) -> list[Message]:
        """The (call, result) pair as tau2 trajectory messages.

        Emitted as a tool-call ``AssistantMessage`` immediately followed by its
        ``ToolMessage``. Both evaluators depend on exactly this shape:
        ``Environment.set_state`` pops the ToolMessage that must follow each
        call and matches on id, and ``ActionEvaluator.extract_tool_calls``
        reads ``tool_calls`` off assistant messages.
        """
        call = ToolCall(
            id=self.call_id,
            name=self.tool_name,
            arguments=self.arguments,
            requestor="assistant",
        )
        return [
            AssistantMessage(role="assistant", content=None, tool_calls=[call]),
            ToolMessage(
                id=self.call_id,
                content=self.content,
                requestor="assistant",
                role="tool",
                error=self.error,
            ),
        ]


def build_trajectory_records(records: list[BridgeCallRecord]) -> list[Message]:
    """Flatten executed calls into trajectory messages, ordered by ``seq``."""
    out: list[Message] = []
    for rec in sorted(records, key=lambda r: r.seq):
        out.extend(rec.to_messages())
    return out


class BridgeError(Exception):
    """A request the bridge refused, carrying the HTTP status to return."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class ToolBridge:
    """Wraps one Environment and serves its agent tools over authenticated HTTP.

    The class is transport-agnostic on purpose: :meth:`handle` contains the whole
    contract and is directly unit-testable, while :meth:`build_app` is a thin
    FastAPI shell over it.
    """

    environment: Environment
    token: str
    _records: list[BridgeCallRecord] = field(default_factory=list)
    _drained: int = 0
    _idempotency: dict[str, BridgeCallRecord] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError(
                f"A bearer token is required; set {BRIDGE_TOKEN_ENV}. The bridge "
                "mutates the database that is being scored and is never opened "
                "unauthenticated."
            )
        self._schemas = {}
        for tool in self.environment.get_tools():
            schema = tool.openai_schema
            fn = schema.get("function", schema)
            self._schemas[fn["name"]] = fn.get("parameters") or {
                "type": "object",
                "properties": {},
            }

    # ── introspection ────────────────────────────────────────────────────────

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._schemas)

    def schema_for(self, tool_name: str) -> dict[str, Any]:
        return self._schemas[tool_name]

    # ── auth ─────────────────────────────────────────────────────────────────

    def check_auth(self, authorization: Optional[str]) -> None:
        """Constant-time bearer check. Raises :class:`BridgeError` on failure."""
        if not authorization:
            raise BridgeError(401, "missing Authorization header")
        scheme, _, presented = authorization.partition(" ")
        if scheme.lower() != "bearer" or not presented:
            raise BridgeError(401, "expected an 'Authorization: Bearer <token>' header")
        if not secrets.compare_digest(presented.strip(), self.token):
            raise BridgeError(403, "invalid bearer token")

    # ── validation ───────────────────────────────────────────────────────────

    def validate(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Check arguments against the tool's own JSON schema.

        Deliberately strict about unknown keys. A saved flow that invents an
        argument name would otherwise reach the tool and raise a TypeError deep
        in the toolkit, which reads like a benchmark bug rather than what it is.
        """
        if tool_name not in self._schemas:
            raise BridgeError(404, f"no such tool: {tool_name}")
        if not isinstance(arguments, dict):
            raise BridgeError(422, "request body must be a JSON object")

        schema = self._schemas[tool_name]
        properties = schema.get("properties") or {}
        required = schema.get("required") or []

        missing = [key for key in required if key not in arguments]
        if missing:
            raise BridgeError(
                422, f"{tool_name}: missing required argument(s): {', '.join(missing)}"
            )
        unknown = [key for key in arguments if key not in properties]
        if unknown:
            raise BridgeError(
                422, f"{tool_name}: unknown argument(s): {', '.join(sorted(unknown))}"
            )
        for key, value in arguments.items():
            expected = (properties.get(key) or {}).get("type")
            allowed = _JSON_TYPES.get(expected)
            if not allowed or value is None:
                continue
            # bool is an int subclass; a boolean passed for a number is a bug.
            if isinstance(value, bool) and expected in ("number", "integer"):
                raise BridgeError(422, f"{tool_name}: '{key}' expects {expected}")
            if not isinstance(value, allowed):
                raise BridgeError(422, f"{tool_name}: '{key}' expects {expected}")

    # ── execution ────────────────────────────────────────────────────────────

    def handle(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        authorization: Optional[str],
        idempotency_key: Optional[str] = None,
        turn_hint: Optional[int] = None,
    ) -> dict[str, Any]:
        """Authenticate, validate, execute once, record, and return the result.

        Executes through :meth:`Environment.get_response`, which is the same
        entry point the orchestrator uses — so it mutates state and calls
        ``sync_tools`` identically, and the recorded content is byte-for-byte
        what a replay of this call will produce.
        """
        self.check_auth(authorization)
        self.validate(tool_name, arguments)

        with self._lock:
            if idempotency_key is not None:
                cached = self._idempotency.get(idempotency_key)
                if cached is not None:
                    # A Whissle-side retry. Returning the cached result is the
                    # whole point: re-running create_support_case would write a
                    # second row and break the DB hash the task is scored on.
                    logger.info(
                        "bridge: replayed idempotency key for {} (seq={}) — not re-executed",
                        tool_name,
                        cached.seq,
                    )
                    return {
                        "call_id": cached.call_id,
                        "result": cached.content,
                        "error": cached.error,
                        "seq": cached.seq,
                        "replayed": True,
                    }

            seq = len(self._records) + 1
            call_id = f"bridge_{seq:04d}"
            tool_call = ToolCall(
                id=call_id,
                name=tool_name,
                arguments=dict(arguments),
                requestor="assistant",
            )
            tool_message = self.environment.get_response(tool_call)

            record = BridgeCallRecord(
                seq=seq,
                call_id=call_id,
                tool_name=tool_name,
                arguments=dict(arguments),
                content=tool_message.content or "",
                error=bool(tool_message.error),
                turn_hint=turn_hint,
            )
            self._records.append(record)
            if idempotency_key is not None:
                self._idempotency[idempotency_key] = record

        logger.info(
            "bridge[{}] {} args={} error={}",
            record.seq,
            record.tool_name,
            json.dumps(record.arguments, sort_keys=True)[:200],
            record.error,
        )
        return {
            "call_id": record.call_id,
            "result": record.content,
            "error": record.error,
            "seq": record.seq,
            "replayed": False,
        }

    # ── record access ────────────────────────────────────────────────────────

    @property
    def records(self) -> list[BridgeCallRecord]:
        """Every call executed so far, in execution order."""
        with self._lock:
            return list(self._records)

    def drain(self) -> list[BridgeCallRecord]:
        """Records executed since the last drain.

        The adapter drains once per agent turn so the calls Whissle made during
        that turn land in the trajectory immediately before that turn's reply.
        """
        with self._lock:
            fresh = self._records[self._drained :]
            self._drained = len(self._records)
            return list(fresh)

    # ── transport ────────────────────────────────────────────────────────────

    def build_app(self):
        """A FastAPI app serving ``POST /tools/{tool_name}`` plus ``/healthz``.

        Imported lazily so the bridge stays unit-testable without FastAPI.
        """
        # NOTE: this module uses `from __future__ import annotations`, so FastAPI
        # resolves these endpoints' annotations as strings against MODULE globals.
        # Anything named in a signature below must therefore be importable there —
        # which is why the body is a plain `dict` rather than a `Request`.
        from fastapi import Body, FastAPI, Header
        from fastapi.responses import JSONResponse

        app = FastAPI(
            title=f"tau2 tool bridge: {self.environment.get_domain_name()}",
            description=(
                "Authenticated access to this benchmark environment's AGENT tools. "
                "User tools are deliberately not served."
            ),
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )

        @app.get("/healthz")
        async def healthz() -> dict:
            # Unauthenticated on purpose, and deliberately says nothing about
            # state: it exists so a tunnel can be checked without a token.
            return {"ok": True, "domain": self.environment.get_domain_name()}

        @app.post("/tools/{tool_name}")
        async def call_tool(
            tool_name: str,
            payload: Optional[dict] = Body(default=None),
            authorization: Optional[str] = Header(default=None),
            idempotency_key: Optional[str] = Header(
                default=None, alias="Idempotency-Key"
            ),
        ):
            try:
                result = self.handle(
                    tool_name,
                    payload or {},
                    authorization=authorization,
                    idempotency_key=idempotency_key,
                )
            except BridgeError as exc:
                return JSONResponse(
                    status_code=exc.status, content={"detail": exc.detail}
                )
            # The tool's own string output is what the flow's LLM should read.
            return JSONResponse(status_code=200, content=result)

        return app
