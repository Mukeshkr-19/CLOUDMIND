"""Bounded-label Prometheus instrumentation for InfraMirror."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

INCIDENTS = Counter("cloudmind_aiops_incidents_total", "AIOps incidents accepted", ["target"])
DIAGNOSES = Counter("cloudmind_aiops_diagnoses_total", "Diagnoses by source", ["source"])
FALLBACKS = Counter("cloudmind_aiops_model_fallbacks_total", "Provider fallbacks", ["reason_category"])
POLICY = Counter("cloudmind_aiops_policy_decisions_total", "Policy decisions", ["decision", "action", "mode"])
DENIALS = Counter("cloudmind_aiops_policy_denials_total", "Policy denials", ["reason_category", "target"])
REMEDIATIONS = Counter("cloudmind_aiops_remediations_total", "Remediation attempts", ["target", "result"])
RECOVERY = Counter("cloudmind_aiops_recovery_results_total", "Recovery outcomes", ["target", "result"])
DIAGNOSIS_DURATION = Histogram("cloudmind_aiops_diagnosis_duration_seconds", "Diagnosis duration")
RECOVERY_DURATION = Histogram("cloudmind_aiops_recovery_duration_seconds", "Recovery verification duration")
QUEUE_DEPTH = Gauge("cloudmind_aiops_queue_depth", "AIOps queued and active work")
CIRCUIT_OPEN = Gauge("cloudmind_aiops_circuit_breaker_open", "Per-target circuit state", ["target"])
BUDGET_REMAINING = Gauge("cloudmind_aiops_restart_budget_remaining", "Per-target restart budget", ["target"])


def reason_category(reason: str) -> str:
    value = (reason or "unknown").lower()
    for category in (
        "cooldown", "evidence", "confidence", "risk", "allowlist",
        "budget", "circuit", "lease", "mode", "validation",
    ):
        if category in value:
            return category
    return "other"
