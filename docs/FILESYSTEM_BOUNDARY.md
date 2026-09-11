# Filesystem Authority Boundary

Foundation 0E introduces a user-scoped filesystem policy for structured MCP actions. The policy is
loaded from Blender add-on preferences before the listener starts and remains immutable for that
server run. Changes take effect after a server restart.

## Local Authority

- `Filesystem Read Root` and `Filesystem Write Root` are separate, existing local directories.
- An empty root grants no authority. Roots are never inferred from the process working directory,
  user profile, open `.blend`, an environment variable, or caller input.
- UNC shares and Windows device-path syntax are rejected. A configured root must be absolute and
  must already exist; the add-on does not create authority roots.
- Relative action paths resolve below the applicable root. Absolute paths are accepted only when
  their canonical target remains below that root.
- The policy does not expand `~` or environment variables. A handler may expand Blender `//`
  syntax before authorization, but the resulting canonical path still has to be inside the
  configured root.
- Existing output files require the local `Allow MCP File Overwrite` preference. Export-pipeline
  actions that expose an `overwrite` argument require both the local preference and request intent.

## Enforcement

`core/filesystem_boundary.py` resolves links before component-aware containment, rejects ambiguous
Windows names, alternate streams, drive-relative paths, directories, and multiply-linked files,
and returns a structured decision containing only an opaque root ID and root-relative path.
Directory creation happens only after authorization and is followed by revalidation.

The first migrated source-to-sink family covers:

- scene `.blend` open, explicit save, and save-to-current-file;
- standard, pipeline, batch-variant, and Unity export routes;
- UV layout export;
- sequencer movie, sound, and still-image reads;
- `.hdr` and `.exr` environment-image reads;
- single-file `.bvh` motion-capture reads;
- single viewport screenshot outputs;
- cloud-render current-file saves.

This slice authorizes only exporter outputs whose complete output family is known. glTF writes are
single-file GLB only. Caller-supplied exporter settings cannot select paths or output families, FBX
texture path mode is fixed to `STRIP`, and USD texture export/overwrite is forced off. Cloud asset
packaging and SheepIt preparation are quarantined because Blender's pack operation can read every
linked external asset; they remain disabled until each input can be enumerated and granted read
authority before mutation. Sequencer preview rendering is likewise quarantined because animation
rendering derives a multi-file output family from mutable scene settings. Single viewport captures
default to a unique `captures/` path below the write root, are reauthorized at the final OpenGL
sink, and return base64 inline without a JSON sidecar. Multi-angle and multi-view captures are
disabled until all derived output paths can be preauthorized.

Primary frame/animation render actions and their aliases declare both `FILESYSTEM_WRITE` and
`PROCESS` but fail closed at the registered and direct helpers. Their former flow copied the whole
scene to an unmanaged temporary `.blend` and launched a subprocess, so filesystem write approval
alone cannot authorize it. Re-enablement requires explicit process policy, a controlled temporary
artifact root, credential scrubbing, cleanup, and complete frame-family authorization.

The separate headless-render action and the version-specific direct helper also fail closed with
`OUTPUT_FAMILY_DISABLED`. Although they accepted one output path, a render can write additional
files through compositor File Output nodes and other mutable scene configuration. Those entry
points remain unavailable until the entire output family can be enumerated and authorized before
any scene mutation.

Motion-capture BVH import accepts one authorized `.bvh` file. FBX animation import remains disabled
because an approved primary file can name textures or other linked inputs outside the read root;
those dependencies must be enumerated and authorized before Blender receives the file.

Physics bake, simulation-play, and cache-clear actions are disabled because Blender derives cache
directories and multiple cache members from mutable scene state. Their direct helpers contain no
bake, playback, or deletion sinks. Physics setup rejects caller-selected `cache_path` values before
scene access; setup without that parameter may configure simulation state but cannot invoke the
quarantined cache-producing routes.

Texture baking remains available for Blender-internal image datablocks. Every bake action capable
of accepting `output_path` declares `FILESYSTEM_WRITE`, and caller-selected external output paths
fail with `BAKE_OUTPUT_PATH_DISABLED` before scene access. The registered handler, each direct bake
helper, and the low-level operator wrapper enforce the same rule.

The dispatcher requires `FILESYSTEM_READ` or `FILESYSTEM_WRITE` for those migrated actions. A
configured root grants only the matching dedicated capability; Safe Mode still permits no mutation.
The deprecated `force_export` input is denied and cannot skip resource or path policy.

## Known Limits

This is a path-authority boundary, not an operating-system sandbox. Blender exporters accept path
strings, so another local process could race a checked path after final revalidation. Handle-bound
I/O, controlled staging, and atomic publication are required before claiming TOCTOU-resistant
overwrite protection.

Mapped-drive and other network-backed local-looking roots are not yet identified reliably. Select
roots on a trusted local volume until volume-origin enforcement is implemented.

Other imported assets, temporary provider artifacts, subprocess paths, and
other exporter sidecars still need a repository-wide capability
and sink audit. Multi-file glTF and USD texture sidecars remain disabled rather than implicitly
sharing authority with a primary output. Some legacy callers already reach the central helper and
therefore fail closed, but their action metadata and sidecar behavior are not yet fully classified.
Windows unit coverage exercises traversal,
sibling-prefix, ambiguous syntax, overwrite, and final-extension behavior. Blender 5.2.1 live
validation passes outside-root open/save/export denial, sentinel preservation, approved overwrite,
approved GLB export, approved `.blend` open, a Windows junction escape, outside-root HDRI/BVH denial,
approved `.hdr` and BVH loading, and headless/FBX/physics-cache/texture-bake quarantine without
filesystem side effects.
Supported POSIX and broader live operator coverage remain required.

Until those gates pass, use disposable `.blend` copies and treat Foundation 0E as partial.
