"""Blender-live validation for thread-safe authorization policy snapshots."""

from __future__ import annotations

import sys
import threading
from pathlib import Path


ProjectRoot = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ProjectRoot))

from blender_mcp.core import security as SecurityModule  # noqa: E402
from blender_mcp.core.security import (  # noqa: E402
    Capability,
    ConfigureSecurityPolicy,
    ResetSecurityPolicy,
    SecurityManager,
)
from blender_mcp.core.thread_safety import is_main_thread  # noqa: E402


if not is_main_thread():
    raise AssertionError("security policy validation did not start on Blender's main thread")
if hasattr(SecurityModule, "bpy"):
    raise AssertionError("the control-plane security module retains a Blender API reference")

Results: list[bool] = []
Errors: list[BaseException] = []


def ValidateFromWorker() -> None:
    try:
        Results.extend(
            [
                SecurityManager.validate_action("inspect", "GET", [Capability.READ.value]),
                SecurityManager.validate_action(
                    "manage_scene", "RENAME", [Capability.MUTATE.value]
                ),
                SecurityManager.validate_action(
                    "execute_code", "EXECUTE_CODE", [Capability.EXECUTE_CODE.value]
                ),
            ]
        )
    except BaseException as Error:
        Errors.append(Error)


try:
    ConfigureSecurityPolicy(SafeMode=False, RawCodeEnabled=False)
    Worker = threading.Thread(target=ValidateFromWorker, name="BlenderMCPSecurityPolicyCanary")
    Worker.start()
    Worker.join(timeout=2.0)
    if Worker.is_alive():
        raise AssertionError("worker authorization did not terminate")
    if Errors:
        raise Errors[0]
    if Results != [True, True, False]:
        raise AssertionError(f"unexpected worker authorization results: {Results}")
finally:
    ResetSecurityPolicy()

print("[BlenderMCP:SecurityLive] PASS worker authorization policy snapshot", flush=True)
