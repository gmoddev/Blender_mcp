"""Blender-live validation for bounded provider GLB inspection and import outcomes."""

from __future__ import annotations

import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path

import bpy


RepoRoot = Path(__file__).resolve().parents[2]
if str(RepoRoot) not in sys.path:
    sys.path.insert(0, str(RepoRoot))

from blender_mcp.core.archive_boundary import ArtifactStore  # noqa: E402
from blender_mcp.core.provider_content import (  # noqa: E402
    BinChunkType,
    GlbMagic,
    GlbVersion,
    InspectProviderGlb,
    JsonChunkType,
    ProviderContentError,
)
from blender_mcp.core.provider_import import (  # noqa: E402
    CommitProviderGlb,
    ProviderImportCommitError,
    TrackedDataCollections,
)


def BuildTriangleGlb() -> bytes:
    Positions = struct.pack("<9f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    Document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(Positions)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(Positions), "target": 34962}
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "min": [0.0, 0.0, 0.0],
                "max": [1.0, 1.0, 0.0],
            }
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "mode": 4}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    JsonBytes = json.dumps(Document, separators=(",", ":")).encode("utf-8")
    JsonBytes += b" " * (-len(JsonBytes) % 4)
    Chunks = struct.pack("<II", len(JsonBytes), JsonChunkType) + JsonBytes
    Chunks += struct.pack("<II", len(Positions), BinChunkType) + Positions
    return struct.pack("<III", GlbMagic, GlbVersion, 12 + len(Chunks)) + Chunks


def SnapshotIdentities() -> dict[str, frozenset[int]]:
    return {
        Name: frozenset(Item.as_pointer() for Item in getattr(bpy.data, Name))
        for Name in TrackedDataCollections
    }


BaseRoot = Path(tempfile.mkdtemp(prefix="blender-mcp-provider-content-live-"))
Workspace = None
try:
    Workspace = ArtifactStore(BaseRoot).CreateWorkspace("provider-content-live")
    Artifact = Workspace.Root / "model.glb"
    Artifact.write_bytes(BuildTriangleGlb())
    Workspace.RelativeFiles = ("model.glb",)

    Plan = InspectProviderGlb(Workspace, "model.glb")
    OriginalIds = SnapshotIdentities()
    OriginalSelected = frozenset(Item.as_pointer() for Item in bpy.context.selected_objects)
    OriginalActive = bpy.context.view_layer.objects.active
    OriginalActiveId = OriginalActive.as_pointer() if OriginalActive is not None else None
    try:
        CommitProviderGlb(Workspace, Plan, Clock=iter((0.0, 31.0)).__next__)
    except ProviderImportCommitError as Error:
        if Error.Code != "PROVIDER_IMPORT_DEADLINE_EXCEEDED" or not Error.RollbackComplete:
            raise
    else:
        raise AssertionError("expired native import was not rolled back")
    if SnapshotIdentities() != OriginalIds:
        raise AssertionError("native import rollback left a Blender datablock")
    if frozenset(Item.as_pointer() for Item in bpy.context.selected_objects) != OriginalSelected:
        raise AssertionError("native import rollback changed selection")
    Active = bpy.context.view_layer.objects.active
    if (Active.as_pointer() if Active is not None else None) != OriginalActiveId:
        raise AssertionError("native import rollback changed the active object")

    Outcome = CommitProviderGlb(Workspace, Plan)
    if Outcome.Code != "PROVIDER_GLB_IMPORTED" or Outcome.AffectedCount != 1:
        raise AssertionError("native GLB import produced an unexpected bounded result")

    Artifact.write_bytes(BuildTriangleGlb() + b"\x00\x00\x00\x00")
    try:
        CommitProviderGlb(Workspace, Plan)
    except (ProviderContentError, ProviderImportCommitError) as Error:
        if Error.Code not in ("PROVIDER_GLB_HEADER_INVALID", "PROVIDER_CONTENT_CHANGED"):
            raise
    else:
        raise AssertionError("changed provider artifact was accepted")

    print(
        "[BlenderMCP:ProviderContent] Live GLB inspection and bounded import outcome passed "
        f"(sha256={Plan.Sha256[:12]}, objects={Outcome.AffectedCount}, rollback=verified)"
    )
finally:
    for Object in list(bpy.data.objects):
        bpy.data.objects.remove(Object, do_unlink=True)
    for Mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(Mesh)
    if Workspace is not None:
        Workspace.Cleanup()
    shutil.rmtree(BaseRoot, ignore_errors=True)
