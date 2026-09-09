"""
Thread Safety V2 for Blender MCP 1.0.0

High-performance, event-driven thread safety for Blender MCP.
Uses optimized queue + threading.Event for sub-second latency.

High Mode Philosophy: Thread-safe execution without limiting functionality.
Performance Target: <100ms latency for all operations.
"""

import functools
import json
import queue
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from .logging_config import get_logger

try:
    import bpy

    BPY_AVAILABLE = True
except ImportError:
    BPY_AVAILABLE = False

logger = get_logger()


class ExecutionStatus(Enum):
    """Execution status for tracked operations."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    TIMED_OUT_PENDING = "timed_out_pending"
    RUNNING_AFTER_TIMEOUT = "running_after_timeout"
    COMPLETED_LATE = "completed_late"
    FAILED_LATE = "failed_late"
    COMPLETED_RESULT_DROPPED = "completed_result_dropped"
    COMPLETED_LATE_RESULT_DROPPED = "completed_late_result_dropped"
    TIMEOUT = "timed_out_pending"  # Compatibility alias; use TIMED_OUT_PENDING.


TERMINAL_STATUSES = frozenset(
    {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.TIMED_OUT_PENDING,
        ExecutionStatus.COMPLETED_LATE,
        ExecutionStatus.FAILED_LATE,
        ExecutionStatus.COMPLETED_RESULT_DROPPED,
        ExecutionStatus.COMPLETED_LATE_RESULT_DROPPED,
    }
)
SUCCESS_STATUSES = frozenset({ExecutionStatus.COMPLETED, ExecutionStatus.COMPLETED_LATE})
FAILED_STATUSES = frozenset({ExecutionStatus.FAILED, ExecutionStatus.FAILED_LATE})

RESULT_DROPPED_STATUSES = frozenset(
    {
        ExecutionStatus.COMPLETED_RESULT_DROPPED,
        ExecutionStatus.COMPLETED_LATE_RESULT_DROPPED,
    }
)

MAX_LEDGER_ENTRIES = 4096
LEDGER_RETENTION_SECONDS = 15 * 60.0
MAX_RETAINED_RESULT_BYTES = 4 * 1024 * 1024
MAX_LEDGER_RESULT_BYTES = 32 * 1024 * 1024
IDLE_TIMER_INTERVAL_SECONDS = 0.05


def _NoOp() -> None:
    """Release a completed command's callable without changing its public shape."""


class CommandLifecycleError(RuntimeError):
    """Structured lifecycle failure suitable for dispatcher error responses."""

    def __init__(
        self,
        Code: str,
        Message: str,
        RequestId: str,
        Status: ExecutionStatus,
        RetrySafe: bool,
    ) -> None:
        super().__init__(Message)
        self.Code = Code
        self.PublicMessage = Message
        self.RequestId = RequestId
        self.Status = Status
        self.RetrySafe = RetrySafe


class CommandTimeoutError(CommandLifecycleError, TimeoutError):
    """Timeout whose state distinguishes non-execution from indeterminate execution."""


@dataclass
class MCPCommand:
    """
    Structured command payload for XAI-compliant execution.
    Adheres to 'CommandQueue' pattern.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    func: Callable[..., Any] = field(default=print)
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)

    # XAI & Traceability
    tool_id: Optional[str] = None
    intent: Optional[str] = None
    context_snapshot: Optional[Dict[str, Any]] = None

    # Execution State
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[Any] = None
    error: Optional[Exception] = None
    status: ExecutionStatus = ExecutionStatus.PENDING
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    RequestDigest: str = ""
    StateLock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    ResultPolicy: Optional[Callable[[Any], Tuple[bool, int, Optional[str]]]] = field(
        default=None, repr=False
    )
    ResultByteSize: int = 0
    ResultUnavailableReason: Optional[str] = None

    def TryStart(self) -> bool:
        """Atomically claim a pending command for main-thread execution."""
        with self.StateLock:
            if self.status != ExecutionStatus.PENDING:
                return False
            self.status = ExecutionStatus.RUNNING
            return True

    def MarkTimedOut(self) -> ExecutionStatus:
        """Tombstone pending work or mark already-running work indeterminate."""
        with self.StateLock:
            if self.status == ExecutionStatus.PENDING:
                self.status = ExecutionStatus.TIMED_OUT_PENDING
                self.end_time = time.time()
                self.event.set()
            elif self.status == ExecutionStatus.RUNNING:
                self.status = ExecutionStatus.RUNNING_AFTER_TIMEOUT
            return self.status

    def Cancel(self) -> bool:
        """Cancel only work that has not been claimed by the queue consumer."""
        with self.StateLock:
            if self.status != ExecutionStatus.PENDING:
                return False
            self.status = ExecutionStatus.CANCEL_REQUESTED
            self.status = ExecutionStatus.CANCELLED
            self.end_time = time.time()
            self.event.set()
            return True

    def Snapshot(self, IncludeResult: bool = True) -> Dict[str, Any]:
        """Return a synchronized, wire-safe view of the command lifecycle."""
        with self.StateLock:
            Snapshot: Dict[str, Any] = {
                "request_id": self.id,
                "state": self.status.value,
                "terminal": self.status in TERMINAL_STATUSES,
                "retry_safe": self.status
                in {ExecutionStatus.TIMED_OUT_PENDING, ExecutionStatus.CANCELLED},
                "result_available": self.status in SUCCESS_STATUSES,
                "started_at": self.start_time,
                "ended_at": self.end_time,
                "duration_ms": self.duration_ms,
            }
            if IncludeResult and self.status in SUCCESS_STATUSES:
                Snapshot["result"] = self.result
            if self.status in RESULT_DROPPED_STATUSES:
                Snapshot["result_unavailable_reason"] = self.ResultUnavailableReason
            if self.status in FAILED_STATUSES:
                Snapshot["error_type"] = type(self.error).__name__ if self.error else "RuntimeError"
            return Snapshot

    def execute(self) -> bool:
        """Execute only when the pending-to-running claim succeeds."""
        if not self.TryStart():
            return False
        try:
            Result = self.func(*self.args, **self.kwargs)
            RetainResult = True
            ResultByteSize = 0
            UnavailableReason = None
            if self.ResultPolicy is not None:
                RetainResult, ResultByteSize, UnavailableReason = self.ResultPolicy(Result)
            with self.StateLock:
                self.ResultByteSize = ResultByteSize
                if RetainResult:
                    self.result = Result
                    if self.status == ExecutionStatus.RUNNING_AFTER_TIMEOUT:
                        self.status = ExecutionStatus.COMPLETED_LATE
                    elif self.status == ExecutionStatus.RUNNING:
                        self.status = ExecutionStatus.COMPLETED
                else:
                    self.result = None
                    self.ResultUnavailableReason = UnavailableReason
                    if self.status == ExecutionStatus.RUNNING_AFTER_TIMEOUT:
                        self.status = ExecutionStatus.COMPLETED_LATE_RESULT_DROPPED
                    elif self.status == ExecutionStatus.RUNNING:
                        self.status = ExecutionStatus.COMPLETED_RESULT_DROPPED
        except Exception as Error:
            Error.__traceback__ = None
            Error.__context__ = None
            Error.__cause__ = None
            with self.StateLock:
                self.error = Error
                if self.status == ExecutionStatus.RUNNING_AFTER_TIMEOUT:
                    self.status = ExecutionStatus.FAILED_LATE
                elif self.status == ExecutionStatus.RUNNING:
                    self.status = ExecutionStatus.FAILED
        finally:
            with self.StateLock:
                self.end_time = time.time()
                self.func = _NoOp
                self.args = ()
                self.kwargs = {}
                self.event.set()
        return True

    @property
    def duration_ms(self) -> float:
        """Get execution duration in milliseconds."""
        end = self.end_time or time.time()
        return (end - self.start_time) * 1000


class ThreadSafety:
    """
    High-performance thread-safe execution for Blender MCP.

    Features:
    - Event-driven (no polling)
    - Automatic main thread detection
    - Performance metrics
    - Task cancellation
    - Batch execution
    """

    _instance: Optional["ThreadSafety"] = None
    _lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> "ThreadSafety":
        """Singleton pattern for global access."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        """Initialize the thread safety system."""
        if self._initialized:
            return

        self._initialized = True
        self._task_queue: queue.Queue[MCPCommand] = queue.Queue()
        self._active_tasks: Dict[str, MCPCommand] = {}
        self._request_ledger: OrderedDict[str, MCPCommand] = OrderedDict()
        self._ledger_lock = threading.RLock()
        self._MaxLedgerEntries = MAX_LEDGER_ENTRIES
        self._LedgerRetentionSeconds = LEDGER_RETENTION_SECONDS
        self._MaxRetainedResultBytes = MAX_RETAINED_RESULT_BYTES
        self._MaxLedgerResultBytes = MAX_LEDGER_RESULT_BYTES
        self._LedgerResultBytes = 0
        self._timer_registered = False
        self._TimerCallback = self._process_queue
        self._stats = {
            "total_executed": 0,
            "total_failed": 0,
            "avg_latency_ms": 0.0,
        }

        # Health Monitoring (Logical Stall Detector)
        self._last_depsgraph_update: float = time.time()
        self._last_main_thread_tick: float = time.time()
        self._stop_monitor = threading.Event()

    def Start(self) -> bool:
        """Register Blender-owned callbacks from Blender's main thread."""
        if not BPY_AVAILABLE:
            return False
        if not is_main_thread():
            logger.error("[BlenderMCP:CommandQueue] Startup rejected outside Blender's main thread")
            return False

        self._register_health_monitors()
        if self._timer_registered:
            try:
                if bpy.app.timers.is_registered(self._TimerCallback):
                    return True
            except Exception as Error:
                logger.error(
                    "[BlenderMCP:CommandQueue] Timer state check failed "
                    f"error_type={type(Error).__name__}"
                )
            self._timer_registered = False
        return self._ensure_timer()

    def _register_health_monitors(self) -> None:
        """Register Blender handlers for health monitoring."""
        if not BPY_AVAILABLE:
            return
        if not is_main_thread():
            logger.error(
                "[BlenderMCP:Health] Handler registration rejected outside Blender's main thread"
            )
            return

        # Track Depsgraph Updates (Scene changes)
        # We use a persistent wrapper to avoid double registration
        if not hasattr(bpy.app.handlers, "depsgraph_update_post"):
            return

        handlers = bpy.app.handlers.depsgraph_update_post
        # Remove existing if present (to allow reloading)
        handlers[:] = [h for h in handlers if getattr(h, "__name__", "") != "_mcp_depsgraph_hook"]

        _depsgraph_throttle: Dict[str, float] = {"last": 0.0}

        @bpy.app.handlers.persistent
        def _mcp_depsgraph_hook(scene: Any, depsgraph: Any) -> None:
            # Throttle to at most once per 0.1s — depsgraph fires on every
            # viewport move/property change/animation frame, so an unthrottled
            # hook causes Python overhead on every GPU frame.
            now = time.time()
            if now - _depsgraph_throttle["last"] < 0.1:
                return
            _depsgraph_throttle["last"] = now
            if self._instance:
                self._instance._last_depsgraph_update = now

        handlers.append(_mcp_depsgraph_hook)

        # Start Daemon Monitor Thread (guard against double-start on reload)
        monitor: threading.Thread | None = getattr(self, "_monitor_thread", None)
        if not (monitor is not None and monitor.is_alive()):
            self._stop_monitor.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, name="MCP-Stall-Detector", daemon=True
            )
            self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        """
        Background thread checking for logical stalls.
        Runs every 5 seconds.
        """
        while not self._stop_monitor.is_set():
            time.sleep(5.0)
            try:
                self._check_logical_stall()
            except Exception as Error:
                logger.error(
                    f"[BlenderMCP:Health] Monitor failed error_type={type(Error).__name__}"
                )

    def _check_logical_stall(self) -> None:
        """
        Compare timestamps written by Blender main-thread callbacks.
        """
        if not BPY_AVAILABLE:
            return

        now = time.time()
        tick_delta = now - self._last_main_thread_tick
        deps_delta = now - self._last_depsgraph_update

        # Threshold: 30 seconds of silence
        THRESHOLD = 30.0

        if tick_delta > THRESHOLD and deps_delta > THRESHOLD:
            logger.warning(
                "[BlenderMCP:Health] Logical stall detected "
                f"main_thread_silence_seconds={tick_delta:.2f} "
                f"depsgraph_silence_seconds={deps_delta:.2f}"
            )

    def _ensure_timer(self) -> bool:
        """
        Ensure the Blender timer is registered.

        Returns:
            True if timer is active
        """
        if not BPY_AVAILABLE:
            return False

        if self._timer_registered:
            return True
        if not is_main_thread():
            logger.error(
                "[BlenderMCP:CommandQueue] Timer registration rejected outside Blender's main thread"
            )
            return False

        try:
            bpy.app.timers.register(
                self._TimerCallback,
                first_interval=0.001,  # 1ms initial delay
                persistent=True,
            )
            self._timer_registered = True
            return True
        except Exception as e:
            logger.error(f"[BlenderMCP:CommandQueue] Timer registration failed: {e}")
            return False

    def _process_queue(self) -> Optional[float]:
        """
        Process pending tasks on main thread.
        Called by bpy.app.timers.

        Returns:
            Next interval or None to stop
        """
        # Update Heartbeat
        self._last_main_thread_tick = time.time()

        processed = 0
        max_per_tick = 20  # Process up to 20 tasks per frame

        while processed < max_per_tick:
            try:
                task = self._task_queue.get_nowait()
            except queue.Empty:
                break

            # Claim and execute atomically. Tombstoned commands are drained but
            # never invoke their callable.
            DidExecute = task.execute()

            with self._ledger_lock:
                if task.status in TERMINAL_STATUSES:
                    self._active_tasks.pop(task.id, None)

            # Update stats
            if DidExecute:
                self._stats["total_executed"] += 1
            if DidExecute and task.status in FAILED_STATUSES:
                self._stats["total_failed"] += 1

            # Update average latency
            if DidExecute:
                n = self._stats["total_executed"]
                current_avg = self._stats["avg_latency_ms"]
                self._stats["avg_latency_ms"] = ((n - 1) * current_avg + task.duration_ms) / n

            processed += 1

        # Return interval based on queue state
        if not self._task_queue.empty():
            return 0.001  # Keep processing
        return IDLE_TIMER_INTERVAL_SECONDS

    @classmethod
    def execute_on_main(
        cls,
        func: Callable[..., Any],
        *args: Any,
        tool_id: Optional[str] = None,
        intent: Optional[str] = None,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> Any:
        """
        Execute a function on the main thread and wait for result.

        Args:
            func: Function to execute
            *args: Positional arguments
            tool_id: ID of the calling tool (for XAI)
            intent: User intent description (for XAI)
            timeout: Maximum wait time in seconds
            **kwargs: Keyword arguments for func

        Returns:
            Function result

        Raises:
            TimeoutError: If execution exceeds timeout
            Exception: If function raises an exception
        """
        # Fast path: not in Blender
        if not BPY_AVAILABLE:
            return func(*args, **kwargs)

        # Fast path: already on main thread
        if is_main_thread():
            return func(*args, **kwargs)

        instance = cls()
        RequestId = str(uuid.uuid4())
        return instance.ExecuteRequest(
            func,
            RequestId=RequestId,
            RequestDigest=f"internal:{RequestId}",
            Timeout=timeout,
            Args=args,
            Kwargs=kwargs,
            ToolId=tool_id,
            Intent=intent,
        )

    def ExecuteRequest(
        self,
        Func: Callable[..., Any],
        *,
        RequestId: str,
        RequestDigest: str,
        Timeout: float,
        Args: Tuple[Any, ...] = (),
        Kwargs: Optional[Dict[str, Any]] = None,
        ToolId: Optional[str] = None,
        Intent: Optional[str] = None,
    ) -> Any:
        """Execute or reconcile one request through the bounded command ledger."""
        if not self._ensure_timer():
            raise CommandLifecycleError(
                "MAIN_THREAD_UNAVAILABLE",
                "Blender main-thread scheduling is unavailable",
                RequestId,
                ExecutionStatus.CANCELLED,
                True,
            )

        Command, IsNew = self._GetCommand(
            Func,
            RequestId,
            RequestDigest,
            Args,
            Kwargs or {},
            ToolId,
            Intent,
        )
        if IsNew:
            self._task_queue.put(Command)
        else:
            return self._ResolveCommand(Command, IsDuplicate=True)

        if not Command.event.wait(Timeout):
            State = Command.MarkTimedOut()
            if State == ExecutionStatus.TIMED_OUT_PENDING:
                with self._ledger_lock:
                    self._active_tasks.pop(Command.id, None)
            return self._ResolveCommand(Command)
        return self._ResolveCommand(Command)

    def _GetCommand(
        self,
        Func: Callable[..., Any],
        RequestId: str,
        RequestDigest: str,
        Args: Tuple[Any, ...],
        Kwargs: Dict[str, Any],
        ToolId: Optional[str],
        Intent: Optional[str],
    ) -> Tuple[MCPCommand, bool]:
        """Get an identical request or create a new ledger entry atomically."""
        with self._ledger_lock:
            self._PruneLedger()
            Existing = self._request_ledger.get(RequestId)
            if Existing is not None:
                self._request_ledger.move_to_end(RequestId)
                if Existing.RequestDigest != RequestDigest:
                    raise CommandLifecycleError(
                        "REQUEST_ID_CONFLICT",
                        "Request ID was already used for different command content",
                        RequestId,
                        Existing.status,
                        False,
                    )
                return Existing, False

            self._MakeLedgerSpace()
            Command = MCPCommand(
                id=RequestId,
                func=Func,
                args=Args,
                kwargs=Kwargs,
                tool_id=ToolId,
                intent=Intent,
                RequestDigest=RequestDigest,
            )
            Command.ResultPolicy = self._ReserveResult
            self._request_ledger[RequestId] = Command
            self._active_tasks[RequestId] = Command
            return Command, True

    def _ResolveCommand(self, Command: MCPCommand, IsDuplicate: bool = False) -> Any:
        """Return a stored result or raise a structured lifecycle outcome."""
        with Command.StateLock:
            State = Command.status
            Result = Command.result
            Error = Command.error

        if State in SUCCESS_STATUSES:
            return Result
        if State in FAILED_STATUSES:
            if Error is not None:
                raise Error
            raise CommandLifecycleError(
                "EXECUTION_ERROR",
                "Execution failed without error details",
                Command.id,
                State,
                False,
            )
        if State in RESULT_DROPPED_STATUSES:
            raise CommandLifecycleError(
                "RESULT_NOT_RETAINED",
                "Command completed, but its result exceeded the reconciliation ledger limits",
                Command.id,
                State,
                False,
            )
        if State == ExecutionStatus.TIMED_OUT_PENDING:
            raise CommandTimeoutError(
                "COMMAND_TIMED_OUT_PENDING",
                "Command timed out while pending and will not execute",
                Command.id,
                State,
                True,
            )
        if State == ExecutionStatus.RUNNING_AFTER_TIMEOUT:
            raise CommandTimeoutError(
                "REQUEST_INDETERMINATE",
                "Command was running at timeout; reconcile by request ID before retrying",
                Command.id,
                State,
                False,
            )
        if State == ExecutionStatus.CANCELLED:
            raise CommandLifecycleError(
                "REQUEST_CANCELLED",
                "Command was cancelled before execution",
                Command.id,
                State,
                True,
            )

        Message = (
            "Identical request is already in progress"
            if IsDuplicate
            else "Command did not reach a terminal state"
        )
        raise CommandLifecycleError(
            "REQUEST_IN_PROGRESS",
            Message,
            Command.id,
            State,
            False,
        )

    def GetRequestStatus(
        self, RequestId: str, IncludeResult: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Get a retained request state without crossing Blender's main thread."""
        with self._ledger_lock:
            self._PruneLedger()
            Command = self._request_ledger.get(RequestId)
            if Command is None:
                return None
            self._request_ledger.move_to_end(RequestId)
        return Command.Snapshot(IncludeResult=IncludeResult)

    def CancelRequest(self, RequestId: str) -> Optional[Dict[str, Any]]:
        """Tombstone a retained pending request; running work is not misreported cancelled."""
        with self._ledger_lock:
            Command = self._request_ledger.get(RequestId)
        if Command is None:
            return None
        Cancelled = Command.Cancel()
        if Cancelled:
            with self._ledger_lock:
                self._active_tasks.pop(RequestId, None)
        Snapshot = Command.Snapshot()
        Snapshot["cancelled"] = Cancelled
        return Snapshot

    def Shutdown(self) -> None:
        """Tombstone queued work and unregister the persistent Blender timer."""
        self._stop_monitor.set()
        while True:
            try:
                Command = self._task_queue.get_nowait()
            except queue.Empty:
                break
            Command.Cancel()
            with self._ledger_lock:
                self._active_tasks.pop(Command.id, None)

        if BPY_AVAILABLE and self._timer_registered:
            if not is_main_thread():
                logger.error(
                    "[BlenderMCP:CommandQueue] Timer shutdown rejected outside Blender's main thread"
                )
                return
            try:
                if bpy.app.timers.is_registered(self._TimerCallback):
                    bpy.app.timers.unregister(self._TimerCallback)
            except Exception as Error:
                logger.error(
                    "[BlenderMCP:CommandQueue] Timer shutdown failed "
                    f"error_type={type(Error).__name__}"
                )
            finally:
                self._timer_registered = False

    def _PruneLedger(self) -> None:
        """Expire old terminal entries while retaining every active request."""
        Now = time.time()
        Expired = []
        for RequestId, Command in self._request_ledger.items():
            with Command.StateLock:
                IsExpired = (
                    Command.status in TERMINAL_STATUSES
                    and Command.end_time is not None
                    and Now - Command.end_time >= self._LedgerRetentionSeconds
                )
            if IsExpired:
                Expired.append(RequestId)
        for RequestId in Expired:
            self._RemoveLedgerEntry(RequestId)

    def _MakeLedgerSpace(self) -> None:
        """Evict the oldest terminal entry, never active work, at the hard limit."""
        if len(self._request_ledger) >= self._MaxLedgerEntries:
            raise CommandLifecycleError(
                "COMMAND_LEDGER_FULL",
                "Command ledger is at capacity within its reconciliation window",
                "unassigned",
                ExecutionStatus.PENDING,
                True,
            )

    def _RemoveLedgerEntry(self, RequestId: str) -> None:
        """Remove one expired entry and release its accounted retained-result bytes."""
        Command = self._request_ledger.pop(RequestId, None)
        if Command is not None:
            self._LedgerResultBytes = max(
                0,
                self._LedgerResultBytes - Command.ResultByteSize,
            )

    def _ReserveResult(self, Result: Any) -> Tuple[bool, int, Optional[str]]:
        """Bound retained result memory before publishing a terminal success state."""
        try:
            ResultBytes = len(
                json.dumps(
                    Result,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            )
        except (TypeError, ValueError):
            return False, 0, "not_json_serializable"

        if ResultBytes > self._MaxRetainedResultBytes:
            return False, 0, "per_result_limit"
        with self._ledger_lock:
            if self._LedgerResultBytes + ResultBytes > self._MaxLedgerResultBytes:
                return False, 0, "ledger_byte_limit"
            self._LedgerResultBytes += ResultBytes
        return True, ResultBytes, None

    def execute_batch(
        self, operations: List[Tuple[Any, ...]], timeout: float = 60.0, stop_on_error: bool = True
    ) -> List[Any]:
        """
        Execute multiple operations efficiently using MCPCommands.

        Args:
            operations: List of (func, args, kwargs) tuples
            timeout: Total timeout for all operations
            stop_on_error: Stop on first error

        Returns:
            List of results
        """
        results: List[Any] = []

        if is_main_thread():
            for op in operations:
                func, args, kwargs = self._ParseBatchOperation(op)
                try:
                    results.append(func(*args, **kwargs))
                except Exception:
                    results.append(None)
                    if stop_on_error:
                        break
            return results

        if not self._ensure_timer():
            raise RuntimeError("Blender main-thread scheduling is unavailable")

        ParsedOperations = [self._ParseBatchOperation(Operation) for Operation in operations]
        cmds: List[MCPCommand] = []
        with self._ledger_lock:
            self._PruneLedger()
            if len(self._request_ledger) + len(ParsedOperations) > self._MaxLedgerEntries:
                raise CommandLifecycleError(
                    "COMMAND_LEDGER_FULL",
                    "Batch does not fit within the command reconciliation window",
                    "unassigned",
                    ExecutionStatus.PENDING,
                    True,
                )
            for func, args, kwargs in ParsedOperations:
                RequestId = str(uuid.uuid4())
                cmd = MCPCommand(
                    id=RequestId,
                    func=func,
                    args=args,
                    kwargs=kwargs,
                    intent="batch_execution",
                    RequestDigest=f"batch:{RequestId}",
                    ResultPolicy=self._ReserveResult,
                )
                self._request_ledger[RequestId] = cmd
                self._active_tasks[RequestId] = cmd
                cmds.append(cmd)

        for cmd in cmds:
            self._task_queue.put(cmd)

        # Wait for all with timeout
        start = time.time()
        remaining_timeout = timeout

        for cmd in cmds:
            if remaining_timeout <= 0:
                cmd.MarkTimedOut()
                results.append(None)
                continue

            if not cmd.event.wait(timeout=remaining_timeout):
                cmd.MarkTimedOut()
                results.append(None)
                if stop_on_error:
                    self._CancelPendingBatch(cmds, cmd)
                    break
            else:
                if cmd.status in FAILED_STATUSES:
                    results.append(None)
                    if stop_on_error:
                        self._CancelPendingBatch(cmds, cmd)
                        break
                elif cmd.status in SUCCESS_STATUSES:
                    results.append(cmd.result)
                else:
                    results.append(None)

            remaining_timeout = timeout - (time.time() - start)

        return results

    @staticmethod
    def _ParseBatchOperation(
        Operation: Tuple[Any, ...],
    ) -> Tuple[Callable[..., Any], Tuple[Any, ...], Dict[str, Any]]:
        """Validate one legacy batch tuple without changing its public format."""
        if len(Operation) == 2:
            Func, Args = Operation
            Kwargs: Dict[str, Any] = {}
        elif len(Operation) == 3:
            Func, Args, Kwargs = Operation
        else:
            raise ValueError(f"Invalid operation format: {Operation}")
        return Func, Args, Kwargs

    @staticmethod
    def _CancelPendingBatch(Commands: List[MCPCommand], Current: MCPCommand) -> None:
        """Tombstone unclaimed siblings after stop-on-error."""
        SeenCurrent = False
        for Command in Commands:
            if Command is Current:
                SeenCurrent = True
                continue
            if SeenCurrent:
                Command.Cancel()

    def get_stats(self) -> Dict[str, Any]:
        """Get execution statistics."""
        with self._ledger_lock:
            self._PruneLedger()
            return {
                **self._stats,
                "queue_size": self._task_queue.qsize(),
                "active_tasks": len(self._active_tasks),
                "retained_requests": len(self._request_ledger),
                "ledger_capacity": self._MaxLedgerEntries,
                "ledger_retention_seconds": self._LedgerRetentionSeconds,
                "retained_result_bytes": self._LedgerResultBytes,
                "ledger_result_byte_capacity": self._MaxLedgerResultBytes,
            }


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def is_main_thread() -> bool:
    """
    Check if current thread is Blender's main thread.

    Returns:
        True if on main thread
    """
    try:
        return threading.current_thread() is threading.main_thread()
    except Exception:
        return False


def execute_on_main_thread(
    func: Callable[..., Any], *args: Any, timeout: float = 30.0, **kwargs: Any
) -> Any:
    """
    Convenience function for main thread execution.

    Example:
        result = execute_on_main_thread(bpy.ops.object.mode_set, mode='EDIT')
    """
    return ThreadSafety().execute_on_main(func, *args, timeout=timeout, **kwargs)


def thread_safe(timeout: float = 30.0) -> Callable[..., Any]:
    """
    Decorator to automatically route function to main thread.

    Usage:
        @thread_safe(timeout=10.0)
        def my_bpy_operation():
            bpy.ops.mesh.primitive_cube_add()
            return bpy.context.active_object
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return ThreadSafety().execute_on_main(func, *args, timeout=timeout, **kwargs)

        return wrapper

    return decorator


def ensure_main_thread(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorator that ensures function runs on main thread.
    Only redirects if NOT on main thread.

    Usage:
        @ensure_main_thread
        def my_handler():
            # Always runs on main thread
            pass
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if is_main_thread():
            return func(*args, **kwargs)
        return ThreadSafety().execute_on_main(func, *args, **kwargs)

    return wrapper


# =============================================================================
# SAFE OPERATORS - Pre-bound safe operations
# =============================================================================


class SafeOperators:
    """
    Pre-bound safe operations for common bpy.ops calls.
    All operations automatically execute on main thread.

    Usage:
        from ..core.thread_safety import SafeOperators

        # These automatically execute on main thread
        SafeOperators.mode_set(mode='EDIT')
        SafeOperators.cube_add(size=2.0)
    """

    @staticmethod
    def mode_set(mode: str, **kwargs: Any) -> Any:
        """Safe mode_set."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.object.mode_set, mode=mode, **kwargs)

    @staticmethod
    def cube_add(size: float = 2.0, **kwargs: Any) -> Any:
        """Safe cube add."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.mesh.primitive_cube_add, size=size, **kwargs)

    @staticmethod
    def sphere_add(radius: float = 1.0, **kwargs: Any) -> Any:
        """Safe sphere add."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.mesh.primitive_uv_sphere_add, radius=radius, **kwargs)

    @staticmethod
    def select_all(action: str = "SELECT") -> Any:
        """Safe select all."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.object.select_all, action=action)

    @staticmethod
    def delete(**kwargs: Any) -> Any:
        """Safe delete."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.object.delete, **kwargs)

    @staticmethod
    def modifier_apply(modifier: str, **kwargs: Any) -> Any:
        """Safe modifier apply."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.object.modifier_apply, modifier=modifier, **kwargs)

    @staticmethod
    def join() -> Any:
        """Safe join."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.object.join)

    @staticmethod
    def duplicate(linked: bool = False, mode: str = "TRANSLATION", **kwargs: Any) -> Any:
        """Safe duplicate."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.object.duplicate, linked=linked, mode=mode, **kwargs)

    @staticmethod
    def modifier_add(type: str, **kwargs: Any) -> Any:
        """Safe modifier add."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.object.modifier_add, type=type, **kwargs)

    @staticmethod
    def subdivide(number_cuts: int = 1, **kwargs: Any) -> Any:
        """Safe mesh subdivide."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.mesh.subdivide, number_cuts=number_cuts, **kwargs)

    @staticmethod
    def nla_bake(frame_start: int, frame_end: int, **kwargs: Any) -> Any:
        """Safe NLA bake."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(
            bpy.ops.nla.bake, frame_start=frame_start, frame_end=frame_end, **kwargs
        )

    @staticmethod
    def export_gltf(filepath: str, **kwargs: Any) -> Any:
        """Safe glTF export."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.export_scene.gltf, filepath=filepath, **kwargs)

    @staticmethod
    def export_usd(filepath: str, **kwargs: Any) -> Any:
        """Safe USD export."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.wm.usd_export, filepath=filepath, **kwargs)

    @staticmethod
    def export_alembic(filepath: str, **kwargs: Any) -> Any:
        """Safe Alembic export."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.wm.alembic_export, filepath=filepath, **kwargs)

    @staticmethod
    def export_fbx(filepath: str, **kwargs: Any) -> Any:
        """Safe FBX export."""
        if not BPY_AVAILABLE:
            return None
        return execute_on_main_thread(bpy.ops.export_scene.fbx, filepath=filepath, **kwargs)

    @staticmethod
    def export_obj(filepath: str, **kwargs: Any) -> Any:
        """Safe OBJ export."""
        if not BPY_AVAILABLE:
            return None
        export_scene = getattr(bpy.ops, "export_scene", None)
        if export_scene is not None and hasattr(export_scene, "obj"):
            return execute_on_main_thread(export_scene.obj, filepath=filepath, **kwargs)
        wm_ops = getattr(bpy.ops, "wm", None)
        if wm_ops is not None and hasattr(wm_ops, "obj_export"):
            return execute_on_main_thread(wm_ops.obj_export, filepath=filepath, **kwargs)
        raise RuntimeError("No OBJ export operator available in this Blender build")


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "ThreadSafety",
    "MCPCommand",
    "ExecutionStatus",
    "CommandLifecycleError",
    "CommandTimeoutError",
    "execute_on_main_thread",
    "is_main_thread",
    "thread_safe",
    "ensure_main_thread",
    "SafeOperators",
]
