# Foundation 0D Command Lifecycle Security Review

## Outcome

Commits `e5ad7b9` and `4ff7586` implement and live-validate finding 5 from scan
`4c01fbde-3fea-430a-b777-d3fcc91b574e` for the registered dispatcher/shared command queue and the
headless render path. The disposition for that source-to-sink path is **FIXED**. Direct provider
timer callbacks and restart-durable recovery remain explicitly separate coverage gaps.

## Closed Source-to-Sink Path

An authenticated request now carries its stable wire request ID and canonical command digest from
the dispatcher into the shared main-thread queue. Pending timeout/cancel and dequeue claim the same
per-command lock. A tombstone cannot transition to running, and a running timeout transitions only
to an indeterminate state followed by an explicit late terminal result.

Duplicate identical IDs return retained state/result and do not enqueue a second callable. An ID
bound to different content fails closed. Reconciliation runs outside Blender's timer through
`manage_command_lifecycle`, so it remains callable while the main thread is occupied.

## Independent Bypass Review

The required fresh review found four issues in the first patch revision:

1. Capacity eviction could discard duplicate-suppression evidence before the retention deadline.
2. A handler exception could lose lifecycle metadata in generic dispatcher error handling.
3. Partial batch admission could queue early children before a later capacity failure.
4. Retained result memory had entry/time bounds but no byte bound.

The final change fails closed at capacity until records expire, preserves failed-state metadata,
admits batches atomically, and caps retained results at 4 MiB each and 32 MiB total. Tests exercise
each correction.

## Live Blender Review

The first Blender 5.2.1 run exposed three runtime-specific defects not visible in the mocked suite:

1. Cold `ThreadSafety` initialization could register Blender timers and dependency handlers from a
   socket thread.
2. The idle timer interval was one second even though the public minimum command timeout is 100 ms.
3. Re-reading the bound `_process_queue` method produced a different callback object, so Blender
   could execute the timer while `is_registered()` and shutdown cleanup could not identify it.

Commit `4ff7586` starts Blender-owned callbacks from the main thread before the listener accepts
work, makes cold worker startup fail closed, removes background-thread `bpy` calls from the health
monitor, polls the idle queue every 50 ms, and retains one stable timer callback for registration
and teardown.

## Verification

- Focused lifecycle/authenticated-transport suite: 26 passed.
- Unit suite: 558 passed.
- Full suite: 582 passed, 24 legacy live-Blender tests skipped.
- Project quality gate: 8 of 8 checks passed.
- Ruff lint and format on all changed Python files: passed.
- Mypy on the changed control-plane files, with imported baseline modules skipped and the existing
  `thread_safety.py` Blender-stub index issue disabled: passed.
- Blender 5.2.1 live suite: wrong authentication, pending timeout, running timeout/late completion,
  duplicate replay, request-ID conflict, response loss/reconnect, pending cancellation, main-thread
  execution, shutdown tombstoning, and timer removal all passed.
- The reusable command is `tests/live/run_lifecycle_validation.ps1 -BlenderPath <blender.exe>`; it
  runs against disposable factory sessions and never opens the user's active `.blend`.

## Remaining Coverage

- Give direct provider `bpy.app.timers` callbacks their own observable job identity; they are not
  covered by the shared command ledger.
- The ledger is process-local. Restart-lost or expired request IDs remain unknown and are never proof
  that retry is safe.
- The other repository-scan findings remain governed by `REMEDIATION_LEDGER.md`; this review does
  not claim filesystem, provider, credential-storage, or archive safety.
