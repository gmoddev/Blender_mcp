"""Deterministic security tests for the Blender main-thread command lifecycle."""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

BpyMock = MagicMock()
BpyMock.app.version = (5, 0, 0)
sys.modules.setdefault("bpy", BpyMock)
sys.modules.setdefault("mathutils", MagicMock())

import blender_mcp.core.thread_safety as ThreadSafetyModule  # noqa: E402
from blender_mcp.core.thread_safety import (  # noqa: E402
    CommandLifecycleError,
    CommandTimeoutError,
    ExecutionStatus,
    ThreadSafety,
)


def GetLifecycle(monkeypatch: pytest.MonkeyPatch) -> ThreadSafety:
    """Create an isolated lifecycle without starting Blender health monitors."""
    ThreadSafety._instance = None
    ThreadSafety._initialized = False
    monkeypatch.setattr(ThreadSafetyModule, "BPY_AVAILABLE", False)
    Lifecycle = ThreadSafety()
    monkeypatch.setattr(ThreadSafetyModule, "BPY_AVAILABLE", True)
    monkeypatch.setattr(ThreadSafetyModule, "is_main_thread", lambda: False)
    monkeypatch.setattr(Lifecycle, "_ensure_timer", lambda: True)
    return Lifecycle


def WaitForQueue(Lifecycle: ThreadSafety) -> None:
    """Wait briefly for a worker to enqueue without introducing race-prone sleeps."""
    Deadline = time.monotonic() + 1.0
    while Lifecycle._task_queue.empty() and time.monotonic() < Deadline:
        threading.Event().wait(0.001)
    assert not Lifecycle._task_queue.empty()


class TestCommandLifecycle:
    def test_worker_cannot_register_blender_timer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ThreadSafety._instance = None
        ThreadSafety._initialized = False
        monkeypatch.setattr(ThreadSafetyModule, "BPY_AVAILABLE", False)
        Lifecycle = ThreadSafety()
        monkeypatch.setattr(ThreadSafetyModule, "BPY_AVAILABLE", True)
        monkeypatch.setattr(ThreadSafetyModule, "is_main_thread", lambda: False)
        Register = MagicMock()
        monkeypatch.setattr(ThreadSafetyModule.bpy.app.timers, "register", Register)

        assert Lifecycle.Start() is False
        assert Lifecycle._ensure_timer() is False
        Register.assert_not_called()

    def test_main_thread_start_registers_timer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ThreadSafety._instance = None
        ThreadSafety._initialized = False
        monkeypatch.setattr(ThreadSafetyModule, "BPY_AVAILABLE", False)
        Lifecycle = ThreadSafety()
        monkeypatch.setattr(ThreadSafetyModule, "BPY_AVAILABLE", True)
        monkeypatch.setattr(ThreadSafetyModule, "is_main_thread", lambda: True)
        RegisterHealth = MagicMock()
        Register = MagicMock()
        monkeypatch.setattr(Lifecycle, "_register_health_monitors", RegisterHealth)
        monkeypatch.setattr(ThreadSafetyModule.bpy.app.timers, "register", Register)

        assert Lifecycle.Start() is True
        RegisterHealth.assert_called_once_with()
        Register.assert_called_once_with(
            Lifecycle._TimerCallback,
            first_interval=0.001,
            persistent=True,
        )

    def test_worker_shutdown_does_not_call_blender_timer_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ThreadSafety._instance = None
        ThreadSafety._initialized = False
        monkeypatch.setattr(ThreadSafetyModule, "BPY_AVAILABLE", False)
        Lifecycle = ThreadSafety()
        Lifecycle._timer_registered = True
        monkeypatch.setattr(ThreadSafetyModule, "BPY_AVAILABLE", True)
        monkeypatch.setattr(ThreadSafetyModule, "is_main_thread", lambda: False)
        IsRegistered = MagicMock()
        Unregister = MagicMock()
        monkeypatch.setattr(ThreadSafetyModule.bpy.app.timers, "is_registered", IsRegistered)
        monkeypatch.setattr(ThreadSafetyModule.bpy.app.timers, "unregister", Unregister)

        Lifecycle.Shutdown()

        IsRegistered.assert_not_called()
        Unregister.assert_not_called()
        assert Lifecycle._timer_registered is True

    def test_background_monitor_does_not_call_blender_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        IsJobRunning = MagicMock()
        monkeypatch.setattr(ThreadSafetyModule.bpy.app, "is_job_running", IsJobRunning)
        Lifecycle._last_main_thread_tick = time.time() - 31.0
        Lifecycle._last_depsgraph_update = time.time() - 31.0

        Lifecycle._check_logical_stall()

        IsJobRunning.assert_not_called()

    def test_idle_timer_interval_stays_below_minimum_dispatch_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Lifecycle = GetLifecycle(monkeypatch)

        assert Lifecycle._process_queue() < 0.1

    def test_pending_timeout_tombstones_callable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        Invocations: list[str] = []

        with pytest.raises(CommandTimeoutError) as Error:
            Lifecycle.ExecuteRequest(
                lambda: Invocations.append("ran"),
                RequestId="pending-timeout",
                RequestDigest="digest-a",
                Timeout=0.001,
            )

        assert Error.value.Code == "COMMAND_TIMED_OUT_PENDING"
        assert isinstance(Error.value, TimeoutError)
        assert Error.value.Status == ExecutionStatus.TIMED_OUT_PENDING
        assert Error.value.RetrySafe is True
        Lifecycle._process_queue()
        assert Invocations == []
        assert Lifecycle.GetRequestStatus("pending-timeout")["state"] == "timed_out_pending"

    def test_running_timeout_remains_indeterminate_until_late_completion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        Started = threading.Event()
        Release = threading.Event()
        Errors: list[BaseException] = []

        def Mutate() -> str:
            Started.set()
            assert Release.wait(1.0)
            return "changed"

        def Submit() -> None:
            try:
                Lifecycle.ExecuteRequest(
                    Mutate,
                    RequestId="running-timeout",
                    RequestDigest="digest-b",
                    Timeout=0.02,
                )
            except BaseException as Error:
                Errors.append(Error)

        Waiter = threading.Thread(target=Submit)
        Waiter.start()
        WaitForQueue(Lifecycle)
        Consumer = threading.Thread(target=Lifecycle._process_queue)
        Consumer.start()
        assert Started.wait(1.0)
        Waiter.join(1.0)

        assert len(Errors) == 1
        assert isinstance(Errors[0], CommandTimeoutError)
        assert Errors[0].Code == "REQUEST_INDETERMINATE"
        assert Lifecycle.GetRequestStatus("running-timeout")["state"] == "running_after_timeout"

        Release.set()
        Consumer.join(1.0)
        Snapshot = Lifecycle.GetRequestStatus("running-timeout")
        assert Snapshot["state"] == "completed_late"
        assert Snapshot["result"] == "changed"

    def test_duplicate_same_content_never_reexecutes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        InvocationCount = 0
        Results: list[Any] = []

        def Mutate() -> str:
            nonlocal InvocationCount
            InvocationCount += 1
            return "once"

        def Submit() -> None:
            Results.append(
                Lifecycle.ExecuteRequest(
                    Mutate,
                    RequestId="duplicate-complete",
                    RequestDigest="digest-c",
                    Timeout=1.0,
                )
            )

        Waiter = threading.Thread(target=Submit)
        Waiter.start()
        WaitForQueue(Lifecycle)
        Lifecycle._process_queue()
        Waiter.join(1.0)

        DuplicateResult = Lifecycle.ExecuteRequest(
            Mutate,
            RequestId="duplicate-complete",
            RequestDigest="digest-c",
            Timeout=1.0,
        )
        assert Results == ["once"]
        assert DuplicateResult == "once"
        assert InvocationCount == 1

    def test_request_id_reuse_with_different_content_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        with pytest.raises(CommandTimeoutError):
            Lifecycle.ExecuteRequest(
                lambda: None,
                RequestId="digest-conflict",
                RequestDigest="digest-original",
                Timeout=0.001,
            )

        with pytest.raises(CommandLifecycleError) as Error:
            Lifecycle.ExecuteRequest(
                lambda: None,
                RequestId="digest-conflict",
                RequestDigest="digest-changed",
                Timeout=1.0,
            )

        assert Error.value.Code == "REQUEST_ID_CONFLICT"
        Lifecycle._process_queue()

    def test_terminal_state_cannot_be_overwritten(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        Command, _ = Lifecycle._GetCommand(
            lambda: "done",
            "terminal-state",
            "digest-terminal",
            (),
            {},
            None,
            None,
        )

        assert Command.execute() is True
        assert Command.status == ExecutionStatus.COMPLETED
        assert Command.MarkTimedOut() == ExecutionStatus.COMPLETED
        assert Command.Cancel() is False
        assert Command.status == ExecutionStatus.COMPLETED

    def test_pending_cancel_wakes_waiter_and_consumer_skips_callable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        Invocations: list[str] = []
        Errors: list[BaseException] = []

        def Submit() -> None:
            try:
                Lifecycle.ExecuteRequest(
                    lambda: Invocations.append("ran"),
                    RequestId="cancel-pending",
                    RequestDigest="digest-d",
                    Timeout=1.0,
                )
            except BaseException as Error:
                Errors.append(Error)

        Waiter = threading.Thread(target=Submit)
        Waiter.start()
        WaitForQueue(Lifecycle)
        Snapshot = Lifecycle.CancelRequest("cancel-pending")
        Waiter.join(1.0)
        Lifecycle._process_queue()

        assert Snapshot["state"] == "cancelled"
        assert Snapshot["cancelled"] is True
        assert len(Errors) == 1
        assert Errors[0].Code == "REQUEST_CANCELLED"
        assert Invocations == []

    def test_timer_registration_failure_does_not_queue_work(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        monkeypatch.setattr(Lifecycle, "_ensure_timer", lambda: False)
        Invocations: list[str] = []

        with pytest.raises(CommandLifecycleError) as Error:
            Lifecycle.ExecuteRequest(
                lambda: Invocations.append("ran"),
                RequestId="timer-failure",
                RequestDigest="digest-e",
                Timeout=1.0,
            )

        assert Error.value.Code == "MAIN_THREAD_UNAVAILABLE"
        assert Lifecycle._task_queue.empty()
        assert Invocations == []

    def test_shutdown_tombstones_every_queued_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        Invocations: list[str] = []
        Command, _ = Lifecycle._GetCommand(
            lambda: Invocations.append("ran"),
            "shutdown-pending",
            "digest-shutdown",
            (),
            {},
            None,
            None,
        )
        Lifecycle._task_queue.put(Command)

        Lifecycle.Shutdown()

        assert Command.status == ExecutionStatus.CANCELLED
        assert Lifecycle._task_queue.empty()
        assert Invocations == []

    def test_ledger_never_evicts_active_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        Lifecycle._MaxLedgerEntries = 1
        Lifecycle._GetCommand(
            lambda: None,
            "active-request",
            "digest-f",
            (),
            {},
            None,
            None,
        )

        with pytest.raises(CommandLifecycleError) as Error:
            Lifecycle._GetCommand(
                lambda: None,
                "second-request",
                "digest-g",
                (),
                {},
                None,
                None,
            )

        assert Error.value.Code == "COMMAND_LEDGER_FULL"

    def test_ledger_does_not_evict_terminal_request_before_retention_expiry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        Lifecycle._MaxLedgerEntries = 1
        Results: list[str] = []

        def Submit() -> None:
            Results.append(
                Lifecycle.ExecuteRequest(
                    lambda: "retained",
                    RequestId="retained-request",
                    RequestDigest="digest-retained",
                    Timeout=1.0,
                )
            )

        Waiter = threading.Thread(target=Submit)
        Waiter.start()
        WaitForQueue(Lifecycle)
        Lifecycle._process_queue()
        Waiter.join(1.0)

        with pytest.raises(CommandLifecycleError) as Error:
            Lifecycle.ExecuteRequest(
                lambda: "new",
                RequestId="new-request",
                RequestDigest="digest-new",
                Timeout=1.0,
            )

        assert Error.value.Code == "COMMAND_LEDGER_FULL"
        assert (
            Lifecycle.ExecuteRequest(
                lambda: "must-not-run",
                RequestId="retained-request",
                RequestDigest="digest-retained",
                Timeout=1.0,
            )
            == "retained"
        )
        assert Results == ["retained"]

    def test_oversized_result_is_dropped_but_completion_remains_observable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        Lifecycle._MaxRetainedResultBytes = 8
        Errors: list[BaseException] = []

        def Submit() -> None:
            try:
                Lifecycle.ExecuteRequest(
                    lambda: "result-too-large",
                    RequestId="oversized-result",
                    RequestDigest="digest-result",
                    Timeout=1.0,
                )
            except BaseException as Error:
                Errors.append(Error)

        Waiter = threading.Thread(target=Submit)
        Waiter.start()
        WaitForQueue(Lifecycle)
        Lifecycle._process_queue()
        Waiter.join(1.0)

        assert len(Errors) == 1
        assert Errors[0].Code == "RESULT_NOT_RETAINED"
        Snapshot = Lifecycle.GetRequestStatus("oversized-result")
        assert Snapshot["state"] == "completed_result_dropped"
        assert Snapshot["result_available"] is False
        assert Snapshot["retry_safe"] is False
        assert Snapshot["result_unavailable_reason"] == "per_result_limit"

    def test_batch_admission_is_all_or_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        Lifecycle = GetLifecycle(monkeypatch)
        Lifecycle._MaxLedgerEntries = 1
        Invocations: list[str] = []

        with pytest.raises(CommandLifecycleError) as Error:
            Lifecycle.execute_batch(
                [
                    (lambda: Invocations.append("first"), ()),
                    (lambda: Invocations.append("second"), ()),
                ]
            )

        assert Error.value.Code == "COMMAND_LEDGER_FULL"
        assert Lifecycle._task_queue.empty()
        assert Lifecycle.GetRequestStatus("unassigned") is None
        assert Invocations == []
