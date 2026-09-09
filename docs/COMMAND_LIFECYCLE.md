# Command Lifecycle Contract

## Implemented Foundation 0D Behavior

A stable, log-safe request ID now travels from the JSON-RPC call through the bridge envelope,
Blender dispatcher, response envelope, logs, and JSON-RPC response. Unsafe string identifiers are
mapped deterministically to a SHA-256-derived wire ID while the original JSON-RPC ID remains in the
outer response.

The bridge never automatically replays a command after its frame may have been sent. It closes a
timed-out or mismatched socket so a late response cannot be consumed by a later request. Such an
outcome is `REQUEST_INDETERMINATE`.

The dispatcher binds that wire ID to a SHA-256 digest of the canonical tool and parameter object.
The in-process ledger retains at most 4096 requests for 15 minutes after their terminal state. An
identical duplicate never enqueues a second callable: it returns the retained result, terminal
state, or current in-progress state. Reusing an ID with different content fails closed as
`REQUEST_ID_CONFLICT`.

The Blender timer and timeout/cancel paths share a per-command lock. Dequeue claims only `pending`
work. A pending timeout becomes `timed_out_pending`, a pending cancellation becomes `cancelled`,
and the timer drains either tombstone without invoking its callable. If the callable was already
claimed, timeout becomes `running_after_timeout`; late success or failure is retained as
`completed_late` or `failed_late`.

`manage_command_lifecycle` runs outside the Blender timer. `GET_STATUS` can therefore reconcile a
retained request while Blender's main thread is occupied, and `CANCEL` tombstones only work that is
still pending. It never reports running work as cancelled.

## Target State Machine

```text
PENDING -> RUNNING -> COMPLETED
   |          |  \-> FAILED
   |          \----> RUNNING_AFTER_TIMEOUT -> COMPLETED_LATE | FAILED_LATE
   \-> CANCEL_REQUESTED -> CANCELLED
   \-> TIMED_OUT_PENDING
```

Transitions share one synchronization boundary. `TIMED_OUT_PENDING`, `CANCELLED`, and completed
terminal states cannot return to `RUNNING`. Dequeue atomically claims a pending request. A timeout
while running reports an observable indeterminate state and retains the eventual result.

## Reconciliation Contract

Call `manage_command_lifecycle` with:

- `GET_STATUS` and `target_request_id` after `REQUEST_INDETERMINATE`, response loss, or reconnect.
- `CANCEL` and `target_request_id` only when pending work should be tombstoned.

`retry_safe=true` is returned only when the retained state proves that the callable never started:
`timed_out_pending` or `cancelled`. `running_after_timeout`, missing/expired records, and failures do
not authorize an automatic retry. A late completed result is replayable with the original request
ID and canonical request content.

## Bounds and Limitations

- Dispatcher wait budgets are finite and server-bounded to 0.1 through 7200 seconds.
- The ledger is memory-only. It does not survive Blender restart and is not a durable transaction
  log.
- Terminal records expire after 15 minutes. Before then, the server rejects new work at capacity
  instead of evicting duplicate-suppression evidence. An unknown/expired request is not proof that
  retry is safe.
- Retained results are capped at 4 MiB each and 32 MiB total. When a successful result cannot be
  retained, state becomes `completed_result_dropped` (or its late equivalent), remains terminal,
  and is never retry-safe.
- The claim covers the registered dispatcher-to-`ThreadSafety` queue and the headless render path,
  which now uses that queue. Direct `bpy.app.timers` callbacks in provider handlers remain separate
  asynchronous operations and need their own job identity before they are covered by `REC-001`.
- Live Blender race, reconnect, shutdown/reload, and commit-before-response tests remain required.

## Foundation 0D Acceptance Evidence

- Bounded request ledger keyed by request ID plus canonical request digest: implemented, unit tested.
- Duplicate identical requests return stored state/result; reuse with different content denies:
  implemented, unit tested.
- Pending timeout and cancellation tombstone before the queue consumer can execute: implemented,
  deterministic negative tests pass.
- Running timeout remains queryable and never claims non-execution: implemented, deterministic late
  completion test passes; reconnect validation remains live-only.
- Mutations serialize and terminal states are immutable: implemented on the shared timer queue;
  live Blender scheduling validation remains.
- Response loss can be reconciled by request ID before retry: endpoint implemented; live socket
  fault injection remains.
