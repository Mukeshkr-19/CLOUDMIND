# Demo Guide

## Safe Recommendation Demo

1. Copy `.env.example` to `.env` and set unique `WHISPER_TOKEN` and Grafana credentials.
2. Leave `HEALING_ENABLED=false` and `AIOPS_EXECUTION_MODE=recommend`.
3. Start the stack: `docker compose up -d --build`.
4. Confirm `docker compose ps`, Prometheus targets, and Grafana health.
5. Run `python3 scripts/run_aiops_scenarios.py all --expect-mode recommend`.
6. Open the operator dashboard and the InfraMirror metrics endpoint.
7. Show the incident record fields: source, grounded evidence, model confidence, policy evidence score, decision, and no execution.

## Controlled Execute Demo

Only on an operator-owned local Docker environment:

1. Set `HEALING_ENABLED=true` and `AIOPS_EXECUTION_MODE=execute`.
2. Recreate InfraMirror: `docker compose up -d --force-recreate inframirror`.
3. Wait beyond `AIOPS_EXECUTION_GRACE_SEC`.
4. Run one scenario: `python3 scripts/run_aiops_scenarios.py database-bottleneck --expect-mode execute --settle-window 5`.
5. Verify exactly one execution record and a real recovery outcome.
6. Restore recommend mode immediately after the demonstration.

## Deterministic CI Matrix

```bash
python3 scripts/run_aiops_scenarios.py --generate-deterministic-report
```

This writes `artifacts/aiops-validation-results.json` and generates `docs/validation-results.md` from it. It makes no live Gemini request and performs no Docker restart.

## Capture Checklist

Capture authentic screenshots only after verifying the displayed state:

- operator dashboard
- Grafana service health
- policy approved and denied panels
- restart budget and circuit breaker panels
- one complete incident diagnosis
- post-action recovery result
- generated validation report

Redact credentials, host paths, and private environment data. Do not use the social preview as runtime evidence.
