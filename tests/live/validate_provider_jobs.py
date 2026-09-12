"""Exercise provider preparation and main-thread commit in Blender's embedded Python."""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
from pathlib import Path

import bpy


RepoRoot = Path(__file__).resolve().parents[2]
if str(RepoRoot) not in sys.path:
    sys.path.insert(0, str(RepoRoot))

from blender_mcp.core.provider_jobs import (  # noqa: E402
    ProviderCommitOutcome,
    ProviderJobManager,
    ProviderJobStatus,
    ProviderPreparedPayload,
)


MainThreadId = threading.get_ident()
BaseRoot = Path(tempfile.mkdtemp(prefix="blender_mcp_provider_job_live_"))
ArtifactPath = BaseRoot / "prepared.bin"
PrepareThreadId: int | None = None
CleanupThreadId: int | None = None
Manager = ProviderJobManager()


def Prepare(Token: object) -> ProviderPreparedPayload:
    global PrepareThreadId
    PrepareThreadId = threading.get_ident()
    assert PrepareThreadId != MainThreadId
    assert not Token.IsCancellationRequested
    ArtifactPath.write_bytes(b"bounded-provider-artifact")

    def Cleanup() -> None:
        global CleanupThreadId
        CleanupThreadId = threading.get_ident()
        assert CleanupThreadId != MainThreadId
        ArtifactPath.unlink(missing_ok=True)

    return ProviderPreparedPayload(ArtifactPath, Cleanup)


def Commit(Value: object) -> ProviderCommitOutcome:
    assert threading.get_ident() == MainThreadId
    assert isinstance(Value, Path)
    assert Value.read_bytes() == b"bounded-provider-artifact"
    Object = bpy.data.objects.new("BlenderMCPProviderJobCanary", None)
    bpy.context.scene.collection.objects.link(Object)
    return ProviderCommitOutcome("IMPORTED", 1)


try:
    Job = Manager.Submit(
        "blender-live-provider-job",
        "a" * 64,
        "ProviderImport",
        Prepare,
        Commit,
    )
    Pending = Manager.WaitForSettledPreparation(Job.JobId, 5.0)
    assert Pending.Status == ProviderJobStatus.COMMIT_PENDING
    Manager.RunNextCommit()
    Completed = Manager.WaitForSettledPreparation(Job.JobId, 5.0)
    assert Completed.Status == ProviderJobStatus.COMPLETED
    assert Completed.OutcomeCode == "IMPORTED"
    assert Completed.AffectedCount == 1
    assert bpy.data.objects.get("BlenderMCPProviderJobCanary") is not None
    assert not ArtifactPath.exists()
    assert PrepareThreadId is not None and PrepareThreadId != MainThreadId
    assert CleanupThreadId is not None and CleanupThreadId != MainThreadId
    print("[BlenderMCP:ProviderJobs] Live provider job boundary validation passed")
finally:
    Manager.Shutdown(Wait=True)
    Object = bpy.data.objects.get("BlenderMCPProviderJobCanary")
    if Object is not None:
        bpy.data.objects.remove(Object, do_unlink=True)
    shutil.rmtree(BaseRoot, ignore_errors=True)
