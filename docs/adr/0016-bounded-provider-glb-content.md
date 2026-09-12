# ADR 0016: Bounded provider GLB content admission

- Status: Accepted; shared helper and Blender 5.2.1 canary live-validated
- Date: 2026-09-12

## Context

The network and archive boundaries can prove where provider bytes came from and where they were
stored, but a valid download or ZIP does not make its contents safe for Blender's native importer.
A binary glTF file may select external resources, advertise extensions with additional decoding
work, contain inconsistent buffers and accessors, embed decompression-heavy images, or create an
unbounded Blender data graph. File extension and HTTP media type checks do not establish those
properties.

The [Khronos glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html)
defines the GLB header, JSON-first chunk order, optional binary chunk, four-byte alignment, version
checks, and the possibility of external resources and extensions. The security boundary must be
stricter than a general-purpose conforming reader because provider content is untrusted.

## Decision

Add a Blender-independent provider-content boundary that accepts exactly one `.glb` file already
owned by a live `ManagedArtifactDirectory`. It rejects unlisted or additional workspace files,
links, junctions, hard links, containment failures, invalid headers/chunks/versions, malformed or
over-complex JSON, external buffer/image URIs, all glTF extensions, sparse accessors, invalid
buffer-view/accessor ranges, and unsupported embedded image formats.

Explicit ceilings cover artifact and JSON bytes, JSON depth/nodes/string length, scene nodes,
meshes, primitives, buffers, buffer views, accessors and represented elements, materials, textures,
images and decoded pixels, animations/channels, skins, scenes, and cameras. PNG and JPEG dimensions
are inspected before native decode. A successful plan contains only bounded counts, the canonical
workspace path, and a SHA-256 digest. The complete inspection repeats immediately before a future
native import; any changed byte or structural result fails closed.

A Blender-independent post-import checker compares typed before/after count snapshots and rejects
empty, decreasing, or over-budget object, mesh, material, image, armature, vertex, and polygon
deltas. A future provider commit callback must clean every newly created Blender datablock when
native import fails or the delta is rejected; this helper does not claim rollback or interruption.

## Consequences

The reusable content path can now distinguish a bounded, self-contained GLB from an arbitrary
download and can bind worker preparation to the exact bytes later presented to Blender. Blender
5.2.1 imports a minimal inspected triangle GLB in a disposable factory session, passes the expected
result delta, and rejects a changed artifact on revalidation.

All providers remain quarantined. Provider-specific endpoint/response contracts, trusted artifact
root startup policy, credential binding, controlled network fixtures, job wiring, native-import
deadline behavior, deterministic rollback of partial/oversized imports, cancellation, and restart
reconciliation remain required before re-enablement.
