# Local Control Protocol

## Effective Version

Wire protocol version `1` is a deliberate break from the unauthenticated legacy payload. There is
no silent legacy fallback. A peer that does not speak the envelope receives a bounded protocol
error or a closed connection.

Each TCP message is a four-byte unsigned big-endian length followed by one UTF-8 JSON object.
Frames must be between 1 byte and 8 MiB. JSON is limited to 64 levels and 50,000 aggregate nodes.
The listener admits at most four active clients by default and applies separate authentication,
body, and idle deadlines.

## Envelope

```json
{
  "protocol_version": 1,
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

The bridge returns a client nonce and `HMAC-SHA256` proof over length-delimited fields:

```text
domain = BLENDER_MCP_AUTH_V1 / CLIENT
version, instance_id, auth_epoch, session_id, request_id, server_nonce, client_nonce
```

Blender compares the proof in constant time and returns a proof over the same transcript under the
separate `SERVER` domain. This authenticates both peers without transmitting the pre-shared token.
Captured proofs do not authenticate against another instance, epoch, session, nonce, or request.

The handshake authenticates the TCP connection. Version 1 does not MAC each post-handshake frame;
session and request IDs provide correlation, not independent message integrity. Loopback binding
remains mandatory defense-in-depth.

## Credential Configuration and Rotation

The Blender add-on reads its credential from user-scoped Addon Preferences, falling back to
`BLENDER_MCP_AUTH_TOKEN` only when the preference is empty; it never reads Scene data. The bridge
reads the environment variable unless explicitly supplied by an embedding application. Protocol v1
accepts only the canonical 43-character base64url encoding produced from 32 random bytes by the
add-on's generate/rotate action. Preference precedence ensures a stale environment value cannot
become authoritative again after UI rotation and restart. Rotation increments the auth epoch,
changes the instance identity, and closes active sockets.

This initial storage is outside `.blend` files but is not yet an OS credential manager. Foundation
0G must replace it with OS-backed storage and verify ACLs.

## Retry Contract

The bridge serializes each complete request/reply transaction. It may retry connection and
authentication before sending a command. Once a command-frame write begins, timeout, EOF, correlation
failure, or transport failure closes the socket and returns `REQUEST_INDETERMINATE`; the bridge does
not replay the command. Foundation 0D will add durable status reconciliation and result deduplication.
