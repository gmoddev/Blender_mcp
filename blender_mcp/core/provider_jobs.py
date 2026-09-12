"""Bounded provider preparation and serialized Blender commit lifecycle."""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum


class ProviderJobStatus(str, Enum):
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    COMMIT_PENDING = "COMMIT_PENDING"
    COMMITTING = "COMMITTING"
    CLEANING = "CLEANING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    COMPLETED_CLEANUP_FAILED = "COMPLETED_CLEANUP_FAILED"
    FAILED_CLEANUP_FAILED = "FAILED_CLEANUP_FAILED"
    CANCELLED_CLEANUP_FAILED = "CANCELLED_CLEANUP_FAILED"


TerminalStatuses = frozenset(
    {
        ProviderJobStatus.COMPLETED,
        ProviderJobStatus.FAILED,
        ProviderJobStatus.CANCELLED,
        ProviderJobStatus.COMPLETED_CLEANUP_FAILED,
        ProviderJobStatus.FAILED_CLEANUP_FAILED,
        ProviderJobStatus.CANCELLED_CLEANUP_FAILED,
    }
)


class ProviderJobError(RuntimeError):
    """Structured, redacted provider-job failure."""

    def __init__(self, Code: str, PublicMessage: str, RetrySafe: bool = False):
        super().__init__(PublicMessage)
        self.Code = Code
        self.PublicMessage = PublicMessage
        self.RetrySafe = RetrySafe


class ProviderCommitError(RuntimeError):
    """Structured failure raised by a trusted provider commit boundary."""

    def __init__(self, Code: str, PublicMessage: str):
        if not isinstance(Code, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", Code) is None:
            raise ValueError("Provider commit failures require a bounded code")
        super().__init__(PublicMessage)
        self.Code = Code
        self.PublicMessage = PublicMessage


class ProviderJobCancelled(RuntimeError):
    """Private cooperative-cancellation signal for preparation callbacks."""


@dataclass(frozen=True)
class ProviderJobLimits:
    MaxWorkers: int = 2
    MaxActiveJobs: int = 16
    MaxRetainedJobs: int = 256
    RetentionSeconds: float = 15 * 60.0

    def __post_init__(self) -> None:
        IntegerValues = (self.MaxWorkers, self.MaxActiveJobs, self.MaxRetainedJobs)
        if any(
            not isinstance(Value, int) or isinstance(Value, bool) or Value <= 0
            for Value in IntegerValues
        ):
            raise ValueError("Provider job limits must be positive integers")
        if self.MaxWorkers > self.MaxActiveJobs:
            raise ValueError("Provider workers cannot exceed active job capacity")
        if self.MaxActiveJobs > self.MaxRetainedJobs:
            raise ValueError("Active job capacity cannot exceed retained job capacity")
        if (
            not isinstance(self.RetentionSeconds, (int, float))
            or isinstance(self.RetentionSeconds, bool)
            or not 0 < self.RetentionSeconds <= 24 * 60 * 60
        ):
            raise ValueError("Provider job retention must be positive and bounded")


@dataclass(frozen=True)
class ProviderPreparedPayload:
    Value: object
    Cleanup: Callable[[], None]

    def __post_init__(self) -> None:
        if not callable(self.Cleanup):
            raise ValueError("Prepared provider payloads require cleanup")


@dataclass(frozen=True)
class ProviderCommitOutcome:
    Code: str
    AffectedCount: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.Code, str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", self.Code) is None
        ):
            raise ValueError("Provider outcomes require a bounded code")
        if (
            not isinstance(self.AffectedCount, int)
            or isinstance(self.AffectedCount, bool)
            or self.AffectedCount < 0
        ):
            raise ValueError("Provider outcome counts must be non-negative integers")


@dataclass(frozen=True)
class ProviderJobSnapshot:
    JobId: str
    RequestId: str
    Purpose: str
    Status: ProviderJobStatus
    FailureCode: str | None
    OutcomeCode: str | None
    AffectedCount: int
    RetrySafe: bool
    Terminal: bool


class ProviderCancellationToken:
    def __init__(self, Event: threading.Event):
        self._Event = Event

    @property
    def IsCancellationRequested(self) -> bool:
        return self._Event.is_set()

    def RaiseIfCancellationRequested(self) -> None:
        if self._Event.is_set():
            raise ProviderJobCancelled


PrepareCallback = Callable[[ProviderCancellationToken], ProviderPreparedPayload]
CommitCallback = Callable[[object], ProviderCommitOutcome]
ClockFunction = Callable[[], float]


@dataclass
class _ProviderJob:
    JobId: str
    RequestId: str
    RequestDigest: str
    Purpose: str
    Prepare: PrepareCallback | None
    Commit: CommitCallback | None
    CancelEvent: threading.Event
    CreatedAt: float
    Sequence: int
    Status: ProviderJobStatus = ProviderJobStatus.QUEUED
    Prepared: ProviderPreparedPayload | None = None
    FailureCode: str | None = None
    Outcome: ProviderCommitOutcome | None = None
    RetrySafe: bool = False
    TerminalAt: float | None = None


class ProviderJobManager:
    """Prepare provider artifacts on workers and commit them only on one main thread."""

    def __init__(
        self,
        Limits: ProviderJobLimits | None = None,
        Clock: ClockFunction | None = None,
    ) -> None:
        MainThreadId = threading.main_thread().ident
        if MainThreadId is None or threading.get_ident() != MainThreadId:
            raise _Deny(
                "PROVIDER_JOB_MAIN_THREAD_REQUIRED",
                "Provider job initialization requires Blender's main thread",
            )
        if Limits is not None and not isinstance(Limits, ProviderJobLimits):
            raise ValueError("Provider jobs require ProviderJobLimits")
        if Clock is not None and not callable(Clock):
            raise ValueError("Provider jobs require a monotonic clock")
        self.Limits = Limits or ProviderJobLimits()
        self._Clock = Clock or time.monotonic
        self._MainThreadId = MainThreadId
        self._Condition = threading.Condition(threading.RLock())
        self._Jobs: dict[str, _ProviderJob] = {}
        self._Requests: dict[str, str] = {}
        self._CommitQueue: deque[str] = deque()
        self._Sequence = 0
        self._Accepting = True
        self._Executor = ThreadPoolExecutor(
            max_workers=self.Limits.MaxWorkers,
            thread_name_prefix="BlenderMCP-Provider",
        )

    def Submit(
        self,
        RequestId: str,
        RequestDigest: str,
        Purpose: str,
        Prepare: PrepareCallback,
        Commit: CommitCallback,
    ) -> ProviderJobSnapshot:
        _ValidateIdentity(RequestId, RequestDigest, Purpose, Prepare, Commit)
        with self._Condition:
            if not self._Accepting:
                raise _Deny("PROVIDER_JOB_MANAGER_CLOSED", "Provider jobs are not accepting work")
            self._EvictLocked()
            ExistingId = self._Requests.get(RequestId)
            if ExistingId is not None:
                Existing = self._Jobs[ExistingId]
                if Existing.RequestDigest != RequestDigest or Existing.Purpose != Purpose:
                    raise _Deny(
                        "PROVIDER_JOB_REQUEST_CONFLICT",
                        "The provider request identity was already used for different work",
                    )
                return self._SnapshotLocked(Existing)
            ActiveCount = sum(Job.Status not in TerminalStatuses for Job in self._Jobs.values())
            if ActiveCount >= self.Limits.MaxActiveJobs:
                raise _Deny("PROVIDER_JOB_CAPACITY_EXCEEDED", "Provider job capacity is exhausted")
            if len(self._Jobs) >= self.Limits.MaxRetainedJobs:
                raise _Deny(
                    "PROVIDER_JOB_LEDGER_CAPACITY_EXCEEDED",
                    "Provider job history capacity is exhausted",
                )
            self._Sequence += 1
            Job = _ProviderJob(
                JobId=str(uuid.uuid4()),
                RequestId=RequestId,
                RequestDigest=RequestDigest,
                Purpose=Purpose,
                Prepare=Prepare,
                Commit=Commit,
                CancelEvent=threading.Event(),
                CreatedAt=self._Clock(),
                Sequence=self._Sequence,
            )
            self._Jobs[Job.JobId] = Job
            self._Requests[RequestId] = Job.JobId
            Snapshot = self._SnapshotLocked(Job)
            self._Executor.submit(self._PrepareJob, Job.JobId)
            return Snapshot

    def Get(self, JobId: str) -> ProviderJobSnapshot:
        _ValidateJobId(JobId)
        with self._Condition:
            Job = self._Jobs.get(JobId)
            if Job is None:
                raise _Deny("PROVIDER_JOB_UNKNOWN", "The provider job is unknown")
            return self._SnapshotLocked(Job)

    def Cancel(self, JobId: str) -> ProviderJobSnapshot:
        _ValidateJobId(JobId)
        Cleanup: ProviderPreparedPayload | None = None
        with self._Condition:
            Job = self._Jobs.get(JobId)
            if Job is None:
                raise _Deny("PROVIDER_JOB_UNKNOWN", "The provider job is unknown")
            if Job.Status in TerminalStatuses:
                return self._SnapshotLocked(Job)
            if Job.Status == ProviderJobStatus.QUEUED:
                Job.CancelEvent.set()
                Job.Status = ProviderJobStatus.CANCELLED
                Job.RetrySafe = True
                Job.TerminalAt = self._Clock()
                Job.Prepare = None
                Job.Commit = None
                self._Condition.notify_all()
                return self._SnapshotLocked(Job)
            if Job.Status in (ProviderJobStatus.PREPARING, ProviderJobStatus.CANCEL_REQUESTED):
                Job.CancelEvent.set()
                Job.Status = ProviderJobStatus.CANCEL_REQUESTED
                self._Condition.notify_all()
                return self._SnapshotLocked(Job)
            if Job.Status == ProviderJobStatus.COMMIT_PENDING:
                Job.CancelEvent.set()
                Job.Status = ProviderJobStatus.CLEANING
                Cleanup = Job.Prepared
                Job.Prepared = None
                Job.Commit = None
            elif Job.Status == ProviderJobStatus.COMMITTING:
                raise _Deny(
                    "PROVIDER_JOB_COMMITTING",
                    "The provider job is already committing and must be reconciled",
                )
            else:
                return self._SnapshotLocked(Job)
        assert Cleanup is not None
        self._Executor.submit(
            self._CleanupJob,
            JobId,
            Cleanup.Cleanup,
            ProviderJobStatus.CANCELLED,
        )
        return self.Get(JobId)

    def RunNextCommit(self) -> ProviderJobSnapshot | None:
        if threading.get_ident() != self._MainThreadId:
            raise _Deny(
                "PROVIDER_JOB_MAIN_THREAD_REQUIRED",
                "Provider commits require Blender's main thread",
            )
        with self._Condition:
            Job: _ProviderJob | None = None
            while self._CommitQueue:
                Candidate = self._Jobs.get(self._CommitQueue.popleft())
                if Candidate is not None and Candidate.Status == ProviderJobStatus.COMMIT_PENDING:
                    Job = Candidate
                    break
            if Job is None:
                return None
            Prepared = Job.Prepared
            Commit = Job.Commit
            Job.Commit = None
            if Prepared is None or Commit is None:
                Job.Status = ProviderJobStatus.FAILED
                Job.FailureCode = "PROVIDER_PREPARED_PAYLOAD_MISSING"
                Job.TerminalAt = self._Clock()
                self._Condition.notify_all()
                return self._SnapshotLocked(Job)
            Job.Status = ProviderJobStatus.COMMITTING

        DesiredStatus = ProviderJobStatus.COMPLETED
        FailureCode: str | None = None
        try:
            Outcome = Commit(Prepared.Value)
            if not isinstance(Outcome, ProviderCommitOutcome):
                raise TypeError
        except ProviderCommitError as Error:
            Outcome = None
            DesiredStatus = ProviderJobStatus.FAILED
            FailureCode = Error.Code
        except Exception:
            Outcome = None
            DesiredStatus = ProviderJobStatus.FAILED
            FailureCode = "PROVIDER_COMMIT_FAILED"

        with self._Condition:
            Job.Outcome = Outcome
            Job.FailureCode = FailureCode
            Job.Prepared = None
            Job.Status = ProviderJobStatus.CLEANING
            self._Condition.notify_all()
        self._Executor.submit(self._CleanupJob, Job.JobId, Prepared.Cleanup, DesiredStatus)
        return self.Get(Job.JobId)

    def Shutdown(self, Wait: bool = False) -> None:
        if not isinstance(Wait, bool):
            raise _Deny("PROVIDER_JOB_SHUTDOWN_INVALID", "Provider job shutdown mode is invalid")
        if threading.get_ident() != self._MainThreadId:
            raise _Deny(
                "PROVIDER_JOB_MAIN_THREAD_REQUIRED",
                "Provider job shutdown requires Blender's main thread",
            )
        CleanupJobs: list[tuple[str, Callable[[], None]]] = []
        with self._Condition:
            if not self._Accepting:
                return
            if any(Job.Status == ProviderJobStatus.COMMITTING for Job in self._Jobs.values()):
                raise _Deny(
                    "PROVIDER_JOB_COMMITTING",
                    "A provider commit is running and must finish before shutdown",
                )
            self._Accepting = False
            for Job in self._Jobs.values():
                if Job.Status == ProviderJobStatus.QUEUED:
                    Job.CancelEvent.set()
                    Job.Status = ProviderJobStatus.CANCELLED
                    Job.RetrySafe = True
                    Job.TerminalAt = self._Clock()
                    Job.Prepare = None
                    Job.Commit = None
                elif Job.Status in (
                    ProviderJobStatus.PREPARING,
                    ProviderJobStatus.CANCEL_REQUESTED,
                ):
                    Job.CancelEvent.set()
                    Job.Status = ProviderJobStatus.CANCEL_REQUESTED
                elif Job.Status == ProviderJobStatus.COMMIT_PENDING and Job.Prepared is not None:
                    Job.CancelEvent.set()
                    Job.Status = ProviderJobStatus.CLEANING
                    CleanupJobs.append((Job.JobId, Job.Prepared.Cleanup))
                    Job.Prepared = None
                    Job.Commit = None
            self._CommitQueue.clear()
            self._Condition.notify_all()
        for JobId, Cleanup in CleanupJobs:
            self._Executor.submit(
                self._CleanupJob,
                JobId,
                Cleanup,
                ProviderJobStatus.CANCELLED,
            )
        self._Executor.shutdown(wait=Wait, cancel_futures=False)

    def WaitForSettledPreparation(self, JobId: str, TimeoutSeconds: float) -> ProviderJobSnapshot:
        _ValidateJobId(JobId)
        if (
            not isinstance(TimeoutSeconds, (int, float))
            or isinstance(TimeoutSeconds, bool)
            or not 0 < TimeoutSeconds <= 60
        ):
            raise _Deny("PROVIDER_JOB_WAIT_INVALID", "The provider wait deadline is invalid")
        Deadline = self._Clock() + TimeoutSeconds
        with self._Condition:
            while True:
                Job = self._Jobs.get(JobId)
                if Job is None:
                    raise _Deny("PROVIDER_JOB_UNKNOWN", "The provider job is unknown")
                if Job.Status not in (
                    ProviderJobStatus.QUEUED,
                    ProviderJobStatus.PREPARING,
                    ProviderJobStatus.CANCEL_REQUESTED,
                    ProviderJobStatus.CLEANING,
                ):
                    return self._SnapshotLocked(Job)
                Remaining = Deadline - self._Clock()
                if Remaining <= 0:
                    raise _Deny(
                        "PROVIDER_JOB_WAIT_TIMEOUT",
                        "The provider job did not settle before the wait deadline",
                    )
                self._Condition.wait(Remaining)

    def _PrepareJob(self, JobId: str) -> None:
        with self._Condition:
            Job = self._Jobs.get(JobId)
            if Job is None or Job.Status != ProviderJobStatus.QUEUED:
                return
            Job.Status = ProviderJobStatus.PREPARING
            Prepare = Job.Prepare
            Job.Prepare = None
            self._Condition.notify_all()
        if Prepare is None:
            self._FinalizePreparationFailure(JobId, "PROVIDER_PREPARE_CALLBACK_MISSING", False)
            return
        Token = ProviderCancellationToken(Job.CancelEvent)
        try:
            Prepared = Prepare(Token)
            if not isinstance(Prepared, ProviderPreparedPayload):
                raise TypeError
        except ProviderJobCancelled:
            self._FinalizePreparationFailure(JobId, "PROVIDER_JOB_CANCELLED", True)
            return
        except Exception:
            self._FinalizePreparationFailure(JobId, "PROVIDER_PREPARE_FAILED", False)
            return

        with self._Condition:
            if Job.CancelEvent.is_set() or not self._Accepting:
                Job.Status = ProviderJobStatus.CLEANING
                DesiredStatus = ProviderJobStatus.CANCELLED
            else:
                Job.Prepared = Prepared
                Job.Status = ProviderJobStatus.COMMIT_PENDING
                self._CommitQueue.append(Job.JobId)
                self._Condition.notify_all()
                return
        self._CleanupJob(JobId, Prepared.Cleanup, DesiredStatus)

    def _FinalizePreparationFailure(self, JobId: str, FailureCode: str, Cancelled: bool) -> None:
        with self._Condition:
            Job = self._Jobs.get(JobId)
            if Job is None or Job.Status in TerminalStatuses:
                return
            Job.Status = ProviderJobStatus.CANCELLED if Cancelled else ProviderJobStatus.FAILED
            Job.FailureCode = None if Cancelled else FailureCode
            Job.RetrySafe = False
            Job.TerminalAt = self._Clock()
            Job.Prepare = None
            Job.Commit = None
            self._Condition.notify_all()

    def _CleanupJob(
        self,
        JobId: str,
        Cleanup: Callable[[], None],
        DesiredStatus: ProviderJobStatus,
    ) -> None:
        CleanupFailed = False
        try:
            Cleanup()
        except Exception:
            CleanupFailed = True
        with self._Condition:
            Job = self._Jobs.get(JobId)
            if Job is None:
                return
            if CleanupFailed:
                Job.Status = {
                    ProviderJobStatus.COMPLETED: ProviderJobStatus.COMPLETED_CLEANUP_FAILED,
                    ProviderJobStatus.FAILED: ProviderJobStatus.FAILED_CLEANUP_FAILED,
                    ProviderJobStatus.CANCELLED: ProviderJobStatus.CANCELLED_CLEANUP_FAILED,
                }[DesiredStatus]
                Job.FailureCode = "PROVIDER_ARTIFACT_CLEANUP_FAILED"
                Job.RetrySafe = False
            else:
                Job.Status = DesiredStatus
                Job.RetrySafe = False
            Job.TerminalAt = self._Clock()
            Job.Prepare = None
            Job.Commit = None
            Job.Prepared = None
            self._Condition.notify_all()

    def _EvictLocked(self) -> None:
        Now = self._Clock()
        Expired = [
            Job
            for Job in self._Jobs.values()
            if Job.Status in TerminalStatuses
            and Job.TerminalAt is not None
            and Now - Job.TerminalAt >= self.Limits.RetentionSeconds
        ]
        for Job in sorted(Expired, key=lambda Value: Value.Sequence):
            self._RemoveLocked(Job)
        while len(self._Jobs) >= self.Limits.MaxRetainedJobs:
            Terminal = [Job for Job in self._Jobs.values() if Job.Status in TerminalStatuses]
            if not Terminal:
                break
            self._RemoveLocked(min(Terminal, key=lambda Value: Value.Sequence))

    def _RemoveLocked(self, Job: _ProviderJob) -> None:
        self._Jobs.pop(Job.JobId, None)
        if self._Requests.get(Job.RequestId) == Job.JobId:
            self._Requests.pop(Job.RequestId, None)

    @staticmethod
    def _SnapshotLocked(Job: _ProviderJob) -> ProviderJobSnapshot:
        return ProviderJobSnapshot(
            JobId=Job.JobId,
            RequestId=Job.RequestId,
            Purpose=Job.Purpose,
            Status=Job.Status,
            FailureCode=Job.FailureCode,
            OutcomeCode=Job.Outcome.Code if Job.Outcome is not None else None,
            AffectedCount=Job.Outcome.AffectedCount if Job.Outcome is not None else 0,
            RetrySafe=Job.RetrySafe,
            Terminal=Job.Status in TerminalStatuses,
        )


def _ValidateIdentity(
    RequestId: str,
    RequestDigest: str,
    Purpose: str,
    Prepare: PrepareCallback,
    Commit: CommitCallback,
) -> None:
    if (
        not isinstance(RequestId, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", RequestId) is None
    ):
        raise _Deny(
            "PROVIDER_REQUEST_ID_INVALID", "A bounded provider request identity is required"
        )
    if not isinstance(RequestDigest, str) or re.fullmatch(r"[0-9a-f]{64}", RequestDigest) is None:
        raise _Deny("PROVIDER_REQUEST_DIGEST_INVALID", "A provider request digest is required")
    if (
        not isinstance(Purpose, str)
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{0,63}", Purpose) is None
    ):
        raise _Deny("PROVIDER_PURPOSE_INVALID", "A bounded provider purpose is required")
    if not callable(Prepare) or not callable(Commit):
        raise _Deny("PROVIDER_CALLBACK_INVALID", "Provider job callbacks are invalid")


def _ValidateJobId(JobId: str) -> None:
    if (
        not isinstance(JobId, str)
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            JobId,
        )
        is None
    ):
        raise _Deny("PROVIDER_JOB_ID_INVALID", "The provider job identity is invalid")


def _Deny(Code: str, PublicMessage: str, RetrySafe: bool = False) -> ProviderJobError:
    return ProviderJobError(Code, PublicMessage, RetrySafe)
