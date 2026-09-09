"""Validate command-queue teardown inside a real Blender runtime."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


RepoRoot = Path(__file__).resolve().parents[2]
if str(RepoRoot) not in sys.path:
    sys.path.insert(0, str(RepoRoot))

from blender_mcp.core.thread_safety import (  # noqa: E402
    ExecutionStatus,
    ThreadSafety,
    is_main_thread,
)


if not is_main_thread():
    raise AssertionError("shutdown validation did not start on Blender's main thread")

Lifecycle = ThreadSafety()
if not Lifecycle.Start():
    raise AssertionError("command lifecycle failed to start")
if not bpy.app.timers.is_registered(Lifecycle._TimerCallback):
    raise AssertionError("command queue timer was not registered")

Invocations: list[str] = []
Command, _IsNew = Lifecycle._GetCommand(
    lambda: Invocations.append("ran"),
    "live-shutdown-pending",
    "live-shutdown-digest",
    (),
    {},
    None,
    None,
)
Lifecycle._task_queue.put(Command)
Lifecycle.Shutdown()

if Command.status != ExecutionStatus.CANCELLED:
    raise AssertionError(f"shutdown left queued request in state {Command.status.value}")
if Invocations:
    raise AssertionError("shutdown executed a queued mutation")
if bpy.app.timers.is_registered(Lifecycle._TimerCallback):
    raise AssertionError("shutdown left the command queue timer registered")

print("[BlenderMCP:LiveValidation] PASS shutdown tombstone and timer cleanup", flush=True)
