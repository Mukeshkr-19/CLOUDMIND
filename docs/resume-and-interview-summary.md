# Resume and Interview Summary

## Resume Bullets

- Built a policy-governed AIOps and closed-loop SRE platform that correlates Prometheus telemetry, alerts, and service dependencies to produce structured root-cause diagnoses, grounded evidence, governed remediation recommendations, and post-action recovery verification.
- Implemented schema-constrained Gemini diagnosis with deterministic fallback, separate evidence scoring, allowlisted actions, cooldowns, per-target leases, restart budgets, circuit breakers, incident deduplication, and a 224-test safety suite with a 10-scenario zero-cost validation matrix.

## 30-Second Explanation

CloudMind is a local Docker Compose SRE lab. Prometheus and Alertmanager detect incidents across five Flask services. InfraMirror correlates the telemetry and dependencies, asks Gemini for a structured advisory diagnosis or uses deterministic rules, grounds every evidence value against the snapshot, and lets deterministic policy decide whether to recommend or safely restart one allowlisted service. It then verifies recovery and stores the complete audit trail.

## Two-Minute Architecture

Explain the workload and observability layers first, then the trust boundary: Gemini selects a cause and relevant signals, but Python owns values and actions. Walk through evidence grounding, the deterministic score, recommend/execute configuration, cooldown/lease/budget/circuit guards, and recovery verification. Close with the Docker socket limitation and local controlled scope.

## Five-Minute Walkthrough

1. Show Prometheus service and dependency metrics.
2. Trigger a controlled dependency failure.
3. Open the structured diagnosis and grounded evidence.
4. Compare `model_confidence` with `policy_evidence_score`.
5. Show recommend mode recording without execution.
6. In an isolated environment, demonstrate one execute-mode restart after all gates pass.
7. Show recovery checks and the stored incident.
8. Show circuit/budget metrics and the deterministic scenario report.

## Interview Answers

### Why is this AIOps rather than ordinary alerting?

It correlates multiple operational signals and service dependencies, produces structured incident intelligence, governs a remediation recommendation, and closes the loop with recovery verification and audit records.

### What decisions does Gemini make?

Gemini advises on probable cause, affected services, relevant signal names, risk, and one allowlisted recommendation. It does not supply authoritative values or execute anything.

### What remains deterministic?

Schema validation, telemetry values, evidence scoring, action/target allowlists, all execution gates, Docker invocation, recovery verification, budgets, and circuit breakers.

### How do you prevent hallucinated actions?

The structured schema and Python models allow only `restart_service` or `no_action`, and only five service targets. Unknown output falls back to rules.

### How do you ground model evidence?

The model selects signal references. Python looks each signal up in the captured snapshot, replaces numeric mismatches, and rejects invented or wrong-target evidence.

### Why is recommend mode the default?

Docker socket access is powerful, and provider output is variable. Recommendation mode preserves diagnostic value while keeping the operator in control.

### What happens when Gemini is unavailable?

Authentication, timeout, rate-limit, server, empty, malformed, schema, and dependency failures all lead to deterministic rules fallback.

### How do restart budgets prevent loops?

Each service has a rolling hourly budget. Atomic reservation prevents concurrent incidents from spending the same slot.

### How does recovery verification work?

It requires consecutive healthy Prometheus samples and, for API dependency incidents, an active `/work` dependency probe.

### What are the current limitations?

Local Compose scope, privileged Docker socket, no learned anomaly model, no formal causal inference, provider variability, process-local guard state, and pending new live measurements.

### Why use Docker socket access, and what are the risks?

It makes a local remediation demo concrete, but effectively grants host-level Docker control. The project therefore restricts targets and requires isolation; a production design would use a narrow external controller.

### How would this change for Kubernetes?

Replace the Docker adapter with a least-privilege controller using a dedicated service account, namespace and workload allowlists, rollout/restart budgets, Kubernetes events and health, and admission/audit controls. Kubernetes is not implemented here and should not be claimed as a project skill from CloudMind.
