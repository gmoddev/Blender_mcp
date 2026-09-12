# ADR 0013: Purpose-scoped network and download boundary

- Status: Accepted; shared helper live-validated, provider integration pending
- Date: 2026-09-11

## Context

The quarantined provider implementations previously mixed arbitrary or provider-returned URLs,
ambient proxy behavior, HTTP redirects, whole-response buffering, temporary files, and Blender
imports. A hostname allowlist alone does not prevent SSRF: DNS may return local addresses, a
redirect may change authority, and a connected peer may differ from the approved resolution.
Timeouts and `Content-Length` also cannot be trusted as complete response budgets.

Python's [`http.client`](https://docs.python.org/3/library/http.client.html) supports explicit
request targets and streamed reads. Its HTTPS layer accepts an `SSLContext`. Python's
[`ssl`](https://docs.python.org/3/library/ssl.html) documentation establishes that
`PROTOCOL_TLS_CLIENT` enables certificate and hostname verification; it also documents that
`create_default_context()` may enable key logging from `SSLKEYLOGFILE`. The boundary must not
silently inherit that credential-exposing behavior. Python's
[`socket`](https://docs.python.org/3/library/socket.html) documents `getaddrinfo()` for IPv4/IPv6
resolution and per-operation timeouts, but those primitives do not provide a purpose policy or a
total request deadline.

## Decision

Add a Blender-independent network boundary with an immutable `HttpPurposePolicy`. Each purpose
names exact canonical DNS hosts, component-aware path prefixes, allowed ports, query behavior,
accepted media types, redirect count, DNS/address/header/body/chunk limits, and DNS/connect/read/
total deadlines.

The client:

- accepts HTTPS only and rejects userinfo, fragments, literal addresses, ambiguous paths, and
  destinations outside the purpose policy before DNS;
- bounds outstanding resolver work, requires every answer to be a public unicast address, pins the
  connection to those numeric answers, and verifies the connected peer before and after TLS;
- uses a verified `PROTOCOL_TLS_CLIENT` context, TLS 1.2 minimum, HTTP/1.1 ALPN, system trust, and no
  environment-enabled TLS key log;
- follows redirects manually and repeats URL authorization, DNS validation, and peer binding for
  every hop;
- accepts only `GET`, fixed non-secret headers, status 200, allowed content types, identity content
  encoding, and unambiguous body framing;
- streams into an exclusively created file under an opaque `ArtifactStore` request workspace,
  enforces declared and actual byte ceilings plus exact declared length, and removes partial output
  on every boundary or transport failure.

No provider may supply arbitrary request headers, credentials, destination policies, artifact
roots, or caller-selected secondary URLs. The provider layer must define those trusted contracts.

## Consequences

The helper closes the reusable HTTP/download portion of Foundation 0F/0I and is deterministic in
unit tests and Blender 5.2.1 embedded Python. It intentionally does not enable any provider. HTTP
response parsing still relies on the standard library's own coarse line/count bounds before the
stricter accepted-header budget is evaluated.

Provider-specific endpoint evidence, credential binding, asset identity, content/archive format
selection, hashing, cancellation, startup cleanup/reconciliation, off-main-thread job execution,
and bounded main-thread import remain mandatory. Network integration tests against controlled DNS,
redirect, TLS, slow-response, and rebinding fixtures are also required before re-enablement.
