# ADR 0004: Capability Authorization and Raw Python

- Status: Accepted
- Date: 2026-09-09

## Context

Safe Mode is displayed as active while `validate_action()` unconditionally authorizes all actions.
Raw Python and structured mutations therefore share the same authority, and names are not reliable
evidence that an action is read-only.

## Decision

Attach capability requirements to each registered action. Unknown or absent metadata denies.
Safe Mode allows only explicit `READ`; Full Structured Mode adds `MUTATE`; Raw Code Mode separately
adds `EXECUTE_CODE` only when Safe Mode is off. Dedicated filesystem, network, process, and
credential capabilities remain denied unless a later dedicated policy explicitly grants them.
ADR 0006 defines the first conditional filesystem grants.

During migration, every unaudited legacy action is conservatively classified `MUTATE`, so Safe Mode
blocks it. Core discovery/status/validation and every raw execution route have explicit initial
classifications. Completion requires a deliberate audit of every action; no name inference is used.

## Consequences

Safe Mode initially exposes fewer operations than users may expect, which is preferable to a false
security claim. Raw Python remains unrestricted code under the Blender user's authority and is never
described as sandboxed. UI state is only configuration; dispatcher tests prove effective policy.
