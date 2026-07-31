# Security Policy

CloudMind is an operational SRE telemetry and AIOps auto-remediation system that integrates Docker socket access, Prometheus TSDB telemetry, Alertmanager routing, Grafana dashboards, and optional AI diagnostic APIs.

---

## 🔐 Secrets & Configuration Management

Secrets belong **only** in an ignored `.env` file or a dedicated secret manager. Never commit real credentials to Git.

Sensitive configuration keys include:

- `WHISPER_TOKEN`: Protects InfraMirror's `/whisper` webhook. Validated by `alertmanager/entrypoint.sh` requiring 32–128 ASCII characters matching `[A-Za-z0-9._~-]`. Rendered to `/tmp/alertmanager.yml` with restrictive `0600` permissions. The token must never be printed to logs.
- `GRAFANA_ADMIN_PASSWORD`: Required for Grafana admin authentication. Docker Compose fails closed unless provided.
- `GEMINI_API_KEY`: Optional API key for live AI diagnostic generation. Keep private in `.env`.
- `DISCORD_WEBHOOK_URL`: Optional bearer-style URL for Discord incident notifications. Keep private in `.env`.

---

## 🔑 InfraMirror Webhook Authentication

InfraMirror rejects requests to `/whisper` unless they include a valid authentication token:

- **Accepted Authentication Headers**: `Authorization: Bearer <WHISPER_TOKEN>` or `X-CloudMind-Token: <WHISPER_TOKEN>`.
- **Query-String Tokens Rejected**: Token parameters in query strings are rejected to prevent token leakage in web server access logs and browser histories.
- **Network Boundary Protection**: Do not expose port `5055` or the `/whisper` endpoint directly to untrusted public networks.
- **Token Rotation**: Rotate `WHISPER_TOKEN` immediately in `.env` if it appears in logs, shell histories, screenshots, or code repositories.

## Gemini Provider Boundary

- Gemini keys are sent only in the `x-goog-api-key` request header; they are never placed in URLs.
- Endpoint construction is centralized and the configurable model name cannot inject a URL, query string, or path.
- Request headers, credentials, complete provider URLs, and provider error bodies are not logged or persisted.
- Only HTTP 429, 500, 502, 503, and 504 receive at most three bounded retry attempts with backoff and jitter. Authentication and schema failures are not retried.
- Provider absence, timeout, authentication failure, rate limiting, server error, malformed/empty output, or schema failure falls back to deterministic rules.
- Structured output limits services, actions, risk values, field lengths, and evidence signal names. Local Python validation remains authoritative.

---

## 📢 Discord Webhook Safeguards

Discord webhook URLs are bearer-style credentials. Anyone with the URL can post messages to the configured Discord channel:

- **Private Configuration**: Store `DISCORD_WEBHOOK_URL` exclusively in `.env`.
- **Dedicated Channel**: Route alerts to a dedicated, restricted Discord operational channel.
- **Immediate Rotation**: Revoke and rotate the webhook immediately if it is exposed in logs, screenshots, issues, pull requests, or chat logs.

---

## 📊 Grafana Credentials Guidance

- **Password Requirement**: `GRAFANA_ADMIN_PASSWORD` is required before Docker Compose will initialize Grafana.
- **No Placeholder Reuse**: Never reuse the default placeholder from `.env.example` in shared or production environments.
- **Credential Rotation**: Rotate Grafana passwords if exposed, and store them securely in `.env`.

---

## 🐳 Docker Socket Access & Execution Mode Governance

In `execute` mode, `inframirror` mounts `/var/run/docker.sock` to enable container recycling for unhealthy CloudMind microservices.

### Docker Socket Security Risk

Access to `/var/run/docker.sock` grants Docker daemon administrative control over managed host containers. Therefore:

- Run CloudMind only on operator-owned, dedicated, or isolated infrastructure.
- Do not expose `inframirror` (`:5055`) or the `/whisper` endpoint to untrusted networks.
- Enforce network boundary protection around Alertmanager and InfraMirror services.

### Safe Recommendation Mode Default

To prevent unintended container restarts, CloudMind defaults to `recommend` mode:

```bash
HEALING_ENABLED=false
AIOPS_EXECUTION_MODE=recommend
```

In `recommend` mode, AIOps functions as a human-in-the-loop advisory system. Diagnoses and policy decisions are recorded and displayed on the dashboard, but container restart execution is disabled.

### Automated Execution Requirements

Automated container restart execution requires **both** flags to be configured explicitly in environment configuration:

```bash
HEALING_ENABLED=true
AIOPS_EXECUTION_MODE=execute
```

---

## 🛡️ Policy Governance & Safety Safeguards

CloudMind enforces multiple deterministic safety controls across distinct decision stages before authorizing or executing container restarts:

1. **Startup Execution Grace (`AIOPS_EXECUTION_GRACE_SEC=30`)**: During process startup grace (0–300s), effective execution mode is forced to `recommend` before policy evaluation occurs, suppressing automated remediation triggers during startup.
2. **Internal Target Allowlist (`ALLOWED_SERVICES`)**: Internal Python code allowlist in `aiops_models.py` strictly bounds restarts to managed CloudMind microservices (`api`, `database`, `cache`, `auth`, `frontend`). Attempts to target unmanaged containers are rejected.
3. **Advisory Model Confidence (`AIOPS_CONFIDENCE_THRESHOLD=0.75`)**: `model_confidence` is treated as an uncalibrated advisory score, never a correctness probability.
4. **Evidence Grounding**: Model-selected signal values are replaced with values from the captured telemetry snapshot. Invented, missing, duplicate, non-finite, or wrong-target evidence cannot approve execution.
5. **Deterministic Evidence Score (`AIOPS_EVIDENCE_SCORE_THRESHOLD=0.55`)**: Policy independently scores grounded signals, severity, availability, dependency correlation, and matching alerts. A single weak signal cannot approve a restart.
6. **Supporting Abnormal Telemetry**: Policy rechecks CPU, latency, errors, availability, incident state, and dependencies before execution.
7. **Remediation Cooldown (`HEALING_COOLDOWN_SEC=150`)**: Enforces a mandatory cooldown window per target service.
8. **Per-Target Lease Lock**: A thread-safe lease is acquired after approval immediately before execution.
9. **Rolling Restart Budget**: `AIOPS_MAX_RESTARTS_PER_SERVICE_PER_HOUR=3` bounds restarts per target.
10. **Recovery Circuit Breaker**: Two failed recoveries open the target circuit for 900 seconds by default. Only an authenticated target-scoped endpoint can reset it manually; the LLM cannot reset it.
11. **Post-Action Recovery Verification**: Prometheus health and active dependency probes determine recovery.
12. **Bounded Worker Queue**: `AIOPS_MAX_WORKERS=5` and `AIOPS_QUEUE_CAPACITY=10` prevent alert bursts from exhausting resources.

## CI Supply-Chain Controls

GitHub Actions references are pinned to full commit SHAs with release comments. CI uses read-only contents permission; CodeQL receives only the additional `security-events: write` permission it needs. Dependabot groups monthly Python and workflow updates to limit noise. Gitleaks, pip-audit, Trivy, CodeQL, Ruff, mypy, tests, coverage, compilation, and Compose validation run without live Gemini credentials.

---

## 🔄 Safe Activation & Verifiable Rollback Guidance

### Safe Activation of Execute Mode

Updating shell environment variables in your local terminal does not update an already-running container. To activate `execute` mode safely:

1. Deploy and verify cluster stability in `recommend` mode first (`python3 scripts/run_aiops_scenarios.py all --expect-mode recommend`).
2. Update `.env` with `HEALING_ENABLED=true` and `AIOPS_EXECUTION_MODE=execute`.
3. Recreate the InfraMirror container to load updated configuration:
   ```bash
   docker compose up -d --force-recreate inframirror
   ```
4. Wait through the startup grace window (`AIOPS_EXECUTION_GRACE_SEC`, default 30s) so effective execution mode switches to `execute`.
5. Run an execute scenario:
   ```bash
   python3 scripts/run_aiops_scenarios.py database-bottleneck --expect-mode execute --settle-window 5.0
   ```

### Verifiable Rollback to Recommendation Mode

To roll back execute mode safely and verify configuration:

1. Update `.env` with `AIOPS_EXECUTION_MODE=recommend` (and optionally `HEALING_ENABLED=false`).
2. Recreate the InfraMirror container:
   ```bash
   docker compose up -d --force-recreate inframirror
   ```
3. Run a recommendation scenario:
   ```bash
   python3 scripts/run_aiops_scenarios.py database-bottleneck --expect-mode recommend
   ```
4. Inspect the resulting incident record via `GET /aiops-incidents` and confirm `"policy_decision": {"mode": "recommend"}` and `"execution_result": {"executed": false}`.

---

## 🧪 Dependency Hygiene & Verification

Before pushing configuration or documentation updates, run the project verification sequence:

```bash
python3 -m compileall microservices inframirror tests
python3 -m unittest discover -s tests
docker compose config --quiet
```

Review dependency updates in `requirements.txt` and microservice Dockerfiles before rebuilding container images.

---

## 🔄 Secret Rotation Procedure

If a secret or credential is exposed:

1. Revoke or rotate the credential at the provider immediately.
2. Update the value in `.env`.
3. Rebuild and restart the stack:
   ```bash
   docker compose up -d --build
   ```
4. Verify `.env` is not staged in Git (`git status --short`).

---

## 🔒 Security Reporting

To report a potential security issue in CloudMind, submit a private report detailing:

- Affected component or service
- Step-by-step reproduction steps
- Potential impact assessment
- Proposed remediation or patch, if available
