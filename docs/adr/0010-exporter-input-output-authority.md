# ADR 0010: Exporter Input and Output Authority

- Status: Accepted
- Date: 2026-09-11

## Context

Authorizing one destination filename did not bound everything Blender's exporters could touch.
Single-file GLB could still read image paths stored in the scene, OBJ could derive an `.mtl` sibling,
and Blender 5.2 replaced USD's boolean texture switch with a mode enum. Its default world-material
conversion also emitted an EXR beneath a derived `textures` directory.

## Decision

Before any GLB output directory or scene selection mutation, enumerate every unpacked file-backed
image datablock and require its canonical path beneath the user-approved read root. This deliberately
over-approximates selected-object reachability because exporter traversal changes between Blender
versions. Permit packed ordinary `FILE` images and generated images without an external grant.
Reject tiled, sequence, and movie image sources regardless of packed state until their full input
families can be enumerated.

Disable OBJ material export on every known route, preserving a single-file output contract. Resolve
USD texture policy from the runtime operator schema: use `export_textures_mode="KEEP"` when present,
fall back to the older disabled boolean only when supported, always disable texture overwrite, and
disable world-material conversion. Reject caller overrides of those controls.

## Consequences

GLB actions require write-root authority, then conditionally require per-path read-root authority
for every external `FILE` image they discover. Packed ordinary and generated images therefore do not
require an unused read root, and mixed-format batch actions do not require read authority when GLB
is absent. A scene containing any unauthorized external file image cannot export GLB, even when that
image may be unreachable from the selected objects. OBJ exports omit material libraries. USD
retains original texture references but does not copy textures or synthesize the world EXR sidecar.
Extension-defined exporter inputs,
atomic output publication, complex image families, and complete exporter reachability remain open.
