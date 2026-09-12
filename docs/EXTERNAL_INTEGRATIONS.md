# External Integration Boundary

External integrations are untrusted network and content boundaries. Existing Hunyuan, Hyper3D,
Sketchfab, and Poly Haven handlers are not approved for valuable or confidential assets until
Foundation 0F/0G is complete.

All external actions for Hunyuan, Hyper3D, Sketchfab, and Poly Haven are currently quarantined at
both dispatcher and handler boundaries. `STATUS` remains read-only and reports configuration
separately from operational availability. Status does not read provider credentials. This
containment removes the old callable network, local-file, download, temporary-artifact, archive,
and Blender-import sinks; it does not authorize or approximate the target controls below.

Deploying the containment requires a Blender restart. Module reload purges retired provider names,
but cannot revoke a callback or function reference that old code already captured.

## Target Controls

- Explicit, user-controlled provider enablement and capability authorization.
- Provider asset identifiers instead of arbitrary URLs wherever possible.
- Central URL policy covering scheme, host, port, DNS results, private/loopback/link-local/reserved
  addresses, redirects, and DNS changes across redirects.
- Connect, read, and total deadlines plus bounded compressed bytes, expanded bytes, member count,
  member size, compression ratio, response size, and disk use.
- Safe archive extraction through a shared helper before Blender import.
- Prepare network, hashing, and archive work off the Blender main thread; commit only bounded `bpy`
  mutations on the main thread.
- Deterministic temporary-artifact cleanup on success, failure, cancellation, and shutdown.
- Explicit authorization and size/type validation before any local file leaves the machine.
- OS/user-scoped provider credentials; no secrets in Scene or temporary `.blend` copies.

The shared ZIP boundary now supplies preflight member/path/type/collision checks, compressed and
expanded resource budgets, bounded streaming extraction, opaque request-owned workspaces, and
cleanup on success or failure. The shared HTTPS/download boundary now supplies exact purpose
policies, all-address DNS validation, pinned-peer checks, manual redirect reauthorization, verified
TLS, deadlines, response budgets, media/framing checks, exclusive artifact creation, and partial
cleanup. Neither helper is wired to a provider. A configured artifact-root startup policy,
provider-specific endpoint/content contract, credential binding, and handler integration are still
required before external data can reach Blender. The shared provider-job lifecycle now supplies
bounded worker preparation, request/digest reconciliation, cooperative cancellation, serialized
main-thread commit admission, worker cleanup, and metadata-only terminal state. It is likewise not
wired to a provider. A shared single-file GLB boundary now validates workspace ownership, file
identity, container/JSON structure, external-resource absence, graph/accessor/image budgets, and a
SHA-256-bound pre-import plan. It also validates bounded post-import count deltas. This does not
provide importer interruption or rollback and does not replace provider-specific response contracts.

The shared 0G store now defines fixed OS-backed slots and new provider secret properties are absent
from Scene registration. Existing `.blend` files are warned by legacy field name and can be scrubbed
only through an explicit confirmed action; untrusted Scene values are never imported into the OS
store. Secure provider-entry UI, packaged-runtime validation, and temporary-artifact proof remain,
so external provider actions stay quarantined.

## Re-enable Gate

Each provider stays untrusted until its complete source-to-sink path uses the shared controls and
passes SSRF, redirect, timeout, oversize, archive traversal/expansion, cleanup, local-file consent,
and credential-serialization tests. Hiding a handler or checking a UI toggle is not a permanent fix.

Every provider additionally requires an evidence-backed provider contract: fixed purpose-specific
endpoints or provider asset identifiers, documented redirect behavior, response/archive formats,
and safe resource budgets. Its handler must submit through the shared provider-job lifecycle,
retain the initiating MCP request ID through off-main-thread preparation and bounded main-thread
commit, check native import results, and expose truthful terminal/reconciliation state.
