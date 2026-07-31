# Baseline Audit

Date: 2026-07-30

Baseline branch: `main`

Baseline commit: `c3e4d5c4cf3d0bf8c65c045d8f608dfda5406a20`

## Environment and Git State

- Repository: `/Users/sanjay/Documents/CloudMind`
- Working tree before changes: clean; local `main` matched `origin/main`
- Implementation work was based on freshly fetched `origin/main` and released to `aiops-intelligence` and `main` by fast-forward.
- Python: 3.14.6 locally; workflows target Python 3.12
- Docker: 29.2.1
- Docker Compose: v5.1.0
- Existing tags: none

## Existing Architecture

Five Flask services (`frontend`, `api`, `database`, `cache`, `auth`) expose telemetry to Prometheus. Alertmanager sends authenticated alerts to InfraMirror. InfraMirror already implemented telemetry collection, dependency-aware rules, optional Gemini diagnosis, recommend/execute modes, cooldowns, per-target leases, bounded workers, recovery verification, and incident persistence.

## Baseline Verification

`make verify` passed before edits:

- 205 tests passed in 10.889 seconds
- Python compilation passed
- `docker compose config --quiet` passed

`python3 -m compileall .` also passed, although it unnecessarily traversed the virtual environment; subsequent checks use project source directories.

## Baseline Security Controls

- Authenticated `/whisper` webhook
- Query-string webhook tokens rejected
- Default recommend mode and disabled healing
- Service/action allowlists
- Startup grace, cooldown, target lease, bounded work queue
- Atomic bounded incident storage with recursive secret redaction
- Gitleaks, pip-audit, and Trivy workflows

## Confirmed Weaknesses

1. Both Gemini call paths placed the API key in the request URL.
2. Gemini evidence was structurally validated but numeric values were not reconciled with the telemetry snapshot.
3. Model confidence was the only explicit numerical policy confidence.
4. No rolling restart budget or recovery circuit breaker existed.
5. InfraMirror did not export AIOps subsystem metrics.
6. Corrupt incident files were silently treated as empty.
7. The scenario runner did not generate JSON and Markdown results.
8. GitHub Actions used mutable tags, including `aquasecurity/trivy-action@master`.
9. No CodeQL or Dependabot configuration existed.
10. README positioning implied causal behavior more strongly than the implementation supported.

## Planned Changes

- Central credential-safe Gemini client and schema-constrained response mode
- Deterministic evidence grounding and separate evidence score
- Restart budgets, circuit breakers, AIOps metrics, and richer audit fields
- Ten-scenario deterministic report plus retained live local runner
- Expanded automated tests and incremental Ruff/mypy/coverage gates
- Immutable workflow pins, CodeQL, Dependabot, documentation, and social preview

## Evidence Limits

No live Gemini request was authorized or made. Baseline runtime behavior was not re-claimed from documentation. New live Docker scenario and recovery evidence must come from an operator-run local stack and remains distinct from deterministic fixture results.
