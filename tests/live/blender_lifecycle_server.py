"""Start a disposable authenticated Blender MCP server for live lifecycle checks."""

from __future__ import annotations

import atexit
import os
import sys
import time
from pathlib import Path
from typing import Any

import bpy

RepoRoot = Path(__file__).resolve().parents[2]
if str(RepoRoot) not in sys.path:
    sys.path.insert(0, str(RepoRoot))

from blender_mcp import BlenderMCPServer, dispatcher  # noqa: E402
from blender_mcp.core.security import Capability, SecurityManager  # noqa: E402
from blender_mcp.core.thread_safety import is_main_thread  # noqa: E402

ProbeKey = "BlenderMcpLiveLifecycleCount"


# The disposable factory session has no saved add-on preferences. Permit only
# this process's explicitly classified structured probe actions.
SecurityManager.is_safe_mode = staticmethod(lambda: False)
SecurityManager.is_raw_code_enabled = staticmethod(lambda: False)


@dispatcher.register_handler(
    "_live_lifecycle_probe",
    actions=["BLOCK", "MUTATE", "GET_COUNT"],
    category="test",
    capabilities={
        "BLOCK": [Capability.MUTATE.value],
        "MUTATE": [Capability.MUTATE.value],
        "GET_COUNT": [Capability.READ.value],
    },
    requires_main_thread=True,
    schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["BLOCK", "MUTATE", "GET_COUNT"]},
            "duration_seconds": {"type": "number", "minimum": 0.0, "maximum": 2.0},
            "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 5.0},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
)
def _LiveLifecycleProbe(action: str, **Params: Any) -> dict[str, Any]:
    """Provide a bounded mutation and main-thread blocker for live testing."""
    if not is_main_thread():
        raise RuntimeError("Live lifecycle probe executed outside Blender's main thread")
    DurationSeconds = float(Params.get("duration_seconds", 0.0))
    if action == "BLOCK":
        time.sleep(DurationSeconds)
        return {"blocked_seconds": DurationSeconds}
    if action == "MUTATE":
        time.sleep(DurationSeconds)
        CurrentCount = int(bpy.context.scene.get(ProbeKey, 0))
        NextCount = CurrentCount + 1
        bpy.context.scene[ProbeKey] = NextCount
        return {"count": NextCount}
    return {"count": int(bpy.context.scene.get(ProbeKey, 0))}


# Import just the lifecycle handler; loading the full production surface is not
# necessary for this transport/control-plane validation.
import blender_mcp.handlers.manage_command_lifecycle  # noqa: E402, F401

AuthToken = os.environ["BLENDER_MCP_AUTH_TOKEN"]
Port = int(os.environ["BLENDER_MCP_LIVE_TEST_PORT"])
bpy.context.scene.pop(ProbeKey, None)

Server = BlenderMCPServer(
    host="127.0.0.1",
    port=Port,
    auth_token=AuthToken,
    max_active_clients=8,
    handshake_timeout=2.0,
    body_timeout=5.0,
    idle_timeout=30.0,
)
if not Server.start():
    raise RuntimeError("Disposable Blender MCP server failed to start")

setattr(bpy.types, "blendermcp_live_validation_server", Server)
atexit.register(Server.stop)
print(f"[BlenderMCP:LiveValidation] READY port={Server.port}", flush=True)
