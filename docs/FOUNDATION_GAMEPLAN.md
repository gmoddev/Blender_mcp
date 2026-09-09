# Blender MCP Foundation Gameplan

## Outcome

Create a reusable `gmoddev/Blender_mcp` foundation that can safely support long-running AI-driven
Blender sessions, then add character tooling without weakening the boundary.

The project is based on `glonorce/Blender_mcp`. `soozs1/Blender-MCP` is a security design reference,
`ahujasid/blender-mcp` is a compatibility and transport reference, and `6xvl/blender-mcp` is a source
of character-workflow ideas. Ports are selective and reviewed; this is not a four-way merge.

## Workstreams

| ID | Workstream | Exit gate |
|---|---|---|
| F0 | Evidence baseline | Existing tests run; architecture, trust boundaries, and known defects are recorded. |
| F1 | Command lifecycle | A timed-out pending mutation cannot execute; running-after-timeout is reported as indeterminate, never failed. |
| F2 | Protocol identity | Every request and response carries the same request ID; mismatches and duplicate unsafe retries fail closed. |
| F3 | Local authentication | The loopback peer is authenticated with a rotated, least-exposed token and bounded pairing flow. |
| F4 | Authorization modes | Safe Mode enforces a deny policy; raw code is an explicit high-risk capability, not a claimed sandbox. |
| F5 | Privacy and supply chain | Telemetry and parameter logging are removed; updates are explicit and builds are pinned/reproducible. |
| F6 | Recovery | Mutations expose checkpoints, idempotency guidance, and observable reconciliation after disconnects/timeouts. |
| F7 | Character primitives | High-value rig, skinning, weight, and shape-key primitives land behind the hardened boundary. |
| F8 | Inspection and validation | Topology, symmetry, deformation-loop, weight, and shape-key diagnostics return quantitative results. |
| F9 | Project skill layer | Product-specific skeleton, proportions, naming, and acceptance rules live outside the generic MCP core. |

## Delivery Sequence

## First Implementation Slice: Foundation 0A/0B plus minimum 0C

The first slice changes these boundaries:

- `blender_mcp/core/protocol.py`: bounded framing, typed input failures, and parser complexity.
- `blender_mcp/core/session.py`: protocol envelope, mutual HMAC handshake, instance/session/epoch
  binding, and response correlation.
- `blender_mcp/__init__.py`: loopback enforcement, authenticated dispatch, client/deadline limits,
  credential rotation, and user-scoped add-on configuration.
- `stdio_bridge.py`: stable JSON-RPC-derived request identity, authenticated serialized transactions,
  socket poisoning after ambiguity, and no post-send replay.
- `blender_mcp/core/security.py`, `dispatcher.py`, and `handlers/manage_scripting.py`: capability
  registry scaffold and denial of every raw execution path unless separately authorized.
- `blender_mcp/core/logging_config.py`: metadata allowlist on the primary request path.
- Unit tests: framing boundaries, split Unicode, mutual/wrong/expired/replayed authentication,
  pre-auth dispatch denial, cross-instance binding, rotation, client capacity, correlation, all raw
  Python aliases, and logging canaries.

This slice intentionally does not modify the main-thread queue state machine, filesystem sinks,
external providers, provider credentials, archives, or selectors. Their findings remain open.

## Second Implementation Slice: Foundation 0D

The command lifecycle slice changes these boundaries:

- `blender_mcp/core/thread_safety.py`: synchronized monotonic states, timeout/cancel tombstones,
  running-after-timeout retention, duplicate detection, bounded result ledger, batch cleanup, and
  add-on shutdown cancellation.
- `blender_mcp/dispatcher.py`: stable wire request IDs and canonical command digests enter the queue;
  caller wait budgets are finite and bounded; lifecycle outcomes remain structured.
- `blender_mcp/handlers/manage_command_lifecycle.py`: status and pending-cancel reconciliation run
  without waiting behind Blender's main-thread queue.
- `stdio_bridge.py` and the add-on response boundary: lifecycle code, request ID, state, terminal,
  and retry-safety metadata survive response shaping.
- `blender_mcp/core/headless_mode.py`: headless render execution uses the shared queue instead of a
  second busy-waiting timer path.

This slice is unit-verified but still requires live Blender queue congestion, reconnect,
shutdown/reload, and socket-fault validation. Its ledger is process-local and bounded; an evicted or
restart-lost ID is unknown, never proof that retry is safe. Direct provider timer callbacks remain
outside this lifecycle and require separate job identity.

### Milestone 0: Baseline and scan readiness

1. Run the unit suite and record failures without normalizing them away.
2. Add repository agent guidance, architecture decisions, invariant registry, and scan instructions.
3. Define the root security policy after owner review of scope and exclusions.
4. Run a repository-wide security scan against that policy and triage findings before feature work.

### Milestone 1: Trustworthy mutation lifecycle

1. Replace the current timeout-only state model with `Pending`, `Running`, `Completed`, `Failed`,
   `CancelRequested`, `Cancelled`, `TimedOutPending`, and `RunningAfterTimeout`.
2. Tombstone pending commands atomically so the Blender timer skips them.
3. Make state transitions monotonic and synchronized.
4. Return the request ID and execution state on all paths.
5. Add deterministic race tests for timeout-before-dequeue, timeout-during-run, late completion,
   cancellation, and duplicate retry.

Do not add character mutations until this milestone passes.

### Milestone 2: Authenticated, bounded protocol

1. Add a protocol version and capability negotiation handshake.
2. Add request IDs at the wire envelope and verify response correlation.
3. Enforce maximum frame length before allocation or JSON decoding.
4. Bind to loopback by default and reject non-loopback binding unless explicitly configured.
5. Add local token pairing, storage, rotation, and revocation without logging secrets.
6. Serialize the complete request/reply exchange per connection.

### Milestone 3: Honest authorization and privacy

1. Replace `SecurityManager.validate_action()`'s unconditional allow with explicit capability policy.
2. Label raw Blender Python as arbitrary code execution and require explicit enablement.
3. Remove telemetry UI/state and any telemetry compatibility paths.
4. Redact logs at their source; never serialize arbitrary parameter dictionaries into logs.
5. Confirm there is no automatic remote source replacement or executable download path.

### Milestone 4: Recovery and release discipline

1. Add mutation preflight metadata and post-operation state summaries.
2. Define checkpoint/save-copy behavior for destructive or long-running operations.
3. Add reconciliation queries for unknown outcomes.
4. Pin supported Blender/Python ranges and dependency locks.
5. Produce a signed or checksummed add-on artifact through CI after tests and security checks.

### Milestone 5: Character tooling

Port behavior in small vertical slices, preserving source attribution where code is copied:

- Rig: armatures, bone chains, edits, mirroring, roll, IK, armature modifiers.
- Skinning: automatic weights, vertex-group assignment/normalization, smoothing, transfer, cleanup,
  mirroring, gradients, falloff, unweighted-vertex discovery.
- Morphology: shape-key CRUD, copy/mirror/capture, delta statistics, bounds, topology comparison,
  and corrective drivers.
- Inspection: topology counts, manifold state, poles, ngons, symmetry error, regional loop density,
  joint deformation loops, and weight-quality metrics.

Each slice includes schema validation, read/mutate classification, negative tests, Blender integration
tests, structured output, and documented retry semantics.

## First Issues to Open

| Priority | Issue | Evidence in current fork |
|---|---|---|
| P0 | Prevent timed-out queued commands from executing | `core/thread_safety.py` sets `TIMEOUT` but leaves the command in the queue. |
| P0 | Report running-after-timeout as indeterminate | The caller currently receives a plain `TimeoutError` regardless of whether execution began. |
| P0 | Enforce Safe Mode | `core/security.py::validate_action()` always returns `True`. |
| P0 | Stop logging arbitrary command parameters | Dispatcher/server debug paths include request content. |
| P1 | Remove telemetry preference and legacy command | Add-on preferences default telemetry consent to enabled. |
| P1 | Add peer authentication and rotation | Loopback TCP accepts an unauthenticated client. |
| P1 | Add explicit frame-size limit | Framing exists, but the accepted payload must be bounded before allocation. |
| P1 | Fix multi-mesh metarig center calculation | `manage_rigging.py` divides all bounding points by eight. |
| P2 | Reconcile package license metadata | `LICENSE` says MIT while `pyproject.toml` says proprietary. |
| P2 | Remove malformed historical comment block | `stdio_bridge.py` contains a large non-functional repair narrative. |

## Scan and Change Gates

- Run the standard repository security scan after the root `SECURITY.md` is approved.
- Treat P0 findings in the command, protocol, authentication, or raw-code boundary as feature blockers.
- Require a focused security diff scan for each F1-F5 change.
- Require live Blender validation for queue scheduling, cancellation, scene mutation, and recovery.
- Work on disposable `.blend` copies until F1 and F4 are complete.
