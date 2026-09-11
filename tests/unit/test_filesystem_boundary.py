"""Unit tests for user-approved filesystem authority."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from blender_mcp.core.filesystem_boundary import (
    ConfigureFilesystemPolicy,
    FilesystemAccess,
    FilesystemPolicy,
    FilesystemPolicyError,
    ResetFilesystemPolicy,
)
from blender_mcp.core.security import Capability, ConfigureSecurityPolicy, SecurityManager
from blender_mcp.utils.path_validator import PathValidator


@pytest.fixture(autouse=True)
def ResetPolicy() -> None:
    ResetFilesystemPolicy()
    yield
    ResetFilesystemPolicy()


def test_missing_roots_deny_without_touching_filesystem(tmp_path: Path) -> None:
    Target = tmp_path / "not-created" / "asset.glb"

    Decision = FilesystemPolicy().EvaluatePath(Target, FilesystemAccess.WRITE)

    assert Decision.Allowed is False
    assert Decision.Code == "FILESYSTEM_ROOT_NOT_CONFIGURED"
    assert not Target.parent.exists()


def test_relative_write_is_resolved_beneath_approved_root(tmp_path: Path) -> None:
    Root = tmp_path / "exports"
    Root.mkdir()
    Policy = FilesystemPolicy(WriteRoot=Root)

    Decision = Policy.RequirePath(
        "models/hero.glb",
        FilesystemAccess.WRITE,
        AllowedExtensions={".glb"},
        CreateParents=True,
    )

    assert Decision.Allowed is True
    assert Decision.ResolvedPath == str((Root / "models" / "hero.glb").resolve())
    assert Decision.RelativePath == "models/hero.glb"
    assert (Root / "models").is_dir()


@pytest.mark.parametrize("Relative", ["../escape.glb", "nested/../../escape.glb"])
def test_traversal_and_sibling_prefix_escape_are_denied(tmp_path: Path, Relative: str) -> None:
    Root = tmp_path / "project"
    Root.mkdir()
    Policy = FilesystemPolicy(WriteRoot=Root)

    Decision = Policy.EvaluatePath(Relative, FilesystemAccess.WRITE)
    SiblingDecision = Policy.EvaluatePath(
        tmp_path / "project-escape" / "asset.glb",
        FilesystemAccess.WRITE,
    )

    assert Decision.Code == "FILESYSTEM_PATH_OUTSIDE_ROOT"
    assert SiblingDecision.Code == "FILESYSTEM_PATH_OUTSIDE_ROOT"


def test_symlink_escape_is_denied_when_platform_supports_symlinks(tmp_path: Path) -> None:
    Root = tmp_path / "root"
    Outside = tmp_path / "outside"
    Root.mkdir()
    Outside.mkdir()
    Link = Root / "linked"
    try:
        Link.symlink_to(Outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable for this account")

    Decision = FilesystemPolicy(WriteRoot=Root).EvaluatePath(
        Link / "escape.glb",
        FilesystemAccess.WRITE,
    )

    assert Decision.Code == "FILESYSTEM_PATH_OUTSIDE_ROOT"


def test_read_requires_existing_regular_file_inside_read_root(tmp_path: Path) -> None:
    Root = tmp_path / "imports"
    Root.mkdir()
    Asset = Root / "hero.blend"
    Asset.write_bytes(b"BLENDER")
    Policy = FilesystemPolicy(ReadRoot=Root)

    Allowed = Policy.RequirePath(Asset, FilesystemAccess.READ, {".blend"})
    Missing = Policy.EvaluatePath(Root / "missing.blend", FilesystemAccess.READ)

    assert Allowed.ResolvedPath == str(Asset.resolve())
    assert Missing.Code == "FILESYSTEM_PATH_OUTSIDE_ROOT"


def test_existing_write_requires_local_overwrite_approval(tmp_path: Path) -> None:
    Root = tmp_path / "exports"
    Root.mkdir()
    Target = Root / "hero.glb"
    Target.write_bytes(b"sentinel")

    Denied = FilesystemPolicy(WriteRoot=Root).EvaluatePath(Target, FilesystemAccess.WRITE)
    Allowed = FilesystemPolicy(WriteRoot=Root, AllowOverwrite=True).EvaluatePath(
        Target,
        FilesystemAccess.WRITE,
    )

    assert Denied.Code == "FILESYSTEM_OVERWRITE_DENIED"
    assert Allowed.Allowed is True
    assert Allowed.OverwriteAllowed is True
    assert Target.read_bytes() == b"sentinel"


def test_hardlinked_file_is_denied(tmp_path: Path) -> None:
    Root = tmp_path / "exports"
    Root.mkdir()
    Original = Root / "original.glb"
    Alias = Root / "alias.glb"
    Original.write_bytes(b"sentinel")
    try:
        os.link(Original, Alias)
    except OSError:
        pytest.skip("hardlink creation is unavailable for this account")

    Decision = FilesystemPolicy(WriteRoot=Root, AllowOverwrite=True).EvaluatePath(
        Alias,
        FilesystemAccess.WRITE,
    )

    assert Decision.Code == "HARDLINK_PATH_DENIED"
    assert Original.read_bytes() == b"sentinel"


@pytest.mark.skipif(os.name != "nt", reason="Windows path syntax")
@pytest.mark.parametrize(
    "Target",
    [
        r"C:drive-relative.glb",
        r"\\server\share\asset.glb",
        r"\\?\C:\asset.glb",
        r"model.glb:secret",
        r"CON.glb",
        "trailing.\\asset.glb",
    ],
)
def test_ambiguous_windows_paths_are_denied(tmp_path: Path, Target: str) -> None:
    Root = tmp_path / "root"
    Root.mkdir()

    Decision = FilesystemPolicy(WriteRoot=Root).EvaluatePath(Target, FilesystemAccess.WRITE)

    assert Decision.Allowed is False


def test_filesystem_capabilities_require_configured_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ConfigureSecurityPolicy(SafeMode=False)
    Required = [Capability.MUTATE.value, Capability.FILESYSTEM_WRITE.value]

    assert not SecurityManager.validate_action("export", "WRITE", Required)
    ConfigureFilesystemPolicy(WriteRoot=tmp_path)
    assert SecurityManager.validate_action("export", "WRITE", Required)


def test_safe_mode_allows_filesystem_reads_only_with_a_read_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ConfigureSecurityPolicy(SafeMode=True)
    Required = [Capability.READ.value, Capability.FILESYSTEM_READ.value]

    assert not SecurityManager.validate_action("scene", "OPEN", Required)
    ConfigureFilesystemPolicy(ReadRoot=tmp_path)
    assert SecurityManager.validate_action("scene", "OPEN", Required)
    assert not SecurityManager.validate_action(
        "scene",
        "OPEN",
        [Capability.MUTATE.value, Capability.FILESYSTEM_READ.value],
    )


def test_request_must_opt_in_before_locally_approved_overwrite(tmp_path: Path) -> None:
    Target = tmp_path / "existing.glb"
    Target.write_bytes(b"sentinel")
    ConfigureFilesystemPolicy(WriteRoot=tmp_path, AllowOverwrite=True)

    with pytest.raises(FilesystemPolicyError) as Captured:
        PathValidator.validate_and_prepare(Target, {".glb"}, overwrite=False)

    assert Captured.value.Code == "FILESYSTEM_OVERWRITE_NOT_REQUESTED"
    assert Target.read_bytes() == b"sentinel"
    assert PathValidator.validate_and_prepare(Target, {".glb"}, overwrite=True) == str(
        Target.resolve()
    )


def test_prepare_revalidates_after_parent_creation(tmp_path: Path) -> None:
    Root = tmp_path / "root"
    Root.mkdir()
    Policy = FilesystemPolicy(WriteRoot=Root)

    Decision = Policy.RequirePath(
        Root / "new" / "asset.fbx",
        FilesystemAccess.WRITE,
        {".fbx"},
        CreateParents=True,
    )

    assert Decision.Allowed is True
    assert (Root / "new").is_dir()


def test_policy_error_is_structured_and_does_not_echo_denied_path(tmp_path: Path) -> None:
    Root = tmp_path / "root"
    Root.mkdir()
    SecretPath = tmp_path / "outside" / "secret.blend"

    with pytest.raises(FilesystemPolicyError) as Captured:
        FilesystemPolicy(ReadRoot=Root).RequirePath(SecretPath, FilesystemAccess.READ)

    assert Captured.value.Code == "FILESYSTEM_PATH_OUTSIDE_ROOT"
    assert str(SecretPath) not in Captured.value.PublicMessage


def test_invalid_access_and_byte_paths_fail_closed(tmp_path: Path) -> None:
    Policy = FilesystemPolicy(WriteRoot=tmp_path)

    ByteDecision = Policy.EvaluatePath(b"asset.glb", FilesystemAccess.WRITE)  # type: ignore[arg-type]
    with pytest.raises(FilesystemPolicyError) as Captured:
        Policy.EvaluatePath("asset.glb", "WRITE")  # type: ignore[arg-type]

    assert ByteDecision.Code == "INVALID_FILESYSTEM_PATH"
    assert Captured.value.Code == "INVALID_FILESYSTEM_ACCESS"
