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
    ProviderImportSnapshot,
    RevalidateProviderGlb,
    ValidateProviderImportOutcome,
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


def Snapshot() -> ProviderImportSnapshot:
    return ProviderImportSnapshot(
        Objects=len(bpy.data.objects),
        Meshes=len(bpy.data.meshes),
        Materials=len(bpy.data.materials),
        Images=len(bpy.data.images),
        Armatures=len(bpy.data.armatures),
        Vertices=sum(len(Mesh.vertices) for Mesh in bpy.data.meshes),
        Polygons=sum(len(Mesh.polygons) for Mesh in bpy.data.meshes),
    )


BaseRoot = Path(tempfile.mkdtemp(prefix="blender-mcp-provider-content-live-"))
Workspace = None
try:
    Workspace = ArtifactStore(BaseRoot).CreateWorkspace("provider-content-live")
    Artifact = Workspace.Root / "model.glb"
    Artifact.write_bytes(BuildTriangleGlb())
    Workspace.RelativeFiles = ("model.glb",)

    Plan = InspectProviderGlb(Workspace, "model.glb")
    Before = Snapshot()
    RevalidateProviderGlb(Workspace, Plan)
    Result = bpy.ops.import_scene.gltf(filepath=str(Plan.Path))
    if "FINISHED" not in Result:
        raise AssertionError("native GLB importer did not finish")
    Delta = ValidateProviderImportOutcome(Before, Snapshot())
    if Delta.Objects != 1 or Delta.Meshes != 1 or Delta.Vertices != 3 or Delta.Polygons != 1:
        raise AssertionError("native GLB import produced an unexpected bounded result")

    Artifact.write_bytes(BuildTriangleGlb() + b"\x00\x00\x00\x00")
    try:
        RevalidateProviderGlb(Workspace, Plan)
    except ProviderContentError as Error:
        if Error.Code not in ("PROVIDER_GLB_HEADER_INVALID", "PROVIDER_CONTENT_CHANGED"):
            raise
    else:
        raise AssertionError("changed provider artifact was accepted")

    print(
        "[BlenderMCP:ProviderContent] Live GLB inspection and bounded import outcome passed "
        f"(sha256={Plan.Sha256[:12]}, objects={Delta.Objects}, vertices={Delta.Vertices})"
    )
finally:
    for Object in list(bpy.data.objects):
        bpy.data.objects.remove(Object, do_unlink=True)
    for Mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(Mesh)
    if Workspace is not None:
        Workspace.Cleanup()
    shutil.rmtree(BaseRoot, ignore_errors=True)
