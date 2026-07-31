# Architecture

## Components

| Layer | Components | Responsibility |
|---|---|---|
| Workload | Five Flask microservices | Health, workload, stress/heal endpoints, Prometheus metrics |
| Observability | Prometheus, Alertmanager, Grafana | Collection, alerting, dashboards |
| Incident intake | InfraMirror `/whisper` | Authenticated, validated, bounded alert ingestion |
| Intelligence | Gemini client or deterministic rules | Advisory structured diagnosis |
| Trust enforcement | Evidence grounding and policy engine | Replace model values with telemetry truth and decide safely |
| Execution guards | Startup grace, cooldown, lease, budget, circuit breaker | Prevent concurrent, repeated, or looping remediation |
| Recovery | Recovery verifier | Prometheus and active dependency probes |
| Audit | Incident store and dashboard | Atomic bounded record of the complete decision |

## Data Flow

```mermaid
sequenceDiagram
    participant AM as Alertmanager
    participant IM as InfraMirror
    participant P as Prometheus
    participant L as Gemini or Rules
    participant G as Grounding and Policy
    participant D as Docker
    participant S as Incident Store

    AM->>IM: Authenticated firing alert
    IM->>P: Collect bounded telemetry snapshot
    IM->>L: Snapshot and strict schema
    L-->>IM: Advisory diagnosis
    IM->>G: Ground signal references and calculate score
    alt recommend mode or denied
        G-->>IM: Record only
    else execute mode and all guards pass
        IM->>D: Restart one allowlisted service
        IM->>P: Verify recovery
    end
    IM->>S: Persist sanitized decision trail
```

## Trust Boundaries

1. Alert payloads are untrusted and validated before work is queued.
2. Prometheus is the numeric source of truth for the captured snapshot.
3. Gemini output is untrusted advisory data even when schema-valid.
4. Deterministic Python owns all action, target, score, budget, and circuit decisions.
5. Docker socket access is host-privileged and limited to controlled local environments.
6. Persisted records are recursively redacted and bounded; provider error bodies are excluded.

## Failure Behavior

- Missing key/provider/library, timeout, authentication failure, rate limit, server failure, malformed output, empty output, or schema failure leads to rules fallback.
- Unknown or missing evidence is rejected; model numeric mismatches are replaced by snapshot values.
- Insufficient score, wrong target, risk, cooldown, budget, circuit, or lease state suppresses execution.
- Failed recovery increments the target circuit-breaker counter.
- A malformed incident file is preserved as a `.corrupt-*` backup before a new atomic store is written.
