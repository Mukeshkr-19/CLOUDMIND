"""Deterministically replace model-authored values with telemetry truth."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Dict, List, Optional, Tuple

try:
    from .aiops_models import EvidenceItem, StructuredDiagnosis, TelemetrySnapshot
except ImportError:
    from aiops_models import EvidenceItem, StructuredDiagnosis, TelemetrySnapshot  # type: ignore[no-redef]


DIRECT_SIGNALS = {
    "cpu_percent",
    "latency_ms",
    "request_rate",
    "error_rate",
    "available",
    "incident_active",
}
DEPENDENCY_SIGNALS = {"dependency_up", "dependency_latency_ms"}
SUPPORTED_SIGNALS = DIRECT_SIGNALS | DEPENDENCY_SIGNALS | {"active_alert", "service_up"}
SIGNAL_ALIASES = {"cpu": "cpu_percent", "latency": "latency_ms", "errors": "error_rate"}


@dataclass(frozen=True)
class GroundingResult:
    diagnosis: StructuredDiagnosis
    grounded: List[EvidenceItem]
    rejected: List[Dict[str, Any]]


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _active_alert(snapshot: TelemetrySnapshot, service: str) -> Optional[float]:
    for alert in snapshot.active_alerts:
        labels = alert.get("labels", {}) if isinstance(alert, dict) else {}
        if labels.get("service") == service and alert.get("state", alert.get("status", "firing")) != "resolved":
            return 1.0
    return 0.0


def lookup_snapshot_value(
    snapshot: TelemetrySnapshot,
    *,
    service: str,
    signal: str,
    dependency: Optional[str] = None,
) -> Tuple[Optional[float], Optional[str]]:
    telemetry = snapshot.services.get(service)
    if telemetry is None:
        return None, "unknown_service"
    signal = SIGNAL_ALIASES.get(signal, signal)
    if signal not in SUPPORTED_SIGNALS:
        return None, "unsupported_signal"
    if signal == "active_alert":
        return _active_alert(snapshot, service), None
    if signal == "service_up":
        return _numeric(telemetry.available), None
    if signal in DIRECT_SIGNALS:
        return _numeric(getattr(telemetry, signal)), None if getattr(
            telemetry, signal
        ) is not None else "missing_signal"
    if dependency is None:
        return None, "missing_dependency"
    dep = telemetry.dependencies.get(dependency)
    if dep is None:
        return None, "missing_dependency"
    raw = dep.up if signal == "dependency_up" else dep.latency_ms
    return _numeric(raw), None if raw is not None else "missing_signal"


def _infer_dependency(snapshot: TelemetrySnapshot, item: EvidenceItem, target: Optional[str]) -> Optional[str]:
    if item.dependency:
        return item.dependency
    service = snapshot.services.get(item.service)
    if target and service and target in service.dependencies:
        return target
    return None


def ground_diagnosis(snapshot: TelemetrySnapshot, diagnosis: StructuredDiagnosis) -> GroundingResult:
    grounded: List[EvidenceItem] = []
    rejected: List[Dict[str, Any]] = []
    seen = set()
    target = diagnosis.recommended_action.target_service or diagnosis.probable_cause_service

    for item in diagnosis.evidence:
        canonical_signal = SIGNAL_ALIASES.get(item.signal, item.signal)
        dependency = (
            _infer_dependency(snapshot, item, target) if canonical_signal in DEPENDENCY_SIGNALS else item.dependency
        )
        identity = (item.service, canonical_signal, dependency)
        if identity in seen:
            rejected.append({"service": item.service, "signal": item.signal, "reason": "duplicate_evidence"})
            continue
        seen.add(identity)
        actual, error = lookup_snapshot_value(
            snapshot,
            service=item.service,
            signal=canonical_signal,
            dependency=dependency,
        )
        if error or actual is None:
            rejected.append({"service": item.service, "signal": item.signal, "reason": error or "missing_signal"})
            continue
        supports_target = item.service == target or dependency == target
        if diagnosis.recommended_action.type == "restart_service" and not supports_target:
            rejected.append({"service": item.service, "signal": item.signal, "reason": "wrong_target"})
            continue
        reason = None
        if not math.isclose(float(item.value), actual, rel_tol=1e-6, abs_tol=1e-6):
            reason = "model_value_replaced_with_snapshot_value"
        grounded.append(
            replace(
                item,
                signal=canonical_signal,
                value=actual,
                dependency=dependency,
                grounded=True,
                actual_value=actual,
                snapshot_timestamp=snapshot.observed_at,
                replacement_reason=reason,
            )
        )

    updated = replace(diagnosis, evidence=grounded, rejected_evidence=rejected)
    return GroundingResult(updated, grounded, rejected)
