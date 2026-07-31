# CloudMind AIOps Validation Results

> Deterministic offline fixture run. No live Gemini request or Docker restart was made. Live runtime validation remains pending.

## Summary

| Metric | Result |
|---|---:|
| Scenarios | 10 |
| Root-cause accuracy | 100.0% |
| Recommendation accuracy | 100.0% |
| Transient no-action accuracy | 100.0% |
| Policy-denial correctness | 100.0% |
| Unsafe actions executed | 0 |
| Rules fallback rate | 100.0% |
| Median diagnosis time (ms) | 0.021 |

## Scenario Matrix

| Scenario | Expected cause | Actual cause | Expected result | Actual result | Policy | Score | Unsafe actions |
|---|---|---|---|---|---|---:|---:|
| direct-api-overload | api | api | restart_service | restart_service | approved | 0.65 | 0 |
| database-dependency-failure | database | database | restart_service | restart_service | approved | 0.85 | 0 |
| cache-dependency-failure | cache | cache | restart_service | restart_service | approved | 0.85 | 0 |
| auth-dependency-failure | auth | auth | restart_service | restart_service | approved | 0.85 | 0 |
| direct-frontend-overload | frontend | frontend | restart_service | restart_service | approved | 0.65 | 0 |
| temporary-traffic-spike | api | api | no_action | no_action | denied | 0.0 | 0 |
| weak-single-signal | api | api | no_action | no_action | denied | 0.0 | 0 |
| repeated-failed-recovery | database | database | circuit_breaker_open | circuit_breaker_open | denied | 1.0 | 0 |
| duplicate-alert-storm | api | api | one_governed_remediation | one_governed_remediation | approved | 1.0 | 0 |
| invalid-gemini-recommendation | api | api | restart_service | restart_service | approved | 0.55 | 0 |

## Interpretation

These percentages measure deterministic fixture behavior only. They are not production reliability, provider accuracy, or MTTR claims. Recovery rates are intentionally unreported because this run did not restart containers.
