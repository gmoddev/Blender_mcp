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

from blender_mcp.core.export_pipeline import ExportValidator, GLTFExporter  # noqa: E402
from blender_mcp.core.filesystem_boundary import (  # noqa: E402
    ConfigureFilesystemPolicy,
    FilesystemAccess,
    FilesystemPolicy,
    FilesystemPolicyError,
    ResetFilesystemPolicy,
)
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

    InsideExport = GLTFExporter.export(bpy.context.scene, list(bpy.context.scene.objects), "mesh/hero")
    ExpectedExport = WriteRoot / "mesh" / "hero.glb"
    if not InsideExport.get("success") or not ExpectedExport.is_file():
        raise AssertionError(f"approved export failed: {InsideExport}")

    PixelData = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    ReadImage = ReadRoot / "pixel.png"
    OutsideImage = OutsideRoot / "pixel.png"
    ReadImage.write_bytes(PixelData)
    OutsideImage.write_bytes(PixelData)
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
        "[BlenderMCP:FilesystemLive] PASS containment, overwrite, export, open, and sequencer read",
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
