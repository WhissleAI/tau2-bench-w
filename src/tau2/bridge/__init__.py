"""Authenticated HTTP bridge exposing a live tau2 Environment's agent tools.

Built so a Whissle agent's *saved flow* can call tau2's tools as Whissle custom
HTTP tools, while tau2 keeps ownership of the database that is scored.
"""

from tau2.bridge.tool_bridge import (
    BridgeCallRecord,
    ToolBridge,
    build_trajectory_records,
)

__all__ = ["BridgeCallRecord", "ToolBridge", "build_trajectory_records"]
