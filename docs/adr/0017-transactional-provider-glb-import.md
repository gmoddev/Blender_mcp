# ADR 0017: Transactional provider GLB import

- Status: Accepted; shared commit boundary and Blender 5.2.1 rollback live-validated
- Date: 2026-09-12

## Context

ADR 0016 admits a bounded, self-contained GLB and binds preparation to its exact bytes, but Blender's
native glTF operator mutates the active process before it returns. Operator failure, an oversized
result, or an elapsed-time violation can therefore leave partial objects and dependent datablocks in
the user's scene. The provider-job ledger previously collapsed every commit exception into one
generic failure code, hiding whether the transactional boundary verified rollback.

Blender's in-process native importer is synchronous and has no supported preemption API. A timer
cannot run while that operator owns the main thread, and killing the process would endanger unsaved
work. A completion deadline can reject and roll back an import after control returns, but it cannot
honestly promise to interrupt a stuck native decoder.

## Decision

Add one main-thread-only provider import boundary. Immediately before mutation it repeats complete
workspace/content validation, requires Object Mode, and captures pointer identities for objects,
collections, actions, armatures, cameras, lights, curves, meshes, materials, node groups, and
images, plus the active object and selection. It then invokes only Blender's native GLB importer.

The commit succeeds only when the native operator finishes, its elapsed time is within a local
bounded completion budget, and the typed object/mesh/material/image/armature/vertex/polygon delta
passes ADR 0016's limits. Every other post-admission result removes newly created datablocks in
dependency-aware order, restores context, and verifies that every captured identity set matches.
Unverified cleanup returns `PROVIDER_IMPORT_ROLLBACK_FAILED`, distinct from a verified rollback.

Trusted commit callbacks may raise a bounded `ProviderCommitError`. The provider-job manager retains
that fixed code while continuing to redact exception text and to run artifact cleanup. Unexpected
exceptions remain `PROVIDER_COMMIT_FAILED`.

## Consequences

Blender 5.2.1 proves a real triangle import can be forced over its completion budget, rolled back to
the exact tracked datablock/context identity, and followed by a successful import. Unit tests cover
wrong-thread admission, changed bytes, native failure, invalid and oversized results, clock faults,
elapsed deadlines, rollback exceptions, rollback mismatches, and job-ledger error propagation.

This is an application transaction, not an operating-system sandbox. The elapsed deadline is
evaluated after the synchronous native operator returns and cannot stop a permanently stuck or
pathologically slow decoder. Providers remain quarantined pending provider-specific content
contracts, controlled network fixtures, artifact-root startup/reconciliation, credential binding,
cancellation wiring, end-to-end job integration, and a defensible decision about process isolation
for hard native-import deadlines.
