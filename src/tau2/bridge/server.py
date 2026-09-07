"""Lifecycle wrapper for serving one live Tau environment to a native platform.

The public tunnel is intentionally outside this class: the benchmark operator
chooses and records that transport.  This server only binds the authenticated
ToolBridge to the configured local port and shuts it down with the Tau agent.
"""

from __future__ import annotations

import threading
import time

import uvicorn

from tau2.bridge.tool_bridge import ToolBridge


class ToolBridgeServer:
    """Run a ToolBridge FastAPI app in a background thread."""

    def __init__(
        self,
        bridge: ToolBridge,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        startup_timeout_s: float = 10.0,
    ) -> None:
        if not 1 <= port <= 65535:
            raise ValueError("bridge port must be between 1 and 65535")
        self.bridge = bridge
        self.host = host
        self.port = port
        self.startup_timeout_s = startup_timeout_s
        self._server = uvicorn.Server(
            uvicorn.Config(
                bridge.build_app(),
                host=host,
                port=port,
                log_level="warning",
                access_log=False,
            )
        )
        self._thread: threading.Thread | None = None

    def start(self) -> "ToolBridgeServer":
        if self._thread is not None:
            raise RuntimeError("tool bridge server has already been started")
        self._thread = threading.Thread(
            target=self._server.run,
            name="tau2-tool-bridge",
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + self.startup_timeout_s
        while not self._server.started and self._thread.is_alive():
            if time.monotonic() >= deadline:
                self.stop()
                raise RuntimeError("tool bridge server did not become ready")
            time.sleep(0.01)
        if not self._server.started:
            raise RuntimeError("tool bridge server stopped during startup")
        return self

    def stop(self) -> None:
        if self._thread is None:
            return
        self._server.should_exit = True
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("tool bridge server did not stop cleanly")
        self._thread = None
