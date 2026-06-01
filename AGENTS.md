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
# Start the entire cluster in a single command
./run_demo.sh
```

## Stack
- Python (Flask)
- Docker & Docker Compose
- Prometheus (Timeseries scraping)
- Grafana (Provisioned Alerting-as-Code)
- Google Gemini API (Dynamic incident dialogs)
