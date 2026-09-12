"""Exercise bounded archive extraction in Blender's embedded Python runtime."""

from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


RepoRoot = Path(__file__).resolve().parents[2]
if str(RepoRoot) not in sys.path:
    sys.path.insert(0, str(RepoRoot))

from blender_mcp.core.archive_boundary import (  # noqa: E402
    ArchiveBoundaryError,
    ArtifactStore,
    ExtractZipArchive,
)


BaseRoot = Path(tempfile.mkdtemp(prefix="blender_mcp_archive_live_"))
ArtifactRoot = BaseRoot / "artifacts"
ArtifactRoot.mkdir()
ValidArchive = BaseRoot / "valid.zip"
UnsafeArchive = BaseRoot / "unsafe.zip"

try:
    with zipfile.ZipFile(ValidArchive, "w", compression=zipfile.ZIP_DEFLATED) as Archive:
        Archive.writestr("model/hero.obj", b"live-mesh-canary")
    with zipfile.ZipFile(UnsafeArchive, "w", compression=zipfile.ZIP_DEFLATED) as Archive:
        Archive.writestr("../outside.obj", b"must-not-extract")

    with ExtractZipArchive(
        ValidArchive,
        ArtifactStore(ArtifactRoot),
        "blender-live-request",
    ) as Workspace:
        assert Workspace.RequestId == "blender-live-request"
        assert (Workspace.Root / "model" / "hero.obj").read_bytes() == b"live-mesh-canary"
        WorkspaceRoot = Workspace.Root
    assert not WorkspaceRoot.exists()

    try:
        ExtractZipArchive(
            UnsafeArchive,
            ArtifactStore(ArtifactRoot),
            "blender-live-negative",
        )
    except ArchiveBoundaryError as Error:
        assert Error.Code == "ARCHIVE_MEMBER_PATH_DENIED"
    else:
        raise AssertionError("Unsafe archive was not denied")
    assert not (BaseRoot / "outside.obj").exists()
    assert list(ArtifactRoot.iterdir()) == []
    print("[BlenderMCP:Archive] Live archive boundary validation passed")
finally:
    shutil.rmtree(BaseRoot, ignore_errors=True)
