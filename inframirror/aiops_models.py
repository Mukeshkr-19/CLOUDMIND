# aiops_models.py – Shared dataclasses and validation for AIOps subsystem
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid as _uuid


ALLOWED_SERVICES = {"frontend", "api", "database", "cache", "auth"}
ALLOWED_ACTIONS = {"restart_service", "no_action"}
ALLOWED_RISKS = {"low", "medium", "high"}
ALLOWED_DEPENDENCIES = ALLOWED_SERVICES

# Bounds
MAX_INCIDENT_ID_LEN = 64
MAX_REASON_LEN = 512
MAX_SIGNAL_LEN = 64
MAX_INTERPRETATION_LEN = 256
MAX_EVIDENCE_ITEMS = 20
MAX_AFFECTED_SERVICES = 10
MAX_ACTIVE_ALERTS = 50
MAX_ALERT_LABELS = 20
MAX_STRING_FIELD_LEN = 512
MAX_RECORDS = 100

# Telemetry numeric bounds
CPU_MIN = 0.0
CPU_MAX = 100.0
LATENCY_MIN = 0.0
LATENCY_MAX = 60000.0  # 60 seconds in ms, generous upper bound
REQUEST_RATE_MIN = 0.0
REQUEST_RATE_MAX = 1_000_000.0  # practical upper bound
ERROR_RATIO_MIN = 0.0
ERROR_RATIO_MAX = 1.0
DEPENDENCY_LATENCY_MIN = 0.0
DEPENDENCY_LATENCY_MAX = 60000.0


def _bounded_str(value: str, name: str, max_len: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value:
        raise ValueError(f"{name} must not be empty")
    if len(value) > max_len:
        raise ValueError(f"{name} exceeds max length {max_len}")
    return value


def _is_finite(value: float | None) -> bool:
    if value is None:
        return True
    try:
        return not (math.isnan(value) or math.isinf(value))
    except TypeError:
        return False


def _finite(value: float | None, name: str = "value") -> float | None:
    if value is None:
        return None
    if not _is_finite(value):
        raise ValueError(f"{name} must be finite: {value!r}")
    return value


def _clip(value: float, low: float, high: float, name: str = "value") -> float:
    if not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"{name} must be finite")
    if value < low or value > high:
        raise ValueError(f"{name} must be between {low} and {high}: {value}")
    return float(value)


@dataclass(frozen=True)
class DependencyInfo:
    up: Optional[bool] = None
    latency_ms: Optional[float] = None

    def __post_init__(self) -> None:
        if self.latency_ms is not None:
            _clip(self.latency_ms, DEPENDENCY_LATENCY_MIN, DEPENDENCY_LATENCY_MAX, "latency_ms")

    def to_dict(self) -> Dict[str, Any]:
        return {"up": self.up, "latency_ms": self.latency_ms}


@dataclass(frozen=True)
class ServiceTelemetry:
    cpu_percent: Optional[float] = None
    latency_ms: Optional[float] = None
    request_rate: Optional[float] = None
    error_rate: Optional[float] = None
    available: Optional[bool] = None
    incident_active: Optional[bool] = None
    dependencies: Dict[str, DependencyInfo] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.cpu_percent is not None:
            _clip(self.cpu_percent, CPU_MIN, CPU_MAX, "cpu_percent")
        if self.latency_ms is not None:
            _clip(self.latency_ms, LATENCY_MIN, LATENCY_MAX, "latency_ms")
        if self.request_rate is not None:
            _clip(self.request_rate, REQUEST_RATE_MIN, REQUEST_RATE_MAX, "request_rate")
        if self.error_rate is not None:
            _clip(self.error_rate, ERROR_RATIO_MIN, ERROR_RATIO_MAX, "error_rate")
        for dep_name in self.dependencies:
            if dep_name not in ALLOWED_DEPENDENCIES:
                raise ValueError(f"Unknown dependency: {dep_name}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_percent": self.cpu_percent,
            "latency_ms": self.latency_ms,
            "request_rate": self.request_rate,
            "error_rate": self.error_rate,
            "available": self.available,
            "incident_active": self.incident_active,
            "dependencies": {k: v.to_dict() for k, v in self.dependencies.items()},
        }


@dataclass(frozen=True)
class Trigger:
    service: str
    alertname: str = "Unknown"
    reason: str = ""

    def __post_init__(self) -> None:
        if self.service not in ALLOWED_SERVICES:
            raise ValueError(f"Unknown trigger service: {self.service}")
        _bounded_str(self.alertname, "alertname", MAX_STRING_FIELD_LEN)
        if len(self.reason) > MAX_REASON_LEN:
            raise ValueError("reason exceeds max length")

    def to_dict(self) -> Dict[str, Any]:
        return {"service": self.service, "alertname": self.alertname, "reason": self.reason}


@dataclass(frozen=True)
class TelemetrySnapshot:
    incident_id: str
    observed_at: str
    trigger: Trigger
    services: Dict[str, ServiceTelemetry]
    active_alerts: List[Dict[str, Any]]
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        _bounded_str(self.incident_id, "incident_id", MAX_INCIDENT_ID_LEN)
        if not self.observed_at:
            raise ValueError("observed_at is required")
        try:
            datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"observed_at must be ISO-8601: {self.observed_at}") from exc
        if not isinstance(self.services, dict):
            raise ValueError("services must be a dict")
        for svc in self.services:
            if svc not in ALLOWED_SERVICES:
                raise ValueError(f"Unknown service in snapshot: {svc}")
        if len(self.active_alerts) > MAX_ACTIVE_ALERTS:
            raise ValueError(f"active_alerts exceeds max {MAX_ACTIVE_ALERTS}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "incident_id": self.incident_id,
            "observed_at": self.observed_at,
            "trigger": self.trigger.to_dict(),
            "services": {k: v.to_dict() for k, v in self.services.items()},
            "active_alerts": self.active_alerts,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TelemetrySnapshot:
        trigger = Trigger(**data["trigger"])
        services = {
            name: ServiceTelemetry(
                cpu_percent=s.get("cpu_percent"),
                latency_ms=s.get("latency_ms"),
                request_rate=s.get("request_rate"),
                error_rate=s.get("error_rate"),
                available=s.get("available"),
                incident_active=s.get("incident_active"),
                dependencies={
                    dep: DependencyInfo(**info)
                    for dep, info in s.get("dependencies", {}).items()
                },
            )
            for name, s in data.get("services", {}).items()
        }
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            incident_id=data["incident_id"],
            observed_at=data["observed_at"],
            trigger=trigger,
            services=services,
            active_alerts=data.get("active_alerts", []),
        )


@dataclass(frozen=True)
class EvidenceItem:
    service: str
    signal: str
    value: float
    interpretation: str

    def __post_init__(self) -> None:
        if self.service not in ALLOWED_SERVICES:
            raise ValueError(f"Unknown evidence service: {self.service}")
        _bounded_str(self.signal, "signal", MAX_SIGNAL_LEN)
        if not _is_finite(self.value):
            raise ValueError(f"Evidence value must be finite: {self.value}")
        _bounded_str(self.interpretation, "interpretation", MAX_INTERPRETATION_LEN)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service": self.service,
            "signal": self.signal,
            "value": self.value,
            "interpretation": self.interpretation,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvidenceItem:
        return cls(**data)


@dataclass(frozen=True)
class RecommendedAction:
    type: str
    target_service: Optional[str]
    reason: str

    def __post_init__(self) -> None:
        if self.type not in ALLOWED_ACTIONS:
            raise ValueError(f"Unknown action: {self.type}")
        if self.target_service is not None and self.target_service not in ALLOWED_SERVICES:
            raise ValueError(f"Unknown target service: {self.target_service}")
        if len(self.reason) > MAX_REASON_LEN:
            raise ValueError("reason exceeds max length")

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "target_service": self.target_service, "reason": self.reason}


@dataclass(frozen=True)
class StructuredDiagnosis:
    probable_cause_service: str
    probable_cause: str
    confidence: float
    affected_services: List[str]
    evidence: List[EvidenceItem]
    recommended_action: RecommendedAction
    risk: str
    source: str

    def __post_init__(self) -> None:
        if self.probable_cause_service not in ALLOWED_SERVICES:
            raise ValueError(f"Unknown service: {self.probable_cause_service}")
        _bounded_str(self.probable_cause, "probable_cause", MAX_REASON_LEN)
        if self.risk not in ALLOWED_RISKS:
            raise ValueError(f"Unknown risk: {self.risk}")
        _clip(self.confidence, 0.0, 1.0, "confidence")
        if len(self.affected_services) > MAX_AFFECTED_SERVICES:
            raise ValueError(f"affected_services exceeds max {MAX_AFFECTED_SERVICES}")
        for svc in self.affected_services:
            if svc not in ALLOWED_SERVICES:
                raise ValueError(f"Unknown affected service: {svc}")
        if len(self.evidence) > MAX_EVIDENCE_ITEMS:
            raise ValueError(f"evidence exceeds max {MAX_EVIDENCE_ITEMS}")
        if self.recommended_action.type == "restart_service" and not self.recommended_action.target_service:
            raise ValueError("restart_service requires a target_service")
        if self.recommended_action.type == "no_action" and self.recommended_action.target_service is not None:
            raise ValueError("no_action must not have a target_service")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "1.0",
            "probable_cause_service": self.probable_cause_service,
            "probable_cause": self.probable_cause,
            "confidence": self.confidence,
            "affected_services": self.affected_services,
            "evidence": [e.to_dict() for e in self.evidence],
            "recommended_action": self.recommended_action.to_dict(),
            "risk": self.risk,
            "source": self.source,
        }


@dataclass(frozen=True)
class PolicyDecision:
    approved: bool
    action: str
    target: Optional[str]
    mode: str
    reason: str
    confidence_threshold: float

    def __post_init__(self) -> None:
        if self.action not in ALLOWED_ACTIONS:
            raise ValueError(f"Unknown action: {self.action}")
        if self.mode not in {"recommend", "execute"}:
            raise ValueError(f"Invalid mode: {self.mode}")
        if self.action == "restart_service" and not self.target:
            raise ValueError("restart_service requires a target")
        if self.action == "no_action" and self.target is not None:
            raise ValueError("no_action must not have a target")
        if len(self.reason) > MAX_REASON_LEN:
            raise ValueError("reason exceeds max length")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "action": self.action,
            "target": self.target,
            "mode": self.mode,
            "reason": self.reason,
            "confidence_threshold": self.confidence_threshold,
        }


@dataclass(frozen=True)
class RecoveryResult:
    status: str
    details: str

    def __post_init__(self) -> None:
        if self.status not in {"recovered", "not_recovered", "inconclusive", "not_executed"}:
            raise ValueError(f"Invalid recovery status: {self.status}")
        if len(self.details) > MAX_REASON_LEN:
            raise ValueError("details exceeds max length")

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "details": self.details}


@dataclass(frozen=True)
class ExecutionResult:
    executed: bool
    target: Optional[str]
    details: str

    def __post_init__(self) -> None:
        if len(self.details) > MAX_REASON_LEN:
            raise ValueError("details exceeds max length")

    def to_dict(self) -> Dict[str, Any]:
        return {"executed": self.executed, "target": self.target, "details": self.details}


@dataclass(frozen=True)
class IncidentRecord:
    incident_id: str
    started_at: str
    completed_at: Optional[str]
    trigger: Dict[str, Any]
    snapshot: Dict[str, Any]
    diagnosis: Dict[str, Any]
    policy_decision: Dict[str, Any]
    execution_result: Dict[str, Any]
    recovery_result: Dict[str, Any]
    model_source: str
    errors: List[str]

    def __post_init__(self) -> None:
        if self.model_source not in {"gemini", "rules"}:
            raise ValueError(f"Unknown model_source: {self.model_source}")
        if len(self.errors) > MAX_EVIDENCE_ITEMS:
            raise ValueError("errors list exceeds max length")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "trigger": self.trigger,
            "snapshot": self.snapshot,
            "diagnosis": self.diagnosis,
            "policy_decision": self.policy_decision,
            "execution_result": self.execution_result,
            "recovery_result": self.recovery_result,
            "model_source": self.model_source,
            "errors": self.errors,
        }

    @classmethod
    def from_diagnosis(
        cls,
        snapshot: TelemetrySnapshot,
        diagnosis: StructuredDiagnosis,
        policy_decision: PolicyDecision,
        execution_result: ExecutionResult,
        recovery_result: RecoveryResult,
        completed_at: Optional[str] = None,
    ) -> IncidentRecord:
        return cls(
            incident_id=snapshot.incident_id,
            started_at=snapshot.observed_at,
            completed_at=completed_at,
            trigger=snapshot.trigger.to_dict(),
            snapshot=snapshot.to_dict(),
            diagnosis=diagnosis.to_dict(),
            policy_decision=policy_decision.to_dict(),
            execution_result=execution_result.to_dict(),
            recovery_result=recovery_result.to_dict(),
            model_source=diagnosis.source,
            errors=[],
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_incident_id() -> str:
    return str(_uuid.uuid4())
