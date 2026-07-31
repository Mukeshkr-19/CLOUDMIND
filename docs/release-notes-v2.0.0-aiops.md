# CloudMind v2.0.0-aiops — Draft Release Notes

Status: prepared, not released.

## Highlights

- Structured Gemini diagnosis with header-based authentication and bounded retries
- Deterministic rules fallback for all provider failures
- Snapshot-backed evidence grounding and explicit rejected-evidence audit data
- Separate advisory model confidence and deterministic policy evidence score
- Default recommend mode and explicit governed execute mode
- Rolling restart budgets and per-service recovery circuit breakers
- Incident fingerprints, duplicate accounting, and corrupt-store preservation
- Post-action Prometheus and active-probe recovery verification
- InfraMirror AIOps metrics and Grafana policy/budget panels
- Ten-scenario deterministic validation report with zero unsafe actions
- SHA-pinned CI/security actions, CodeQL, Dependabot, Ruff, mypy, and coverage gates

## Known Limitations

- Local Docker Compose and privileged Docker socket
- No learned anomaly detection or formal causal inference
- Circuit-breaker state is process-local
- Deterministic fixture results are not live provider/runtime accuracy
- Safety-module coverage is 80.17%, below the 85% stretch goal

## Release Preconditions

- Branch merged with green CI/security/CodeQL
- Authentic live recommend and execute demonstration captured
- Historical pre-AIOps commit confirmed for `v1.0.0-sre`
- User approval for tags and GitHub release
