# CloudMind - Agent Context

## Project Overview
Microservices-based AI SRE system with multiple independent services.

## Architecture
- **microservices/** - Core services (frontend, auth, api, database, cache)
- **prometheus/** - Monitoring configuration
- **inframirror/** - SRE Watcher daemon & AI Whisper engine

## Key Files
- `docker-compose.yml` - Service orchestration
- `microservices/*/service.py` - Flask service implementations
- `microservices/*/Dockerfile` - Service container recipes

## Services
| Service | Port | Purpose | Character Persona |
|---------|------|---------|-------------------|
| frontend | 5050 | SRE Visual Dashboard & Dialogue console | Joy |
| api | 5051 | Backend REST API & Logical gateway | Logic |
| database | 5052 | Database adapter & Index indexing manager | Memory |
| cache | 5053 | High-speed cache cluster | Swift |
| auth | 5054 | Token-based security and authentication | Gatekeeper |
| SRE watcher | 5055 | Closed-Loop Auto-Remediation & AI Webhook | InfraMirror |

## Commands
```bash
# Start the entire cluster
docker compose up -d --build

# Stop the cluster
docker compose down

# Run local unit tests
venv/bin/python -m unittest discover -s tests

# Validate Python syntax and Compose config
python3 -m compileall microservices inframirror tests
docker compose config --quiet
```

## Stack
- Python (Flask)
- Docker & Docker Compose
- Prometheus (Timeseries scraping)
- Grafana (Provisioned Alerting-as-Code)
- Google Gemini API (Dynamic incident dialogs)

## Notes
- Keep `.env` local; use `.env.example` for placeholders only.
- `inframirror` mounts the local Docker socket for demo auto-remediation.
- `/whisper` should acknowledge alerts quickly and perform slower dialogue/healing work in the background.
