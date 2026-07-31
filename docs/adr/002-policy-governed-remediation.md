# ADR 002: Policy-Governed Remediation

## Context

A diagnosis is not sufficient authorization for a host-level container restart.

## Decision

Keep recommend mode as default. Require deterministic action/target/risk/evidence/configuration checks before execution and revalidate immediately before the per-target lease.

## Alternatives Considered

- Execute every critical alert: rejected because threshold noise can cause loops.
- Human approval only: safe, but does not demonstrate a guarded closed loop.

## Consequences

Some incidents are deliberately denied or deferred. The incident record explains why.

## Security Implications

Only five managed services and two action names exist. No command or arbitrary Docker argument enters the policy boundary.
