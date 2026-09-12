# ADR 0014: Provider preparation and commit lifecycle

- Status: Accepted; shared lifecycle live-validated, provider integration pending
- Date: 2026-09-11

## Context

The quarantined provider handlers previously mixed remote requests, polling, downloads, archive
work, temporary files, timers, and Blender imports. Detached callbacks did not preserve the
initiating MCP request identity, cancellation could not distinguish queued work from work already
running, and provider results could cross into `bpy` from ad hoc timer paths. The legacy generic job
manager also retains arbitrary metadata and callback-specific behavior, so it is not the security
boundary for external providers.

## Decision

Add a separate Blender-independent `ProviderJobManager` with one explicit two-phase contract:

1. A bounded worker pool runs a trusted preparation callback. Network, hashing, archive inspection,
   extraction, and artifact cleanup belong in this phase and must not call `bpy`.
2. Successfully prepared work enters a bounded commit queue. `RunNextCommit()` accepts one item only
   on the manager's captured Blender main thread and serializes the trusted bounded commit callback.
   Cleanup returns to the worker pool after commit.

Every job retains a bounded request ID, purpose, and canonical request digest. Reusing the same
identity and digest returns the existing job; changing the digest or purpose fails closed. Active
and retained job counts, worker count, retention, and waits are bounded. Public snapshots expose
only IDs, purpose, state, fixed failure/outcome codes, affected count, terminal state, and retry
safety. They never expose request digests, callbacks, prepared values, exception text, credentials,
or arbitrary provider metadata.

Queued cancellation tombstones work before preparation and is retry-safe. Once preparation starts,
cancellation is cooperative and never claims retry safety. Prepared cancellation skips Blender
commit and schedules cleanup. A commit already in progress cannot be cancelled and must be
reconciled. Commit failure is not proof that Blender was unchanged. Cleanup failure receives a
distinct terminal state that preserves whether commit succeeded, failed, or was cancelled.

Shutdown is main-thread-only, refuses to race a running commit, denies new admissions, cancels
queued work, requests cancellation of preparation, and schedules cleanup for prepared work.
Terminal jobs release callbacks and prepared payloads before retention.

## Consequences

Provider preparation can no longer require direct `bpy` access, arbitrary timers, or an unbounded
executor queue. Blender 5.2.1 embedded-Python validation proves worker preparation, serialized
main-thread commit, worker cleanup, and truthful terminal output in a disposable factory session.

This lifecycle is not wired to any provider and does not make external actions available. A trusted
artifact-root startup policy, crash/restart reconciliation, controlled network/provider fixtures,
provider-specific endpoint/content contracts, credential binding, cancellation checks inside each
preparation phase, and bounded native-import result validation remain re-enable gates. The generic
legacy job manager remains a separate audit target and must not be used as a provider bypass.
