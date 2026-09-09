# Command Lifecycle Contract

## Current First-Slice Behavior

A stable, log-safe request ID now travels from the JSON-RPC call through the bridge envelope,
Blender dispatcher, response envelope, logs, and JSON-RPC response. Unsafe string identifiers are
mapped deterministically to a SHA-256-derived wire ID while the original JSON-RPC ID remains in the
outer response.

The bridge never automatically replays a command after its frame may have been sent. It closes a
timed-out or mismatched socket so a late response cannot be consumed by a later request. Such an
outcome is `REQUEST_INDETERMINATE`.

This does not make mutation execution idempotent. The current main-thread queue can still execute a
pending command after its waiter times out. Valuable assets remain blocked until 0D is complete.

## Target State Machine

```text
RECEIVED -> QUEUED -> RUNNING -> SUCCEEDED
              |          |  \-> FAILED
              |          \----> RUNNING_AFTER_TIMEOUT -> SUCCEEDED_LATE | FAILED_LATE
              \-> CANCEL_REQUESTED -> CANCELLED
              \-> TIMED_OUT_PENDING
```

Transitions share one synchronization boundary. `TIMED_OUT_PENDING`, `CANCELLED`, and completed
terminal states cannot return to `RUNNING`. Dequeue atomically claims a pending request. A timeout
while running reports an observable indeterminate state and retains the eventual result.

## Foundation 0D Acceptance

- Bounded request ledger keyed by request ID plus canonical request digest.
- Duplicate identical requests return stored state/result; reuse with different content denies.
- Pending timeout and cancellation tombstone before the queue consumer can execute.
- Running timeout remains queryable after reconnect and never claims non-execution.
- Mutations serialize and terminal states are immutable.
- Response loss is reconciled by request ID before any retry.
