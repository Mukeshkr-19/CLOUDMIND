# policy_engine.py – Deterministic safety policy for AIOps remediation
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from .aiops_models import (
        ALLOWED_ACTIONS,
        ALLOWED_SERVICES,
        PolicyDecision,
        StructuredDiagnosis,
        TelemetrySnapshot,
    )
except ImportError:
    from aiops_models import (
        ALLOWED_ACTIONS,
        ALLOWED_SERVICES,
        PolicyDecision,
        StructuredDiagnosis,
        TelemetrySnapshot,
    )


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
CPU_HARD = float(os.getenv("CPU_HARD_THRESHOLD", "85"))
LAT_PAIN_MS = float(os.getenv("LAT_PAIN_MS", "350"))


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
        for dep_name, dep in svc.dependencies.items():
            if dep.up is False:
                return True
            if dep.latency_ms is not None and dep.latency_ms >= LAT_PAIN_MS:
                return True
    # Also support the case where the target is an unhealthy dependency of another service.
    for service_name, service in snapshot.services.items():
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
) -> PolicyDecision:
    if mode not in {"recommend", "execute"}:
        return PolicyDecision(
            approved=False,
            action="no_action",
            target=None,
            mode=mode,
            reason="Invalid execution mode",
            confidence_threshold=confidence_threshold if confidence_threshold is not None else DEFAULT_CONFIDENCE_THRESHOLD,
        )

    threshold = confidence_threshold if confidence_threshold is not None else DEFAULT_CONFIDENCE_THRESHOLD
    action = diagnosis.recommended_action.type
    target = diagnosis.recommended_action.target_service

    if diagnosis.confidence < threshold:
        return PolicyDecision(
            approved=False,
            action="no_action",
            target=None,
            mode=mode,
            reason=f"Confidence {diagnosis.confidence} below threshold {threshold}",
            confidence_threshold=threshold,
        )

    if action not in ALLOWED_ACTIONS:
        return PolicyDecision(
            approved=False,
            action="no_action",
            target=None,
            mode=mode,
            reason=f"Action {action} not allowlisted",
            confidence_threshold=threshold,
        )

    if action == "restart_service":
        if not target:
            return PolicyDecision(
                approved=False,
                action="no_action",
                target=None,
                mode=mode,
                reason="restart_service requires a target",
                confidence_threshold=threshold,
            )
        if target not in ALLOWED_SERVICES:
            return PolicyDecision(
                approved=False,
                action="no_action",
                target=None,
                mode=mode,
                reason=f"Target service {target} not allowlisted",
                confidence_threshold=threshold,
            )

    if action == "no_action" and target is not None:
        return PolicyDecision(
            approved=False,
            action="no_action",
            target=None,
            mode=mode,
            reason="no_action must not have a target",
            confidence_threshold=threshold,
        )

    if action == "restart_service" and diagnosis.risk != "low":
        return PolicyDecision(
            approved=False,
            action="no_action",
            target=None,
            mode=mode,
            reason=f"Risk {diagnosis.risk} is not low; automatic restart denied",
            confidence_threshold=threshold,
        )

    if action == "restart_service" and not diagnosis.evidence:
        return PolicyDecision(
            approved=False,
            action="no_action",
            target=None,
            mode=mode,
            reason="Evidence required for restart_service",
            confidence_threshold=threshold,
        )

    if action == "restart_service" and not has_supporting_abnormal_telemetry(snapshot, target, err_ratio_threshold=err_ratio_threshold):
        return PolicyDecision(
            approved=False,
            action="no_action",
            target=None,
            mode=mode,
            reason=f"No abnormal telemetry supports restarting {target}",
            confidence_threshold=threshold,
        )

    if mode == "execute" and action == "restart_service":
        current_time = now or datetime.now(timezone.utc)
        if _cooldown_active(target, last_heal or {}, current_time, cooldown_seconds):
            return PolicyDecision(
                approved=False,
                action="no_action",
                target=None,
                mode=mode,
                reason=f"Cooldown active for {target}",
                confidence_threshold=threshold,
            )

    return PolicyDecision(
        approved=True,
        action=action,
        target=target,
        mode=mode,
        reason="Policy approved",
        confidence_threshold=threshold,
    )
