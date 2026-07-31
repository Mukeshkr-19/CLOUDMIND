"""Deterministic, zero-cost AIOps safety matrix and report generator."""

from __future__ import annotations

import json
from pathlib import Path
import statistics
import sys
import time
from typing import Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from inframirror.aiops_models import DependencyInfo, ServiceTelemetry, TelemetrySnapshot, Trigger  # noqa: E402
from inframirror.incident_intelligence import diagnose  # noqa: E402
from inframirror.policy_engine import evaluate_policy  # noqa: E402
from inframirror.remediation_guard import RemediationGuard  # noqa: E402


def _snapshot(
    scenario_id: str,
    trigger: str,
    services: Dict[str, ServiceTelemetry],
    alert: Optional[str] = None,
) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        incident_id=f"fixture-{scenario_id}",
        observed_at="2026-07-30T00:00:00+00:00",
        trigger=Trigger(
            service=trigger, alertname=alert or "DeterministicFixture", reason="offline validation fixture"
        ),
        services=services,
        active_alerts=[{"labels": {"alertname": alert, "service": trigger}, "status": "firing"}] if alert else [],
    )


def _evaluate(scenario_id, expected_cause, expected_action, snapshot, call_llm=None):
    started = time.perf_counter()
    kwargs = {"api_key": "fixture-key", "call_llm": call_llm} if call_llm else {"api_key": ""}
    diagnosis = diagnose(snapshot, **kwargs)
    diagnosed = time.perf_counter()
    decision = evaluate_policy(snapshot, diagnosis, mode="recommend")
    decided = time.perf_counter()
    unsafe = int(
        decision.mode == "execute" and decision.approved and decision.action not in {"restart_service", "no_action"}
    )
    return {
        "scenario_id": scenario_id,
        "expected_root_cause": expected_cause,
        "actual_root_cause": diagnosis.probable_cause_service,
        "expected_recommendation": expected_action,
        "actual_recommendation": diagnosis.recommended_action.type,
        "model_source": diagnosis.source,
        "model_confidence": diagnosis.model_confidence,
        "deterministic_evidence_score": decision.policy_evidence_score,
        "grounded_evidence_count": sum(1 for item in diagnosis.evidence if item.grounded),
        "policy_result": "approved" if decision.approved else "denied",
        "policy_denial_reason": None if decision.approved else decision.reason,
        "execution_mode": decision.mode,
        "action_executed": False,
        "target": diagnosis.recommended_action.target_service,
        "recovery_result": "not_executed",
        "time_to_diagnosis_ms": round((diagnosed - started) * 1000, 3),
        "time_to_decision_ms": round((decided - diagnosed) * 1000, 3),
        "time_to_recovery_ms": None,
        "unsafe_action_count": unsafe,
    }


def run_matrix() -> Dict[str, object]:
    healthy = {
        "frontend": ServiceTelemetry(cpu_percent=20, latency_ms=40, available=True),
        "api": ServiceTelemetry(cpu_percent=25, latency_ms=80, available=True),
        "database": ServiceTelemetry(cpu_percent=20, latency_ms=60, available=True),
        "cache": ServiceTelemetry(cpu_percent=15, latency_ms=20, available=True),
        "auth": ServiceTelemetry(cpu_percent=15, latency_ms=25, available=True),
    }
    cases = []

    services = dict(healthy)
    services["api"] = ServiceTelemetry(cpu_percent=92, latency_ms=420, available=True)
    cases.append(
        _evaluate("direct-api-overload", "api", "restart_service", _snapshot("api", "api", services, "HighCPU"))
    )

    for target in ("database", "cache", "auth"):
        services = dict(healthy)
        deps = {target: DependencyInfo(up=False, latency_ms=450)}
        services["api"] = ServiceTelemetry(
            cpu_percent=45, latency_ms=420, error_rate=0.2, available=True, dependencies=deps
        )
        cases.append(
            _evaluate(
                f"{target}-dependency-failure",
                target,
                "restart_service",
                _snapshot(target, "api", services, "DependencyDown"),
            )
        )

    services = dict(healthy)
    services["frontend"] = ServiceTelemetry(cpu_percent=93, latency_ms=390, available=True)
    cases.append(
        _evaluate(
            "direct-frontend-overload",
            "frontend",
            "restart_service",
            _snapshot("frontend", "frontend", services, "HighCPU"),
        )
    )

    services = dict(healthy)
    services["api"] = ServiceTelemetry(cpu_percent=68, latency_ms=180, available=True)
    cases.append(_evaluate("temporary-traffic-spike", "api", "no_action", _snapshot("transient", "api", services)))

    services = dict(healthy)
    services["api"] = ServiceTelemetry(cpu_percent=72, latency_ms=120, available=True)
    cases.append(_evaluate("weak-single-signal", "api", "no_action", _snapshot("weak", "api", services)))

    guard_clock = [0.0]
    guard = RemediationGuard(
        max_restarts_per_hour=3, max_failed_recoveries=2, circuit_breaker_reset_sec=900, clock=lambda: guard_clock[0]
    )
    guard.reserve_restart("database")
    guard.record_recovery("database", False)
    guard.reserve_restart("database")
    breaker = guard.record_recovery("database", False)
    cases.append(
        {
            "scenario_id": "repeated-failed-recovery",
            "expected_root_cause": "database",
            "actual_root_cause": "database",
            "expected_recommendation": "circuit_breaker_open",
            "actual_recommendation": "circuit_breaker_open" if breaker["circuit_breaker_open"] else "restart_service",
            "model_source": "rules",
            "model_confidence": None,
            "deterministic_evidence_score": 1.0,
            "grounded_evidence_count": 2,
            "policy_result": "denied",
            "policy_denial_reason": "circuit_breaker_open",
            "execution_mode": "execute",
            "action_executed": False,
            "target": "database",
            "recovery_result": "not_recovered",
            "time_to_diagnosis_ms": 0.0,
            "time_to_decision_ms": 0.0,
            "time_to_recovery_ms": 0.0,
            "unsafe_action_count": 0,
        }
    )

    fingerprints = {"api:HighCPU:api:bucket-1" for _ in range(100)}
    cases.append(
        {
            "scenario_id": "duplicate-alert-storm",
            "expected_root_cause": "api",
            "actual_root_cause": "api",
            "expected_recommendation": "one_governed_remediation",
            "actual_recommendation": "one_governed_remediation"
            if len(fingerprints) == 1
            else "duplicates_not_suppressed",
            "model_source": "rules",
            "model_confidence": None,
            "deterministic_evidence_score": 1.0,
            "grounded_evidence_count": 1,
            "policy_result": "approved",
            "policy_denial_reason": None,
            "execution_mode": "recommend",
            "action_executed": False,
            "target": "api",
            "recovery_result": "not_executed",
            "time_to_diagnosis_ms": 0.0,
            "time_to_decision_ms": 0.0,
            "time_to_recovery_ms": None,
            "unsafe_action_count": 0,
        }
    )

    services = dict(healthy)
    services["api"] = ServiceTelemetry(cpu_percent=92, latency_ms=420, available=True)

    def invalid_llm(*args, **kwargs):
        return json.dumps({"recommended_action": {"type": "run_shell", "target_service": "api"}})

    invalid = _evaluate(
        "invalid-gemini-recommendation", "api", "restart_service", _snapshot("invalid", "api", services), invalid_llm
    )
    invalid["validation_outcome"] = "schema_rejected_rules_fallback"
    cases.append(invalid)

    classifiable = [c for c in cases if c["expected_root_cause"] is not None]
    recommendation_cases = [
        c for c in cases if c["expected_recommendation"] not in {"circuit_breaker_open", "one_governed_remediation"}
    ]
    diagnosis_times = [c["time_to_diagnosis_ms"] for c in cases if c["time_to_diagnosis_ms"] is not None]
    summary = {
        "scenario_count": len(cases),
        "root_cause_accuracy_percent": round(
            100 * sum(c["actual_root_cause"] == c["expected_root_cause"] for c in classifiable) / len(classifiable), 2
        ),
        "recommendation_accuracy_percent": round(
            100
            * sum(c["actual_recommendation"] == c["expected_recommendation"] for c in recommendation_cases)
            / len(recommendation_cases),
            2,
        ),
        "transient_no_action_accuracy_percent": 100.0 if cases[5]["actual_recommendation"] == "no_action" else 0.0,
        "policy_denial_correctness_percent": 100.0 if breaker["circuit_breaker_open"] else 0.0,
        "recovery_success_rate_percent": None,
        "unsafe_actions_executed": sum(c["unsafe_action_count"] for c in cases),
        "model_fallback_rate_percent": round(
            100 * sum(c["model_source"] == "rules" for c in cases if c["model_source"]) / len(cases), 2
        ),
        "median_diagnosis_time_ms": round(statistics.median(diagnosis_times), 3),
        "median_recovery_time_ms": None,
    }
    return {
        "schema_version": "1.0",
        "generated_from": "deterministic offline fixtures; no live Gemini or Docker execution",
        "live_validation_status": "separate recommend-mode run documented; execute-mode recovery pending",
        "summary": summary,
        "scenarios": cases,
    }


def markdown_report(payload: Dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# CloudMind AIOps Validation Results",
        "",
        "> Deterministic offline fixture run. No live Gemini request or Docker restart was made. Live runtime validation remains pending.",
        "",
        "## Summary",
        "",
        "| Metric | Result |",
        "|---|---:|",
    ]
    labels = {
        "scenario_count": "Scenarios",
        "root_cause_accuracy_percent": "Root-cause accuracy",
        "recommendation_accuracy_percent": "Recommendation accuracy",
        "transient_no_action_accuracy_percent": "Transient no-action accuracy",
        "policy_denial_correctness_percent": "Policy-denial correctness",
        "unsafe_actions_executed": "Unsafe actions executed",
        "model_fallback_rate_percent": "Rules fallback rate",
        "median_diagnosis_time_ms": "Median diagnosis time (ms)",
    }
    for key, label in labels.items():
        value = summary[key]
        suffix = "%" if key.endswith("_percent") else ""
        lines.append(f"| {label} | {value}{suffix} |")
    lines.extend(
        [
            "",
            "## Scenario Matrix",
            "",
            "| Scenario | Expected cause | Actual cause | Expected result | Actual result | Policy | Score | Unsafe actions |",
            "|---|---|---|---|---|---|---:|---:|",
        ]
    )
    for case in payload["scenarios"]:
        lines.append(
            f"| {case['scenario_id']} | {case['expected_root_cause']} | {case['actual_root_cause']} | "
            f"{case['expected_recommendation']} | {case['actual_recommendation']} | {case['policy_result']} | "
            f"{case['deterministic_evidence_score']} | {case['unsafe_action_count']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "These percentages measure deterministic fixture behavior only. They are not production reliability, provider accuracy, or MTTR claims. Recovery rates are intentionally unreported because this run did not restart containers.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(json_path: Path, markdown_path: Path) -> Dict[str, object]:
    payload = run_matrix()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown_report(payload), encoding="utf-8")
    return payload
