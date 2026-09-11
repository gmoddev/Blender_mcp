# ADR 0003: Authenticated Local Protocol

- Status: Accepted; request identity extended by ADR 0009
- Date: 2026-09-09

## Context

Loopback accepts every process under the local networking boundary. The legacy wire payload has no
version, authenticated identity, stable correlation, limits, or safe replay semantics. A reference
fork adds a token but exposes it through pairing and Scene/temp-file paths; that conflicts with the
secret invariant.

## Decision

Use a server-first, short-lived HMAC-SHA256 challenge with distinct client/server proof domains.
Bind proofs to protocol version, Blender instance, auth epoch, connection session, handshake request,
and both nonces. Configure the pre-shared credential in user-scoped Addon Preferences or the bridge
environment; never Scene or a known temp file. Rotation changes epoch/instance and closes sessions.

The original decision used version-one length-prefixed envelopes with request/session correlation, an 8
MiB maximum, JSON complexity bounds, deadlines, and a four-client default cap. Do not accept silent
legacy fallback. After a command may have been sent, transport ambiguity closes the connection and
does not replay.

## Consequences

Bridge and add-on must upgrade together. The handshake authenticates a connection but does not MAC
every subsequent frame; documentation must not claim per-frame cryptographic integrity. ADR 0009
extends the handshake and request identity in protocol v2. OS-backed credential storage and durable
mutation reconciliation remain later foundations.

The implementation is original to this fork. The ahujasid and soozs1 repositories informed the
review of locking, framing, and auth failure modes, but their code and unsafe pairing behavior were
not copied.
