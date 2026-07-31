# ADR 001: LLM Advisory Boundary

## Context

Provider output can vary, fail, or contain unsafe recommendations. Docker access is privileged.

## Decision

Gemini may propose only a schema-constrained diagnosis and one allowlisted recommendation. It cannot call Docker, reset circuit breakers, construct commands, or bypass local validation. Deterministic rules replace it on any provider failure.

## Alternatives Considered

- Direct model-to-tool execution: rejected as incompatible with the safety goal.
- No LLM: safer but removes the structured provider comparison the project demonstrates.
- Official SDK migration: deferred because the existing REST path remains lightweight, mockable, and supports structured JSON.

## Consequences

The model remains useful for interpretation while local code retains authority. More validation and fallback code is required.

## Security Implications

Keys use the `x-goog-api-key` header. Error bodies, headers, and credential-bearing URLs are not logged or stored.
