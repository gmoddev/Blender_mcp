# ADR 0009: Authenticated Request Namespaces

- Status: Accepted
- Date: 2026-09-11

## Context

The first lifecycle ledger keyed requests only by a normalized string. JSON-RPC numeric `1` and
string `"1"` collapsed to the same value, and independent authenticated bridge processes shared one
global request namespace. One client could therefore collide with, query, or cancel another
client's retained request when it knew or guessed the wire ID.

## Decision

Protocol v2 adds a bridge-instance ID to the strict authentication payload and both HMAC proof
transcripts. A bridge creates this identity once and retains it across its TCP reconnects. JSON-RPC
IDs receive a type-tagged canonical digest before crossing the local socket. At the authenticated
server boundary, Blender derives a bounded private ledger key from the bridge instance and wire ID.

Lifecycle target IDs are converted to that private key using the current authenticated session;
callers cannot select another namespace. Known request-ID fields are translated back to wire IDs
before the response leaves Blender, so private ledger identities do not become a public API.

## Consequences

Independent bridges can safely reuse ordinary JSON-RPC IDs, and only the originating bridge
instance can reconcile or cancel its retained work. Reconnect within the same bridge process keeps
working. Restarting the bridge creates a new namespace, and restarting Blender still loses the
memory-only ledger; neither unknown state authorizes retry. Protocol v1 peers are rejected rather
than accepted through a compatibility fallback.
