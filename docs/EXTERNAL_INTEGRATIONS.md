# External Integration Boundary

External integrations are untrusted network and content boundaries. Existing Hunyuan, Hyper3D,
Sketchfab, and Poly Haven handlers are not approved for valuable or confidential assets until
Foundation 0F/0G is complete.

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

## Re-enable Gate

Each provider stays untrusted until its complete source-to-sink path uses the shared controls and
passes SSRF, redirect, timeout, oversize, archive traversal/expansion, cleanup, local-file consent,
and credential-serialization tests. Hiding a handler or checking a UI toggle is not a permanent fix.
