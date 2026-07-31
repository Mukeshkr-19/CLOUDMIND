# policy_engine.py – Deterministic safety policy for AIOps remediation
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict, Optional

try:
    from .aiops_models import (
        ALLOWED_ACTIONS,
        ALLOWED_SERVICES,
        EvidenceAssessment,
        PolicyDecision,
        StructuredDiagnosis,
        TelemetrySnapshot,
    )
    from . import evidence_grounding
except ImportError:
    from aiops_models import (  # type: ignore[no-redef]
        ALLOWED_ACTIONS,
        ALLOWED_SERVICES,
        EvidenceAssessment,
        PolicyDecision,
        StructuredDiagnosis,
        TelemetrySnapshot,
    )
    import evidence_grounding  # type: ignore[no-redef]


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)
    if value < low or value > high:
        return float(default)
    return value


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)
    if value < low or value > high:
        return int(default)
    return int(value)


DEFAULT_CONFIDENCE_THRESHOLD = _env_float("AIOPS_CONFIDENCE_THRESHOLD", 0.75, 0.0, 1.0)
DEFAULT_EVIDENCE_SCORE_THRESHOLD = _env_float("AIOPS_EVIDENCE_SCORE_THRESHOLD", 0.55, 0.0, 1.0)
CPU_HARD = float(os.getenv("CPU_HARD_THRESHOLD", "85"))
LAT_PAIN_MS = float(os.getenv("LAT_PAIN_MS", "350"))


def assess_evidence(
    snapshot: TelemetrySnapshot,
    diagnosis: StructuredDiagnosis,
    target: Optional[str],
    err_ratio_threshold: float = 0.10,
) -> EvidenceAssessment:
    """Create a bounded deterministic score from snapshot truth, not LLM confidence.

    Formula (capped at 1.0): 0.15 per grounded signal (max 0.30), 0.40
    for each severe target signal (max 0.40), 0.70 for direct availability or
    dependency failure, 0.15 for correlated downstream impact, and 0.10 for a
    matching firing alert. A lone non-severe signal therefore cannot approve a
    restart at the default 0.55 threshold.
    """
    if not target:
        return EvidenceAssessment(0, 0, 0, False, False, False, False, 0.0, [], ["missing_target"])

    grounded = [item for item in diagnosis.evidence if item.grounded]
    target_consistency = any(item.service == target or item.dependency == target for item in grounded)
    service = snapshot.services.get(target)
    availability_failure = bool(service and service.available is False)
    severe = 0
    if service:
        severe += int(service.cpu_percent is not None and service.cpu_percent >= CPU_HARD)
        severe += int(service.latency_ms is not None and service.latency_ms >= LAT_PAIN_MS)
        severe += int(service.error_rate is not None and service.error_rate >= err_ratio_threshold)
        severe += int(service.incident_active is True)

    dependency_failure = False
    dependency_correlation = False
    for _name, observer in snapshot.services.items():
        dep = observer.dependencies.get(target)
        if dep and (dep.up is False or (dep.latency_ms is not None and dep.latency_ms >= LAT_PAIN_MS)):
            dependency_failure = True
            observer_impacted = (
                observer.available is False
                or observer.incident_active is True
                or (observer.latency_ms is not None and observer.latency_ms >= LAT_PAIN_MS)
                or (observer.error_rate is not None and observer.error_rate >= err_ratio_threshold)
            )
            dependency_correlation = dependency_correlation or observer_impacted

    matching_alerts = 0
    for alert in snapshot.active_alerts:
        labels = alert.get("labels", {}) if isinstance(alert, dict) else {}
        if labels.get("service") == target or labels.get("dependency") == target:
            matching_alerts += 1

    score = min(0.30, 0.15 * len(grounded))
    score += min(0.40, 0.40 * severe)
    if availability_failure or dependency_failure:
        score = max(score, 0.70)
    if dependency_correlation:
        score += 0.15
    if matching_alerts:
        score += 0.10
    score = min(1.0, score)
    approvals = []
    denials = []
    if grounded:
        approvals.append("grounded_evidence_present")
    else:
        denials.append("no_grounded_evidence")
    if target_consistency:
        approvals.append("evidence_matches_target")
    else:
        denials.append("evidence_target_mismatch")
    if severe:
        approvals.append("severe_target_signal")
    if availability_failure:
        approvals.append("target_unavailable")
    if dependency_failure:
        approvals.append("dependency_failure")
    if dependency_correlation:
        approvals.append("dependency_impact_correlated")
    return EvidenceAssessment(
        grounded_signal_count=len(grounded),
        severe_signal_count=severe,
        matching_alert_count=matching_alerts,
        availability_failure=availability_failure,
        dependency_failure=dependency_failure,
        dependency_correlation=dependency_correlation,
        target_consistency=target_consistency,
        evidence_score=score,
        approval_reasons=approvals,
        denial_reasons=denials,
    )


def has_supporting_abnormal_telemetry(
    snapshot: TelemetrySnapshot,
    target: str,
    err_ratio_threshold: float = 0.10,
) -> bool:
    svc = snapshot.services.get(target)
    if svc:
        if svc.available is False:
            return True
        if svc.cpu_percent is not None and svc.cpu_percent >= CPU_HARD:
            return True
        if svc.latency_ms is not None and svc.latency_ms >= LAT_PAIN_MS:
            return True
        if svc.error_rate is not None and svc.error_rate >= err_ratio_threshold:
            return True
        if svc.incident_active is True:
            return True
        for _dep_name, dep in svc.dependencies.items():
            if dep.up is False:
                return True
            if dep.latency_ms is not None and dep.latency_ms >= LAT_PAIN_MS:
                return True
    # Also support the case where the target is an unhealthy dependency of another service.
    for _service_name, service in snapshot.services.items():
        for dep_name, dep in service.dependencies.items():
            if dep_name == target:
                if dep.up is False:
                    return True
                if dep.latency_ms is not None and dep.latency_ms >= LAT_PAIN_MS:
                    return True
    return False


def _cooldown_active(
    target: str,
    last_heal: Dict[str, datetime],
    now: datetime,
    cooldown_seconds: int,
) -> bool:
    last = last_heal.get(target)
    if last is None:
        return False
    return (now - last).total_seconds() < cooldown_seconds


def evaluate_policy(
    snapshot: TelemetrySnapshot,
    diagnosis: StructuredDiagnosis,
    mode: str = "recommend",
    confidence_threshold: Optional[float] = None,
    last_heal: Optional[Dict[str, datetime]] = None,
    now: Optional[datetime] = None,
    cooldown_seconds: int = 150,
    err_ratio_threshold: float = 0.10,
    evidence_score_threshold: Optional[float] = None,
) -> PolicyDecision:
    if mode not in {"recommend", "execute"}:
        return PolicyDecision(
            approved=False,
            action="no_action",
            target=None,
            mode=mode,
            reason="Invalid execution mode",
            confidence_threshold=confidence_threshold
            if confidence_threshold is not None
            else DEFAULT_CONFIDENCE_THRESHOLD,
        )

    threshold = confidence_threshold if confidence_threshold is not None else DEFAULT_CONFIDENCE_THRESHOLD
    score_threshold = (
        evidence_score_threshold if evidence_score_threshold is not None else DEFAULT_EVIDENCE_SCORE_THRESHOLD
    )
    action = diagnosis.recommended_action.type
    target = diagnosis.recommended_action.target_service
    if not (action == "no_action" and target is not None) and any(not item.grounded for item in diagnosis.evidence):
        diagnosis = evidence_grounding.ground_diagnosis(snapshot, diagnosis).diagnosis
    assessment = assess_evidence(snapshot, diagnosis, target, err_ratio_threshold)

    def decision(approved: bool, selected_action: str, selected_target: Optional[str], reason: str) -> PolicyDecision:
        return PolicyDecision(
            approved=approved,
            action=selected_action,
            target=selected_target,
            mode=mode,
            reason=reason,
            confidence_threshold=threshold,
            policy_evidence_score=assessment.evidence_score,
            evidence_score_threshold=score_threshold,
            evidence_assessment=assessment.to_dict(),
        )

    if diagnosis.confidence < threshold:
        return decision(
            False,
            "no_action",
            None,
            f"Model confidence {diagnosis.model_confidence} below advisory threshold {threshold}",
        )

    if action not in ALLOWED_ACTIONS:
        return decision(False, "no_action", None, f"Action {action} not allowlisted")

    if action == "restart_service":
        if not target:
            return decision(False, "no_action", None, "restart_service requires a target")
        if target not in ALLOWED_SERVICES:
            return decision(False, "no_action", None, f"Target service {target} not allowlisted")

    if action == "no_action" and target is not None:
        return decision(False, "no_action", None, "no_action must not have a target")

    if action == "restart_service" and diagnosis.risk != "low":
        return decision(False, "no_action", None, f"Risk {diagnosis.risk} is not low; automatic restart denied")

    if action == "restart_service" and not diagnosis.evidence:
        return decision(False, "no_action", None, "Evidence required for restart_service")

    if action == "restart_service" and (not assessment.grounded_signal_count or not assessment.target_consistency):
        return decision(False, "no_action", None, "Grounded evidence matching the target is required")

    if action == "restart_service" and assessment.evidence_score < score_threshold:
        return decision(
            False,
            "no_action",
            None,
            f"Policy evidence score {assessment.evidence_score:.2f} below threshold {score_threshold:.2f}",
        )

    if action == "restart_service" and not has_supporting_abnormal_telemetry(
        snapshot, target or "", err_ratio_threshold=err_ratio_threshold
    ):
        return decision(False, "no_action", None, f"No abnormal telemetry supports restarting {target}")

    if mode == "execute" and action == "restart_service":
        current_time = now or datetime.now(timezone.utc)
        if _cooldown_active(target or "", last_heal or {}, current_time, cooldown_seconds):
            return decision(False, "no_action", None, f"Cooldown active for {target}")

    return decision(True, action, target, "Policy approved")
