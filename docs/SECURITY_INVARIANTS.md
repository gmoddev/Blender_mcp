# Security Invariant Registry

This is the engineering registry for controls that must become executable tests. Status `Target`
means the invariant is required but not yet proven by the current implementation.

| ID | Invariant | Verification | Status |
|---|---|---|---|
| CMD-001 | A command cancelled while pending never invokes its callable. | Deterministic queue/timer race test. | Target |
| CMD-002 | Timeout never implies non-execution; the response distinguishes pending timeout from running-after-timeout. | State-machine unit and Blender integration tests. | Target |
| CMD-003 | Command state transitions are atomic, monotonic, and terminal states cannot be overwritten by late workers. | Concurrency tests with controlled events. | Target |
| CMD-004 | Retrying a mutation requires an idempotency key or explicit reconciliation of the earlier request. | Duplicate-request integration tests. | Target |
| PROTO-001 | Every response is correlated to exactly one request ID; mismatch or omission fails closed. | Socket-pair protocol tests. | Implemented; live pending |
| PROTO-002 | Frames use unsigned 32-bit big-endian length prefixes and reject zero, truncated, malformed, or oversized payloads. | Boundary, split-Unicode, and live hostile-frame tests. | Implemented; live pending |
| PROTO-003 | A connection serializes each complete request/reply transaction unless multiplexing is explicitly negotiated. | Concurrent client tests. | Implemented; concurrency stress pending |
| AUTH-001 | The server binds to loopback by default; remote exposure is not supported in protocol v1. | Configuration and socket binding tests. | Implemented; live pending |
| AUTH-002 | Every request authenticates before tool discovery, action parsing, or dispatch. | Missing, invalid, expired, replayed, and revoked proof tests. | Implemented; live pending |
| AUTH-003 | Credentials are high-entropy, rotatable, revocable, never logged, and stored with user-only access where supported. | Unit tests plus platform storage inspection. | Partial: rotation/revocation implemented; OS store pending |
| AUTHZ-001 | Safe Mode permits only explicit reads; unknown/unclassified actions and structured mutations deny. | Policy and dispatcher matrix tests. | Partial: raw paths gated; action audit pending |
| AUTHZ-002 | Raw Blender Python is documented and surfaced as arbitrary code execution, not a sandbox. | Schema/UI/docs assertions. | Implemented; live UI review pending |
| THREAD-001 | Only Blender's main thread may call `bpy`; network and worker threads only enqueue work. | Thread assertions and live Blender tests. | Partial |
| MUT-001 | Mutations serialize and expose `Pending`, `Running`, and a truthful terminal/indeterminate state. | Ordered execution tests. | Target |
| PRIV-001 | Logs contain request IDs, action names, states, and timing, but never code, tokens, prompts, arbitrary params, or asset contents. | Capture-and-scan tests with canary secrets. | Partial: core request path fixed; all handlers pending audit |
| PRIV-002 | No telemetry leaves the process and telemetry is not enabled or implied by default. | Static scan and network-isolation test. | Target |
| SUPPLY-001 | Runtime code never silently replaces itself from a remote branch. Updates are explicit and artifacts are pinned. | Static scan and release tests. | Target |
| INPUT-001 | Untrusted lengths, paths, URLs, schemas, and imported asset metadata are bounded and validated before use. | Boundary and traversal tests. | Partial |
| ERROR-001 | Boundary failures return structured, redacted errors without modal or focus-stealing UI. | UI behavior review and error-contract tests. | Partial: protocol/auth structured; live UI pending |
| REC-001 | After disconnect or indeterminate completion, a client can query execution state and reconcile scene state before retrying. | Disconnect/reconnect integration test. | Target |
| LIC-001 | Copied or adapted code retains required copyright and license notices with provenance. | Release checklist and dependency/source inventory. | Target |

## Current Evidence Needing Immediate Review

- `blender_mcp/core/thread_safety.py`: timeout changes status and removes active tracking but leaves the
  queued callable available to the timer.
- `blender_mcp/core/security.py`: initial capability enforcement is active, but unaudited structured
  actions retain a conservative migration classification.
- `blender_mcp/handlers/manage_scripting.py`: raw execution receives normal Python builtins.
- Provider credentials remain ordinary Scene properties and temporary render copies still need
  deterministic secret scrubbing and cleanup.
- The primary bridge/server/dispatcher log path is metadata-only; provider and job logging still
  require a repository-wide canary audit.
- `blender_mcp/handlers/manage_rigging.py`: multi-mesh bounds are summed then divided by eight.
- `pyproject.toml` and `LICENSE`: package metadata disagrees about the license.

Status must only move to `Enforced` when a negative test proves the control and the relevant runtime
path has been inspected. A passing happy-path test is not sufficient.
