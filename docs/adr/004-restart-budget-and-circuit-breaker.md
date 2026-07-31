# ADR 004: Restart Budget and Circuit Breaker

## Context

Cooldown and leases prevent immediate concurrency but do not bound repeated restarts across a longer failure window.

## Decision

Use a per-service rolling hourly restart budget and open a per-service circuit breaker after repeated failed recoveries. Make checks and reservations thread-safe. Permit only an authenticated target-scoped operator reset.

## Alternatives Considered

- Global budget: rejected because one failing target would block unrelated remediation.
- Unlimited retries with exponential delay: rejected because a broken dependency could still cause repeated disruption.

## Consequences

Repeated failure moves the service back to recommendation-only behavior until reset. State is process-local in this Docker Compose implementation.

## Security Implications

The LLM cannot reset or mutate guard state. A process restart resets in-memory guard history, which is a known local-demo limitation.
