# Filesystem Boundary

## Required Policy

Filesystem access is authority, not string cleanup. Foundation 0E will define separate
user-approved read and write roots. Every file sink must canonicalize first and then authorize the
resolved target against those roots.

Containment must use path-component semantics and account for `..`, alternate separators,
case-insensitive Windows paths, sibling-prefix tricks, symlinks, junctions/reparse points, UNC
paths, nonexistent output parents, and overwrite intent. Caller-controlled `force`, `bypass`, or
`disable_security` flags may not expand authority.

## Planned API

A central policy object will return a structured decision containing the resolved path, approved
root identifier, access mode, overwrite decision, and error code. Handlers will not independently
normalize or authorize paths. Project responses and normal logs should use opaque root-relative
paths where practical.

## Migration Gate

Before enforcement is declared complete, trace scene open/save, exports, renders, captures,
packages, local integration uploads, archives, temporary files, scripts, and subprocess inputs.
Tests must cover traversal, sibling-prefix escape, symlink escape, Windows junction/reparse escape,
unauthorized overwrite, race-relevant parent changes, and removal of every remote bypass flag.

The current implementation does not satisfy this boundary. Use disposable workspaces only.
