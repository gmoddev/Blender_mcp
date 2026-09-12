"""Security tests for transactional provider GLB imports."""

from __future__ import annotations

import json
import struct
import threading
from pathlib import Path

import pytest

from blender_mcp.core.archive_boundary import ArtifactStore
from blender_mcp.core.provider_content import (
    BinChunkType,
    GlbMagic,
    GlbVersion,
    InspectProviderGlb,
    JsonChunkType,
    ProviderImportLimits,
    ProviderImportSnapshot,
)
from blender_mcp.core.provider_import import (
    CommitProviderGlb,
    ProviderImportCommitError,
    ProviderNativeImportLimits,
)


Before = ProviderImportSnapshot(1, 1, 0, 0, 0, 3, 1)
After = ProviderImportSnapshot(2, 2, 0, 0, 0, 6, 2)


class FakeBackend:
    def __init__(self) -> None:
        self.State = object()
        self.After = After
        self.ImportResult = True
        self.ImportError: Exception | None = None
        self.RollbackError: Exception | None = None
        self.StateMatches = True
        self.Calls: list[str] = []

    def CaptureState(self) -> object:
        self.Calls.append("capture")
        return self.State

    def Snapshot(self) -> ProviderImportSnapshot:
        self.Calls.append("snapshot")
        return Before if self.Calls.count("snapshot") == 1 else self.After

    def ImportGlb(self, PathValue: Path) -> bool:
        self.Calls.append("import")
        assert PathValue.suffix == ".glb"
        if self.ImportError is not None:
            raise self.ImportError
        return self.ImportResult

    def RestoreContext(self, State: object) -> None:
        self.Calls.append("restore")
        assert State is self.State

    def Rollback(self, State: object) -> None:
        self.Calls.append("rollback")
        assert State is self.State
        if self.RollbackError is not None:
            raise self.RollbackError

    def MatchesState(self, State: object) -> bool:
        self.Calls.append("verify")
        assert State is self.State
        return self.StateMatches


def BuildGlb() -> bytes:
    BinBytes = b"\x00" * 36
    Document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(BinBytes)}],
        "bufferViews": [{"buffer": 0, "byteLength": len(BinBytes)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    JsonBytes = json.dumps(Document, separators=(",", ":")).encode("utf-8")
    JsonBytes += b" " * (-len(JsonBytes) % 4)
    Chunks = struct.pack("<II", len(JsonBytes), JsonChunkType) + JsonBytes
    Chunks += struct.pack("<II", len(BinBytes), BinChunkType) + BinBytes
    return struct.pack("<III", GlbMagic, GlbVersion, 12 + len(Chunks)) + Chunks


def PreparedWorkspace(tmp_path: Path):
    Root = tmp_path / "artifacts"
    Root.mkdir()
    Workspace = ArtifactStore(Root).CreateWorkspace("request-1")
    Artifact = Workspace.Root / "model.glb"
    Artifact.write_bytes(BuildGlb())
    Workspace.RelativeFiles = ("model.glb",)
    return Workspace, Artifact, InspectProviderGlb(Workspace, "model.glb")


def ErrorCode(Captured: pytest.ExceptionInfo[ProviderImportCommitError]) -> str:
    return Captured.value.Code


def test_success_revalidates_imports_validates_and_restores_context(tmp_path: Path) -> None:
    Workspace, _Artifact, Plan = PreparedWorkspace(tmp_path)
    Backend = FakeBackend()

    Outcome = CommitProviderGlb(Workspace, Plan, Backend=Backend, Clock=iter((1.0, 2.0)).__next__)

    assert Outcome.Code == "PROVIDER_GLB_IMPORTED"
    assert Outcome.AffectedCount == 1
    assert Backend.Calls == ["capture", "snapshot", "import", "snapshot", "restore"]
    Workspace.Cleanup()


@pytest.mark.parametrize(
    ("Configure", "ClockValues", "Code"),
    [
        (
            lambda Backend: setattr(Backend, "ImportResult", False),
            (1.0, 2.0),
            "PROVIDER_IMPORT_OPERATOR_FAILED",
        ),
        (
            lambda Backend: setattr(Backend, "ImportError", RuntimeError("secret-canary")),
            (1.0, 2.0),
            "PROVIDER_IMPORT_FAILED",
        ),
        (lambda Backend: setattr(Backend, "After", Before), (1.0, 2.0), "PROVIDER_IMPORT_EMPTY"),
        (
            lambda Backend: setattr(Backend, "After", ProviderImportSnapshot(3, 2, 0, 0, 0, 6, 2)),
            (1.0, 2.0),
            "PROVIDER_IMPORT_LIMIT_EXCEEDED",
        ),
        (lambda Backend: None, (1.0, 50.0), "PROVIDER_IMPORT_DEADLINE_EXCEEDED"),
        (lambda Backend: None, (2.0, 1.0), "PROVIDER_IMPORT_CLOCK_INVALID"),
    ],
)
def test_every_post_admission_failure_rolls_back(
    tmp_path: Path, Configure, ClockValues: tuple[float, float], Code: str
) -> None:
    Workspace, _Artifact, Plan = PreparedWorkspace(tmp_path)
    Backend = FakeBackend()
    Configure(Backend)
    ImportLimits = ProviderImportLimits(MaxObjects=1)

    with pytest.raises(ProviderImportCommitError) as Captured:
        CommitProviderGlb(
            Workspace,
            Plan,
            ImportLimits=ImportLimits,
            Backend=Backend,
            Clock=iter(ClockValues).__next__,
        )

    assert ErrorCode(Captured) == Code
    assert Captured.value.RollbackComplete is True
    assert "secret-canary" not in str(Captured.value)
    assert Backend.Calls[-2:] == ["rollback", "verify"]
    Workspace.Cleanup()


@pytest.mark.parametrize("Mismatch", [False, True])
def test_unverified_rollback_has_a_distinct_terminal_failure(
    tmp_path: Path, Mismatch: bool
) -> None:
    Workspace, _Artifact, Plan = PreparedWorkspace(tmp_path)
    Backend = FakeBackend()
    Backend.ImportResult = False
    Backend.StateMatches = not Mismatch
    if not Mismatch:
        Backend.RollbackError = RuntimeError("rollback-canary")

    with pytest.raises(ProviderImportCommitError) as Captured:
        CommitProviderGlb(Workspace, Plan, Backend=Backend, Clock=iter((1.0, 2.0)).__next__)

    assert ErrorCode(Captured) == "PROVIDER_IMPORT_ROLLBACK_FAILED"
    assert Captured.value.RollbackComplete is False
    assert "canary" not in str(Captured.value)
    Workspace.Cleanup()


def test_changed_artifact_fails_before_baseline_or_import(tmp_path: Path) -> None:
    Workspace, Artifact, Plan = PreparedWorkspace(tmp_path)
    Backend = FakeBackend()
    Artifact.write_bytes(BuildGlb() + b"\x00\x00\x00\x00")

    with pytest.raises(ProviderImportCommitError) as Captured:
        CommitProviderGlb(Workspace, Plan, Backend=Backend)

    assert ErrorCode(Captured) in ("PROVIDER_GLB_HEADER_INVALID", "PROVIDER_CONTENT_CHANGED")
    assert Captured.value.RollbackComplete is True
    assert Backend.Calls == []
    Workspace.Cleanup()


def test_worker_thread_fails_before_revalidation_or_backend_use(tmp_path: Path) -> None:
    Workspace, _Artifact, Plan = PreparedWorkspace(tmp_path)
    Backend = FakeBackend()
    Errors: list[ProviderImportCommitError] = []

    def Run() -> None:
        try:
            CommitProviderGlb(Workspace, Plan, Backend=Backend)
        except ProviderImportCommitError as Error:
            Errors.append(Error)

    Worker = threading.Thread(target=Run)
    Worker.start()
    Worker.join(1.0)

    assert Errors[0].Code == "PROVIDER_IMPORT_MAIN_THREAD_REQUIRED"
    assert Errors[0].RollbackComplete is True
    assert Backend.Calls == []
    Workspace.Cleanup()


def test_invalid_native_import_limits_and_clock_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ProviderNativeImportLimits(MaxElapsedSeconds=float("inf"))
    Workspace, _Artifact, Plan = PreparedWorkspace(tmp_path)
    Backend = FakeBackend()

    with pytest.raises(ProviderImportCommitError) as Captured:
        CommitProviderGlb(Workspace, Plan, Backend=Backend, Clock=lambda: float("nan"))

    assert ErrorCode(Captured) == "PROVIDER_IMPORT_CLOCK_INVALID"
    assert Captured.value.RollbackComplete is True
    assert "import" not in Backend.Calls
    Workspace.Cleanup()
