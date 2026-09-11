# Security Invariant Registry

This is the engineering registry for controls that must become executable tests. Status `Target`
means the invariant is required but not yet proven by the current implementation.

| ID | Invariant | Verification | Status |
|---|---|---|---|
| CMD-001 | A command cancelled while pending never invokes its callable. | Deterministic queue/timer race test. | Enforced on shared queue; unit and Blender 5.2.1 negative tests pass |
| CMD-002 | Timeout never implies non-execution; the response distinguishes pending timeout from running-after-timeout. | State-machine unit and Blender integration tests. | Enforced on shared queue; pending and running timeout paths pass live |
| CMD-003 | Command state transitions are atomic, monotonic, and terminal states cannot be overwritten by late workers. | Concurrency tests with controlled events. | Enforced on shared queue; unit transitions and live late completion pass |
| CMD-004 | Retrying a mutation requires an idempotency key or explicit reconciliation of the earlier request. | Duplicate-request integration tests. | Enforced for the retained process ledger: proof-bound bridge namespaces and typed IDs isolate replay/conflict decisions; restart durability remains REC-001 |
| PROTO-001 | Every response is correlated to exactly one request ID; mismatch or omission fails closed. | Socket-pair protocol tests. | Enforced in protocol v2: session correlation, JSON value type, and authenticated bridge namespace are distinct identity dimensions |
| PROTO-002 | Frames use unsigned 32-bit big-endian length prefixes and reject zero, truncated, malformed, or oversized payloads. | Boundary, split-Unicode, and live hostile-frame tests. | Implemented; live pending |
| PROTO-003 | A connection serializes each complete request/reply transaction unless multiplexing is explicitly negotiated. | Concurrent client tests. | Implemented; concurrency stress pending |
| AUTH-001 | The server binds to loopback by default; remote exposure is not supported in protocol v2. | Configuration and socket binding tests. | Enforced; negative configuration tests and live loopback binding pass |
| AUTH-002 | Every request authenticates before tool discovery, action parsing, or dispatch. | Missing, invalid, expired, replayed, and revoked proof tests. | Enforced; negative unit matrix and live wrong-credential test pass |
| AUTH-003 | Credentials are high-entropy, rotatable, revocable, never logged, and stored with user-only access where supported. | Unit tests plus platform storage inspection. | Partial: rotation/revocation implemented; OS store pending |
| AUTHZ-001 | Safe Mode permits only explicit reads; unknown/unclassified actions and structured mutations deny. | Policy and dispatcher matrix tests. | Partial: raw paths gated; action audit pending |
| AUTHZ-002 | Raw Blender Python is documented and surfaced as arbitrary code execution, not a sandbox. | Schema/UI/docs assertions. | Implemented; live UI review pending |
| THREAD-001 | Only Blender's main thread may call `bpy`; network and worker threads only enqueue work. | Thread assertions and live Blender tests. | Partial: shared lifecycle and pre-queue authorization are main-thread safe; execution-engine and remaining background paths need repository-wide audit |
| MUT-001 | Mutations serialize and expose `Pending`, `Running`, and a truthful terminal/indeterminate state. | Ordered execution tests. | Enforced on shared queue; direct provider jobs remain separate coverage |
| PRIV-001 | Logs contain request IDs, action names, states, and timing, but never code, tokens, prompts, arbitrary params, or asset contents. | Capture-and-scan tests with canary secrets. | Partial: core request path fixed; all handlers pending audit |
| PRIV-002 | No telemetry leaves the process and telemetry is not enabled or implied by default. | Static scan and network-isolation test. | Target |
| SUPPLY-001 | Runtime code never silently replaces itself from a remote branch. Updates are explicit and artifacts are pinned. | Static scan and release tests. | Target |
| INPUT-001 | Untrusted lengths, paths, URLs, schemas, and imported asset metadata are bounded and validated before use. | Boundary and traversal tests. | Partial |
| EXT-001 | External provider actions cannot perform file, network, archive, timer, credential, temporary-artifact, or Blender mutation work until their dedicated capability foundations exist. | Dispatcher-denial, direct-call, sink-removal, reload-residue, prohibited-I/O, bypass-input, and Blender-live tests. | Enforced for Hunyuan, Hyper3D, Sketchfab, and Poly Haven after Blender restart; provider functionality intentionally unavailable |
| EXT-002 | External-integration status distinguishes saved configuration from operational availability, performs no external I/O, and does not read provider credentials. | Safe Mode status, credential-read canary, and truthful-field assertions. | Enforced for Hunyuan, Hyper3D, Sketchfab, and Poly Haven |
| FS-001 | A migrated file action reaches a Blender/file sink only with a canonical regular-file path contained beneath the matching user-approved read or write root. | Traversal, sibling-prefix, link, Windows ambiguity, final-extension, direct-caller, and legitimate-path tests. | Partial: primary paths and GLB file-backed images pass Windows coverage; remaining file surfaces are not fully inventoried |
| FS-002 | Existing outputs are not replaced unless overwrite is enabled locally; export actions with request-level overwrite intent require both decisions. | Sentinel-file and denied-operator tests. | Partial: primary destination overwrite checks pass and OBJ material sidecars are disabled; atomic publication remains unproven |
| FS-003 | A scene-derived or content-selected input/output family remains unavailable until every member and destructive effect can be enumerated and authorized before mutation. | Registered/direct denial, sink-removal, scene-nonmutation, and sentinel tests. | Partial: GLB file images are preauthorized, complex image families are quarantined, and OBJ/USD sidecars are disabled and live-proven; other compound families remain |
| ERROR-001 | Boundary failures return structured, redacted errors without modal or focus-stealing UI. | UI behavior review and error-contract tests. | Partial: protocol/auth structured; live UI pending |
| REC-001 | After disconnect or indeterminate completion, a client can query execution state and reconcile scene state before retrying. | Disconnect/reconnect integration test. | Partial: same-bridge reconnect is isolated and reconciles in process; disconnect shutdown, process restart, and direct callbacks remain |
| LIC-001 | Copied or adapted code retains required copyright and license notices with provenance. | Release checklist and dependency/source inventory. | Target |

## Current Evidence Needing Immediate Review

- `blender_mcp/core/thread_safety.py`: the shared queue tombstones pending timeouts/cancellations,
  retains running-after-timeout outcomes, deduplicates stable request IDs, and registers/removes its
  stable timer only on Blender's main thread. Blender 5.2.1 races pass; restart loss remains.
- `blender_mcp/core/security.py`: capability enforcement reads an immutable policy snapshot and
  retains no Blender API reference; unaudited structured actions keep a conservative migration
  classification.
- `blender_mcp/handlers/manage_scripting.py`: raw execution receives normal Python builtins.
- Provider credentials remain ordinary Scene properties and temporary render copies still need
  deterministic secret scrubbing and cleanup.
- Hunyuan external actions are quarantined at dispatcher and handler boundaries. Restoring them
  requires the 0E-0G controls and provider-job lifecycle in ADR 0005; other provider paths remain
  unaudited. Deploying the quarantine requires a Blender restart to invalidate callbacks captured
  by the retired implementation.
- The primary bridge/server/dispatcher log path is metadata-only; provider and job logging still
  require a repository-wide canary audit.
- The scene/export family now uses explicit read/write roots and local overwrite approval. GLB is
  single-file only and conservatively preauthorizes every unpacked file image below the read root;
  tiled, sequence, and movie image families remain disabled. OBJ material output, USD texture
  copying, and USD world-material conversion are locked off. Asset packing remains quarantined.
  Other file surfaces, atomic publication, supported POSIX behavior, and broader live Blender
  operators remain Foundation 0E work. Blender 5.2.1 passes its disposable file-operator harness.
- Sequencer media reads require a configured read root and action-specific media type before the
  editor is created. Preview rendering is quarantined until its derived output family is explicit.
- Single viewport captures are write-root confined and final-sink revalidated. Multi-output capture
  and primary background rendering remain quarantined pending output-family, process, temporary
  artifact, credential-scrubbing, and cleanup controls.
- HDRI setup accepts only authorized `.hdr` or `.exr` reads before image or world mutation. Legacy
  headless render helpers are quarantined because one output path does not authorize compositor or
  scene-derived sidecars.
- BVH motion-capture import accepts one authorized `.bvh` file. FBX animation import is quarantined
  until every content-selected linked input can be authorized before Blender mutation.
- Physics bake, simulation-play, and cache-clear routes are quarantined at registered and direct
  boundaries because Blender derives multi-file cache targets from scene state. Custom cache paths
  are rejected before rigid-body, cloth, or fluid setup mutates the scene.
- Texture bake actions declare filesystem-write authority and reject caller-selected `output_path`
  values before scene access at registered, direct-helper, and low-level operator boundaries.
- Hyper3D, Sketchfab, and Poly Haven now match Hunyuan quarantine: `STATUS` is read-only and truthful;
  every external action declares its real dedicated capabilities and fails before retired network,
  local-file, download, temporary-artifact, credential, or Blender-import sinks.
- `blender_mcp/handlers/manage_rigging.py`: multi-mesh bounds are summed then divided by eight.
- Package metadata and `LICENSE` agree on MIT; the complete copied/adapted-source provenance and
  release-notice inventory remains open under `LIC-001`.

Status must only move to `Enforced` when a negative test proves the control and the relevant runtime
path has been inspected. A passing happy-path test is not sufficient.
