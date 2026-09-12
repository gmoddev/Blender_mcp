# ADR 0012: Bounded Archive and Temporary Artifact Boundary

- Status: Accepted; shared helper live-validated, provider integration pending
- Date: 2026-09-11

## Context

The quarantined external providers formerly expanded downloaded ZIP files into unmanaged process
temporary directories. Archive names, declared sizes, compression ratios, member types, collisions,
and cleanup were not bounded. A primary archive URL or filename is not authority for every path and
resource effect encoded by its contents.

[Python's ZIP documentation](https://docs.python.org/3/library/zipfile.html) warns that untrusted
archives require prior inspection and that decompression bombs can exhaust storage. Its convenience
extraction API also normalizes platform-specific names, which is unsuitable as the repository's
security policy.

## Decision

Add a Blender-independent archive boundary with two explicit phases:

1. Inspect the complete central-directory member set before creating an output workspace. Reject
   empty archives, links and special files, encryption, unsupported compression, unsafe or
   ambiguous paths, Unicode/case collisions, file/directory collisions, and any compressed,
   expanded, member, ratio, count, path-length, or path-depth budget violation.
2. Extract by bounded streaming into a new opaque child of an existing, pre-authorized local
   artifact root. Never call `extract()` or `extractall()`, preserve archive permissions, replace an
   existing output, or derive a workspace name from caller input. Recheck actual streamed byte
   counts and the workspace boundary while writing.

Every workspace retains the initiating request identity in memory and exposes explicit cleanup plus
context-manager cleanup. Inspection failure creates no workspace; extraction failure removes all
partial output. Public failures use structured, fixed messages that do not expose archive names or
contents.

## Consequences

This establishes the archive and temporary-artifact portion of Foundation 0F/0I, but it does not
enable a provider. The artifact root still needs a user-scoped startup policy, and cleanup cannot
survive process termination without a startup reconciliation contract. The purpose-scoped HTTP
client, DNS/redirect/peer checks, bounded download streaming, provider format selection, content
validation, and provider job lifecycle remain mandatory before any external action is restored.

The helper is an application boundary rather than an operating-system sandbox. Another process
with the same user authority could race filesystem entries, so providers must use private trusted
artifact roots and keep extraction off Blender's main thread.

Blender 5.2.1 embedded-Python validation passes bounded extraction, request-workspace cleanup, and
traversal denial without an outside file or residual artifact.
