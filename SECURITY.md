# Security Policy

CloudMind is an SRE observability and auto-remediation system that can integrate with external APIs, Discord webhooks, Docker, Prometheus, and Grafana. Treat configuration and runtime access carefully.

## Secrets

Never commit real credentials.

Sensitive values include:

- `GEMINI_API_KEY`
- `DISCORD_WEBHOOK_URL`
- Any future cloud provider credentials, tokens, or webhook URLs

Use `.env.example` as the template and keep real values in `.env`, which is ignored by Git.

## Rotation Procedure

If a credential is exposed:

1. Revoke or rotate the exposed credential at the provider.
2. Replace the value in `.env`.
3. Restart the stack:

   ```bash
   docker compose up -d --build
   ```

4. Check `git status --short` and confirm `.env` is not staged.
5. Search the repository before pushing:

   ```bash
   git grep -n "GEMINI_API_KEY\\|DISCORD_WEBHOOK_URL\\|discord.com/api/webhooks"
   ```

## Docker Socket Access

InfraMirror mounts `/var/run/docker.sock` so it can restart managed CloudMind containers during remediation. Docker socket access is powerful and should only be used in controlled environments.

Recommended safeguards:

- Run CloudMind on a dedicated operator-owned machine or isolated environment.
- Do not expose InfraMirror publicly.
- Keep `/whisper` behind a trusted network boundary.
- Review container restart behavior before connecting CloudMind to shared infrastructure.

## Discord Webhooks

Discord webhooks are bearer-style secrets. Anyone with the URL can post to the channel.

Recommended safeguards:

- Store webhook URLs only in `.env`.
- Use a dedicated Discord channel for infrastructure alerts.
- Rotate the webhook if it appears in logs, screenshots, issues, pull requests, or chat.
- Delete the webhook when the channel is no longer needed.

## Dependency Hygiene

Before pushing changes, run:

```bash
python3 -m compileall microservices inframirror tests
python3 -m unittest discover -s tests
docker compose config --quiet
```

Review dependency updates in `requirements.txt` and Dockerfiles before rebuilding images.

## Grafana Credentials

Grafana defaults to `admin` / `admin` through `.env.example` so a fresh stack can start without manual setup. Override `GRAFANA_ADMIN_USER` and `GRAFANA_ADMIN_PASSWORD` in `.env` before running CloudMind anywhere persistent or shared.

## Reporting

If you find a security issue in CloudMind, open a private report with:

- Affected file or component
- Reproduction steps
- Expected impact
- Suggested fix, if known
