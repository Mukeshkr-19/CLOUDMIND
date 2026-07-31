# CloudMind Live Recommend-Mode Validation

Date: 2026-07-30

Environment: local Docker Compose

Safety mode: `AIOPS_EXECUTION_MODE=recommend`, `HEALING_ENABLED=false`

Gemini usage: disabled; zero provider requests and zero provider cost

The live stack was started with throwaway runtime-only credentials. All five application services, Prometheus, Alertmanager, Grafana, and InfraMirror reported healthy. Prometheus reported all six configured scrape targets up, including InfraMirror's `/metrics` endpoint.

The following command completed successfully:

```bash
python3 scripts/run_aiops_scenarios.py all \
  --expect-mode recommend \
  --requests 10 \
  --incident-timeout 30 \
  --settle-window 2
```

| Controlled scenario | Result | Persisted decision |
|---|---|---|
| API overload | Passed | Rules diagnosis; governed restart recommendation; not executed |
| Database bottleneck | Passed | Dependency-aware recommendation; not executed |
| Cache failure | Passed | Dependency-aware recommendation; not executed |
| Auth failure | Passed | Dependency-aware recommendation; not executed |
| Transient spike | Passed | No unsafe restart recommendation |

The persisted records included grounded evidence, separate model-confidence and policy-evidence scores, execution mode, target, and non-executed recovery status. Grafana rendered the AIOps policy-decision series from the live metrics. No service restart was attempted, so this run does not claim a recovery rate or MTTR.
