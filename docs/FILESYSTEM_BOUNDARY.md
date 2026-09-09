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
- cloud-render current-file saves.

This slice authorizes only exporter outputs whose complete output family is known. glTF writes are
single-file GLB only. Caller-supplied exporter settings cannot select paths or output families, FBX
texture path mode is fixed to `STRIP`, and USD texture export/overwrite is forced off. Cloud asset
packaging and SheepIt preparation are quarantined because Blender's pack operation can read every
linked external asset; they remain disabled until each input can be enumerated and granted read
authority before mutation.

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

Rendering outputs, viewport captures, sequencer media, imported assets, caches, temporary provider
artifacts, subprocess paths, and other exporter sidecars still need a repository-wide capability
and sink audit. Multi-file glTF and USD texture sidecars remain disabled rather than implicitly
sharing authority with a primary output. Some legacy callers already reach the central helper and
therefore fail closed, but their action metadata and sidecar behavior are not yet fully classified.
Windows unit coverage exercises traversal,
sibling-prefix, ambiguous syntax, overwrite, and final-extension behavior. Blender 5.2.1 live
validation passes outside-root open/save/export denial, sentinel preservation, approved overwrite,
approved GLB export, approved `.blend` open, and a Windows junction escape. Supported POSIX and
broader live operator coverage remain required.

Until those gates pass, use disposable `.blend` copies and treat Foundation 0E as partial.
