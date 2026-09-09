# ADR 0001: Foundation and Selective Porting Strategy

- Status: Accepted
- Date: 2026-09-08

## Decision

Use `glonorce/Blender_mcp` as the architectural base. Selectively reimplement or port reviewed
security ideas from `soozs1/Blender-MCP`, transport behavior from `ahujasid/blender-mcp`, and
character primitives from `6xvl/blender-mcp`.

The registered-handler architecture, schema metadata, main-thread queue, and length-prefixed protocol
remain the organizing foundation. No upstream is merged wholesale, and only one MCP implementation
is connected to Blender.

## Consequences

- Hardening the command lifecycle and trust boundary precedes new mutation features.
- Ports must fit registered handler modules and include provenance, license preservation, tests, and
  explicit read/mutate/execute-code classification.
- Product-specific character rules belong in a separate skill or policy layer, not the generic MCP.
- Upstream comparison remains useful, but behavior is accepted only after local review and testing.
