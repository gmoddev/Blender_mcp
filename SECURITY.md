# Security Policy

## System and Scope

This repository implements a local MCP bridge between an AI client and a live
Blender process. Security review covers the stdio bridge, TCP protocol, Blender
add-on, dispatcher, registered handlers, command queue, external integrations,
release tooling, and dependencies.

The Blender process, local files, credentials, scene data, and valuable `.blend`
assets are protected resources. The MCP client and imported or downloaded
content must not be assumed trustworthy.

## Threat Model and Trust Boundaries

Attacker-controlled inputs may include MCP requests, raw-code bodies, JSON
fields, lengths, paths, URLs, downloaded assets, archive contents, scene names,
Blender metadata, and responses from external services.

Trust boundaries exist between:

- the MCP client and `stdio_bridge.py`;
- the bridge and the loopback TCP server;
- network threads and Blender's main thread;
- structured handlers and arbitrary Python execution;
- Blender and local files, subprocesses, credentials, or remote services;
- repository source and downloaded or installed release artifacts.

Loopback binding reduces exposure but is not authentication. Safe Mode is an
authorization boundary. Raw Blender Python is arbitrary code execution and is
not considered sandboxed.

## Security Invariants

- A timed-out pending mutation must never execute later.
- A timeout during execution must report an indeterminate or running state,
  never imply that the mutation failed or did not happen.
- Command states must be atomic, monotonic, observable, and correlated using
  request IDs.
- Blender API operations execute only on Blender's main thread.
- Mutations serialize unless a reviewed capability explicitly permits
  concurrency.
- Protocol frames are authenticated, bounded, correctly framed, and correlated
  before dispatch.
- Safe Mode denies raw code and destructive mutations by default.
- Unknown tools, actions, protocol versions, and authentication states fail
  closed.
- Logs never contain tokens, credentials, code bodies, prompts, arbitrary
  parameters, or imported asset contents.
- Runtime code never silently replaces itself from a remote source.
- Telemetry is absent unless separately introduced through an explicit,
  reviewed, opt-in decision.
- Indeterminate mutations can be queried and reconciled before retry.
- Boundary errors remain structured and must not open modal or focus-stealing
  UI.
- File reads and writes require a canonical path beneath the matching explicit,
  user-approved root; overwrite is a separate local decision.

The engineering verification registry is maintained in
`docs/SECURITY_INVARIANTS.md`.

## Reportable Findings and Severity Context

A finding is reportable when a reachable path violates an invariant and can
cause unauthorized code execution, unauthorized Blender mutation, loss or
corruption of assets, credential or sensitive-data disclosure, protocol
confusion, unsafe stale-command execution, traversal outside an authorized
path, or unapproved executable supply-chain changes.

Issues affecting mutation ordering, cancellation, authentication, raw-code
authorization, or valuable `.blend` recovery are high priority even when access
is limited to the local machine.

Reliability bugs without a security or asset-integrity consequence should be
reported separately as engineering defects.

## Out of Scope, Exclusions, and Accepted Risk

No vulnerability classes, runtime components, or trust boundaries are
currently excluded, and no security risks are accepted by this policy.

Blender and operating-system vulnerabilities outside code or configuration
controlled by this repository are upstream dependencies, but unsafe use of
those interfaces by this repository remains in scope.

## Known Limitations and Compensating Controls

Protocol v2 implements bounded framing, mutual local authentication, proof-bound bridge-instance
request namespaces, typed JSON-RPC identity, correlation, active-client limits, metadata-only
logging on the primary request path, and initial capability enforcement. Effective Safe Mode and
raw-code state is snapshotted before listener startup so socket-thread authorization does not touch
Blender APIs. The action-level capability audit is incomplete.

The shared dispatcher queue now has timeout/cancellation tombstones, duplicate detection, bounded
in-process reconciliation, and truthful running-after-timeout states. Blender 5.2.1 validates
same-bridge reconnect, response loss, cross-bridge status/cancel denial, and shutdown behavior.
Process-restart durability and direct provider timer callbacks remain outside the ledger. The
scene/export family now has a partial filesystem authority boundary. Single-file GLB conditionally
authorizes every unpacked ordinary `FILE` image beneath the read root; complex image families fail
closed regardless of packed state. Multi-file glTF, OBJ material sidecars, USD texture/world
sidecars, and external asset packing are disabled pending complete input/output-family grants.
Single viewport captures are write-root confined; multi-output capture
and primary background rendering are disabled pending process, temporary-artifact, cleanup, and
complete output-family policy. Other render/capture paths, remaining imports/providers, sequencer
preview outputs, other sidecars, atomic publication, and live cross-platform validation remain
open. HDRI setup is read-root confined to `.hdr`/`.exr`, and legacy headless render entry points are
disabled until compositor and scene-derived outputs can be authorized as a complete family.
Single-file motion-capture input is limited to authorized `.bvh`; FBX animation import is disabled
until its linked inputs can be preauthorized. Physics bake, simulation-play, and cache-clear routes
are disabled and custom physics cache paths are rejected until their scene-derived file families
can be enumerated, authorized, bounded, and reconciled. Texture bakes remain available internally,
but caller-selected external output paths are rejected before scene access. Hunyuan, Hyper3D,
Sketchfab, and Poly Haven external actions are quarantined; only truthful read-only status remains.
Windows x64 credential storage is OS-backed and self-contained in the extension, while secure
provider credential entry and non-Windows backend validation remain open. Shared purpose-scoped
HTTPS/DNS/redirect/peer enforcement, bounded downloads, ZIP inspection, bounded extraction, and
in-process artifact cleanup are implemented but not connected to providers; controlled live-network
fixtures, startup cleanup reconciliation, provider jobs, selector complexity,
checkpoint recovery, and the remaining logging surface remain open.
These are scan targets, not accepted risks.

Until Foundation 0 is complete and live-validated, agent-driven mutations
should run only against disposable copies of valuable `.blend` files. Loopback
binding remains defense-in-depth and must not be treated as proof of peer
identity; the protocol handshake provides the current peer-authentication
boundary.
