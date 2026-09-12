# Local Control Protocol

## Effective Version

Wire protocol version `2` adds an authenticated bridge-instance identity to the version 1 security
envelope. Both are deliberate breaks from earlier payloads, and only version 2 is accepted. There is
no silent legacy fallback. A peer that does not speak the envelope receives a bounded protocol
error or a closed connection.

Each TCP message is a four-byte unsigned big-endian length followed by one UTF-8 JSON object.
Frames must be between 1 byte and 8 MiB. JSON is limited to 64 levels and 50,000 aggregate nodes.
The listener admits at most four active clients by default and applies separate authentication,
body, and idle deadlines.

## Envelope

```json
{
  "protocol_version": 2,
  "message_type": "REQUEST",
  "request_id": "json-rpc-42",
  "session_id": "d5f91e8a-...",
  "payload": {}
}
```

Allowed message types are `CHALLENGE`, `AUTH`, `AUTH_OK`, `REQUEST`, `RESPONSE`, and `ERROR`.
Post-authentication responses must match both the request and session identities. Unknown versions,
message types, missing identities, wrong sessions, malformed JSON, truncation, zero-length frames,
and oversized frames fail closed.

## Authentication Transcript

Blender sends a short-lived challenge immediately after accept. It contains only protocol metadata,
a unique Blender instance ID, authentication epoch, connection session ID, request ID, and random
server nonce. It contains no tools or parameters.

The bridge returns a stable process-local bridge instance ID, a client nonce, and an `HMAC-SHA256`
proof over length-delimited fields:

```text
domain = BLENDER_MCP_AUTH_V2 / CLIENT
version, instance_id, auth_epoch, session_id, request_id, server_nonce,
bridge_instance_id, client_nonce
```

Blender compares the proof in constant time and returns a proof over the same transcript under the
separate `SERVER` domain. This authenticates both peers without transmitting the pre-shared token.
Captured proofs do not authenticate against another Blender instance, bridge instance, epoch,
session, nonce, or request.

The handshake authenticates the TCP connection and binds its bridge namespace. Version 2 does not
MAC each post-handshake frame;
session and request IDs provide correlation, not independent message integrity. Loopback binding
remains mandatory defense-in-depth.

JSON-RPC string and numeric IDs are normalized with distinct type tags before crossing the TCP
boundary. Blender derives a private bounded ledger key from the authenticated bridge instance and
that wire ID. The private key is translated back to the wire ID in response metadata. Lifecycle
status and cancellation targets are scoped at the server boundary, so another bridge using the
same JSON-RPC ID addresses a different ledger entry. A bridge keeps its instance ID across TCP
reconnects for in-process reconciliation; a new bridge process receives a new namespace.

## Credential Configuration and Rotation

The Blender add-on and stdio bridge read the same `control/auth-token` entry from the user OS
credential store and never consult Scene data. An explicit embedding argument takes precedence.
`BLENDER_MCP_AUTH_TOKEN` remains a process-scoped compatibility fallback only when no OS value can
be resolved. Protocol v2 accepts only the canonical 43-character base64url encoding produced from
32 random bytes by the add-on's generate/rotate action.

Rotation commits the new OS value before changing server state, then increments the auth epoch,
changes the instance identity, and closes active sockets. Null, failure, unrecognized, and
third-party plaintext keyring backends fail closed. Packaged-Blender validation and platform ACL
inspection remain before AUTH-003 can be marked complete.

## Retry Contract

The bridge serializes each complete request/reply transaction. It may retry connection and
authentication before sending a command. Once a command-frame write begins, timeout, EOF, correlation
failure, or transport failure closes the socket and returns `REQUEST_INDETERMINATE`; the bridge does
not replay the command. The bounded in-process Foundation 0D ledger supports reconciliation within
the same authenticated bridge namespace; durable restart recovery remains open.
