# Safety Model

## Core Invariant

No LLM output directly invokes Docker. A restart occurs only after deterministic code validates the schema, grounds evidence, computes policy evidence, confirms configuration, and atomically passes runtime guards.

## Evidence Authority

Supported signals are explicitly allowlisted. Direct signals come from a service telemetry object. Dependency signals must reference a relationship present in the snapshot. Unknown services, invented metrics, missing relationships, non-finite values, duplicate evidence, and wrong-target evidence are rejected.

Model-provided values are never authoritative. If a model value differs from the snapshot, CloudMind stores the snapshot value and records `model_value_replaced_with_snapshot_value`.

## Deterministic Score

The score is bounded to `[0, 1]`:

- `0.15` per grounded signal, capped at `0.30`
- up to `0.40` for severe target telemetry
- floor of `0.70` for direct unavailability or a dependency failure
- `0.15` for correlated downstream dependency impact
- `0.10` for a matching alert

The default approval threshold is `0.55`. A single non-severe signal scores `0.15` and cannot approve a restart. Model confidence is a separate advisory threshold and is not described as a probability.

## Execution Gates

All applicable gates must pass:

1. action and target allowlists
2. low risk
3. grounded target-consistent evidence
4. deterministic evidence score threshold
5. advisory model-confidence threshold when Gemini is used
6. abnormal telemetry still supports the target
7. explicit execute mode and healing enabled
8. startup grace complete
9. cooldown clear
10. per-target lease acquired
11. restart budget available
12. circuit breaker closed

## Restart Budget and Circuit Breaker

Each target receives three restart reservations per rolling hour by default. Two failed recoveries open its circuit breaker for 900 seconds. While open, execution is suppressed for that target; other targets remain independent. The reset endpoint is authenticated, target-scoped, and cannot be invoked by the LLM.

## Metrics Label Safety

AIOps metrics use fixed bounded label dimensions such as `target`, `source`, `decision`, `action`, `mode`, `result`, and a categorized denial reason. Incident IDs, exception messages, prompts, URLs, timestamps, and arbitrary model text are never labels.

## Residual Risk

The Docker socket grants powerful host access. Webhook authentication, allowlists, and policy reduce accidental action but do not turn the socket into a production-grade control plane. Network isolation and operator ownership remain mandatory.
