# ADR 0005: Quarantine Hunyuan External Actions

- Status: Accepted
- Date: 2026-09-09

## Context

The Hunyuan handler accepted caller-selected local paths and download URLs. It could read an entire
local file into a Tencent request, fetch loopback or private services, write and expand an
unbounded ZIP, and schedule a detached Blender import. Network and archive work ran on Blender's
main thread without total deadlines or resource budgets. Import results and cleanup failures were
not reconciled with the initiating MCP request.

The repository does not yet have an authorized-file abstraction, purpose-scoped HTTP client, safe
archive extractor, OS-backed provider credential store, or provider job contract. The supported
Tencent asset hosts, redirects, archive layout, and defensible resource budgets are also not
documented locally. Preserving partial behavior would therefore require guessing at a security
contract.

## Decision

Quarantine Hunyuan `GENERATE`, `CHECK_JOB`, and `IMPORT`.

- Declare the real dedicated capabilities for every action. Current authorization modes do not
  grant network, credential, or filesystem capabilities, so the dispatcher denies external actions
  before schema parsing or handler invocation.
- Independently deny every external action inside the registered handler with the structured code
  `EXTERNAL_CAPABILITY_DISABLED`. This protects direct internal callers and future dispatcher
  mistakes.
- Remove the existing request, signing, local-file upload, temporary download, archive extraction,
  detached timer, and native import helpers so dormant callable sinks do not remain. Explicitly
  purge their names from the reused Python module dictionary during add-on reload.
- Preserve `STATUS` as an explicit `READ`. Report `configured_enabled` and `operational` separately;
  the compatibility field `enabled` continues to mean saved configuration, never readiness.
- Mark a denied external mutation as not retry-safe. A later implementation must use a stable
  provider job and original MCP request identity before it can make truthful retry claims.

## Re-enable Requirements

Re-enabling any action requires all relevant controls to exist and be negative-tested:

1. User-scoped provider enablement and OS-backed credentials that are absent from Scene data and
   temporary `.blend` files.
2. An authorized regular-file handle or opaque grant bound to request ID, destination, identity,
   type, size, and explicit user consent; validation and reading must use the same handle.
3. A purpose-scoped HTTP client with exact scheme/host/port policy, all-address DNS checks,
   connected-peer verification, redirect revalidation, explicit proxy policy, connect/read/total
   deadlines, and cumulative response budgets.
4. Provider-returned asset identity instead of caller-selected archive URLs wherever possible.
5. A safe archive pipeline with path/collision/link/type checks, compressed and expanded budgets,
   member/count/ratio/disk limits, explicit OBJ/MTL/texture selection, and deterministic cleanup.
6. Off-main-thread preparation followed by a bounded main-thread Blender commit that keeps the
   original request/job identity, checks the native importer result, and exposes observable
   terminal and reconciliation state.
7. Provider documentation or captured fixtures proving endpoints, redirect behavior, response
   format, archive layout, and chosen limits.

## Consequences

Hunyuan generation, polling, and import are intentionally unavailable in every security mode. The
configuration UI may still exist, but `STATUS` explicitly reports that the integration is not
operational. This is a visible compatibility reduction and a temporary containment state, not
completion of Foundation 0E-0G. Restoring the removed behavior from history without meeting this
ADR is a security regression.

Deploying this containment requires a Blender restart. Purging legacy module names prevents a
normal `importlib.reload()` from retaining those entry points, but Python cannot revoke a callback
or function reference captured before the new module code ran.
