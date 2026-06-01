# CloudMind - Agent Context

## Project Overview
Microservices-based AI system with multiple independent services.

## Architecture
- **microservices/** - Core services (frontend, auth, api, database, cache)
- **prometheus/** - Monitoring configuration
- **inframirror/** - Infrastructure mirror/Docker setup

## Key Files
- `docker-compose.yml` - Service orchestration
- `microservices/*/service.py` - Service implementations
- `microservices/*/Dockerfile` - Service containers

## Services
| Service | Port | Purpose |
|---------|------|---------|
| frontend | 8000 | User interface |
| auth | 8001 | Authentication |
| api | 8002 | REST API |
| database | 5432 | PostgreSQL |
| cache | 6379 | Redis |

## Commands
```bash
cd CloudMind && docker-compose up -d
```

## Stack
- Python (FastAPI)
- Docker
- PostgreSQL
- Redis
- Prometheus
