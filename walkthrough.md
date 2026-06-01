# CloudMind Walkthrough

This file is the practical demo script for CloudMind. It reflects the current repo state after the reviewer pass and Mira verification.

## What CloudMind Does

CloudMind runs five local Flask microservices and turns their telemetry into a playful incident narrative:

- `frontend` / Joy on port `5050`
- `api` / Logic on port `5051`
- `database` / Memory on port `5052`
- `cache` / Swift on port `5053`
- `auth` / Gatekeeper on port `5054`

Prometheus scrapes all five services. InfraMirror runs on port `5055`, watches telemetry, writes dialogue to `shared-data/dialogues.json`, and can restart stressed containers through the local Docker socket.

## Start the Demo

```bash
./run_demo.sh
```

Then open:

- Dashboard: http://localhost:5050
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000
- Webhook: http://localhost:5055/whisper

## Run Tests

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python -m unittest discover -s tests
python3 -m compileall microservices inframirror tests
docker compose config --quiet
```

Expected result: all unit tests pass and Compose config validates.

## Trigger an Incident

Use the interactive helper:

```bash
./chaos.sh
```

Or directly stress one service:

```bash
curl -X POST http://localhost:5052/stress
```

The dashboard should show higher load, Prometheus should observe the metric change, and InfraMirror should generate an incident dialogue with all five services plus the SRE line.

## Test the Webhook

```bash
curl -i -X POST http://localhost:5055/whisper \
  -H 'Content-Type: application/json' \
  -d '{"service":"database","cpu":91.2,"latency":401}'
```

Expected response:

```json
{"remediation":"queued","service":"database","status":"accepted"}
```

The endpoint returns `202 Accepted` quickly. Dialogue generation and remediation continue in a background thread.

## Optional AI Mode

By default, CloudMind uses deterministic local fallback dialogue, so it works offline.

For Gemini and Discord:

```bash
cp .env.example .env
```

Then add:

```bash
GEMINI_API_KEY=your_gemini_key
DISCORD_WEBHOOK_URL=your_discord_webhook
```

Never commit `.env`.

## Current Verified Scope

- All five services expose `/status`, `/load`, `/incident`, `/stress`, `/heal`, and `/metrics`.
- The dialogue engine has local fallback scripts and optional Gemini generation.
- Gemini key handling supports explicit keys in tests and environment keys in Docker.
- `/whisper` validates payloads and responds immediately instead of blocking on healing work.
- `run_demo.sh` supports both `docker compose` and legacy `docker-compose`.
- Unit tests cover service endpoints and dialogue fallback behavior.

## Known Demo Boundaries

- This is a local demo, not hardened production infrastructure.
- Docker socket access is intentionally powerful and should stay local.
- Grafana is provisioned with default `admin/admin` credentials for demonstration only.
- GitHub Actions workflow push may require a GitHub token with `workflow` scope.
