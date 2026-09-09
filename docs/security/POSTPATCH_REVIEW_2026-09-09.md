# Foundation 0A/0B Post-Patch Security Review

Date: 2026-09-09
Implementation commit: `71e3258`

## Independent Review Result

A fresh read-only reviewer inspected the working-tree implementation after the focused security
tests. It verified authentication before payload dispatch, exact protocol-v1 handshake fields, the
pre-auth and normal frame limits, absolute receive deadlines, loopback enforcement, the active-client
cap, request/session correlation, removal of automatic bridge replay, fail-closed unknown actions,
and explicit `EXECUTE_CODE` classification on all located raw-Python routes.

The correction cycle then addressed these review residuals:

- Addon Preferences now precede the environment fallback, so UI rotation cannot silently revert to
  a stale environment token after restart.
- Protocol v1 accepts only canonical 43-character base64url tokens representing 32 random bytes and
  rejects simple low-diversity manual values.
- A socket write is considered indeterminate before the first write attempt, not only after
  `sendall()` returns.
- Persistent core JSON/console logging stores a code-owned event label and allowlisted metadata,
  rather than caller-controlled message bodies or arbitrary extras.
- Normal frames were tightened from 16 MiB/100,000 JSON nodes to 8 MiB/50,000 nodes. Parsing still
  materializes the bounded JSON value before the shape walk, so four-client peak-memory profiling
  remains a live release gate.

## Residual Risks

- The Blender main-thread queue can still execute a pending mutation after its waiter times out and
  can overwrite `TIMEOUT` with later states. Foundation 0D must introduce atomic tombstones,
  monotonic state, a bounded result ledger, and reconciliation before retry.
- Logging is metadata-only on the inspected core request path, but every provider, job, handler, and
  OS file ACL has not been verified. Finding 6 remains partial.
- A pre-shared HMAC protocol inherently exposes known-transcript verifiers. Generated high-entropy
  tokens are therefore mandatory; OS-backed credential storage remains Foundation 0G.
- JSON peak memory, Windows socket fault behavior, preference persistence/ACLs, UI behavior, and
  Blender main-thread timing require live or OS-specific validation.
- Protocol v1 authenticates the handshake but does not MAC every later frame. Session/request IDs
  are correlation controls, not independent post-authentication integrity.

## Verification Evidence

- Focused security boundary: 57 passed.
- Full unit suite: 535 passed.
- Full repository suite: 559 passed, 24 live-Blender tests skipped because no disposable live
  instance was configured.
- Project fast quality gate: 8/8 passed.
- Black and Ruff on touched files: passed.
- Mypy on the eight changed control-plane source files with imported baseline modules skipped:
  passed. Full-project mypy remains blocked by pre-existing Blender-stub/baseline errors and a mypy
  internal error.
- `pip-audit --local`: no known vulnerabilities found.
- `pip check`: no broken requirements found.

No original scan finding is promoted to `FIXED` by this review. The canonical dispositions and live
gates remain in [REMEDIATION_LEDGER.md](REMEDIATION_LEDGER.md).
