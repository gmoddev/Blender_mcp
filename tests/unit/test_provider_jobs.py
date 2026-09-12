"""Security tests for the provider preparation and commit lifecycle."""

from __future__ import annotations

import threading
from dataclasses import fields

import pytest

from blender_mcp.core.provider_jobs import (
    ProviderCommitOutcome,
    ProviderJobError,
    ProviderJobLimits,
    ProviderJobManager,
    ProviderJobSnapshot,
    ProviderJobStatus,
    ProviderPreparedPayload,
)


DigestA = "a" * 64
DigestB = "b" * 64


def AssertDenied(Code: str, Callback: object) -> ProviderJobError:
    with pytest.raises(ProviderJobError) as Captured:
        Callback()
    assert Captured.value.Code == Code
    assert str(Captured.value) == Captured.value.PublicMessage
    return Captured.value


def WaitForStatus(
    Manager: ProviderJobManager,
    JobId: str,
    Statuses: set[ProviderJobStatus],
) -> ProviderJobSnapshot:
    for _ in range(100):
        Snapshot = Manager.Get(JobId)
        if Snapshot.Status in Statuses:
            return Snapshot
        threading.Event().wait(0.01)
    raise AssertionError(f"Job did not reach one of {Statuses}")


def test_preparation_commit_and_cleanup_use_their_intended_threads() -> None:
    MainThreadId = threading.get_ident()
    PrepareThreadIds: list[int] = []
    CommitThreadIds: list[int] = []
    CleanupThreadIds: list[int] = []
    Cleaned = threading.Event()
    Manager = ProviderJobManager()

    def Prepare(Token: object) -> ProviderPreparedPayload:
        del Token
        PrepareThreadIds.append(threading.get_ident())

        def Cleanup() -> None:
            CleanupThreadIds.append(threading.get_ident())
            Cleaned.set()

        return ProviderPreparedPayload({"opaque": 1}, Cleanup)

    def Commit(Value: object) -> ProviderCommitOutcome:
        assert Value == {"opaque": 1}
        CommitThreadIds.append(threading.get_ident())
        return ProviderCommitOutcome("IMPORTED", 3)

    try:
        Submitted = Manager.Submit("request-1", DigestA, "AssetImport", Prepare, Commit)
        Pending = Manager.WaitForSettledPreparation(Submitted.JobId, 2.0)
        assert Pending.Status == ProviderJobStatus.COMMIT_PENDING

        Manager.RunNextCommit()
        Completed = Manager.WaitForSettledPreparation(Submitted.JobId, 2.0)

        assert Completed.Status == ProviderJobStatus.COMPLETED
        assert Completed.OutcomeCode == "IMPORTED"
        assert Completed.AffectedCount == 3
        assert PrepareThreadIds[0] != MainThreadId
        assert CommitThreadIds == [MainThreadId]
        assert CleanupThreadIds[0] != MainThreadId
        assert Cleaned.is_set()
    finally:
        Manager.Shutdown(Wait=True)


def test_duplicate_request_returns_existing_job_and_conflict_fails_closed() -> None:
    Release = threading.Event()
    Calls = 0

    def Prepare(Token: object) -> ProviderPreparedPayload:
        nonlocal Calls
        del Token
        Calls += 1
        Release.wait(2.0)
        return ProviderPreparedPayload(None, lambda: None)

    Manager = ProviderJobManager(ProviderJobLimits(MaxWorkers=1, MaxActiveJobs=2))
    try:
        First = Manager.Submit("same-request", DigestA, "AssetImport", Prepare, lambda Value: None)  # type: ignore[arg-type,return-value]
        Duplicate = Manager.Submit(
            "same-request",
            DigestA,
            "AssetImport",
            Prepare,
            lambda Value: None,  # type: ignore[arg-type,return-value]
        )
        assert Duplicate.JobId == First.JobId
        AssertDenied(
            "PROVIDER_JOB_REQUEST_CONFLICT",
            lambda: Manager.Submit(
                "same-request",
                DigestB,
                "AssetImport",
                Prepare,
                lambda Value: None,  # type: ignore[arg-type,return-value]
            ),
        )
        assert Calls == 1
    finally:
        Manager.Cancel(First.JobId)
        Release.set()
        Manager.Shutdown(Wait=True)


def test_active_capacity_is_bounded_before_executor_queue_growth() -> None:
    Release = threading.Event()

    def Prepare(Token: object) -> ProviderPreparedPayload:
        del Token
        Release.wait(2.0)
        return ProviderPreparedPayload(None, lambda: None)

    Manager = ProviderJobManager(ProviderJobLimits(MaxWorkers=1, MaxActiveJobs=1))
    try:
        First = Manager.Submit("capacity-1", DigestA, "Download", Prepare, lambda Value: None)  # type: ignore[arg-type,return-value]
        AssertDenied(
            "PROVIDER_JOB_CAPACITY_EXCEEDED",
            lambda: Manager.Submit(
                "capacity-2",
                DigestB,
                "Download",
                Prepare,
                lambda Value: None,  # type: ignore[arg-type,return-value]
            ),
        )
    finally:
        Manager.Cancel(First.JobId)
        Release.set()
        Manager.Shutdown(Wait=True)


def test_queued_cancellation_never_runs_preparation() -> None:
    Release = threading.Event()
    SecondCalled = threading.Event()

    def BlockingPrepare(Token: object) -> ProviderPreparedPayload:
        del Token
        Release.wait(2.0)
        return ProviderPreparedPayload(None, lambda: None)

    def SecondPrepare(Token: object) -> ProviderPreparedPayload:
        del Token
        SecondCalled.set()
        return ProviderPreparedPayload(None, lambda: None)

    Manager = ProviderJobManager(ProviderJobLimits(MaxWorkers=1, MaxActiveJobs=2))
    try:
        First = Manager.Submit(
            "queued-1",
            DigestA,
            "Download",
            BlockingPrepare,
            lambda Value: None,  # type: ignore[arg-type,return-value]
        )
        WaitForStatus(Manager, First.JobId, {ProviderJobStatus.PREPARING})
        Second = Manager.Submit(
            "queued-2",
            DigestB,
            "Download",
            SecondPrepare,
            lambda Value: None,  # type: ignore[arg-type,return-value]
        )

        Cancelled = Manager.Cancel(Second.JobId)

        assert Cancelled.Status == ProviderJobStatus.CANCELLED
        assert Cancelled.RetrySafe is True
        Release.set()
        Manager.Shutdown(Wait=True)
        assert not SecondCalled.is_set()
    finally:
        Release.set()
        Manager.Shutdown(Wait=True)


def test_running_preparation_cancellation_is_truthful_and_skips_commit() -> None:
    Started = threading.Event()
    CommitCalled = threading.Event()

    def Prepare(Token: object) -> ProviderPreparedPayload:
        Started.set()
        while not Token.IsCancellationRequested:  # type: ignore[attr-defined]
            threading.Event().wait(0.005)
        Token.RaiseIfCancellationRequested()  # type: ignore[attr-defined]
        raise AssertionError

    Manager = ProviderJobManager()
    try:
        Job = Manager.Submit(
            "cancel-running",
            DigestA,
            "Download",
            Prepare,
            lambda Value: CommitCalled.set(),  # type: ignore[arg-type,return-value]
        )
        assert Started.wait(1.0)

        Requested = Manager.Cancel(Job.JobId)
        Completed = Manager.WaitForSettledPreparation(Job.JobId, 2.0)

        assert Requested.Status == ProviderJobStatus.CANCEL_REQUESTED
        assert Completed.Status == ProviderJobStatus.CANCELLED
        assert Completed.RetrySafe is False
        assert not CommitCalled.is_set()
    finally:
        Manager.Shutdown(Wait=True)


def test_cancelling_prepared_job_cleans_without_committing() -> None:
    Cleaned = threading.Event()
    CommitCalled = threading.Event()
    Manager = ProviderJobManager()
    try:
        Job = Manager.Submit(
            "cancel-prepared",
            DigestA,
            "Download",
            lambda Token: ProviderPreparedPayload(None, Cleaned.set),
            lambda Value: CommitCalled.set(),  # type: ignore[arg-type,return-value]
        )
        Manager.WaitForSettledPreparation(Job.JobId, 2.0)

        Manager.Cancel(Job.JobId)
        Cancelled = Manager.WaitForSettledPreparation(Job.JobId, 2.0)

        assert Cancelled.Status == ProviderJobStatus.CANCELLED
        assert Cancelled.RetrySafe is False
        assert Cleaned.is_set()
        assert not CommitCalled.is_set()
    finally:
        Manager.Shutdown(Wait=True)


def test_non_main_thread_commit_attempt_fails_before_callback() -> None:
    CommitCalled = threading.Event()
    Errors: list[ProviderJobError] = []
    Manager = ProviderJobManager()
    try:
        Job = Manager.Submit(
            "wrong-thread",
            DigestA,
            "Import",
            lambda Token: ProviderPreparedPayload(None, lambda: None),
            lambda Value: CommitCalled.set(),  # type: ignore[arg-type,return-value]
        )
        Manager.WaitForSettledPreparation(Job.JobId, 2.0)

        def Run() -> None:
            try:
                Manager.RunNextCommit()
            except ProviderJobError as Error:
                Errors.append(Error)

        Worker = threading.Thread(target=Run)
        Worker.start()
        Worker.join(1.0)

        assert Errors[0].Code == "PROVIDER_JOB_MAIN_THREAD_REQUIRED"
        assert not CommitCalled.is_set()
        assert Manager.Get(Job.JobId).Status == ProviderJobStatus.COMMIT_PENDING
    finally:
        Manager.Cancel(Job.JobId)
        Manager.Shutdown(Wait=True)


def test_non_main_thread_shutdown_fails_without_changing_manager_state() -> None:
    Errors: list[ProviderJobError] = []
    Manager = ProviderJobManager()

    def Run() -> None:
        try:
            Manager.Shutdown()
        except ProviderJobError as Error:
            Errors.append(Error)

    Worker = threading.Thread(target=Run)
    Worker.start()
    Worker.join(1.0)

    try:
        assert Errors[0].Code == "PROVIDER_JOB_MAIN_THREAD_REQUIRED"
        Job = Manager.Submit(
            "after-worker-shutdown",
            DigestA,
            "Download",
            lambda Token: ProviderPreparedPayload(None, lambda: None),
            lambda Value: ProviderCommitOutcome("DONE"),
        )
        assert Job.Status == ProviderJobStatus.QUEUED
    finally:
        Manager.Shutdown(Wait=True)


def test_non_main_thread_manager_initialization_fails_closed() -> None:
    Errors: list[ProviderJobError] = []

    def Run() -> None:
        try:
            ProviderJobManager()
        except ProviderJobError as Error:
            Errors.append(Error)

    Worker = threading.Thread(target=Run)
    Worker.start()
    Worker.join(1.0)

    assert Errors[0].Code == "PROVIDER_JOB_MAIN_THREAD_REQUIRED"


def test_prepare_and_commit_failures_are_redacted_and_cleanup_runs() -> None:
    PrepareManager = ProviderJobManager()
    try:
        PrepareJob = PrepareManager.Submit(
            "prepare-fail",
            DigestA,
            "Download",
            lambda Token: (_ for _ in ()).throw(RuntimeError("secret-prepare-canary")),
            lambda Value: ProviderCommitOutcome("UNREACHABLE"),
        )
        PrepareFailed = PrepareManager.WaitForSettledPreparation(PrepareJob.JobId, 2.0)
        assert PrepareFailed.Status == ProviderJobStatus.FAILED
        assert PrepareFailed.FailureCode == "PROVIDER_PREPARE_FAILED"
        assert "secret" not in repr(PrepareFailed)
    finally:
        PrepareManager.Shutdown(Wait=True)

    Cleaned = threading.Event()
    CommitManager = ProviderJobManager()
    try:
        CommitJob = CommitManager.Submit(
            "commit-fail",
            DigestB,
            "Import",
            lambda Token: ProviderPreparedPayload(None, Cleaned.set),
            lambda Value: (_ for _ in ()).throw(RuntimeError("secret-commit-canary")),
        )
        CommitManager.WaitForSettledPreparation(CommitJob.JobId, 2.0)
        CommitManager.RunNextCommit()
        CommitFailed = CommitManager.WaitForSettledPreparation(CommitJob.JobId, 2.0)
        assert CommitFailed.Status == ProviderJobStatus.FAILED
        assert CommitFailed.FailureCode == "PROVIDER_COMMIT_FAILED"
        assert Cleaned.is_set()
        assert "secret" not in repr(CommitFailed)
    finally:
        CommitManager.Shutdown(Wait=True)


def test_cleanup_failure_after_commit_preserves_successful_commit_fact() -> None:
    Manager = ProviderJobManager()
    try:
        Job = Manager.Submit(
            "cleanup-fail",
            DigestA,
            "Import",
            lambda Token: ProviderPreparedPayload(
                None,
                lambda: (_ for _ in ()).throw(RuntimeError("cleanup-canary")),
            ),
            lambda Value: ProviderCommitOutcome("IMPORTED", 1),
        )
        Manager.WaitForSettledPreparation(Job.JobId, 2.0)
        Manager.RunNextCommit()
        Failed = Manager.WaitForSettledPreparation(Job.JobId, 2.0)

        assert Failed.Status == ProviderJobStatus.COMPLETED_CLEANUP_FAILED
        assert Failed.OutcomeCode == "IMPORTED"
        assert Failed.AffectedCount == 1
        assert Failed.FailureCode == "PROVIDER_ARTIFACT_CLEANUP_FAILED"
        assert Failed.RetrySafe is False
    finally:
        Manager.Shutdown(Wait=True)


def test_cleanup_failure_preserves_failed_and_cancelled_outcomes() -> None:
    def FailCleanup() -> None:
        raise RuntimeError("cleanup-canary")

    FailedManager = ProviderJobManager()
    try:
        FailedJob = FailedManager.Submit(
            "failed-cleanup",
            DigestA,
            "Import",
            lambda Token: ProviderPreparedPayload(None, FailCleanup),
            lambda Value: (_ for _ in ()).throw(RuntimeError("commit-canary")),
        )
        FailedManager.WaitForSettledPreparation(FailedJob.JobId, 2.0)
        FailedManager.RunNextCommit()
        Failed = FailedManager.WaitForSettledPreparation(FailedJob.JobId, 2.0)
        assert Failed.Status == ProviderJobStatus.FAILED_CLEANUP_FAILED
        assert Failed.FailureCode == "PROVIDER_ARTIFACT_CLEANUP_FAILED"
        assert Failed.OutcomeCode is None
    finally:
        FailedManager.Shutdown(Wait=True)

    CancelledManager = ProviderJobManager()
    try:
        CancelledJob = CancelledManager.Submit(
            "cancelled-cleanup",
            DigestB,
            "Download",
            lambda Token: ProviderPreparedPayload(None, FailCleanup),
            lambda Value: ProviderCommitOutcome("UNREACHABLE"),
        )
        CancelledManager.WaitForSettledPreparation(CancelledJob.JobId, 2.0)
        CancelledManager.Cancel(CancelledJob.JobId)
        Cancelled = CancelledManager.WaitForSettledPreparation(CancelledJob.JobId, 2.0)
        assert Cancelled.Status == ProviderJobStatus.CANCELLED_CLEANUP_FAILED
        assert Cancelled.FailureCode == "PROVIDER_ARTIFACT_CLEANUP_FAILED"
        assert Cancelled.RetrySafe is False
    finally:
        CancelledManager.Shutdown(Wait=True)


def test_wait_timeout_does_not_change_running_preparation_state() -> None:
    Release = threading.Event()
    Manager = ProviderJobManager()

    def Prepare(Token: object) -> ProviderPreparedPayload:
        del Token
        Release.wait(2.0)
        return ProviderPreparedPayload(None, lambda: None)

    try:
        Job = Manager.Submit(
            "wait-timeout",
            DigestA,
            "Download",
            Prepare,
            lambda Value: ProviderCommitOutcome("DONE"),
        )
        WaitForStatus(Manager, Job.JobId, {ProviderJobStatus.PREPARING})

        Error = AssertDenied(
            "PROVIDER_JOB_WAIT_TIMEOUT",
            lambda: Manager.WaitForSettledPreparation(Job.JobId, 0.01),
        )

        assert Error.RetrySafe is False
        assert Manager.Get(Job.JobId).Status == ProviderJobStatus.PREPARING
    finally:
        Manager.Cancel(Job.JobId)
        Release.set()
        Manager.Shutdown(Wait=True)


def test_cancellation_after_commit_starts_is_denied_as_indeterminate() -> None:
    CommitStarted = threading.Event()
    ReleaseCommit = threading.Event()
    CancellationErrors: list[ProviderJobError] = []
    Manager = ProviderJobManager()

    def Commit(Value: object) -> ProviderCommitOutcome:
        del Value
        CommitStarted.set()

        def AttemptCancel() -> None:
            try:
                Manager.Cancel(Job.JobId)
            except ProviderJobError as Error:
                CancellationErrors.append(Error)
            finally:
                ReleaseCommit.set()

        threading.Thread(target=AttemptCancel).start()
        assert ReleaseCommit.wait(1.0)
        return ProviderCommitOutcome("IMPORTED")

    try:
        Job = Manager.Submit(
            "commit-cancel",
            DigestA,
            "Import",
            lambda Token: ProviderPreparedPayload(None, lambda: None),
            Commit,
        )
        Manager.WaitForSettledPreparation(Job.JobId, 2.0)
        Manager.RunNextCommit()
        Completed = Manager.WaitForSettledPreparation(Job.JobId, 2.0)

        assert CommitStarted.is_set()
        assert CancellationErrors[0].Code == "PROVIDER_JOB_COMMITTING"
        assert CancellationErrors[0].RetrySafe is False
        assert Completed.Status == ProviderJobStatus.COMPLETED
    finally:
        Manager.Shutdown(Wait=True)


def test_shutdown_during_commit_is_denied_without_stranding_cleanup() -> None:
    ShutdownErrors: list[ProviderJobError] = []
    Cleaned = threading.Event()
    Manager = ProviderJobManager()

    def Commit(Value: object) -> ProviderCommitOutcome:
        del Value
        try:
            Manager.Shutdown()
        except ProviderJobError as Error:
            ShutdownErrors.append(Error)
        return ProviderCommitOutcome("IMPORTED")

    try:
        Job = Manager.Submit(
            "shutdown-during-commit",
            DigestA,
            "Import",
            lambda Token: ProviderPreparedPayload(None, Cleaned.set),
            Commit,
        )
        Manager.WaitForSettledPreparation(Job.JobId, 2.0)
        Manager.RunNextCommit()
        Completed = Manager.WaitForSettledPreparation(Job.JobId, 2.0)

        assert ShutdownErrors[0].Code == "PROVIDER_JOB_COMMITTING"
        assert Completed.Status == ProviderJobStatus.COMPLETED
        assert Cleaned.is_set()
    finally:
        Manager.Shutdown(Wait=True)


def test_shutdown_cleans_prepared_work_and_denies_new_submissions() -> None:
    Cleaned = threading.Event()
    Manager = ProviderJobManager()
    Job = Manager.Submit(
        "shutdown",
        DigestA,
        "Download",
        lambda Token: ProviderPreparedPayload(None, Cleaned.set),
        lambda Value: ProviderCommitOutcome("UNREACHABLE"),
    )
    Manager.WaitForSettledPreparation(Job.JobId, 2.0)

    Manager.Shutdown(Wait=True)

    assert Manager.Get(Job.JobId).Status == ProviderJobStatus.CANCELLED
    assert Cleaned.is_set()
    AssertDenied(
        "PROVIDER_JOB_MANAGER_CLOSED",
        lambda: Manager.Submit(
            "after-shutdown",
            DigestB,
            "Download",
            lambda Token: ProviderPreparedPayload(None, lambda: None),
            lambda Value: ProviderCommitOutcome("UNREACHABLE"),
        ),
    )


def test_expired_terminal_job_is_evicted_with_its_request_identity() -> None:
    TimeValue = [0.0]
    Manager = ProviderJobManager(
        ProviderJobLimits(RetentionSeconds=10.0),
        Clock=lambda: TimeValue[0],
    )
    try:
        First = Manager.Submit(
            "evict-me",
            DigestA,
            "Import",
            lambda Token: ProviderPreparedPayload(None, lambda: None),
            lambda Value: ProviderCommitOutcome("DONE"),
        )
        Manager.WaitForSettledPreparation(First.JobId, 2.0)
        Manager.RunNextCommit()
        WaitForStatus(Manager, First.JobId, {ProviderJobStatus.COMPLETED})
        TimeValue[0] = 11.0

        Replacement = Manager.Submit(
            "evict-me",
            DigestB,
            "Import",
            lambda Token: ProviderPreparedPayload(None, lambda: None),
            lambda Value: ProviderCommitOutcome("DONE"),
        )

        assert Replacement.JobId != First.JobId
        AssertDenied("PROVIDER_JOB_UNKNOWN", lambda: Manager.Get(First.JobId))
    finally:
        Manager.Shutdown(Wait=True)


def test_snapshot_never_exposes_callbacks_payloads_or_request_digest() -> None:
    SnapshotFields = {Field.name for Field in fields(ProviderJobSnapshot)}
    assert "RequestDigest" not in SnapshotFields
    assert "Prepare" not in SnapshotFields
    assert "Commit" not in SnapshotFields
    assert "Prepared" not in SnapshotFields


def test_terminal_ledger_releases_callback_closures_and_prepared_payload() -> None:
    Manager = ProviderJobManager()
    try:
        Job = Manager.Submit(
            "release-closures",
            DigestA,
            "Import",
            lambda Token: ProviderPreparedPayload(object(), lambda: None),
            lambda Value: ProviderCommitOutcome("DONE"),
        )
        Manager.WaitForSettledPreparation(Job.JobId, 2.0)
        Manager.RunNextCommit()
        Manager.WaitForSettledPreparation(Job.JobId, 2.0)

        Retained = Manager._Jobs[Job.JobId]
        assert Retained.Prepare is None
        assert Retained.Commit is None
        assert Retained.Prepared is None
    finally:
        Manager.Shutdown(Wait=True)


def test_limits_identity_outcomes_and_wait_deadlines_validate() -> None:
    with pytest.raises(ValueError):
        ProviderJobLimits(MaxWorkers=2, MaxActiveJobs=1)
    with pytest.raises(ValueError):
        ProviderJobLimits(MaxActiveJobs=2, MaxRetainedJobs=1)
    with pytest.raises(ValueError):
        ProviderCommitOutcome("lowercase")
    with pytest.raises(ValueError):
        ProviderCommitOutcome(1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ProviderPreparedPayload(None, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ProviderJobManager(Limits="invalid")  # type: ignore[arg-type]

    Manager = ProviderJobManager()
    try:
        AssertDenied(
            "PROVIDER_REQUEST_ID_INVALID",
            lambda: Manager.Submit(
                "../bad",
                DigestA,
                "Import",
                lambda Token: None,
                lambda Value: None,  # type: ignore[arg-type,return-value]
            ),
        )
        AssertDenied(
            "PROVIDER_REQUEST_DIGEST_INVALID",
            lambda: Manager.Submit(
                "valid",
                "short",
                "Import",
                lambda Token: None,
                lambda Value: None,  # type: ignore[arg-type,return-value]
            ),
        )
        AssertDenied(
            "PROVIDER_JOB_WAIT_INVALID",
            lambda: Manager.WaitForSettledPreparation("00000000-0000-4000-8000-000000000000", 0),
        )
        AssertDenied("PROVIDER_JOB_ID_INVALID", lambda: Manager.Get("unknown"))
        AssertDenied("PROVIDER_JOB_SHUTDOWN_INVALID", lambda: Manager.Shutdown(Wait="yes"))  # type: ignore[arg-type]
    finally:
        Manager.Shutdown(Wait=True)
