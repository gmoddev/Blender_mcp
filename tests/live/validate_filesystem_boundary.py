"""Exercise filesystem authority against real Blender file operators."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import bpy


RepoRoot = Path(__file__).resolve().parents[2]
if str(RepoRoot) not in sys.path:
    sys.path.insert(0, str(RepoRoot))

from blender_mcp.core.export_pipeline import (  # noqa: E402
    BatchExporter,
    ExportValidator,
    GLTFExporter,
    USDExporter,
)
from blender_mcp.core.filesystem_boundary import (  # noqa: E402
    ConfigureFilesystemPolicy,
    FilesystemAccess,
    FilesystemPolicy,
    FilesystemPolicyError,
    ResetFilesystemPolicy,
)
from blender_mcp.handlers.manage_headless_mode import manage_headless_mode  # noqa: E402
from blender_mcp.handlers.manage_bake import manage_bake  # noqa: E402
from blender_mcp.handlers.manage_light import manage_light  # noqa: E402
from blender_mcp.handlers.manage_mocap import manage_mocap  # noqa: E402
from blender_mcp.handlers.manage_physics import manage_physics  # noqa: E402
from blender_mcp.handlers.manage_scene import _handle_open_file, _handle_save_file  # noqa: E402
from blender_mcp.handlers.manage_sequencer import _get_sequences, manage_sequencer  # noqa: E402


def _ErrorCode(Result: dict) -> str:
    return str(Result["errors"][0]["code"])


def _Digest(Target: Path) -> str:
    return hashlib.sha256(Target.read_bytes()).hexdigest()


BaseRoot = Path(tempfile.mkdtemp(prefix="blender_mcp_filesystem_live_"))
ReadRoot = BaseRoot / "read"
WriteRoot = BaseRoot / "write"
OutsideRoot = BaseRoot / "outside"
JunctionPath = WriteRoot / "junction"
for Directory in (ReadRoot, WriteRoot, OutsideRoot):
    Directory.mkdir()

try:
    ProtectedTarget = OutsideRoot / "protected.blend"
    ProtectedTarget.write_bytes(b"sentinel-protected-file")
    ProtectedDigest = _Digest(ProtectedTarget)
    ConfigureFilesystemPolicy(ReadRoot=ReadRoot, WriteRoot=WriteRoot)

    DeniedSave = _handle_save_file(filepath=str(ProtectedTarget))
    if _ErrorCode(DeniedSave) != "FILESYSTEM_PATH_OUTSIDE_ROOT":
        raise AssertionError(f"outside save returned {DeniedSave}")
    if _Digest(ProtectedTarget) != ProtectedDigest:
        raise AssertionError("outside save changed the protected sentinel")

    bpy.ops.mesh.primitive_cube_add(size=1.0)
    ApprovedBlend = WriteRoot / "approved.blend"
    ApprovedSave = _handle_save_file(filepath=str(ApprovedBlend))
    if not ApprovedSave.get("success") or not ApprovedBlend.is_file():
        raise AssertionError(f"approved save failed: {ApprovedSave}")

    ExistingDigest = _Digest(ApprovedBlend)
    DeniedOverwrite = _handle_save_file(filepath=str(ApprovedBlend))
    if _ErrorCode(DeniedOverwrite) != "FILESYSTEM_OVERWRITE_DENIED":
        raise AssertionError(f"overwrite denial returned {DeniedOverwrite}")
    if _Digest(ApprovedBlend) != ExistingDigest:
        raise AssertionError("denied overwrite changed the approved file")

    ConfigureFilesystemPolicy(ReadRoot=ReadRoot, WriteRoot=WriteRoot, AllowOverwrite=True)
    ApprovedOverwrite = _handle_save_file(filepath=str(ApprovedBlend))
    if not ApprovedOverwrite.get("success"):
        raise AssertionError(f"locally approved overwrite failed: {ApprovedOverwrite}")

    OutsideExport = GLTFExporter.export(bpy.context.scene, [], str(OutsideRoot / "escape"))
    if OutsideExport.get("code") != "EXPORT_ERROR":
        raise AssertionError(f"outside export returned {OutsideExport}")
    if (OutsideRoot / "escape.glb").exists():
        raise AssertionError("outside export created a file")

    PixelData = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    ReadImage = ReadRoot / "pixel.png"
    OutsideImage = OutsideRoot / "pixel.png"
    ReadImage.write_bytes(PixelData)
    OutsideImage.write_bytes(PixelData)
    OutsideImageData = bpy.data.images.load(str(OutsideImage))
    DeniedImageExport = GLTFExporter.export(
        bpy.context.scene,
        list(bpy.context.scene.objects),
        "mesh/denied-image.glb",
    )
    if DeniedImageExport.get("code") != "EXPORT_ERROR":
        raise AssertionError(f"outside GLB image input returned {DeniedImageExport}")
    if (WriteRoot / "mesh" / "denied-image.glb").exists():
        raise AssertionError("denied GLB image input created an output")
    bpy.data.images.remove(OutsideImageData)

    ApprovedImageData = bpy.data.images.load(str(ReadImage))
    InsideExport = GLTFExporter.export(bpy.context.scene, list(bpy.context.scene.objects), "mesh/hero")
    ExpectedExport = WriteRoot / "mesh" / "hero.glb"
    if not InsideExport.get("success") or not ExpectedExport.is_file():
        raise AssertionError(f"approved export failed: {InsideExport}")

    ObjExport = BatchExporter._export_obj(
        list(bpy.context.scene.objects),
        str(WriteRoot / "mesh" / "hero.obj"),
    )
    if not ObjExport.get("success") or not (WriteRoot / "mesh" / "hero.obj").is_file():
        raise AssertionError(f"single-file OBJ export failed: {ObjExport}")
    if (WriteRoot / "mesh" / "hero.mtl").exists():
        raise AssertionError("OBJ export created an unauthorized material sidecar")

    UsdExport = USDExporter.export(
        bpy.context.scene,
        list(bpy.context.scene.objects),
        str(WriteRoot / "mesh" / "hero.usd"),
    )
    if not UsdExport.get("success") or not (WriteRoot / "mesh" / "hero.usd").is_file():
        raise AssertionError(f"USD texture-mode export failed: {UsdExport}")
    if (WriteRoot / "mesh" / "textures").exists():
        TextureMembers = [
            str(PathEntry.relative_to(WriteRoot / "mesh"))
            for PathEntry in (WriteRoot / "mesh" / "textures").rglob("*")
        ]
        raise AssertionError(
            f"USD export created an unauthorized texture directory: {TextureMembers}"
        )
    bpy.data.images.remove(ApprovedImageData)

    EditorBeforeDenial = bpy.context.scene.sequence_editor
    HadEditorBeforeDenial = EditorBeforeDenial is not None
    StripCountBeforeDenial = len(_get_sequences(EditorBeforeDenial))
    DeniedImage = manage_sequencer(action="ADD_IMAGE", filepath=str(OutsideImage))
    if _ErrorCode(DeniedImage) != "FILESYSTEM_PATH_OUTSIDE_ROOT":
        raise AssertionError(f"outside sequencer image returned {DeniedImage}")
    EditorAfterDenial = bpy.context.scene.sequence_editor
    HasEditorAfterDenial = EditorAfterDenial is not None
    StripCountAfterDenial = len(_get_sequences(EditorAfterDenial))
    if HasEditorAfterDenial != HadEditorBeforeDenial or StripCountAfterDenial != StripCountBeforeDenial:
        raise AssertionError("denied sequencer import created an editor")
    ApprovedImage = manage_sequencer(action="ADD_IMAGE", filepath=str(ReadImage))
    if not ApprovedImage.get("success"):
        raise AssertionError(f"approved sequencer image failed: {ApprovedImage}")

    ReadHdri = ReadRoot / "studio.hdr"
    OutsideHdri = OutsideRoot / "studio.hdr"
    GeneratedHdri = bpy.data.images.new(
        "BlenderMCPFilesystemLiveHDRI",
        width=1,
        height=1,
        float_buffer=True,
    )
    GeneratedHdri.pixels = [0.25, 0.5, 0.75, 1.0]
    GeneratedHdri.filepath_raw = str(ReadHdri)
    GeneratedHdri.file_format = "HDR"
    GeneratedHdri.save()
    bpy.data.images.remove(GeneratedHdri)
    shutil.copy2(ReadHdri, OutsideHdri)
    WorldBeforeHdriDenial = bpy.context.scene.world
    DeniedHdri = manage_light(action="SETUP_HDRI", filepath=str(OutsideHdri))
    if _ErrorCode(DeniedHdri) != "FILESYSTEM_PATH_OUTSIDE_ROOT":
        raise AssertionError(f"outside HDRI returned {DeniedHdri}")
    if bpy.context.scene.world is not WorldBeforeHdriDenial:
        raise AssertionError("denied HDRI changed the scene world")
    ApprovedHdri = manage_light(action="SETUP_HDRI", filepath=str(ReadHdri))
    if not ApprovedHdri.get("success"):
        raise AssertionError(f"approved HDRI failed: {ApprovedHdri}")

    HeadlessOutput = OutsideRoot / "headless.png"
    DeniedHeadlessRender = manage_headless_mode(
        action="RENDER_HEADLESS",
        output_path=str(HeadlessOutput),
    )
    if _ErrorCode(DeniedHeadlessRender) != "OUTPUT_FAMILY_DISABLED":
        raise AssertionError(f"headless render quarantine returned {DeniedHeadlessRender}")
    if HeadlessOutput.exists():
        raise AssertionError("quarantined headless render created an output")

    BvhText = """HIERARCHY
ROOT Hips
{
    OFFSET 0 0 0
    CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
    End Site
    {
        OFFSET 0 1 0
    }
}
MOTION
Frames: 1
Frame Time: 0.0333333
0 0 0 0 0 0
"""
    ReadBvh = ReadRoot / "walk.bvh"
    OutsideBvh = OutsideRoot / "walk.bvh"
    ReadBvh.write_text(BvhText, encoding="utf-8")
    OutsideBvh.write_text(BvhText, encoding="utf-8")
    ObjectsBeforeBvhDenial = len(bpy.data.objects)
    DeniedBvh = manage_mocap(action="IMPORT_BVH", filepath=str(OutsideBvh))
    if _ErrorCode(DeniedBvh) != "FILESYSTEM_PATH_OUTSIDE_ROOT":
        raise AssertionError(f"outside BVH returned {DeniedBvh}")
    if len(bpy.data.objects) != ObjectsBeforeBvhDenial:
        raise AssertionError("denied BVH changed the scene")
    ApprovedBvh = manage_mocap(action="IMPORT_BVH", filepath=str(ReadBvh))
    if not ApprovedBvh.get("success"):
        raise AssertionError(f"approved BVH failed: {ApprovedBvh}")
    DeniedFbx = manage_mocap(action="IMPORT_FBX_ANIMATION", filepath="linked.fbx")
    if _ErrorCode(DeniedFbx) != "INPUT_FAMILY_DISABLED":
        raise AssertionError(f"FBX input-family quarantine returned {DeniedFbx}")

    OutsideCache = OutsideRoot / "physics-cache"
    OutsideCache.mkdir()
    CacheSentinel = OutsideCache / "sentinel.bin"
    CacheSentinel.write_bytes(b"physics-cache-sentinel")
    CacheDigest = _Digest(CacheSentinel)
    RigidWorldBeforeDenial = bpy.context.scene.rigidbody_world
    ObjectsBeforeCacheDenial = len(bpy.data.objects)
    DeniedCachePath = manage_physics(
        action="RIGID_BODY_WORLD_SETUP",
        cache_path=str(OutsideCache),
    )
    if _ErrorCode(DeniedCachePath) != "CACHE_PATH_DISABLED":
        raise AssertionError(f"physics cache-path quarantine returned {DeniedCachePath}")
    for CacheAction in ("SIMULATION_PLAY", "ALL_BAKE", "ALL_CACHE_CLEAR"):
        DeniedCacheAction = manage_physics(action=CacheAction)
        if _ErrorCode(DeniedCacheAction) != "CACHE_FAMILY_DISABLED":
            raise AssertionError(f"physics cache-family quarantine returned {DeniedCacheAction}")
    if bpy.context.scene.rigidbody_world is not RigidWorldBeforeDenial:
        raise AssertionError("denied physics cache path changed the rigid-body world")
    if len(bpy.data.objects) != ObjectsBeforeCacheDenial:
        raise AssertionError("denied physics cache operation changed scene objects")
    if _Digest(CacheSentinel) != CacheDigest:
        raise AssertionError("denied physics cache operation changed the sentinel")

    BakeSentinel = OutsideRoot / "baked-texture.png"
    BakeSentinel.write_bytes(b"bake-output-sentinel")
    BakeDigest = _Digest(BakeSentinel)
    ObjectsBeforeBakeDenial = len(bpy.data.objects)
    DeniedBakeOutput = manage_bake(
        action="BAKE_NORMAL",
        output_path=str(BakeSentinel),
    )
    if _ErrorCode(DeniedBakeOutput) != "BAKE_OUTPUT_PATH_DISABLED":
        raise AssertionError(f"texture bake output quarantine returned {DeniedBakeOutput}")
    if len(bpy.data.objects) != ObjectsBeforeBakeDenial:
        raise AssertionError("denied texture bake output changed scene objects")
    if _Digest(BakeSentinel) != BakeDigest:
        raise AssertionError("denied texture bake output changed the sentinel")

    try:
        ExportValidator.check_path_injection(
            str(OutsideRoot / "force.glb"),
            force_export=True,
        )
    except FilesystemPolicyError as Error:
        if Error.Code != "FILESYSTEM_PATH_OUTSIDE_ROOT":
            raise
    else:
        raise AssertionError("force_export bypassed the filesystem policy")

    JunctionTarget = OutsideRoot / "junction-target"
    JunctionTarget.mkdir()
    JunctionResult = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(JunctionPath), str(JunctionTarget)],
        capture_output=True,
        text=True,
        check=False,
    )
    if JunctionResult.returncode == 0:
        JunctionDecision = FilesystemPolicy(WriteRoot=WriteRoot).EvaluatePath(
            JunctionPath / "escape.glb",
            FilesystemAccess.WRITE,
        )
        if JunctionDecision.Code != "FILESYSTEM_PATH_OUTSIDE_ROOT":
            raise AssertionError(f"junction escape returned {JunctionDecision}")
        os.rmdir(JunctionPath)
    else:
        print(
            "[BlenderMCP:FilesystemLive] SKIP junction creation unavailable",
            flush=True,
        )

    ReadBlend = ReadRoot / "approved.blend"
    shutil.copy2(ApprovedBlend, ReadBlend)
    DeniedOpen = _handle_open_file(filepath=str(ApprovedBlend))
    if _ErrorCode(DeniedOpen) != "FILESYSTEM_PATH_OUTSIDE_ROOT":
        raise AssertionError(f"outside read-root open returned {DeniedOpen}")
    ApprovedOpen = _handle_open_file(filepath=str(ReadBlend))
    if not ApprovedOpen.get("success"):
        raise AssertionError(f"approved open failed: {ApprovedOpen}")

    print(
        "[BlenderMCP:FilesystemLive] PASS containment, export, imports, caches, bakes, and quarantines",
        flush=True,
    )
finally:
    ResetFilesystemPolicy()
    if JunctionPath.exists():
        os.rmdir(JunctionPath)
    ResolvedBase = BaseRoot.resolve()
    ResolvedTemp = Path(tempfile.gettempdir()).resolve()
    if ResolvedBase.parent != ResolvedTemp:
        raise AssertionError("refusing to clean a non-temporary live-test directory")
    shutil.rmtree(ResolvedBase)
