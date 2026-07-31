# CloudMind AIOps Hardening Report

Date: 2026-07-30

Released branches: `main` and `aiops-intelligence`

Base commit: `c3e4d5c4cf3d0bf8c65c045d8f608dfda5406a20` (`origin/main` at branch creation)

## Outcome

CloudMind now has a policy-governed AIOps control path with schema-constrained provider output, deterministic telemetry grounding, a policy evidence score separate from model confidence, restart budgets, recovery-driven circuit breakers, incident fingerprints, bounded Prometheus metrics, and an authenticated operator reset. Recommend mode remains the default and the model cannot directly invoke Docker.

The work was verified with 224 collected tests, a generated ten-scenario offline matrix, a live five-scenario recommend-mode run, Docker Compose health checks, and authentic operator/Grafana screenshots. No live Gemini request or execute-mode restart was made.

## Files Changed

The branch changes configuration, CI, source, tests, dashboards, and documentation. The principal files are:

- Runtime and safety: `inframirror/gemini_client.py`, `evidence_grounding.py`, `policy_engine.py`, `remediation_guard.py`, `aiops_metrics.py`, `incident_store.py`, `incident_intelligence.py`, `watcher.py`, and `aiops_models.py`.
- Configuration and observability: `.env.example`, `docker-compose.yml`, `prometheus/prometheus.yml`, and Grafana datasource/dashboard provisioning.
- Validation and quality: `tests/test_aiops_hardening.py`, `scripts/aiops_validation.py`, `scripts/run_aiops_scenarios.py`, `requirements-dev.txt`, `pyproject.toml`, and `Makefile`.
- DevSecOps: the CI and security workflows, a new CodeQL workflow, and `.github/dependabot.yml`.
- Presentation and evidence: `README.md`, `SECURITY.md`, generated validation artifacts, architecture/safety/demo/ADR/release/interview documentation, two authentic screenshots, and `docs/social-preview.png`.

## Architecture Changes

1. Gemini calls are centralized in a mockable client with configurable model/API version, header authentication, bounded retries, jitter, `Retry-After` support, structured response schema, and bounded error categories.
2. Model-selected evidence is resolved against the immutable telemetry snapshot. Unknown services/signals, missing dependency relationships, duplicate evidence, wrong-target evidence, and non-finite values cannot authorize execution.
3. Advisory `model_confidence` is persisted separately from a deterministic `policy_evidence_score`. The documented score combines grounded signals, severity, availability/dependency failures, downstream correlation, and matching alerts.
4. Per-target rolling restart budgets and recovery-driven circuit breakers are evaluated in the real execution path. The operator-only reset endpoint uses the existing authenticated request boundary.
5. Incident records now include a fingerprint, duplicate count, first/last-seen times, grounded and rejected evidence, policy assessment, budget/circuit state, and recovery result. Corrupt stores are preserved before a new atomic write.
6. InfraMirror exposes `/metrics`; Prometheus scrapes it; Grafana includes policy, diagnosis/fallback, recovery, budget, and circuit panels.

## Security Changes

- Gemini API keys are sent only in `x-goog-api-key`, never in a URL.
- Provider response bodies are not persisted as errors, authentication/schema errors are not retried, and only bounded retryable statuses are retried.
- Action and service allowlists remain local and deterministic; provider output cannot introduce commands, URLs, container arguments, or arbitrary targets.
- Recommend mode remains the default. Execute mode still requires both `AIOPS_EXECUTION_MODE=execute` and `HEALING_ENABLED=true`, plus startup grace, policy approval, cooldown, lease, budget, and circuit checks.
- GitHub Actions are pinned to verified full commit SHAs with minimal permissions and concurrency cancellation. CodeQL and monthly grouped Dependabot updates were added.
- The tracked-file credential-pattern scan returned no findings. `pip-audit -r requirements.txt` reported no known vulnerabilities.

## Tests Added and Coverage

The new hardening suite covers Gemini header authentication, URL secret exclusion, bounded retries and failure categories, evidence replacement/rejection, dependency grounding, prompt-injection-as-data, weak-signal denial, direct-down approval, rolling budgets, circuit reset behavior, concurrent budget reservations, corrupt-store preservation, and deterministic matrix generation. Existing suites continue to cover cooldowns, leases, bounded queues, execution grace, recovery verification, webhook authentication, incident deduplication, and scenario validation.

- Collected: **224 tests** in 0.31 seconds.
- Pytest: **224 passed, 0 failed, 0 skipped, plus 4 passing subtests** in 11.65 seconds.
- `make verify`: **224 passed** in 10.966 seconds; compilation and Compose validation passed.
- Safety-module branch coverage: **80.17%** across the Gemini client, incident intelligence, grounding, policy, recovery verification, incident store, and remediation guard. The enforced gate is 80%; the 85% stretch goal is not yet met.

## Scenario Results

The generated source of truth is `artifacts/aiops-validation-results.json`; `docs/validation-results.md` is generated from it.

- Scenarios: **10**.
- Fixture root-cause accuracy: **100.0%**.
- Fixture recommendation accuracy: **100.0%**.
- Transient no-action accuracy: **100.0%**.
- Policy-denial correctness: **100.0%**.
- Unsafe actions executed: **0**.
- Rules fallback rate: **100.0%**.
- Median deterministic diagnosis time: **0.021 ms**.
- Recovery success and median recovery time: **not measured**, because the matrix is recommend-only and makes no restart.

The separate live Docker run passed API overload, database bottleneck, cache failure, auth failure, and transient-spike cases in recommend mode. It persisted governed decisions without executing restarts. Six Prometheus targets (`frontend`, `api`, `database`, `cache`, `auth`, and `inframirror`) were up, and the final Compose state showed all configured application and monitoring containers running, with every service that defines a health check reporting healthy.

## Commands Run Successfully

```text
git fetch --all --tags --prune
git switch aiops-intelligence
python3 -m compileall -q .
make verify
docker compose config --quiet
venv/bin/ruff check .
venv/bin/mypy inframirror/gemini_client.py inframirror/evidence_grounding.py inframirror/remediation_guard.py inframirror/aiops_metrics.py
venv/bin/python -m pytest -q
venv/bin/python -m pytest -q --cov=... --cov-branch --cov-fail-under=80
venv/bin/python scripts/run_aiops_scenarios.py --generate-deterministic-report
venv/bin/python -m pip_audit -r requirements.txt
python3 scripts/run_aiops_scenarios.py all --expect-mode recommend --requests 10 --incident-timeout 30 --settle-window 2
docker compose ps
curl http://127.0.0.1:9090/api/v1/targets
curl http://127.0.0.1:5055/metrics
git diff --check
```

JSON assets validated with `jq`. The social preview is 1280×640 and 909,840 bytes. The checked-in screenshots were captured from the live local application and provisioned Grafana dashboard.

## Commands Not Run or Not Completed

- Live Gemini validation was not run because it could consume provider quota or money and was not explicitly authorized.
- Execute-mode remediation/recovery scenarios were not run; the safe live validation remained in recommend mode.
- Local Gitleaks and Trivy scans were not run because neither executable nor container image was installed. Both are configured as SHA-pinned CI jobs. A bounded tracked-file secret-pattern scan was run locally instead.
- The first sandboxed `pip-audit` attempt could not build its isolated resolver environment; the approved network-enabled rerun completed with no known vulnerabilities.
- The first Alertmanager startup used the user's existing invalid-length local token. No secret file was opened or changed; runtime-only throwaway values were supplied for verification.

## GitHub and Release State

- Repository About metadata: **not updated**. `gh` was authenticated as `mukeshkr-05`, while the target repository belongs to `Mukeshkr-19`; the requested update returned HTTP 404. Exact owner-account steps and desired values are in `docs/github-manual-steps.md`.
- Social preview: created locally; manual upload remains.
- Profile README: **not created or modified**. The requested content and pinning plan are in `docs/github-profile-plan.md`.
- Release/tag: **not created**. Release notes are prepared, but the prompt requires explicit approval before creating or pushing tags/releases.
- Remote branches: none deleted.

## Unresolved Limitations

- The safety-module branch coverage is 80.17%, below the 85% stretch goal.
- No execute-mode recovery rate or MTTR is claimed.
- No live provider accuracy is claimed; the published percentages describe deterministic fixtures only.
- This remains a controlled local Docker Compose project. It has no learned anomaly detector, formal causal-inference engine, Kubernetes remediation, or narrow replacement for privileged Docker socket access.
- The existing local `.env` needs a valid 32–128 character `WHISPER_TOKEN` before a normal future Compose recreation; it was deliberately not inspected or modified.
- An edited end-to-end video remains a manual presentation improvement.

## Cost and Recommended Next Steps

Live Gemini requests: **0**. Estimated external API/cloud cost: **$0**.

1. Review the branch and CI results, then merge through the repository owner's normal process.
2. Run an explicitly authorized execute-mode matrix in an isolated disposable Docker environment to measure recovery success and timing.
3. Raise safety-module branch coverage above 85% with meaningful provider failure, policy denial, store I/O, and circuit-state tests.
4. Authenticate `gh` as the repository owner, apply the documented About/topics/social-preview changes, then approve tag/release creation.
5. Replace direct Docker socket access with a narrower remediation adapter before any deployment beyond an operator-owned lab.
