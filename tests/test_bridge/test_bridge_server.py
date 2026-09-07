"""The native-platform bridge starts and is always stoppable."""

import socket

import requests

from tau2.bridge.server import ToolBridgeServer
from tau2.bridge.tool_bridge import ToolBridge
from tau2.domains.appliance_care.environment import get_environment


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_bridge_server_lifecycle():
    port = _free_port()
    bridge = ToolBridge(environment=get_environment(), token="test-token")
    server = ToolBridgeServer(bridge, port=port).start()
    try:
        response = requests.get(f"http://127.0.0.1:{port}/healthz", timeout=2)
        assert response.status_code == 200
        assert response.json()["domain"] == "appliance_care"
    finally:
        server.stop()


def test_bridge_server_rejects_invalid_port():
    bridge = ToolBridge(environment=get_environment(), token="test-token")
    try:
        ToolBridgeServer(bridge, port=0)
    except ValueError as exc:
        assert "port" in str(exc)
    else:
        raise AssertionError("invalid port was accepted")
