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
3. **Confidence Threshold (`AIOPS_CONFIDENCE_THRESHOLD=0.75`)**: Diagnostic confidence score (`diagnosis.confidence`) must satisfy the policy threshold before policy approval.
4. **Supporting Abnormal Telemetry**: Policy evaluation verifies supporting abnormal conditions via `has_supporting_abnormal_telemetry`. Abnormal conditions may be established by error ratio (`error_rate >= AIOPS_ERROR_RATIO_THRESHOLD`, default `0.10`), elevated CPU (`cpu_percent >= CPU_HARD`), pain latency (`latency_ms >= LAT_PAIN_MS`), service unavailability (`available is False`), active incident flags, or downstream dependency failures. Error ratio is one of several valid supporting signals.
5. **Remediation Cooldown (`HEALING_COOLDOWN_SEC=150`)**: Enforces a mandatory cooldown window per target service to prevent restart loops.
6. **Per-Target Lease Lock (`threading.Lock`)**: A thread-safe mutex lease is acquired ONLY after policy approval immediately prior to container restart execution.
7. **Post-Action Recovery Verification**: Post-action verification evaluates Prometheus `up`, `service_cpu_percent`, `service_latency_ms`, and for dependency-caused API incidents, `service_dependency_up` metric plus active HTTP probes to API `/work`. Valid recovery status values are `recovered`, `not_recovered`, `inconclusive`, and `not_executed`.
8. **Bounded Worker Queue (`AIOPS_MAX_WORKERS=5`, `AIOPS_QUEUE_CAPACITY=10`)**: Prevents worker queue overflow and resource starvation under alert bursts.

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
