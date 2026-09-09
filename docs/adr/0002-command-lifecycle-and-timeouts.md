# ADR 0002: Command Lifecycle and Timeout Semantics

- Status: Accepted; shared command path live-validated
- Date: 2026-09-08

## Context

The current queue marks a command timed out and removes active tracking while leaving the callable in
the queue. A stale destructive operation can therefore execute after the caller has attempted
recovery. If execution already began, the caller receives the same timeout and cannot know whether
the scene changed.

## Decision

Use a synchronized state machine with these externally meaningful outcomes:

```text
Pending -> Running -> Completed
   |          |  \-> Failed
   |          \----> RunningAfterTimeout -> CompletedLate | FailedLate
   \-> CancelRequested -> Cancelled
   \-> TimedOutPending
```

A pending timeout atomically tombstones the command before it can transition to `Running`. A running
timeout never claims cancellation or failure; it returns `RunningAfterTimeout` plus the request ID.
Terminal states are immutable. Clients reconcile state before retrying mutations.

The server keeps an in-process ledger of at most 4096 requests. Terminal entries expire after 15
minutes; the server fails closed at capacity rather than evicting unexpired duplicate-suppression
evidence. Retained results are limited to 4 MiB each and 32 MiB total. Oversized/dropped results keep
an explicit completed, non-retry-safe state. Because the ledger is not durable, an unknown request
after expiry or restart does not authorize retry.
Canonical command digests prevent one retained request ID from being rebound to different content.

Reconciliation is exposed through a registered handler that does not require Blender's main thread,
so status remains available while a long operation is running. Caller-visible queue waits are
bounded to 0.1 through 7200 seconds.

The server starts the lifecycle on Blender's main thread before accepting clients. The stable timer
callback polls at most every 50 ms while idle; network threads may observe that registration but may
not call Blender's timer or handler APIs. Shutdown uses the same stable callback identity.

## Acceptance Criteria

- The dequeue and pending-to-running transition share synchronization with cancellation.
- The timer skips tombstoned commands without invoking them.
- Every response exposes request ID and lifecycle state.
- Deterministic tests cover every transition and the timeout/dequeue race.
- Batch execution applies the same semantics to every child command.

Deterministic unit tests cover pending timeout, running timeout and late completion, cancellation,
duplicate replay, request-ID conflict, ledger saturation, timer failure, worker-thread rejection,
and shutdown tombstoning. Blender 5.2.1 tests cover the same queue path over authenticated sockets,
including response loss/reconnect and actual timer removal. Direct provider callbacks and durable
restart recovery are outside this ADR's implemented boundary.
