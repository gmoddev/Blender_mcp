# ADR 0002: Command Lifecycle and Timeout Semantics

- Status: Proposed
- Date: 2026-09-08

## Context

The current queue marks a command timed out and removes active tracking while leaving the callable in
the queue. A stale destructive operation can therefore execute after the caller has attempted
recovery. If execution already began, the caller receives the same timeout and cannot know whether
the scene changed.

## Proposed Decision

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

## Acceptance Criteria

- The dequeue and pending-to-running transition share synchronization with cancellation.
- The timer skips tombstoned commands without invoking them.
- Every response exposes request ID and lifecycle state.
- Deterministic tests cover every transition and the timeout/dequeue race.
- Batch execution applies the same semantics to every child command.
