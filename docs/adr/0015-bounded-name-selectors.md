# ADR 0015: Bounded regex-free name selectors

- Status: Accepted; Blender 5.2.1 live-validated
- Date: 2026-09-12

## Context

The batch handler accepted caller-controlled Python regular expressions and evaluated them over
Blender object names on the main thread. The same behavior appeared in generic batch targeting,
`SELECT_BY_NAME`, advanced pipeline select steps, and advanced object filters. A short expression
with catastrophic backtracking could therefore monopolize Blender's UI thread and put unsaved work
at risk.

Moving Python regex evaluation to a worker would not make later Blender object access safe, and a
timeout cannot interrupt an already-running Python regex reliably. Pattern-length limits alone also
do not exclude high-complexity expressions.

## Decision

Remove regular-expression execution from all four caller-controlled selector paths. Legacy
`pattern` and `name_pattern` fields fail with `REGEX_SELECTOR_DISABLED` before scene inspection or
mutation. The replacement is one Blender-independent name-selector boundary with four explicit
modes: case-sensitive `EXACT`, `PREFIX`, `SUFFIX`, and `GLOB`.

The glob grammar supports only `*` and `?`; brackets, grouping, alternation, quantifiers, and other
regex syntax are ordinary literal characters. Matching uses a bounded iterative algorithm rather
than Python's regex engine. Selector value length, wildcard count, candidate count, candidate-name
length, selector count, request-wide evaluations, and per-candidate glob steps all have fixed
ceilings. Invalid or over-budget selectors return fixed structured errors without echoing the
caller value or candidate name.

Advanced pipelines preflight every select step and precompute its bounded matches before executing
any prior mutation step. Direct advanced filters use the same parser and matcher. Public schemas
advertise the new selector object and no longer advertise regex fields.

## Consequences

Existing clients using regex selectors must migrate to `{ "mode": "GLOB", "value": "..." }` or
one of the literal modes. Regex-only features such as character classes are intentionally absent.
Explicit object-name arrays and the current Blender selection remain supported, with a fixed target
count ceiling.

Pure-Python tests cover grammar, modes, limits, legacy denial, preflight ordering, and the absence
of regex execution in both handlers. A Blender 5.2.1 factory-session harness creates 512 disposable
objects, proves a previously dangerous regex-shaped input fails without mutation, and measures the
bounded glob path under a one-second acceptance budget.
