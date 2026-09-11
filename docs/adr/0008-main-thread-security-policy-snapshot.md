# ADR 0008: Main-Thread Security Policy Snapshot

- Status: Accepted
- Date: 2026-09-11

## Context

The dispatcher authorizes an action on the authenticated socket client thread before queueing its
handler for Blender's main thread. The original authorization implementation read Safe Mode and raw
Python preferences through `bpy.context` at that point. Authorization therefore violated the same
main-thread boundary it was intended to protect.

Filesystem roots were already converted into ordinary immutable control-plane state before the
listener started, but Safe Mode and raw-code enablement were still read from Blender dynamically.

## Decision

Capture Safe Mode and raw-code enablement on Blender's main thread during server startup. Store the
effective values in a frozen, generation-labelled policy object replaced atomically under a lock.
The security module does not import or retain `bpy`; socket and worker threads read only this
ordinary Python snapshot and the existing thread-safe filesystem policy.

Server startup rejects non-main-thread calls before reading any Blender-backed preference. Invalid
or missing preference types resolve to Safe Mode enabled and raw code disabled. Raw code is always
disabled when Safe Mode is enabled. Authorization and filesystem preference changes take effect
only after a server restart.

## Consequences

Capability denial remains synchronous before schema parsing and queue admission without crossing
the Blender API boundary. A future live-update feature must use a Blender main-thread property
callback that atomically publishes a complete new snapshot; worker threads must never refresh the
policy themselves.

The repository-wide THREAD-001 audit remains open for other utilities and background paths. This
decision closes the known dispatcher authorization violation only.
