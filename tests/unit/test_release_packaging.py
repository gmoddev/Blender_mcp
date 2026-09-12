"""Release integrity tests for the self-contained Blender extension."""

from __future__ import annotations

import hashlib
import tomllib
import zipfile
from pathlib import Path

import pytest

import create_release_zip as Release
from create_release_zip import GetExpectedWheelHashes, SourceDirectory, VerifyBundledWheels, Version


def test_manifest_declares_every_bundled_wheel_and_windows_platform() -> None:
    Manifest = tomllib.loads(
        (SourceDirectory / "blender_manifest.toml").read_text(encoding="utf-8")
    )
    DeclaredNames = {Path(Value).name for Value in Manifest["wheels"]}

    assert Manifest["platforms"] == ["windows-x64"]
    assert Manifest["version"] == Version
    assert DeclaredNames == set(GetExpectedWheelHashes())


def test_extension_source_retains_project_license() -> None:
    assert (SourceDirectory / "LICENSE").read_text(encoding="utf-8") == (
        SourceDirectory.parent / "LICENSE"
    ).read_text(encoding="utf-8")


def test_bundled_wheels_match_pinned_hash_inventory() -> None:
    VerifyBundledWheels()


def test_every_wheel_retains_distribution_metadata_and_license_material() -> None:
    for Name in GetExpectedWheelHashes():
        WheelPath = SourceDirectory / "wheels" / Name
        with zipfile.ZipFile(WheelPath) as Archive:
            Members = Archive.namelist()
            assert any(Member.endswith(".dist-info/METADATA") for Member in Members)
            assert any("LICENSE" in Member.upper() for Member in Members)


def test_hash_inventory_has_no_duplicate_or_unpinned_wheels() -> None:
    Expected = GetExpectedWheelHashes()
    assert len(Expected) == 6
    for Name, Digest in Expected.items():
        assert Name.endswith(".whl")
        assert len(Digest) == 64
        assert (
            Digest == hashlib.sha256((SourceDirectory / "wheels" / Name).read_bytes()).hexdigest()
        )


def test_uninventoried_wheel_fails_release_before_blender(tmp_path: Path, monkeypatch) -> None:
    WheelDirectory = tmp_path / "wheels"
    WheelDirectory.mkdir()
    (WheelDirectory / "expected.whl").write_bytes(b"expected")
    (WheelDirectory / "unexpected.whl").write_bytes(b"unexpected")
    ExpectedHash = hashlib.sha256(b"expected").hexdigest()
    (WheelDirectory / "SHA256SUMS").write_text(
        f"{ExpectedHash}  expected.whl\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Release, "SourceDirectory", tmp_path)

    with pytest.raises(RuntimeError, match="do not match"):
        Release.VerifyBundledWheels()
