"""Validate bounded name selectors in Blender's embedded Python."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import bpy


RepoRoot = Path(__file__).resolve().parents[2]
if str(RepoRoot) not in sys.path:
    sys.path.insert(0, str(RepoRoot))

from blender_mcp.core.enums import BatchAction  # noqa: E402
from blender_mcp.handlers.manage_batch import manage_batch  # noqa: E402


CreatedObjects: list[object] = []


def ErrorCode(Result: dict[str, object]) -> str:
    return str(Result["errors"][0]["code"])  # type: ignore[index]


try:
    for Index in range(512):
        Name = f"SelectorCanary_{Index:04d}_{'a' * 32}!"
        Object = bpy.data.objects.new(Name, None)
        bpy.context.scene.collection.objects.link(Object)
        CreatedObjects.append(Object)

    StartedAt = time.perf_counter()
    LegacyResult = manage_batch(
        action=BatchAction.SET_VISIBILITY.value,
        pattern="(a+)+$",
        visibility=False,
    )
    LegacyDuration = time.perf_counter() - StartedAt
    assert LegacyResult["success"] is False
    assert ErrorCode(LegacyResult) == "REGEX_SELECTOR_DISABLED"
    assert all(not Object.hide_viewport for Object in CreatedObjects)

    StartedAt = time.perf_counter()
    GlobResult = manage_batch(
        action=BatchAction.SET_VISIBILITY.value,
        selector={
            "mode": "GLOB",
            "value": "SelectorCanary_*a*a*a*a*a*a*a*a*!",
        },
        visibility=False,
    )
    GlobDuration = time.perf_counter() - StartedAt
    assert GlobResult["success"] is True
    assert GlobResult["data"]["visibility_set"] == 512
    assert all(Object.hide_viewport for Object in CreatedObjects)
    assert LegacyDuration < 0.25
    assert GlobDuration < 1.0
    print(
        "[BlenderMCP:NameSelector] Live bounded selector validation passed "
        f"(legacy={LegacyDuration:.4f}s, glob={GlobDuration:.4f}s)"
    )
finally:
    for Object in CreatedObjects:
        if Object.name in bpy.data.objects:
            bpy.data.objects.remove(Object, do_unlink=True)
