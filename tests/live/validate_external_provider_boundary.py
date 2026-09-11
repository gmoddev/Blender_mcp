"""Blender-live validation for quarantined external provider handlers."""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

import bpy


ProjectRoot = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ProjectRoot))

from blender_mcp.handlers import hyper3d_handler as Hyper3DModule  # noqa: E402
from blender_mcp.handlers import polyhaven_handler as PolyHavenModule  # noqa: E402
from blender_mcp.handlers import sketchfab_handler as SketchfabModule  # noqa: E402


def Digest(PathValue: Path) -> str:
    return hashlib.sha256(PathValue.read_bytes()).hexdigest()


def AssertDenied(Result: dict, Action: str) -> None:
    if Result.get("code") != "EXTERNAL_CAPABILITY_DISABLED":
        raise AssertionError(f"{Action} was not quarantined: {Result}")
    if Result.get("retry_safe") is not False:
        raise AssertionError(f"{Action} did not report non-retry-safe denial: {Result}")


with tempfile.TemporaryDirectory(prefix="blender_mcp_provider_live_") as TempDirectory:
    Sentinel = Path(TempDirectory) / "provider-sentinel.bin"
    Sentinel.write_bytes(b"provider-boundary-sentinel")
    SentinelDigest = Digest(Sentinel)
    ObjectCount = len(bpy.data.objects)
    World = bpy.context.scene.world

    Cases = (
        (
            PolyHavenModule.integration_polyhaven,
            "IMPORT_HDRI",
            {"asset_id": "provider-canary", "resolution": "1k"},
        ),
        (
            PolyHavenModule.integration_polyhaven,
            "IMPORT_MODEL",
            {"asset_id": "provider-canary"},
        ),
        (
            SketchfabModule.integration_sketchfab,
            "IMPORT",
            {"uid": "provider-canary"},
        ),
        (
            Hyper3DModule.integration_hyper3d,
            "GENERATE",
            {"prompt": "provider-canary", "image_path": str(Sentinel)},
        ),
        (
            Hyper3DModule.integration_hyper3d,
            "IMPORT",
            {"model_url": "http://127.0.0.1/private.glb"},
        ),
    )
    for Handler, Action, Params in Cases:
        AssertDenied(Handler(action=Action, **Params), Action)

    for Module in (PolyHavenModule, SketchfabModule, Hyper3DModule):
        if hasattr(Module, "requests") or hasattr(Module, "tempfile"):
            raise AssertionError(f"retired provider sinks remain in {Module.__name__}")

    if len(bpy.data.objects) != ObjectCount:
        raise AssertionError("denied provider action changed scene objects")
    if bpy.context.scene.world is not World:
        raise AssertionError("denied provider action changed the scene world")
    if Digest(Sentinel) != SentinelDigest:
        raise AssertionError("denied provider action changed the sentinel")

print("[BlenderMCP:ProviderLive] PASS external provider quarantine", flush=True)
