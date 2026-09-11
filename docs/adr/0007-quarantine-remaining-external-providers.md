# ADR 0007: Quarantine Remaining External Providers

- Status: Accepted
- Date: 2026-09-11

## Context

Poly Haven and Sketchfab performed unbounded HTTP downloads into unmanaged temporary directories,
left artifacts behind, and passed downloaded content directly to Blender. Hyper3D accepted local
image paths, read credentials from Scene properties, and made provider requests on Blender's main
thread. These handlers lacked dedicated capability metadata, URL and redirect policy, deadlines,
response budgets, content validation, deterministic cleanup, and provider-job reconciliation.

## Decision

Quarantine every non-status action in Hyper3D, Sketchfab, and Poly Haven, matching Hunyuan:

- Declare the complete intended capability set for every action. Current modes do not grant network
  or credential access, so dispatcher authorization fails closed.
- Return `EXTERNAL_CAPABILITY_DISABLED` from direct handler calls before dependency checks or I/O.
- Remove and hot-reload-purge retired network, local-file, download, temporary-file, and import
  helpers so stale module dictionaries do not preserve callable sinks.
- Keep `STATUS` as `READ`, report configured state separately from operational availability, and do
  not inspect Scene-stored credentials.
- Mark denied external mutations and polling as not retry-safe until provider jobs retain the
  initiating request identity and expose terminal reconciliation.

## Re-enable Requirements

Re-enablement requires purpose-scoped endpoint and redirect policy, DNS/peer validation, bounded
responses and archives, authorized local-file grants, OS-backed credentials, deterministic
temporary-artifact ownership and cleanup, off-main-thread preparation, bounded main-thread commit,
and supported provider fixtures proving the contract.

## Consequences

External search, generation, polling, download-URL, and import behavior is intentionally unavailable.
Saved UI settings may remain for migration, but they do not make a provider operational. Deployment
requires a Blender restart because module reload cannot revoke already captured function references.
