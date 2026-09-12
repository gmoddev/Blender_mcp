# Blender MCP Hardening Roadmap

The roadmap protects the generic Blender control plane before adding authoring breadth. Gantria
rules, measurements, names, and acceptance criteria remain in a future project skill.

## Foundation 0 — Trustworthy Control Plane

| Slice | Scope | Acceptance gate | Status |
|---|---|---|---|
| 0A | Versioned envelope, stable request identity, response correlation, 8 MiB frames, JSON complexity limits, absolute header/body/idle deadlines, four-client cap | Boundary and negative protocol tests pass; legacy unversioned traffic fails clearly | Implemented and Blender-live validated, including bridge-instance and typed JSON-RPC request namespaces; per-frame cryptographic integrity is not claimed |
| 0B | Server-first HMAC challenge, mutual proof, session/instance/epoch binding, user-scoped credential, rotation and socket revocation | Discovery and dispatch are unreachable before auth; wrong, expired, replayed, and cross-instance proofs fail | Implemented and Blender-live validated; Windows x64 OS storage is packaged under 0G |
| 0C | Action capability metadata, fail-closed policy, Safe Mode, Full Structured Mode, separately enabled raw Python | Every action explicitly audited; unknown classes deny; UI and effective policy agree | Policy snapshot is main-thread captured and worker-safe; raw-code routes gated; full action audit pending |
| 0D | Monotonic request state, cancellation tombstones, bounded result ledger, duplicate detection, reconciliation | Pending timeout never runs; running timeout is queryable/indeterminate; duplicates never re-execute | Partial: bridge-scoped in-process state and reconciliation are live-tested; disconnect shutdown, restart durability, and direct provider callbacks remain |
| 0E | Authorized read/write roots, canonical containment, overwrite policy, symlink/junction/reparse handling | All file sinks use the central boundary; traversal and link escapes fail | In progress: primary paths, GLB external images, OBJ material suppression, and Blender 5.2 USD sidecars are live-tested; remaining sinks, atomic publication, and cross-platform gates remain |
| 0F | Explicitly enabled integrations and bounded network/download/archive helpers | Local-file upload, SSRF, redirect, size, archive, cleanup, and consent tests pass | In progress: all providers remain quarantined; shared ZIP inspection, bounded extraction, request-owned artifacts, and failure cleanup are implemented; safe HTTP/download and provider contracts remain |
| 0G | OS-backed secret lifecycle and legacy Scene-property migration | No provider secret appears in saved or temporary `.blend` files | Windows x64 implementation live-validated: self-contained extension, Credential Locker round trip, Scene registration removal, explicit legacy scrub, and scrubbed save pass; secure provider entry and other platforms remain |
| 0H | Metadata-only logging and privacy validation | Canary secrets are absent from all logs and exception paths | In progress |
| 0I | Queue/job/content/selector bounds | Pathological selectors and resource floods fail without blocking Blender | In progress: archive count, member, compressed/expanded byte, compression-ratio, and path-complexity ceilings are enforced; job/content/selector work remains |
| 0J | Explicit scene checkpoints, retention, restore, cleanup | Destructive workflows can checkpoint and recover deterministically | Planned |
| 0K | Disposable-profile Blender harness and supported-version matrix | Static scan gaps are reproduced and verified live | In progress: Blender 5.2.1 lifecycle, security-policy, provider, filesystem, archive, and credential-extension harnesses pass; other supported versions and platforms remain |

Foundation 0 is complete only when every row is live-validated where Blender or OS behavior is
material and the remediation ledger has no open high-risk boundary.

## Foundation 1 — Generic Authoring Primitives

Begin only after Foundation 0 gates pass. Port behavior selectively into registered handler
modules; do not merge a reference fork wholesale.

- Armature, bone-chain, parenting, mirroring, roll, modifier, and IK primitives.
- Skin binding, assignment, normalization, smoothing, transfer, cleanup, mirroring, gradients,
  unweighted-vertex discovery, and influence diagnostics.
- Shape-key CRUD, copy/mirror/capture, range/value, topology validation, delta statistics,
  affected bounds, comparison, and corrective-driver support.
- Manifold, polygon, pole, symmetry, normal, loop, density, and deformation-region diagnostics.
- Deterministic multiview and structured validation outputs.

Exit gate: each action has an explicit capability, bounded schema, deterministic result, retry
semantics, unit coverage, and a disposable-scene Blender test where it uses `bpy`.

## Foundation 2 — Asset-Authoring Reliability

- Before/after scene snapshots and structured mesh statistics diffs.
- Object-level change manifests, reproducible exports, asset hashing, and provenance.
- Deterministic validation renders, UV/LOD/material diagnostics, and performance measurements.
- A reviewable `Observe -> Plan -> Checkpoint -> Mutate -> Measure -> Inspect -> Accept/Revert` loop.

## Foundation 3 — Gantria Character Skill

Build a separate project skill on the generic primitives. It may define canonical dimensions,
skeleton and morphology contracts, topology/clothing/export rules, checkpoints, and acceptance
tests. None of those semantics belong in this repository's core.

## Release Gates

Every security slice requires focused negative tests, the full unit suite, a diff review against
`SECURITY.md`, matching documentation, and a coherent commit. Releases additionally require pinned
dependencies, a reproducible artifact manifest, source revision, checksum, and successful live
Blender validation. Runtime self-update remains prohibited.
