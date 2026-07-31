# ADR 003: Evidence Grounding

## Context

Schema validation proves shape, not truth. A model can return a valid but incorrect numeric metric.

## Decision

Treat model evidence as signal references. Resolve each supported signal from the immutable telemetry snapshot, replace mismatched values, and reject missing, invented, duplicate, or wrong-target evidence.

## Alternatives Considered

- Trust schema-valid values: rejected because numeric hallucination remains possible.
- Compare with a tolerance and keep model values: rejected because the snapshot can be the sole authority.

## Consequences

Stored evidence becomes auditable and repeatable. Model output is less expressive by design.

## Security Implications

Prompt-injection text in signal fields cannot create new metrics or actions because signal names are allowlisted.
