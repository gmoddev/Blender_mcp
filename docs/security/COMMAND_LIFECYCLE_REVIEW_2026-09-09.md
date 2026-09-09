# Foundation 0D Command Lifecycle Security Review

## Outcome

Commit `e5ad7b9` implements the static and unit-test portion of finding 5 from scan
`4c01fbde-3fea-430a-b777-d3fcc91b574e`. Final disposition remains **NEED LIVE VALIDATION** because
Blender is not installed in the verification environment.

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

## Verification

- Focused lifecycle/dispatcher/transport/bridge suite: 61 passed.
- Unit suite: 553 passed.
- Full suite: 577 passed, 24 live-Blender tests skipped.
- Project quality gate: 8 of 8 checks passed.
- Ruff and Black on all changed Python files: passed.
- Mypy on the changed control-plane files, with imported baseline modules skipped and the existing
  `thread_safety.py` Blender-stub index issue disabled: passed.
- Full-project Ruff, Black, and Mypy retain unrelated baseline failures documented by the earlier
  post-patch review; no broad cleanup was mixed into Foundation 0D.

## Remaining Gates

- Run deterministic pending/dequeue and running-timeout races inside a supported live Blender.
- Test disconnect/reconnect and commit-before-response socket faults against the live add-on.
- Verify shutdown/reload tombstones pending work and does not display modal/focus-stealing errors.
- Give direct provider `bpy.app.timers` callbacks their own observable job identity; they are not
  covered by the shared command ledger.
- The ledger is process-local. Restart-lost or expired request IDs remain unknown and are never proof
  that retry is safe.
