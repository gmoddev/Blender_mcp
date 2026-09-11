# Blender MCP Hardening Roadmap

The roadmap protects the generic Blender control plane before adding authoring breadth. Gantria
rules, measurements, names, and acceptance criteria remain in a future project skill.

## Foundation 0 — Trustworthy Control Plane

| Slice | Scope | Acceptance gate | Status |
|---|---|---|---|
| 0A | Versioned envelope, stable request identity, response correlation, 8 MiB frames, JSON complexity limits, absolute header/body/idle deadlines, four-client cap | Boundary and negative protocol tests pass; legacy unversioned traffic fails clearly | Implemented statically; Blender-live validation and peak-memory profiling pending |
| 0B | Server-first HMAC challenge, mutual proof, session/instance/epoch binding, user-scoped credential, rotation and socket revocation | Discovery and dispatch are unreachable before auth; wrong, expired, replayed, and cross-instance proofs fail | Implemented statically; OS-storage and Blender-live validation pending |
| 0C | Action capability metadata, fail-closed policy, Safe Mode, Full Structured Mode, separately enabled raw Python | Every action explicitly audited; unknown classes deny; UI and effective policy agree | Scaffolded; raw-code routes gated; full action audit pending |
| 0D | Monotonic request state, cancellation tombstones, bounded result ledger, duplicate detection, reconciliation | Pending timeout never runs; running timeout is queryable/indeterminate; duplicates never re-execute | Implemented and Blender-live validated for the shared queue; restart durability and direct provider callbacks remain |
| 0E | Authorized read/write roots, canonical containment, overwrite policy, symlink/junction/reparse handling | All file sinks use the central boundary; traversal and link escapes fail | In progress: scene/export, sequencer, viewport, HDRI, BVH, physics cache, external texture-bake, and provider temporary-artifact paths contained; remaining sinks and cross-platform gates pending |
| 0F | Explicitly enabled integrations and bounded network/download/archive helpers | Local-file upload, SSRF, redirect, size, archive, cleanup, and consent tests pass | Containment complete: all provider external actions are quarantined; shared safe implementation remains planned |
| 0G | OS-backed secret lifecycle and legacy Scene-property migration | No provider secret appears in saved or temporary `.blend` files | Planned |
| 0H | Metadata-only logging and privacy validation | Canary secrets are absent from all logs and exception paths | In progress |
| 0I | Queue/job/content/selector bounds | Pathological selectors and resource floods fail without blocking Blender | Planned |
| 0J | Explicit scene checkpoints, retention, restore, cleanup | Destructive workflows can checkpoint and recover deterministically | Planned |
| 0K | Disposable-profile Blender harness and supported-version matrix | Static scan gaps are reproduced and verified live | Planned |

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
