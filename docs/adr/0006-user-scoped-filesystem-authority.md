# ADR 0006: User-Scoped Filesystem Authority

- Status: Accepted
- Date: 2026-09-09

## Context

Scene and export actions accepted caller paths, normalized them, created directories, and passed
them to Blender file operators. The partial export blocklist used lexical prefix comparison and a
caller-controlled `force_export` flag could bypass it. Process working directories, user homes,
environment variables, and the directory of the current `.blend` are not valid substitutes for an
explicit local authorization decision.

The registered Blender operators take path strings rather than pre-opened handles. Complete
time-of-check/time-of-use resistance is therefore not available at this layer.

## Decision

Add one explicit local read root and one explicit local write root to user-scoped add-on
preferences. Snapshot and validate them on Blender's main thread before opening the MCP listener.
Empty roots deny, roots must be existing local absolute directories, and policy changes require a
server restart.

Authorize the final concrete path immediately before the file sink:

1. reject empty, device, UNC, drive-relative, alternate-stream, reserved, and ambiguous paths;
2. resolve the target and existing ancestors, then use component-aware containment beneath the
   canonical root;
3. require a regular file for reads and existing writes, and reject multiply-linked files;
4. enforce action-specific extensions after exporters derive their final suffix;
5. create parents only after authorization, then revalidate;
6. require a local overwrite preference for every existing output, plus request-level overwrite
   intent where that public action already exposes it.
7. reject caller-supplied path/output-family exporter settings; allow only single-file GLB, disable
   USD texture sidecars, fix FBX texture path mode to `STRIP`, and quarantine asset packing until all
   external inputs can receive explicit read grants.
8. authorize sequencer media before creating its editor and quarantine preview rendering until all
   frame-derived outputs can be enumerated and authorized.
9. confine single viewport captures to the write root, remove implicit system-temp/JSON sidecar
   outputs, and quarantine multi-output captures. Quarantine primary background rendering until
   process and temporary-artifact authority are independently defined.
10. authorize `.hdr` and `.exr` environment images before Blender loads the image or mutates the
    world, and quarantine legacy headless render helpers until compositor and scene-derived output
    families can be enumerated as a unit.
11. authorize single-file `.bvh` motion-capture inputs before Blender import, and quarantine FBX
    animation import until its content-selected linked inputs can be enumerated and authorized.
12. reject caller-selected physics cache paths and quarantine physics bake, simulation-play, and
    cache-clear actions until every scene-derived cache member and destructive operation can be
    authorized before mutation.

Grant `FILESYSTEM_READ` and `FILESYSTEM_WRITE` only when the corresponding validated root exists.
Safe Mode may combine `READ` with `FILESYSTEM_READ`, but it never gains `MUTATE` or filesystem
writes. Remove the effect of `force_export`; a request setting it to true fails closed.

The incremental migration covers the source-to-sink families listed in
`docs/FILESYSTEM_BOUNDARY.md`. Other file surfaces remain open roadmap work and must not be inferred
safe from this decision.

## Rejected Alternatives

- Lexical prefix checks and system-directory blocklists fail for sibling prefixes, links, path
  aliases, and new protected locations.
- Treating the current `.blend` directory, process directory, user home, or `MCP_SHARED_ROOT` as
  implicit authority lets untrusted state choose the trust boundary.
- A caller-supplied bypass or overwrite boolean is not local user consent.
- Creating missing authority roots obscures whether the user actually approved their location.

## Consequences

Filesystem actions are disabled until roots are configured, and existing outputs remain protected
until overwrite is enabled locally. Default Unity and batch outputs now resolve relative to the
write root instead of an environment-selected or current-Blender directory.

Canonical path checks materially reduce confused-deputy access but do not bind Blender's later I/O
to the checked file identity. Multi-file formats and packaging lose functionality until the policy
can authorize their entire input/output families. Blender 5.2.1 passes disposable save/open/export,
overwrite sentinel, and junction-escape validation. Atomic staging/publication, sidecar inventories,
mapped/network-backed volume detection, remaining handler migration, supported POSIX tests, and
broader live-Blender coverage remain release gates.
