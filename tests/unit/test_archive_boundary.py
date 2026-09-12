"""Security tests for bounded provider archive extraction."""

from __future__ import annotations

import stat
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("bpy", MagicMock())
sys.modules.setdefault("mathutils", MagicMock())
sys.modules.setdefault("mathutils.bvhtree", MagicMock())
sys.modules.setdefault("bmesh", MagicMock())

from blender_mcp.core.archive_boundary import (
    ArchiveBoundaryError,
    ArchiveLimits,
    ArtifactStore,
    ExtractZipArchive,
    InspectZipArchive,
)


def WriteZip(
    PathValue: Path, Members: list[tuple[str, bytes]], Compression: int = zipfile.ZIP_DEFLATED
) -> None:
    with zipfile.ZipFile(PathValue, "w", compression=Compression) as Archive:
        for Name, Content in Members:
            Archive.writestr(Name, Content)


def AssertDenied(Code: str, Callback: object) -> None:
    with pytest.raises(ArchiveBoundaryError) as Captured:
        Callback()
    assert Captured.value.Code == Code
    assert str(Captured.value) == Captured.value.PublicMessage


def test_extracts_files_only_into_request_workspace_and_cleans_explicitly(tmp_path: Path) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    ArchivePath = tmp_path / "asset.zip"
    WriteZip(ArchivePath, [("model/hero.obj", b"mesh"), ("model/hero.mtl", b"material")])

    Workspace = ExtractZipArchive(ArchivePath, ArtifactStore(ArtifactRoot), "request-1")

    assert Workspace.Root.parent == ArtifactRoot.resolve()
    assert Workspace.RelativeFiles == ("model/hero.obj", "model/hero.mtl")
    assert (Workspace.Root / "model" / "hero.obj").read_bytes() == b"mesh"
    Workspace.Cleanup()
    assert Workspace.Cleaned is True
    assert not Workspace.Root.exists()


def test_context_manager_cleans_workspace_on_success(tmp_path: Path) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    ArchivePath = tmp_path / "asset.zip"
    WriteZip(ArchivePath, [("hero.obj", b"mesh")])

    with ExtractZipArchive(ArchivePath, ArtifactStore(ArtifactRoot), "request-2") as Workspace:
        Root = Workspace.Root
        assert Root.exists()

    assert not Root.exists()


@pytest.mark.parametrize(
    "Name",
    [
        "../escape.obj",
        "/absolute.obj",
        "C:/drive.obj",
        "folder/../escape.obj",
        "folder/CON.txt",
        "folder/trailing. ",
        "folder/control\x01.obj",
    ],
)
def test_unsafe_member_paths_fail_before_workspace_creation(tmp_path: Path, Name: str) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    ArchivePath = tmp_path / "unsafe.zip"
    WriteZip(ArchivePath, [(Name, b"content")])

    AssertDenied(
        "ARCHIVE_MEMBER_PATH_DENIED",
        lambda: ExtractZipArchive(ArchivePath, ArtifactStore(ArtifactRoot), "request-3"),
    )
    assert list(ArtifactRoot.iterdir()) == []


def test_case_and_unicode_normalization_collisions_are_denied(tmp_path: Path) -> None:
    CaseArchive = tmp_path / "case.zip"
    UnicodeArchive = tmp_path / "unicode.zip"
    WriteZip(CaseArchive, [("Model.obj", b"one"), ("model.obj", b"two")])
    WriteZip(UnicodeArchive, [("caf\u00e9.obj", b"one"), ("cafe\u0301.obj", b"two")])

    AssertDenied("ARCHIVE_PATH_COLLISION", lambda: InspectZipArchive(CaseArchive))
    AssertDenied("ARCHIVE_PATH_COLLISION", lambda: InspectZipArchive(UnicodeArchive))


def test_file_directory_collisions_are_denied_in_either_order(tmp_path: Path) -> None:
    ParentFirst = tmp_path / "parent-first.zip"
    ChildFirst = tmp_path / "child-first.zip"
    WriteZip(ParentFirst, [("model", b"file"), ("model/hero.obj", b"child")])
    WriteZip(ChildFirst, [("model/hero.obj", b"child"), ("model", b"file")])

    AssertDenied("ARCHIVE_PATH_COLLISION", lambda: InspectZipArchive(ParentFirst))
    AssertDenied("ARCHIVE_PATH_COLLISION", lambda: InspectZipArchive(ChildFirst))


def test_explicit_directory_after_child_extracts_without_overwrite(tmp_path: Path) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    ArchivePath = tmp_path / "directory-order.zip"
    WriteZip(ArchivePath, [("model/hero.obj", b"mesh"), ("model/", b"")])

    with ExtractZipArchive(
        ArchivePath, ArtifactStore(ArtifactRoot), "request-directory"
    ) as Workspace:
        assert (Workspace.Root / "model" / "hero.obj").read_bytes() == b"mesh"


def test_links_and_special_members_are_denied(tmp_path: Path) -> None:
    ArchivePath = tmp_path / "link.zip"
    LinkInfo = zipfile.ZipInfo("model-link")
    LinkInfo.create_system = 3
    LinkInfo.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(ArchivePath, "w") as Archive:
        Archive.writestr(LinkInfo, "outside")

    AssertDenied("ARCHIVE_MEMBER_TYPE_DENIED", lambda: InspectZipArchive(ArchivePath))


def test_encrypted_member_flag_is_denied_before_extraction(tmp_path: Path) -> None:
    ArchivePath = tmp_path / "encrypted.zip"
    WriteZip(ArchivePath, [("hero.obj", b"mesh")])
    ArchiveBytes = bytearray(ArchivePath.read_bytes())
    LocalHeader = ArchiveBytes.index(b"PK\x03\x04")
    CentralHeader = ArchiveBytes.index(b"PK\x01\x02")
    for Offset in (LocalHeader + 6, CentralHeader + 8):
        Flags = int.from_bytes(ArchiveBytes[Offset : Offset + 2], "little") | 0x1
        ArchiveBytes[Offset : Offset + 2] = Flags.to_bytes(2, "little")
    ArchivePath.write_bytes(ArchiveBytes)

    AssertDenied("ARCHIVE_ENCRYPTION_DENIED", lambda: InspectZipArchive(ArchivePath))


def test_path_depth_and_length_limits_are_enforced(tmp_path: Path) -> None:
    DeepArchive = tmp_path / "deep.zip"
    LongArchive = tmp_path / "long.zip"
    WriteZip(DeepArchive, [("one/two/three.obj", b"mesh")])
    WriteZip(LongArchive, [(f"{'a' * 40}.obj", b"mesh")])

    AssertDenied(
        "ARCHIVE_MEMBER_PATH_DENIED",
        lambda: InspectZipArchive(DeepArchive, ArchiveLimits(MaxPathDepth=2)),
    )
    AssertDenied(
        "ARCHIVE_MEMBER_PATH_DENIED",
        lambda: InspectZipArchive(LongArchive, ArchiveLimits(MaxPathCharacters=20)),
    )


def test_member_count_and_expanded_size_limits_are_enforced(tmp_path: Path) -> None:
    ArchivePath = tmp_path / "limits.zip"
    WriteZip(ArchivePath, [("one.obj", b"1234"), ("two.mtl", b"5678")])

    AssertDenied(
        "ARCHIVE_MEMBER_LIMIT_EXCEEDED",
        lambda: InspectZipArchive(ArchivePath, ArchiveLimits(MaxMembers=1)),
    )
    AssertDenied(
        "ARCHIVE_EXPANDED_LIMIT_EXCEEDED",
        lambda: InspectZipArchive(ArchivePath, ArchiveLimits(MaxExpandedBytes=7)),
    )


def test_per_member_and_compressed_archive_limits_are_enforced(tmp_path: Path) -> None:
    ArchivePath = tmp_path / "limits.zip"
    WriteZip(ArchivePath, [("hero.obj", b"12345678")], Compression=zipfile.ZIP_STORED)

    AssertDenied(
        "ARCHIVE_MEMBER_LIMIT_EXCEEDED",
        lambda: InspectZipArchive(ArchivePath, ArchiveLimits(MaxMemberBytes=7)),
    )
    AssertDenied(
        "ARCHIVE_COMPRESSED_LIMIT_EXCEEDED",
        lambda: InspectZipArchive(ArchivePath, ArchiveLimits(MaxArchiveBytes=1)),
    )


def test_high_compression_ratio_is_denied(tmp_path: Path) -> None:
    ArchivePath = tmp_path / "bomb.zip"
    WriteZip(ArchivePath, [("large.obj", b"A" * 100_000)])

    AssertDenied(
        "ARCHIVE_COMPRESSION_RATIO_EXCEEDED",
        lambda: InspectZipArchive(ArchivePath, ArchiveLimits(MaxCompressionRatio=5.0)),
    )


def test_unsupported_compression_is_denied(tmp_path: Path) -> None:
    ArchivePath = tmp_path / "bzip.zip"
    WriteZip(ArchivePath, [("hero.obj", b"mesh")], Compression=zipfile.ZIP_BZIP2)

    AssertDenied("ARCHIVE_COMPRESSION_DENIED", lambda: InspectZipArchive(ArchivePath))


def test_empty_and_non_zip_archives_are_denied(tmp_path: Path) -> None:
    EmptyArchive = tmp_path / "empty.zip"
    with zipfile.ZipFile(EmptyArchive, "w"):
        pass
    WrongType = tmp_path / "asset.bin"
    WrongType.write_bytes(b"not a zip")

    AssertDenied("ARCHIVE_EMPTY", lambda: InspectZipArchive(EmptyArchive))
    AssertDenied("ARCHIVE_TYPE_DENIED", lambda: InspectZipArchive(WrongType))


def test_extraction_failure_removes_partial_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    ArchivePath = tmp_path / "asset.zip"
    WriteZip(ArchivePath, [("hero.obj", b"mesh")])

    def FailRead(Stream: zipfile.ZipExtFile, Size: int = -1) -> bytes:
        del Stream, Size
        raise OSError("canary-sensitive internal failure")

    monkeypatch.setattr(zipfile.ZipExtFile, "read", FailRead)

    AssertDenied(
        "ARCHIVE_EXTRACTION_FAILED",
        lambda: ExtractZipArchive(ArchivePath, ArtifactStore(ArtifactRoot), "request-4"),
    )
    assert list(ArtifactRoot.iterdir()) == []


def test_actual_stream_overrun_is_denied_and_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    ArchivePath = tmp_path / "asset.zip"
    WriteZip(ArchivePath, [("hero.obj", b"mesh")])
    Calls = 0

    def OversizedRead(Stream: zipfile.ZipExtFile, Size: int = -1) -> bytes:
        nonlocal Calls
        del Stream, Size
        Calls += 1
        return b"unexpected-extra-bytes" if Calls == 1 else b""

    monkeypatch.setattr(zipfile.ZipExtFile, "read", OversizedRead)

    AssertDenied(
        "ARCHIVE_MEMBER_SIZE_MISMATCH",
        lambda: ExtractZipArchive(ArchivePath, ArtifactStore(ArtifactRoot), "request-5"),
    )
    assert list(ArtifactRoot.iterdir()) == []


def test_artifact_store_rejects_missing_relative_and_link_roots(tmp_path: Path) -> None:
    AssertDenied("ARTIFACT_ROOT_INVALID", lambda: ArtifactStore(tmp_path / "missing"))
    AssertDenied("ARTIFACT_ROOT_INVALID", lambda: ArtifactStore("relative-root"))

    RealRoot = tmp_path / "real"
    LinkedRoot = tmp_path / "linked"
    RealRoot.mkdir()
    try:
        LinkedRoot.symlink_to(RealRoot, target_is_directory=True)
    except OSError:
        return
    AssertDenied("ARTIFACT_ROOT_INVALID", lambda: ArtifactStore(LinkedRoot))


def test_invalid_limits_and_request_identity_fail_closed(tmp_path: Path) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    Store = ArtifactStore(ArtifactRoot)

    with pytest.raises(ValueError):
        ArchiveLimits(MaxMembers=0)
    AssertDenied("ARTIFACT_REQUEST_ID_INVALID", lambda: Store.CreateWorkspace(""))
    AssertDenied("ARCHIVE_PATH_INVALID", lambda: InspectZipArchive(None))


def test_workspace_setup_failure_removes_created_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ArtifactRoot = tmp_path / "artifacts"
    ArtifactRoot.mkdir()
    Store = ArtifactStore(ArtifactRoot)
    OriginalChmod = Path.chmod

    def FailWorkspaceChmod(PathValue: Path, Mode: int, *Args: object, **Kwargs: object) -> None:
        del Mode, Args, Kwargs
        if PathValue.parent == ArtifactRoot:
            raise OSError("permission canary")
        OriginalChmod(PathValue, 0o700)

    monkeypatch.setattr(Path, "chmod", FailWorkspaceChmod)

    AssertDenied("ARTIFACT_WORKSPACE_CREATE_FAILED", lambda: Store.CreateWorkspace("request-6"))
    assert list(ArtifactRoot.iterdir()) == []
