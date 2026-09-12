"""Security coverage for bounded provider GLB content inspection."""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import pytest

from blender_mcp.core.archive_boundary import ArtifactStore
from blender_mcp.core.provider_content import (
    BinChunkType,
    GlbMagic,
    GlbVersion,
    InspectProviderGlb,
    JsonChunkType,
    ProviderContentError,
    ProviderContentLimits,
    ProviderImportLimits,
    ProviderImportSnapshot,
    RevalidateProviderGlb,
    ValidateProviderImportOutcome,
)


def BuildGlb(Document: dict[str, object], BinBytes: bytes = b"\x00" * 36) -> bytes:
    JsonBytes = json.dumps(Document, separators=(",", ":")).encode("utf-8")
    return BuildRawGlb(JsonBytes, BinBytes)


def BuildRawGlb(JsonBytes: bytes, BinBytes: bytes = b"\x00" * 36) -> bytes:
    JsonBytes += b" " * (-len(JsonBytes) % 4)
    BinBytes += b"\x00" * (-len(BinBytes) % 4)
    Chunks = struct.pack("<II", len(JsonBytes), JsonChunkType) + JsonBytes
    if BinBytes:
        Chunks += struct.pack("<II", len(BinBytes), BinChunkType) + BinBytes
    return struct.pack("<III", GlbMagic, GlbVersion, 12 + len(Chunks)) + Chunks


def MinimalDocument() -> dict[str, object]:
    return {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": 36}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
            }
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }


def CreateWorkspace(tmp_path: Path, Data: bytes):
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    Workspace = ArtifactStore(ArtifactRoot).CreateWorkspace("request-1")
    Artifact = Workspace.Root / "model.glb"
    Artifact.write_bytes(Data)
    Workspace.RelativeFiles = ("model.glb",)
    return Workspace, Artifact


def ErrorCode(Error: pytest.ExceptionInfo[ProviderContentError]) -> str:
    return Error.value.Code


def test_valid_single_file_glb_produces_digest_bound_plan(tmp_path: Path) -> None:
    Workspace, _ = CreateWorkspace(tmp_path, BuildGlb(MinimalDocument()))

    Plan = InspectProviderGlb(Workspace, "model.glb")

    assert Plan.RelativePath == "model.glb"
    assert len(Plan.Sha256) == 64
    assert Plan.SceneNodes == 1
    assert Plan.Meshes == 1
    assert Plan.Primitives == 1
    assert Plan.AccessorElements == 3
    assert RevalidateProviderGlb(Workspace, Plan) == Plan
    Workspace.Cleanup()


@pytest.mark.parametrize(
    ("Mutate", "Code"),
    [
        (lambda Data: b"bad!" + Data[4:], "PROVIDER_GLB_HEADER_INVALID"),
        (
            lambda Data: Data[:8] + struct.pack("<I", len(Data) + 4) + Data[12:],
            "PROVIDER_GLB_HEADER_INVALID",
        ),
        (
            lambda Data: Data[:4] + struct.pack("<I", 1) + Data[8:],
            "PROVIDER_GLB_HEADER_INVALID",
        ),
    ],
)
def test_invalid_glb_header_fails_closed(tmp_path: Path, Mutate, Code: str) -> None:
    Data = BuildGlb(MinimalDocument())
    Workspace, _ = CreateWorkspace(tmp_path, Mutate(Data))

    with pytest.raises(ProviderContentError) as Error:
        InspectProviderGlb(Workspace, "model.glb")

    assert ErrorCode(Error) == Code
    Workspace.Cleanup()


@pytest.mark.parametrize(
    ("Change", "Code"),
    [
        (
            lambda Doc: Doc["buffers"][0].update(uri="outside.bin"),
            "PROVIDER_GLB_EXTERNAL_RESOURCE_DENIED",
        ),
        (
            lambda Doc: Doc.update(extensionsUsed=["KHR_draco_mesh_compression"]),
            "PROVIDER_GLB_EXTENSION_DENIED",
        ),
        (
            lambda Doc: Doc.update(extensionsRequired=["EXT_meshopt_compression"]),
            "PROVIDER_GLB_EXTENSION_DENIED",
        ),
        (lambda Doc: Doc.update(nodes=[]), "PROVIDER_GLB_MODEL_EMPTY"),
        (lambda Doc: Doc.update(meshes=[]), "PROVIDER_GLB_MODEL_EMPTY"),
        (lambda Doc: Doc["accessors"][0].update(sparse={}), "PROVIDER_GLB_SPARSE_DENIED"),
        (lambda Doc: Doc["bufferViews"][0].update(byteLength=37), "PROVIDER_GLB_STRUCTURE_INVALID"),
        (
            lambda Doc: Doc["meshes"][0]["primitives"][0].update(attributes={"POSITION": 9}),
            "PROVIDER_GLB_STRUCTURE_INVALID",
        ),
        (
            lambda Doc: Doc["accessors"][0].update(count=4),
            "PROVIDER_GLB_ACCESSOR_RANGE_DENIED",
        ),
        (lambda Doc: Doc["nodes"][0].update(mesh=9), "PROVIDER_GLB_STRUCTURE_INVALID"),
        (lambda Doc: Doc.update(scene=9), "PROVIDER_GLB_STRUCTURE_INVALID"),
        (lambda Doc: Doc["asset"].update(version="2.1"), "PROVIDER_GLB_VERSION_DENIED"),
    ],
)
def test_untrusted_glb_features_fail_closed(tmp_path: Path, Change, Code: str) -> None:
    Document = MinimalDocument()
    Change(Document)
    Workspace, _ = CreateWorkspace(tmp_path, BuildGlb(Document))

    with pytest.raises(ProviderContentError) as Error:
        InspectProviderGlb(Workspace, "model.glb")

    assert ErrorCode(Error) == Code
    assert "outside.bin" not in Error.value.PublicMessage
    Workspace.Cleanup()


def test_scene_complexity_is_bounded_before_import(tmp_path: Path) -> None:
    Document = MinimalDocument()
    Document["nodes"] = [{"mesh": 0}, {"mesh": 0}]
    Workspace, _ = CreateWorkspace(tmp_path, BuildGlb(Document))

    with pytest.raises(ProviderContentError) as Error:
        InspectProviderGlb(Workspace, "model.glb", ProviderContentLimits(MaxSceneNodes=1))

    assert ErrorCode(Error) == "PROVIDER_GLB_STRUCTURE_INVALID"
    Workspace.Cleanup()


@pytest.mark.parametrize(
    "JsonMutation",
    [
        lambda Data: Data.replace(b'{"asset":', b'{"asset":{"version":"2.0"},"asset":', 1),
        lambda Data: Data.replace(b'"version":"2.0"', b'"version":"2.0","generator":1e999', 1),
    ],
)
def test_ambiguous_or_nonfinite_json_fails_closed(tmp_path: Path, JsonMutation) -> None:
    JsonBytes = json.dumps(MinimalDocument(), separators=(",", ":")).encode("utf-8")
    Workspace, _ = CreateWorkspace(tmp_path, BuildRawGlb(JsonMutation(JsonBytes)))

    with pytest.raises(ProviderContentError) as Error:
        InspectProviderGlb(Workspace, "model.glb")

    assert ErrorCode(Error) == "PROVIDER_GLB_JSON_INVALID"
    Workspace.Cleanup()


def test_cyclic_and_deep_node_graphs_fail_closed(tmp_path: Path) -> None:
    Cyclic = MinimalDocument()
    Cyclic["nodes"] = [{"mesh": 0, "children": [1]}, {"mesh": 0, "children": [0]}]
    Workspace, Artifact = CreateWorkspace(tmp_path, BuildGlb(Cyclic))

    with pytest.raises(ProviderContentError) as CycleError:
        InspectProviderGlb(Workspace, "model.glb")

    Deep = MinimalDocument()
    Deep["nodes"] = [{"mesh": 0, "children": [1]}, {"mesh": 0}]
    Artifact.write_bytes(BuildGlb(Deep))
    with pytest.raises(ProviderContentError) as DepthError:
        InspectProviderGlb(Workspace, "model.glb", ProviderContentLimits(MaxNodeDepth=1))

    assert ErrorCode(CycleError) == "PROVIDER_GLB_NODE_GRAPH_INVALID"
    assert ErrorCode(DepthError) == "PROVIDER_GLB_NODE_DEPTH_EXCEEDED"
    Workspace.Cleanup()


def test_workspace_rejects_extra_file_and_unlisted_path(tmp_path: Path) -> None:
    Workspace, Artifact = CreateWorkspace(tmp_path, BuildGlb(MinimalDocument()))
    (Workspace.Root / "extra.txt").write_text("canary", encoding="utf-8")

    with pytest.raises(ProviderContentError) as ExtraError:
        InspectProviderGlb(Workspace, "model.glb")
    Workspace.RelativeFiles = ()
    with pytest.raises(ProviderContentError) as UnlistedError:
        InspectProviderGlb(Workspace, "model.glb")

    assert ErrorCode(ExtraError) == "PROVIDER_CONTENT_FILE_SET_DENIED"
    assert ErrorCode(UnlistedError) == "PROVIDER_CONTENT_PATH_INVALID"
    assert Artifact.exists()
    Workspace.Cleanup()


def test_links_and_wrong_extensions_are_denied(tmp_path: Path) -> None:
    Workspace, Artifact = CreateWorkspace(tmp_path, BuildGlb(MinimalDocument()))
    Linked = Workspace.Root / "linked.glb"
    try:
        os.link(Artifact, Linked)
    except OSError:
        Workspace.Cleanup()
        pytest.skip("hardlink creation is unavailable")
    Workspace.RelativeFiles = ("model.glb",)

    with pytest.raises(ProviderContentError) as LinkError:
        InspectProviderGlb(Workspace, "model.glb")
    Workspace.RelativeFiles = ("linked.txt",)
    Wrong = Workspace.Root / "linked.txt"
    Linked.rename(Wrong)
    Artifact.unlink()
    with pytest.raises(ProviderContentError) as ExtensionError:
        InspectProviderGlb(Workspace, "linked.txt")

    assert ErrorCode(LinkError) == "PROVIDER_CONTENT_LINK_DENIED"
    assert ErrorCode(ExtensionError) == "PROVIDER_CONTENT_FORMAT_DENIED"
    Workspace.Cleanup()


def test_revalidation_detects_artifact_replacement(tmp_path: Path) -> None:
    Workspace, Artifact = CreateWorkspace(tmp_path, BuildGlb(MinimalDocument()))
    Plan = InspectProviderGlb(Workspace, "model.glb")
    Document = MinimalDocument()
    Document["nodes"] = [{"mesh": 0}, {"mesh": 0}]
    Artifact.write_bytes(BuildGlb(Document))

    with pytest.raises(ProviderContentError) as Error:
        RevalidateProviderGlb(Workspace, Plan)

    assert ErrorCode(Error) == "PROVIDER_CONTENT_CHANGED"
    Workspace.Cleanup()


def test_native_import_delta_is_bounded_and_requires_a_model() -> None:
    Before = ProviderImportSnapshot(1, 1, 1, 0, 0, 100, 50)
    After = ProviderImportSnapshot(3, 2, 2, 1, 0, 109, 53)

    Delta = ValidateProviderImportOutcome(Before, After)

    assert Delta == ProviderImportSnapshot(2, 1, 1, 1, 0, 9, 3)
    with pytest.raises(ProviderContentError, match="did not create"):
        ValidateProviderImportOutcome(Before, Before)
    with pytest.raises(ProviderContentError, match="baseline changed"):
        ValidateProviderImportOutcome(After, Before)
    with pytest.raises(ProviderContentError, match="result limits"):
        ValidateProviderImportOutcome(
            Before,
            After,
            ProviderImportLimits(MaxObjects=1),
        )


def test_limit_objects_reject_invalid_values() -> None:
    with pytest.raises(ValueError):
        ProviderContentLimits(MaxMeshes=0)
    with pytest.raises(ValueError):
        ProviderImportLimits(MaxImages=True)
    with pytest.raises(ValueError):
        ProviderImportSnapshot(-1, 0, 0, 0, 0, 0, 0)
