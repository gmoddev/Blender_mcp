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

Protocol v1 now implements bounded framing, mutual local authentication,
correlation, active-client limits, metadata-only logging on the primary request
path, and initial capability enforcement. These controls still require live
Blender validation. The action-level capability audit is incomplete.

Known, unremediated gaps remain in timeout cancellation/idempotency,
filesystem authorization, external integration isolation, provider credential
storage, download/archive limits, selector complexity, checkpoint recovery, and
the remaining logging surface. These are scan targets, not accepted risks.

Until Foundation 0 is complete and live-validated, agent-driven mutations
should run only against disposable copies of valuable `.blend` files. Loopback
binding remains defense-in-depth and must not be treated as proof of peer
identity; the protocol handshake provides the current peer-authentication
boundary.
