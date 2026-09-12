"""Bounded inspection and commit-result checks for untrusted provider GLB artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from .archive_boundary import ManagedArtifactDirectory


GlbMagic = 0x46546C67
GlbVersion = 2
JsonChunkType = 0x4E4F534A
BinChunkType = 0x004E4942


class ProviderContentError(ValueError):
    """Structured provider-content failure that never exposes artifact data."""

    def __init__(self, Code: str, PublicMessage: str):
        super().__init__(PublicMessage)
        self.Code = Code
        self.PublicMessage = PublicMessage


@dataclass(frozen=True)
class ProviderContentLimits:
    MaxArtifactBytes: int = 128 * 1024 * 1024
    MaxJsonBytes: int = 8 * 1024 * 1024
    MaxJsonDepth: int = 48
    MaxJsonNodes: int = 100_000
    MaxStringCharacters: int = 4096
    MaxContainerEntries: int = 16_384
    MaxSceneNodes: int = 4096
    MaxNodeDepth: int = 128
    MaxMeshes: int = 1024
    MaxPrimitives: int = 8192
    MaxAccessors: int = 16_384
    MaxAccessorElements: int = 20_000_000
    MaxBufferViews: int = 16_384
    MaxMaterials: int = 1024
    MaxImages: int = 512
    MaxImageBytes: int = 32 * 1024 * 1024
    MaxImageDimension: int = 8192
    MaxImagePixels: int = 16_777_216
    MaxTotalImagePixels: int = 67_108_864
    MaxTextures: int = 512
    MaxAnimations: int = 128
    MaxAnimationChannels: int = 8192
    MaxSkins: int = 128
    MaxScenes: int = 128
    MaxCameras: int = 128

    def __post_init__(self) -> None:
        for Value in vars(self).values():
            if not isinstance(Value, int) or isinstance(Value, bool) or Value <= 0:
                raise ValueError("Provider content limits must be positive integers")


@dataclass(frozen=True)
class ProviderGlbPlan:
    Path: Path
    RelativePath: str
    Bytes: int
    Sha256: str
    SceneNodes: int
    Meshes: int
    Primitives: int
    Accessors: int
    AccessorElements: int
    BufferViews: int
    Materials: int
    Images: int
    ImagePixels: int
    Textures: int
    Animations: int
    Skins: int
    Scenes: int
    Cameras: int


@dataclass(frozen=True)
class ProviderImportSnapshot:
    Objects: int
    Meshes: int
    Materials: int
    Images: int
    Armatures: int
    Vertices: int
    Polygons: int

    def __post_init__(self) -> None:
        for Value in vars(self).values():
            if not isinstance(Value, int) or isinstance(Value, bool) or Value < 0:
                raise ValueError("Provider import snapshot counts must be non-negative integers")


@dataclass(frozen=True)
class ProviderImportLimits:
    MaxObjects: int = 4096
    MaxMeshes: int = 1024
    MaxMaterials: int = 1024
    MaxImages: int = 512
    MaxArmatures: int = 128
    MaxVertices: int = 20_000_000
    MaxPolygons: int = 20_000_000

    def __post_init__(self) -> None:
        for Value in vars(self).values():
            if not isinstance(Value, int) or isinstance(Value, bool) or Value <= 0:
                raise ValueError("Provider import limits must be positive integers")


def InspectProviderGlb(
    Workspace: ManagedArtifactDirectory,
    RelativePath: str,
    Limits: ProviderContentLimits | None = None,
) -> ProviderGlbPlan:
    """Inspect one workspace-owned GLB before native Blender import."""
    EffectiveLimits = Limits or ProviderContentLimits()
    ArtifactPath = _ResolveArtifact(Workspace, RelativePath)
    _ValidateWorkspaceContents(Workspace, RelativePath)
    RawBytes, Digest = _ReadBoundedArtifact(ArtifactPath, EffectiveLimits.MaxArtifactBytes)
    return _InspectGlbBytes(ArtifactPath, RelativePath, RawBytes, Digest, EffectiveLimits)


def RevalidateProviderGlb(
    Workspace: ManagedArtifactDirectory,
    Plan: ProviderGlbPlan,
    Limits: ProviderContentLimits | None = None,
) -> ProviderGlbPlan:
    """Rebind an immutable preparation plan immediately before native import."""
    Current = InspectProviderGlb(Workspace, Plan.RelativePath, Limits)
    if Current != Plan:
        raise _Deny(
            "PROVIDER_CONTENT_CHANGED",
            "The prepared provider artifact changed before import",
        )
    return Current


def ValidateProviderImportOutcome(
    Before: ProviderImportSnapshot,
    After: ProviderImportSnapshot,
    Limits: ProviderImportLimits | None = None,
) -> ProviderImportSnapshot:
    """Validate a main-thread native-import delta before reporting commit success."""
    EffectiveLimits = Limits or ProviderImportLimits()
    Names = tuple(vars(Before))
    Deltas: dict[str, int] = {}
    for Name in Names:
        BeforeValue = getattr(Before, Name)
        AfterValue = getattr(After, Name)
        if AfterValue < BeforeValue:
            raise _Deny(
                "PROVIDER_IMPORT_BASELINE_CHANGED",
                "The Blender import baseline changed unexpectedly",
            )
        Deltas[Name] = AfterValue - BeforeValue
    if Deltas["Objects"] == 0 or Deltas["Meshes"] == 0:
        raise _Deny(
            "PROVIDER_IMPORT_EMPTY",
            "The provider import did not create a model",
        )
    LimitByName = {
        "Objects": EffectiveLimits.MaxObjects,
        "Meshes": EffectiveLimits.MaxMeshes,
        "Materials": EffectiveLimits.MaxMaterials,
        "Images": EffectiveLimits.MaxImages,
        "Armatures": EffectiveLimits.MaxArmatures,
        "Vertices": EffectiveLimits.MaxVertices,
        "Polygons": EffectiveLimits.MaxPolygons,
    }
    if any(Deltas[Name] > LimitByName[Name] for Name in Names):
        raise _Deny(
            "PROVIDER_IMPORT_LIMIT_EXCEEDED",
            "The provider import exceeded its result limits",
        )
    return ProviderImportSnapshot(**Deltas)


def _ResolveArtifact(Workspace: ManagedArtifactDirectory, RelativePath: str) -> Path:
    if not isinstance(Workspace, ManagedArtifactDirectory) or Workspace.Cleaned:
        raise _Deny("PROVIDER_WORKSPACE_INVALID", "A live provider workspace is required")
    if not isinstance(RelativePath, str) or not RelativePath or "\x00" in RelativePath:
        raise _Deny("PROVIDER_CONTENT_PATH_INVALID", "The provider artifact path is invalid")
    PosixPath = PurePosixPath(RelativePath)
    if (
        PosixPath.is_absolute()
        or "\\" in RelativePath
        or any(Part in ("", ".", "..") for Part in PosixPath.parts)
        or RelativePath != PosixPath.as_posix()
        or Workspace.RelativeFiles != (RelativePath,)
    ):
        raise _Deny("PROVIDER_CONTENT_PATH_INVALID", "The provider artifact path is invalid")
    try:
        if Workspace.Root.is_symlink() or _IsJunction(Workspace.Root):
            raise ValueError("linked workspace")
        Workspace.Root.resolve(strict=True).relative_to(Workspace.StoreRoot)
        Candidate = Workspace.Root.joinpath(*PosixPath.parts)
        Resolved = cast(Path, Candidate.resolve(strict=True))
        Resolved.relative_to(Workspace.Root)
    except (OSError, RuntimeError, ValueError) as Error:
        raise _Deny(
            "PROVIDER_CONTENT_BOUNDARY_FAILED",
            "The provider artifact escaped its workspace",
        ) from Error
    if Candidate.is_symlink() or _IsJunction(Candidate) or not Resolved.is_file():
        raise _Deny("PROVIDER_CONTENT_TYPE_DENIED", "The provider artifact type is not accepted")
    try:
        if Resolved.stat().st_nlink > 1:
            raise _Deny(
                "PROVIDER_CONTENT_LINK_DENIED",
                "Linked provider artifacts are not accepted",
            )
    except OSError as Error:
        raise _Deny(
            "PROVIDER_CONTENT_PATH_INVALID", "The provider artifact path is invalid"
        ) from Error
    if Resolved.suffix.lower() != ".glb":
        raise _Deny("PROVIDER_CONTENT_FORMAT_DENIED", "Only binary glTF artifacts are accepted")
    return Resolved


def _ValidateWorkspaceContents(Workspace: ManagedArtifactDirectory, RelativePath: str) -> None:
    ExpectedFile = Workspace.Root.joinpath(*PurePosixPath(RelativePath).parts)
    ExpectedDirectories = set(ExpectedFile.parents) - set(Workspace.Root.parents)
    ExpectedDirectories.discard(ExpectedFile)
    EntryCount = 0
    for Root, Directories, Files in os.walk(Workspace.Root, followlinks=False):
        RootPath = Path(Root)
        for Name in (*Directories, *Files):
            EntryCount += 1
            if EntryCount > 64:
                raise _Deny(
                    "PROVIDER_CONTENT_FILE_SET_DENIED",
                    "The provider workspace contains unexpected content",
                )
            Entry = RootPath / Name
            if Entry.is_symlink() or _IsJunction(Entry):
                raise _Deny(
                    "PROVIDER_CONTENT_LINK_DENIED",
                    "Linked provider artifacts are not accepted",
                )
            if Entry != ExpectedFile and Entry not in ExpectedDirectories:
                raise _Deny(
                    "PROVIDER_CONTENT_FILE_SET_DENIED",
                    "The provider workspace contains unexpected content",
                )


def _ReadBoundedArtifact(PathValue: Path, MaxBytes: int) -> tuple[bytes, str]:
    try:
        MetadataBefore = PathValue.stat()
        if MetadataBefore.st_size < 20 or MetadataBefore.st_size > MaxBytes:
            raise _Deny(
                "PROVIDER_CONTENT_SIZE_DENIED",
                "The provider artifact size is not accepted",
            )
        Digest = hashlib.sha256()
        Chunks: list[bytes] = []
        Total = 0
        with PathValue.open("rb") as Source:
            while True:
                Chunk = Source.read(min(1024 * 1024, MaxBytes + 1 - Total))
                if not Chunk:
                    break
                Total += len(Chunk)
                if Total > MaxBytes:
                    raise _Deny(
                        "PROVIDER_CONTENT_SIZE_DENIED",
                        "The provider artifact size is not accepted",
                    )
                Digest.update(Chunk)
                Chunks.append(Chunk)
        MetadataAfter = PathValue.stat()
    except ProviderContentError:
        raise
    except OSError as Error:
        raise _Deny(
            "PROVIDER_CONTENT_READ_FAILED", "The provider artifact could not be read"
        ) from Error
    if (
        Total != MetadataBefore.st_size
        or MetadataAfter.st_size != MetadataBefore.st_size
        or MetadataAfter.st_mtime_ns != MetadataBefore.st_mtime_ns
    ):
        raise _Deny("PROVIDER_CONTENT_CHANGED", "The provider artifact changed during inspection")
    return b"".join(Chunks), Digest.hexdigest()


def _InspectGlbBytes(
    ArtifactPath: Path,
    RelativePath: str,
    RawBytes: bytes,
    Digest: str,
    Limits: ProviderContentLimits,
) -> ProviderGlbPlan:
    Magic, Version, DeclaredLength = struct.unpack_from("<III", RawBytes, 0)
    if Magic != GlbMagic or Version != GlbVersion or DeclaredLength != len(RawBytes):
        raise _Deny("PROVIDER_GLB_HEADER_INVALID", "The binary glTF header is invalid")
    Offset = 12
    Chunks: list[tuple[int, bytes]] = []
    while Offset < len(RawBytes):
        if len(Chunks) >= 2 or Offset + 8 > len(RawBytes):
            raise _Deny("PROVIDER_GLB_CHUNKS_INVALID", "The binary glTF chunks are invalid")
        ChunkLength, ChunkType = struct.unpack_from("<II", RawBytes, Offset)
        Offset += 8
        End = Offset + ChunkLength
        if ChunkLength % 4 != 0 or End > len(RawBytes):
            raise _Deny("PROVIDER_GLB_CHUNKS_INVALID", "The binary glTF chunks are invalid")
        Chunks.append((ChunkType, RawBytes[Offset:End]))
        Offset = End
    if Offset != len(RawBytes) or not Chunks or Chunks[0][0] != JsonChunkType:
        raise _Deny("PROVIDER_GLB_CHUNKS_INVALID", "The binary glTF chunks are invalid")
    if len(Chunks) == 2 and Chunks[1][0] != BinChunkType:
        raise _Deny("PROVIDER_GLB_CHUNKS_INVALID", "The binary glTF chunks are invalid")
    JsonBytes = Chunks[0][1]
    if not JsonBytes or len(JsonBytes) > Limits.MaxJsonBytes:
        raise _Deny("PROVIDER_GLB_JSON_LIMIT_EXCEEDED", "The binary glTF JSON is too large")
    Document = _ParseJson(JsonBytes, Limits)
    BinBytes = Chunks[1][1] if len(Chunks) == 2 else b""
    Counts = _ValidateGlbDocument(Document, BinBytes, Limits)
    return ProviderGlbPlan(
        Path=ArtifactPath,
        RelativePath=RelativePath,
        Bytes=len(RawBytes),
        Sha256=Digest,
        **Counts,
    )


def _ParseJson(JsonBytes: bytes, Limits: ProviderContentLimits) -> dict[str, Any]:
    def RejectConstant(_Value: str) -> None:
        raise ValueError("non-finite number")

    def RejectDuplicateKeys(Pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        Result: dict[str, Any] = {}
        for Key, Value in Pairs:
            if Key in Result:
                raise ValueError("duplicate key")
            Result[Key] = Value
        return Result

    try:
        Value = json.loads(
            JsonBytes.decode("utf-8"),
            parse_constant=RejectConstant,
            object_pairs_hook=RejectDuplicateKeys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as Error:
        raise _Deny("PROVIDER_GLB_JSON_INVALID", "The binary glTF JSON is invalid") from Error
    if not isinstance(Value, dict):
        raise _Deny("PROVIDER_GLB_JSON_INVALID", "The binary glTF JSON is invalid")
    Pending: list[tuple[Any, int]] = [(Value, 1)]
    Nodes = 0
    while Pending:
        Current, Depth = Pending.pop()
        Nodes += 1
        if Nodes > Limits.MaxJsonNodes or Depth > Limits.MaxJsonDepth:
            raise _Deny("PROVIDER_GLB_JSON_LIMIT_EXCEEDED", "The binary glTF JSON is too complex")
        if isinstance(Current, dict):
            if len(Current) > Limits.MaxContainerEntries:
                raise _Deny(
                    "PROVIDER_GLB_JSON_LIMIT_EXCEEDED", "The binary glTF JSON is too complex"
                )
            Pending.extend((Key, Depth + 1) for Key in Current)
            Pending.extend((Item, Depth + 1) for Item in Current.values())
        elif isinstance(Current, list):
            if len(Current) > Limits.MaxContainerEntries:
                raise _Deny(
                    "PROVIDER_GLB_JSON_LIMIT_EXCEEDED", "The binary glTF JSON is too complex"
                )
            Pending.extend((Item, Depth + 1) for Item in Current)
        elif isinstance(Current, str) and len(Current) > Limits.MaxStringCharacters:
            raise _Deny("PROVIDER_GLB_JSON_LIMIT_EXCEEDED", "The binary glTF JSON is too complex")
        elif isinstance(Current, float) and not math.isfinite(Current):
            raise _Deny("PROVIDER_GLB_JSON_INVALID", "The binary glTF JSON is invalid")
    return Value


def _ValidateGlbDocument(
    Document: dict[str, Any], BinBytes: bytes, Limits: ProviderContentLimits
) -> dict[str, int]:
    Asset = Document.get("asset")
    if not isinstance(Asset, dict) or not _SupportsVersion(Asset.get("version")):
        raise _Deny("PROVIDER_GLB_VERSION_DENIED", "The binary glTF version is not supported")
    if "minVersion" in Asset and not _SupportsVersion(Asset.get("minVersion")):
        raise _Deny("PROVIDER_GLB_VERSION_DENIED", "The binary glTF version is not supported")
    if Document.get("extensionsRequired") or Document.get("extensionsUsed"):
        raise _Deny("PROVIDER_GLB_EXTENSION_DENIED", "Binary glTF extensions are not accepted")

    Nodes = _RequireArray(Document, "nodes", Limits.MaxSceneNodes)
    Meshes = _RequireArray(Document, "meshes", Limits.MaxMeshes)
    Materials = _RequireArray(Document, "materials", Limits.MaxMaterials)
    Images = _RequireArray(Document, "images", Limits.MaxImages)
    Textures = _RequireArray(Document, "textures", Limits.MaxTextures)
    Samplers = _RequireArray(Document, "samplers", Limits.MaxTextures)
    Animations = _RequireArray(Document, "animations", Limits.MaxAnimations)
    Skins = _RequireArray(Document, "skins", Limits.MaxSkins)
    Scenes = _RequireArray(Document, "scenes", Limits.MaxScenes)
    Cameras = _RequireArray(Document, "cameras", Limits.MaxCameras)
    if not Nodes or not Meshes:
        raise _Deny("PROVIDER_GLB_MODEL_EMPTY", "The binary glTF does not contain a model")
    BufferViews = _RequireArray(Document, "bufferViews", Limits.MaxBufferViews)
    Buffers = _RequireArray(Document, "buffers", 1)
    if len(Buffers) != 1 or not BinBytes:
        raise _Deny("PROVIDER_GLB_BUFFER_INVALID", "The binary glTF buffer is invalid")
    Buffer = _RequireObject(Buffers[0], "buffer")
    if "uri" in Buffer:
        raise _Deny(
            "PROVIDER_GLB_EXTERNAL_RESOURCE_DENIED", "External glTF resources are not accepted"
        )
    BufferBytes = _RequireInteger(Buffer.get("byteLength"), 1, len(BinBytes))
    if len(BinBytes) - BufferBytes > 3:
        raise _Deny("PROVIDER_GLB_BUFFER_INVALID", "The binary glTF buffer is invalid")

    ViewRanges: list[tuple[int, int, int | None]] = []
    for RawView in BufferViews:
        View = _RequireObject(RawView, "buffer view")
        if View.get("buffer") != 0:
            raise _Deny("PROVIDER_GLB_BUFFER_INVALID", "The binary glTF buffer is invalid")
        Start = _RequireInteger(View.get("byteOffset", 0), 0, BufferBytes)
        Length = _RequireInteger(View.get("byteLength"), 1, BufferBytes)
        if Start + Length > BufferBytes:
            raise _Deny("PROVIDER_GLB_BUFFER_INVALID", "The binary glTF buffer is invalid")
        RawStride = View.get("byteStride")
        Stride = None
        if RawStride is not None:
            Stride = _RequireInteger(RawStride, 4, 252)
            if Stride % 4 != 0:
                raise _Deny(
                    "PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid"
                )
        ViewRanges.append((Start, Length, Stride))

    Accessors = _RequireArray(Document, "accessors", Limits.MaxAccessors)
    AccessorElements = 0
    for RawAccessor in Accessors:
        Accessor = _RequireObject(RawAccessor, "accessor")
        if "sparse" in Accessor:
            raise _Deny("PROVIDER_GLB_SPARSE_DENIED", "Sparse glTF accessors are not accepted")
        ViewIndex = _RequireInteger(Accessor.get("bufferView"), 0, len(ViewRanges) - 1)
        Count = _RequireInteger(Accessor.get("count"), 1, Limits.MaxAccessorElements)
        RawComponentType = Accessor.get("componentType")
        ComponentBytes = (
            {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}.get(RawComponentType)
            if isinstance(RawComponentType, int) and not isinstance(RawComponentType, bool)
            else None
        )
        RawAccessorType = Accessor.get("type")
        ComponentCount = {
            "SCALAR": 1,
            "VEC2": 2,
            "VEC3": 3,
            "VEC4": 4,
            "MAT4": 16,
        }.get(RawAccessorType if isinstance(RawAccessorType, str) else "")
        if ComponentBytes is None or ComponentCount is None:
            raise _Deny("PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid")
        AccessorOffset = _RequireInteger(Accessor.get("byteOffset", 0), 0, ViewRanges[ViewIndex][1])
        ElementBytes = ComponentBytes * ComponentCount
        if AccessorOffset % ComponentBytes != 0:
            raise _Deny("PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid")
        Stride = ViewRanges[ViewIndex][2] or ElementBytes
        if Stride < ElementBytes:
            raise _Deny("PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid")
        RequiredBytes = AccessorOffset + (Count - 1) * Stride + ElementBytes
        if RequiredBytes > ViewRanges[ViewIndex][1]:
            raise _Deny(
                "PROVIDER_GLB_ACCESSOR_RANGE_DENIED",
                "A glTF accessor exceeds its buffer view",
            )
        if "normalized" in Accessor and not isinstance(Accessor["normalized"], bool):
            raise _Deny("PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid")
        AccessorElements += Count
        if AccessorElements > Limits.MaxAccessorElements:
            raise _Deny(
                "PROVIDER_GLB_ACCESSOR_LIMIT_EXCEEDED", "The glTF accessor budget is exceeded"
            )

    PrimitiveCount = 0
    for RawMesh in Meshes:
        Mesh = _RequireObject(RawMesh, "mesh")
        Primitives = Mesh.get("primitives")
        if not isinstance(Primitives, list) or not Primitives:
            raise _Deny("PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid")
        PrimitiveCount += len(Primitives)
        if PrimitiveCount > Limits.MaxPrimitives:
            raise _Deny(
                "PROVIDER_GLB_PRIMITIVE_LIMIT_EXCEEDED", "The glTF primitive budget is exceeded"
            )
        for RawPrimitive in Primitives:
            Primitive = _RequireObject(RawPrimitive, "primitive")
            Attributes = Primitive.get("attributes")
            if not isinstance(Attributes, dict) or not Attributes or len(Attributes) > 16:
                raise _Deny(
                    "PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid"
                )
            for AccessorIndex in Attributes.values():
                _RequireInteger(AccessorIndex, 0, len(Accessors) - 1)
            if "indices" in Primitive:
                _RequireInteger(Primitive["indices"], 0, len(Accessors) - 1)
            if "material" in Primitive:
                _RequireInteger(Primitive["material"], 0, len(Materials) - 1)
            if "targets" in Primitive and (
                not isinstance(Primitive["targets"], list) or len(Primitive["targets"]) > 8
            ):
                raise _Deny(
                    "PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid"
                )

    for RawMaterial in Materials:
        _RequireObject(RawMaterial, "material")
    ImagePixels = 0
    for RawImage in Images:
        Image = _RequireObject(RawImage, "image")
        if "uri" in Image:
            raise _Deny(
                "PROVIDER_GLB_EXTERNAL_RESOURCE_DENIED", "External glTF resources are not accepted"
            )
        ViewIndex = _RequireInteger(Image.get("bufferView"), 0, len(ViewRanges) - 1)
        MimeType = Image.get("mimeType")
        if MimeType not in ("image/png", "image/jpeg"):
            raise _Deny("PROVIDER_GLB_IMAGE_TYPE_DENIED", "The embedded image type is not accepted")
        Start, Length, _Stride = ViewRanges[ViewIndex]
        if Length > Limits.MaxImageBytes:
            raise _Deny("PROVIDER_GLB_IMAGE_LIMIT_EXCEEDED", "An embedded image is too large")
        Width, Height = _ReadImageDimensions(BinBytes[Start : Start + Length], MimeType)
        if (
            Width > Limits.MaxImageDimension
            or Height > Limits.MaxImageDimension
            or Width * Height > Limits.MaxImagePixels
        ):
            raise _Deny("PROVIDER_GLB_IMAGE_LIMIT_EXCEEDED", "An embedded image is too large")
        ImagePixels += Width * Height
        if ImagePixels > Limits.MaxTotalImagePixels:
            raise _Deny(
                "PROVIDER_GLB_IMAGE_LIMIT_EXCEEDED", "The embedded image budget is exceeded"
            )

    for RawSampler in Samplers:
        _RequireObject(RawSampler, "sampler")
    for RawTexture in Textures:
        Texture = _RequireObject(RawTexture, "texture")
        if "source" in Texture:
            _RequireInteger(Texture["source"], 0, len(Images) - 1)
        if "sampler" in Texture:
            _RequireInteger(Texture["sampler"], 0, len(Samplers) - 1)

    for RawSkin in Skins:
        Skin = _RequireObject(RawSkin, "skin")
        Joints = Skin.get("joints")
        if not isinstance(Joints, list) or not Joints or len(Joints) > Limits.MaxSceneNodes:
            raise _Deny("PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid")
        for NodeIndex in Joints:
            _RequireInteger(NodeIndex, 0, len(Nodes) - 1)

    for RawNode in Nodes:
        Node = _RequireObject(RawNode, "node")
        if "mesh" in Node:
            _RequireInteger(Node["mesh"], 0, len(Meshes) - 1)
        if "skin" in Node:
            _RequireInteger(Node["skin"], 0, len(Skins) - 1)
        if "camera" in Node:
            _RequireInteger(Node["camera"], 0, len(Cameras) - 1)
        if "children" in Node:
            Children = Node["children"]
            if not isinstance(Children, list) or len(Children) > Limits.MaxSceneNodes:
                raise _Deny(
                    "PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid"
                )
            for NodeIndex in Children:
                _RequireInteger(NodeIndex, 0, len(Nodes) - 1)
    _ValidateNodeGraph(Nodes, Limits.MaxNodeDepth)

    for RawScene in Scenes:
        Scene = _RequireObject(RawScene, "scene")
        SceneNodes = Scene.get("nodes", [])
        if not isinstance(SceneNodes, list) or len(SceneNodes) > Limits.MaxSceneNodes:
            raise _Deny("PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid")
        for NodeIndex in SceneNodes:
            _RequireInteger(NodeIndex, 0, len(Nodes) - 1)
    if "scene" in Document:
        _RequireInteger(Document["scene"], 0, len(Scenes) - 1)

    for RawCamera in Cameras:
        _RequireObject(RawCamera, "camera")
    AnimationChannels = 0
    for RawAnimation in Animations:
        Animation = _RequireObject(RawAnimation, "animation")
        Channels = Animation.get("channels", [])
        AnimationSamplers = Animation.get("samplers", [])
        if not isinstance(Channels, list) or not isinstance(AnimationSamplers, list):
            raise _Deny("PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid")
        AnimationChannels += len(Channels)
        if AnimationChannels > Limits.MaxAnimationChannels:
            raise _Deny(
                "PROVIDER_GLB_ANIMATION_LIMIT_EXCEEDED",
                "The glTF animation budget is exceeded",
            )
        for Channel in Channels:
            _RequireObject(Channel, "animation channel")
        for AnimationSampler in AnimationSamplers:
            _RequireObject(AnimationSampler, "animation sampler")

    return {
        "SceneNodes": len(Nodes),
        "Meshes": len(Meshes),
        "Primitives": PrimitiveCount,
        "Accessors": len(Accessors),
        "AccessorElements": AccessorElements,
        "BufferViews": len(BufferViews),
        "Materials": len(Materials),
        "Images": len(Images),
        "ImagePixels": ImagePixels,
        "Textures": len(Textures),
        "Animations": len(Animations),
        "Skins": len(Skins),
        "Scenes": len(Scenes),
        "Cameras": len(Cameras),
    }


def _RequireArray(Document: dict[str, Any], Name: str, Maximum: int) -> list[Any]:
    Value = Document.get(Name, [])
    if not isinstance(Value, list) or len(Value) > Maximum:
        raise _Deny("PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid")
    return Value


def _RequireObject(Value: Any, Label: str) -> dict[str, Any]:
    del Label
    if not isinstance(Value, dict):
        raise _Deny("PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid")
    return Value


def _RequireInteger(Value: Any, Minimum: int, Maximum: int) -> int:
    if not isinstance(Value, int) or isinstance(Value, bool) or Value < Minimum or Value > Maximum:
        raise _Deny("PROVIDER_GLB_STRUCTURE_INVALID", "The binary glTF structure is invalid")
    return Value


def _SupportsVersion(Value: Any) -> bool:
    return isinstance(Value, str) and Value == "2.0"


def _ValidateNodeGraph(Nodes: list[Any], MaxDepth: int) -> None:
    ChildrenByNode: list[tuple[int, ...]] = []
    ParentCounts = [0] * len(Nodes)
    for RawNode in Nodes:
        Node = _RequireObject(RawNode, "node")
        RawChildren = Node.get("children", [])
        Children = tuple(RawChildren) if isinstance(RawChildren, list) else ()
        if len(set(Children)) != len(Children):
            raise _Deny("PROVIDER_GLB_NODE_GRAPH_INVALID", "The glTF node graph is invalid")
        for Child in Children:
            ParentCounts[Child] += 1
            if ParentCounts[Child] > 1:
                raise _Deny("PROVIDER_GLB_NODE_GRAPH_INVALID", "The glTF node graph is invalid")
        ChildrenByNode.append(Children)

    States = [0] * len(Nodes)
    for Start in range(len(Nodes)):
        if States[Start] == 2:
            continue
        Stack: list[tuple[int, int, int]] = [(Start, 0, 1)]
        while Stack:
            NodeIndex, ChildIndex, Depth = Stack[-1]
            if Depth > MaxDepth:
                raise _Deny("PROVIDER_GLB_NODE_DEPTH_EXCEEDED", "The glTF node graph is too deep")
            if States[NodeIndex] == 0:
                States[NodeIndex] = 1
            Children = ChildrenByNode[NodeIndex]
            if ChildIndex >= len(Children):
                States[NodeIndex] = 2
                Stack.pop()
                continue
            Child = Children[ChildIndex]
            Stack[-1] = (NodeIndex, ChildIndex + 1, Depth)
            if States[Child] == 1:
                raise _Deny("PROVIDER_GLB_NODE_GRAPH_INVALID", "The glTF node graph is invalid")
            if States[Child] == 0:
                Stack.append((Child, 0, Depth + 1))


def _ReadImageDimensions(Data: bytes, MimeType: str) -> tuple[int, int]:
    if MimeType == "image/png":
        if len(Data) < 24 or Data[:8] != b"\x89PNG\r\n\x1a\n" or Data[12:16] != b"IHDR":
            raise _Deny("PROVIDER_GLB_IMAGE_INVALID", "An embedded image is invalid")
        Width, Height = struct.unpack_from(">II", Data, 16)
        if Width == 0 or Height == 0:
            raise _Deny("PROVIDER_GLB_IMAGE_INVALID", "An embedded image is invalid")
        return Width, Height
    if len(Data) < 4 or Data[:2] != b"\xff\xd8":
        raise _Deny("PROVIDER_GLB_IMAGE_INVALID", "An embedded image is invalid")
    Offset = 2
    while Offset + 4 <= len(Data):
        if Data[Offset] != 0xFF:
            raise _Deny("PROVIDER_GLB_IMAGE_INVALID", "An embedded image is invalid")
        while Offset < len(Data) and Data[Offset] == 0xFF:
            Offset += 1
        if Offset >= len(Data):
            break
        Marker = Data[Offset]
        Offset += 1
        if Marker in (0xD8, 0xD9) or 0xD0 <= Marker <= 0xD7:
            continue
        if Offset + 2 > len(Data):
            break
        SegmentLength = struct.unpack_from(">H", Data, Offset)[0]
        if SegmentLength < 2 or Offset + SegmentLength > len(Data):
            break
        if Marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if SegmentLength < 7:
                break
            Height, Width = struct.unpack_from(">HH", Data, Offset + 3)
            if Width == 0 or Height == 0:
                break
            return Width, Height
        Offset += SegmentLength
    raise _Deny("PROVIDER_GLB_IMAGE_INVALID", "An embedded image is invalid")


def _IsJunction(PathValue: Path) -> bool:
    IsJunction = getattr(PathValue, "is_junction", None)
    return bool(IsJunction and IsJunction())


def _Deny(Code: str, PublicMessage: str) -> ProviderContentError:
    return ProviderContentError(Code, PublicMessage)
