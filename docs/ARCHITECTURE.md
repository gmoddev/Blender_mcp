# Blender MCP Architecture Assessment

## Current Control Path

```text
MCP client
  -> newline-delimited JSON-RPC on stdio
  -> stdio_bridge.MCPBridge (schema cache, transaction lock, request identity)
  -> loopback TCP, bounded length prefix, protocol-v2 envelope
  -> server-first mutual HMAC authentication
  -> BlenderMCPServer client thread (session/correlation validation)
  -> dispatcher (registry -> action -> capability -> schema)
  -> ThreadSafety main-thread queue
  -> registered handler
  -> user-scoped canonical filesystem boundary when the action performs file I/O
  -> dispatcher result and metadata
  -> correlated RESPONSE envelope
  -> original JSON-RPC response ID
```

The bridge is ordinary Python. It owns MCP JSON-RPC parsing, one serialized socket transaction,
authentication as the client, and response correlation. It must not import or call `bpy`.

The add-on listener and client threads own sockets, frame parsing, authentication as the server,
and command handoff. They must not perform Blender API work. Before opening the listener, server
startup snapshots Safe Mode and raw-code enablement on Blender's main thread into immutable,
lock-protected control-plane state. The dispatcher authorizes from that snapshot and the existing
filesystem-policy snapshot, then schedules handlers that touch `bpy` through the main-thread queue.

`BlenderMCPServer.start()` initializes the shared lifecycle before opening the listener. Timer and
dependency-handler registration occur only on Blender's main thread, the background health monitor
reads timestamps without calling `bpy`, and teardown unregisters the same stable callback object.
Cold worker attempts fail closed instead of registering Blender callbacks from a socket thread.

## Trust Boundaries

1. The MCP client controls JSON-RPC names, arguments, and IDs.
2. A loopback TCP peer is local but untrusted until the HMAC handshake succeeds.
3. Framing/auth/session validation precedes action payload dispatch.
4. The network thread must cross an explicit queue boundary before `bpy` work.
5. Raw Python crosses from structured control into Blender-user code execution.
6. Files, subprocesses, credentials, URLs, downloads, archives, native importers, and external
   responses cross separate authority/content boundaries. The scene/export file family has an
   initial root policy; other file and external surfaces are not yet fully hardened.
7. `.blend` files are protected assets and untrusted inputs; Scene properties are project data.

## Existing Strengths

- Cohesive handlers register through one dispatcher with schemas and action lists.
- Length-prefixed framing handles split TCP and UTF-8 segments without parse-when-valid ambiguity.
- `bpy.app.timers` provides an explicit main-thread marshalling point.
- Handler auto-discovery avoids duplicate routing tables.
- Render jobs already have a job abstraction and subprocess argument lists rather than `shell=True`.
- No active telemetry sender or runtime self-update path was found in the scanned fork.

## Validated Failures

The retained scan reports 11 findings. The first slice directly addresses unauthenticated dispatch,
unbounded framing/clients, cosmetic request identity, blind bridge replay, raw-message logging, and
the Safe Mode raw-code bypass. It does not yet fix the unsafe main-thread timeout state machine,
filesystem roots, Hunyuan local-file/SSRF paths, bounded downloads/archives, provider credential
serialization. Caller-controlled batch-name regex execution is now removed and replaced by a
bounded regex-free selector boundary.

## First-Slice Architecture

`core/protocol.py` owns transport framing and complexity limits. `core/session.py` owns the sole
wire envelope, HMAC transcript, authentication state, session binding, and correlation checks.
`BlenderMCPServer` owns instance/epoch identity, active socket capacity, revocation, and authenticated
dispatch. `MCPBridge` owns the client session and poisons a socket after any ambiguous exchange.
`core/security.py` owns capability policy; dispatcher metadata supplies action-level requirements.
The security module owns no `bpy` reference. Authorization preference changes deliberately require
a server restart; future dynamic updates must publish a new snapshot from a main-thread callback.
The legacy `utils.path` and `utils.path_validator` modules are exact compatibility adapters into the
core-owned filesystem boundary; the architecture gate permits no other `utils`-to-`core` imports.

Secrets never appear in protocol logs or message previews. `core/credential_store.py` owns fixed
control and provider slots in an allowlisted OS keyring backend; the bridge and add-on share the
control slot. Protocol v2 binds a bridge-instance namespace and session identity into mutual-HMAC
authentication; it does not claim per-frame cryptographic integrity.

`core/archive_boundary.py` owns provider ZIP inspection, bounded streaming extraction, and opaque
request-owned artifact workspaces. It is Blender-independent and must run off the main thread.
`core/network_boundary.py` owns immutable HTTPS purpose policies, URL/DNS/redirect/peer/TLS checks,
deadlines, response budgets, and streaming downloads into those workspaces. It is also
Blender-independent and must run off the main thread. Providers remain quarantined until trusted
provider contracts feed both boundaries without accepting caller-selected URLs, arbitrary headers,
or implicit artifact roots. `core/provider_jobs.py` owns bounded worker preparation, request/digest
reconciliation, cancellation, serialized main-thread commit admission, worker cleanup, and a
metadata-only retained ledger. It is not connected to the quarantined handlers.
`core/name_selector.py` owns the only caller-controlled batch-name matching grammar: bounded
case-sensitive exact, prefix, suffix, and iterative `*`/`?` glob matching. Both batch handlers deny
their legacy regex fields before scene access and use this shared boundary.
`core/provider_content.py` owns conservative single-file GLB admission between a request workspace
and a future native import. It revalidates workspace containment and file identity, parses bounded
GLB/JSON structure without `bpy`, rejects external resources and extensions, caps graph, accessor,
buffer, animation, and embedded-image work, and binds the preparation plan to a SHA-256 digest.
Its post-import delta checker is evidence for a future commit callback, not rollback or permission
to enable a provider.

## Migration Risks

- Protocol v2 intentionally rejects v1 and legacy unversioned clients; add-on and bridge must be
  upgraded together. This avoids the silent dual-protocol ambiguity reported upstream.
- Safe Mode now blocks every unaudited legacy structured action because it is conservatively
  classified `MUTATE`. The action audit will restore explicitly proven reads.
- Existing consumers that call `recv_message()` must handle typed malformed/truncated/oversize
  errors rather than treating them as clean EOF.
- A bridge timeout is visibly indeterminate and never automatically replayed. Foundation 0D adds a
  bounded, in-process request ledger and a reconciliation handler that bypasses the Blender timer.
  Protocol v2 namespaces retained identities by proof-bound bridge instance and preserves numeric
  versus string JSON-RPC ID types. The ledger still does not survive Blender restart or cover direct
  callbacks.
- The shared queue's authenticated timeout, cancellation, duplicate, response-loss/reconnect, and
  shutdown paths are live-validated on Blender 5.2.1 in disposable factory sessions.
- Provider credential properties are no longer registered on Scene. Existing `.blend` files may
  retain dormant legacy ID properties until the user confirms cleanup; they are never silently
  promoted into the OS keyring. Scene/export file sinks now use a central
  user-scoped path authority. Every unpacked file-backed image is conservatively authorized before
  GLB export; tiled/sequence/movie image families fail closed. OBJ material sidecars are disabled.
  Blender 5.2 USD export uses the schema-confirmed `KEEP` mode with texture overwrite and
  world-material conversion disabled, and live proof confirms no texture directory is produced.
  Remaining file/network handlers, extension-defined inputs, atomic publication, and string-path
  TOCTOU prevent valuable-asset readiness.
- Sequencer media inputs are authorized before editor creation. Sequencer preview rendering is
  disabled until its frame-derived output family can be authorized as a unit.
- Single viewport captures publish below the write root and return image data inline without JSON
  sidecars. Multi-output capture is disabled. Primary background render helpers are also disabled
  because output authority does not imply process launch or unmanaged temporary-scene authority.
- Environment images are limited to authorized `.hdr`/`.exr` files and load before world mutation.
  Legacy in-process headless render helpers are disabled because mutable compositor state can
  produce outputs not represented by the caller's single path.
- Motion-capture BVH files cross the central read-root boundary before Blender import. FBX animation
  import remains disabled because the native importer can expand one primary file into a family of
  content-selected external reads.
- Physics setup cannot accept caller-selected cache paths. Physics bake, simulation-play, and
  cache-clear entry points are disabled because scene-derived cache families cannot yet be
  enumerated, authorized, bounded, or reconciled before Blender writes or deletes them.
- Texture bakes may mutate Blender-internal image datablocks, but caller-selected external output
  paths are rejected before scene access at the registered handler and direct helper boundaries.
- Every provider module exposes only truthful read-only status until shared network, credential,
  download, content-validation, temporary-artifact, and provider-job services exist. External
  actions carry their real capability metadata and are denied at dispatcher and direct boundaries.
- ZIP artifacts now have a shared preflight and extraction boundary with deterministic in-process
  cleanup. Purpose-scoped HTTPS downloads now reauthorize redirects, bind DNS to the connected peer,
  verify TLS, enforce deadlines and response budgets, and clean partial request workspaces. Startup
  cleanup/reconciliation and controlled live-network fixtures remain open. Provider work now has a
  bounded worker-prepare/main-thread-commit lifecycle. Single-file GLB content can now be inspected
  off-thread and revalidated by digest before commit, and Blender 5.2.1 validates a bounded import
  result delta. Provider-specific contracts, importer rollback, file selection, and handler
  integration remain open, so this foundation does not make a provider operational.

## Reference-Repositories Assessment

- `glonorce/Blender_mcp` remains the architectural base and has no issue/PR history beyond its
  initial release at the inspected revision.
- `ahujasid/blender-mcp` informed connection serialization and exposes real protocol-skew,
  split-UTF-8, and archive-safety history. Its add-on handshake is version discovery, not peer auth,
  and its optional AST Safe Mode is not a Python sandbox.
- `soozs1/blender-mcp` demonstrates constant-time token checks and connection limits, but its
  unauthenticated pairing/token-file/Scene publication conflicts with this fork's secret boundary.
- `6xvl/Blender-MCP` is future authoring-feature research only. Its monolithic transport, ambiguous
  retry, telemetry/update machinery, and forced updater are not port candidates.

As inspected on 2026-09-09, only ahujasid had active issues/PRs. Relevant primary evidence includes
[protocol skew #311](https://github.com/ahujasid/blender-mcp/issues/311),
[archive traversal #306](https://github.com/ahujasid/blender-mcp/issues/306), and
[split UTF-8 PR #324](https://github.com/ahujasid/blender-mcp/pull/324).
