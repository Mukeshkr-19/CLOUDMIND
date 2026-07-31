# incident_intelligence.py – LLM-driven incident intelligence with safe rules fallback
from __future__ import annotations

import json
import math
import os
from typing import Any, Callable, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

try:
    from .aiops_models import (
        ALLOWED_ACTIONS,
        ALLOWED_SERVICES,
        ALLOWED_RISKS,
        ALLOWED_DEPENDENCIES,
        EvidenceItem,
        RecommendedAction,
        ServiceTelemetry,
        StructuredDiagnosis,
        TelemetrySnapshot,
    )
    from . import evidence_grounding, gemini_client
except ImportError:
    from aiops_models import (
        ALLOWED_ACTIONS,
        ALLOWED_SERVICES,
        ALLOWED_RISKS,
        ALLOWED_DEPENDENCIES,
        EvidenceItem,
        RecommendedAction,
        ServiceTelemetry,
        StructuredDiagnosis,
        TelemetrySnapshot,
    )
    import evidence_grounding
    import gemini_client


GEMINI_URL = gemini_client.endpoint()
ERROR_RATIO_THRESHOLD = float(os.getenv("AIOPS_ERROR_RATIO_THRESHOLD", "0.10"))
MAX_OUTPUT_TOKENS = 800
REQUEST_TIMEOUT = 8.0
MAX_PROMPT_LEN = 16000
MAX_RESPONSE_CHARS = 16000

DIAGNOSIS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "string", "enum": ["1.0"]},
        "probable_cause_service": {"type": "string", "enum": sorted(ALLOWED_SERVICES)},
        "probable_cause": {"type": "string", "maxLength": 512},
        "model_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "affected_services": {
            "type": "array",
            "maxItems": 10,
            "items": {"type": "string", "enum": sorted(ALLOWED_SERVICES)},
        },
        "evidence": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "service": {"type": "string", "enum": sorted(ALLOWED_SERVICES)},
                    "signal": {"type": "string", "enum": sorted(evidence_grounding.SUPPORTED_SIGNALS)},
                    "dependency": {"type": ["string", "null"], "enum": sorted(ALLOWED_SERVICES) + [None]},
                    "value": {"type": "number"},
                    "interpretation": {"type": "string", "maxLength": 256},
                },
                "required": ["service", "signal", "dependency", "value", "interpretation"],
            },
        },
        "recommended_action": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
                "target_service": {"type": ["string", "null"], "enum": sorted(ALLOWED_SERVICES) + [None]},
                "reason": {"type": "string", "maxLength": 512},
            },
            "required": ["type", "target_service", "reason"],
        },
        "risk": {"type": "string", "enum": sorted(ALLOWED_RISKS)},
    },
    "required": [
        "schema_version", "probable_cause_service", "probable_cause",
        "model_confidence", "affected_services", "evidence",
        "recommended_action", "risk",
    ],
}


def _clean_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _call_gemini(prompt: str, api_key: str, timeout: float = REQUEST_TIMEOUT) -> Optional[str]:
    return gemini_client.call_gemini_text(
        prompt[:MAX_PROMPT_LEN],
        api_key,
        timeout=timeout,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        response_schema=DIAGNOSIS_SCHEMA,
    )


def _format_value(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    return f"{value:.4f}"


def _snapshot_to_prompt_text(snapshot: TelemetrySnapshot) -> str:
    lines: List[str] = []
    for name in sorted(snapshot.services.keys()):
        svc = snapshot.services[name]
        lines.append(f"Service: {name}")
        lines.append(f"  cpu_percent: {_format_value(svc.cpu_percent)}")
        lines.append(f"  latency_ms: {_format_value(svc.latency_ms)}")
        if svc.request_rate is not None:
            lines.append(f"  request_rate: {svc.request_rate:.4f}")
        if svc.error_rate is not None:
            lines.append(f"  error_rate: {svc.error_rate:.4f}")
        if svc.available is not None:
            lines.append(f"  available: {svc.available}")
        if svc.incident_active is not None:
            lines.append(f"  incident_active: {svc.incident_active}")
        if svc.dependencies:
            for dep in sorted(svc.dependencies.keys()):
                info = svc.dependencies[dep]
                lines.append(f"  dependency {dep}: up={info.up}, latency_ms={_format_value(info.latency_ms)}")
    return "\n".join(lines)


def _alerts_to_prompt_text(snapshot: TelemetrySnapshot) -> str:
    if not snapshot.active_alerts:
        return "None"
    lines: List[str] = []
    for alert in snapshot.active_alerts[:20]:
        labels = alert.get("labels", {})
        alertname = labels.get("alertname", "Unknown")
        service = labels.get("service", "unknown")
        lines.append(f"- {alertname} (service={service})")
    return "\n".join(lines)


def _build_prompt(snapshot: TelemetrySnapshot) -> str:
    allowed = ", ".join(sorted(ALLOWED_SERVICES))
    return f"""You are the CloudMind AIOps intelligence engine. Analyze the cross-service telemetry snapshot and produce a structured JSON diagnosis.

Allowed services: {allowed}.
Allowed actions: restart_service, no_action.
Allowed risks: low, medium, high.

Telemetry:
{_snapshot_to_prompt_text(snapshot)}

Active firing alerts:
{_alerts_to_prompt_text(snapshot)}

Trigger: {snapshot.trigger.service} - {snapshot.trigger.alertname}: {snapshot.trigger.reason}

Return exactly one JSON object with no surrounding text. Use this schema:
{{
  "schema_version": "1.0",
  "probable_cause_service": "<one of allowed services>",
  "probable_cause": "<concise probable cause grounded in the telemetry>",
  "model_confidence": <advisory float 0.0-1.0; not a calibrated probability>,
  "affected_services": ["<service>", ...],
  "evidence": [
    {{
      "service": "<service>",
      "signal": "<metric name>",
      "dependency": "<dependency service or null>",
      "value": <numeric>,
      "interpretation": "<concise interpretation>"
    }}
  ],
  "recommended_action": {{
    "type": "<restart_service or no_action>",
    "target_service": "<service or null>",
    "reason": "<reason>"
  }},
  "risk": "<low, medium, high>"
}}

Rules:
- If a dependency is unavailable or high-latency and the API is impacted, target the dependency for restart_service.
- If a service is directly overloaded (high CPU, high latency, incident_active true, or high error rate) with no unavailable dependency, target that service.
- If the situation is a transient or non-critical spike, use no_action with null target_service and low risk.
- Only use restart_service if risk is low and confidence is high.
- Do not invent metrics, shell commands, container names, URLs, or action names.
"""


def _is_finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))  # type: ignore
    except (TypeError, ValueError):
        return False


def _validate_and_build_diagnosis(raw: Any, source: str) -> StructuredDiagnosis:
    if not isinstance(raw, dict):
        raise ValueError("Diagnosis is not a dict")

    probable_cause_service = raw.get("probable_cause_service")
    if probable_cause_service not in ALLOWED_SERVICES:
        raise ValueError(f"Unknown probable_cause_service: {probable_cause_service}")

    action_data = raw.get("recommended_action", {})
    action_type = action_data.get("type")
    if action_type not in ALLOWED_ACTIONS:
        raise ValueError(f"Unknown action type: {action_type}")

    target_service = action_data.get("target_service")
    if action_type == "restart_service" and not target_service:
        raise ValueError("restart_service requires target_service")
    if action_type == "no_action" and target_service is not None:
        raise ValueError("no_action must not have target_service")
    if target_service is not None and target_service not in ALLOWED_SERVICES:
        raise ValueError(f"Unknown target_service: {target_service}")

    risk = raw.get("risk")
    if risk not in ALLOWED_RISKS:
        raise ValueError(f"Unknown risk: {risk}")

    confidence = raw.get("model_confidence", raw.get("confidence"))
    if not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be numeric")
    if not _is_finite(confidence) or confidence < 0.0 or confidence > 1.0:
        raise ValueError(f"confidence out of range: {confidence}")

    affected_services = raw.get("affected_services", [])
    if not isinstance(affected_services, list):
        raise ValueError("affected_services must be a list")
    for svc in affected_services:
        if svc not in ALLOWED_SERVICES:
            raise ValueError(f"Unknown affected service: {svc}")

    evidence_raw = raw.get("evidence", [])
    if not isinstance(evidence_raw, list):
        raise ValueError("evidence must be a list")
    evidence: List[EvidenceItem] = []
    for idx, ev in enumerate(evidence_raw):
        if not isinstance(ev, dict):
            raise ValueError(f"Evidence item {idx} is not a dict")
        svc = ev.get("service")
        if svc not in ALLOWED_SERVICES:
            raise ValueError(f"Unknown evidence service: {svc}")
        signal = ev.get("signal")
        if not isinstance(signal, str) or not signal:
            raise ValueError(f"Invalid evidence signal: {signal}")
        value = ev.get("value")
        if not _is_finite(value):
            raise ValueError(f"Evidence value must be finite: {value}")
        interpretation = ev.get("interpretation")
        if not isinstance(interpretation, str) or not interpretation:
            raise ValueError("Evidence interpretation must be a non-empty string")
        evidence.append(
            EvidenceItem(
                service=svc,
                signal=signal,
                value=float(value),
                interpretation=interpretation,
                dependency=ev.get("dependency"),
            )
        )

    probable_cause = raw.get("probable_cause")
    if not isinstance(probable_cause, str) or not probable_cause:
        raise ValueError("probable_cause must be a non-empty string")

    return StructuredDiagnosis(
        probable_cause_service=probable_cause_service,
        probable_cause=probable_cause,
        confidence=float(confidence),
        affected_services=list(affected_services),
        evidence=evidence,
        recommended_action=RecommendedAction(
            type=action_type,
            target_service=target_service,
            reason=action_data.get("reason", ""),
        ),
        risk=risk,
        source=source,
    )


def _service_overload_score(svc: ServiceTelemetry, cpu_threshold: float, lat_threshold: float, err_ratio_threshold: float) -> float:
    score = 0.0
    if svc.cpu_percent is not None and svc.cpu_percent >= cpu_threshold:
        score += svc.cpu_percent - cpu_threshold
    if svc.latency_ms is not None and svc.latency_ms >= lat_threshold:
        score += (svc.latency_ms - lat_threshold) / 100.0
    if svc.error_rate is not None and svc.error_rate >= err_ratio_threshold:
        score += svc.error_rate * 100.0
    if svc.incident_active is True:
        score += 50.0
    if svc.available is False:
        score += 100.0
    return score


def _find_dependency_caused_api_impact(snapshot: TelemetrySnapshot, lat_threshold: float, err_ratio_threshold: float) -> Optional[str]:
    api_svc = snapshot.services.get("api")
    if api_svc is None:
        return None
    api_affected = (
        (api_svc.latency_ms is not None and api_svc.latency_ms >= lat_threshold)
        or (api_svc.cpu_percent is not None and api_svc.cpu_percent >= 85.0)
        or (api_svc.error_rate is not None and api_svc.error_rate >= err_ratio_threshold)
        or api_svc.incident_active is True
        or api_svc.available is False
    )
    if not api_affected:
        return None
    # Prefer unavailable dependencies first, then high-latency
    for dep_name in sorted(api_svc.dependencies.keys()):
        dep = api_svc.dependencies[dep_name]
        if dep.up is False and dep_name in ALLOWED_DEPENDENCIES:
            return dep_name
    for dep_name in sorted(api_svc.dependencies.keys()):
        dep = api_svc.dependencies[dep_name]
        if dep.latency_ms is not None and dep.latency_ms >= lat_threshold and dep_name in ALLOWED_DEPENDENCIES:
            return dep_name
    return None


def _find_overloaded_service(snapshot: TelemetrySnapshot, cpu_threshold: float, lat_threshold: float, err_ratio_threshold: float) -> Optional[str]:
    candidates: List[tuple] = []
    for name in sorted(snapshot.services.keys()):
        svc = snapshot.services[name]
        score = _service_overload_score(svc, cpu_threshold, lat_threshold, err_ratio_threshold)
        if score > 0:
            candidates.append((score, name))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][1]


def rules_based_diagnosis(
    snapshot: TelemetrySnapshot,
    cpu_threshold: float = 85.0,
    lat_threshold: float = 350.0,
    err_ratio_threshold: float = 0.10,
) -> StructuredDiagnosis:
    # 1. Dependency causing API impact (prefer dependency cause before downstream API symptoms)
    dep = _find_dependency_caused_api_impact(snapshot, lat_threshold, err_ratio_threshold)
    if dep:
        api_svc = snapshot.services["api"]
        dep_info = api_svc.dependencies[dep]
        evidence_value = 0.0
        signal = "dependency_up"
        interpretation = f"Dependency {dep} is unavailable"
        if dep_info.up is not False and dep_info.latency_ms is not None:
            evidence_value = dep_info.latency_ms
            signal = "dependency_latency_ms"
            interpretation = f"Dependency {dep} latency is high"
        elif dep_info.up is False:
            evidence_value = 0.0
        return StructuredDiagnosis(
            probable_cause_service=dep,
            probable_cause=f"Dependency {dep} is unhealthy and impacting API",
            confidence=0.85,
            affected_services=[dep, "api"],
            evidence=[
                EvidenceItem(
                    service="api",
                    signal=signal,
                    value=evidence_value,
                    interpretation=interpretation,
                )
            ],
            recommended_action=RecommendedAction(
                type="restart_service",
                target_service=dep,
                reason=f"{dep} is the earliest unhealthy dependency affecting API",
            ),
            risk="low",
            source="rules",
        )

    # 2. Directly overloaded service (deterministic severity-based selection)
    overloaded = _find_overloaded_service(snapshot, cpu_threshold, lat_threshold, err_ratio_threshold)
    if overloaded:
        svc = snapshot.services[overloaded]
        signal = "incident_active"
        value = 1.0
        interpretation = f"{overloaded} has an active incident flag"
        if svc.incident_active is not True:
            if svc.cpu_percent is not None and svc.cpu_percent >= cpu_threshold:
                signal = "cpu_percent"
                value = svc.cpu_percent
                interpretation = f"{overloaded} CPU is above threshold"
            elif svc.latency_ms is not None and svc.latency_ms >= lat_threshold:
                signal = "latency_ms"
                value = svc.latency_ms
                interpretation = f"{overloaded} latency is above threshold"
            elif svc.error_rate is not None and svc.error_rate >= err_ratio_threshold:
                signal = "error_rate"
                value = svc.error_rate
                interpretation = f"{overloaded} error rate is at or above threshold {err_ratio_threshold}"
            else:
                signal = "available"
                value = 0.0
                interpretation = f"{overloaded} availability is false"
        return StructuredDiagnosis(
            probable_cause_service=overloaded,
            probable_cause=f"{overloaded} is directly overloaded",
            confidence=0.80,
            affected_services=[overloaded],
            evidence=[
                EvidenceItem(
                    service=overloaded,
                    signal=signal,
                    value=value,
                    interpretation=interpretation,
                )
            ],
            recommended_action=RecommendedAction(
                type="restart_service",
                target_service=overloaded,
                reason=f"{overloaded} is directly overloaded and is the probable cause",
            ),
            risk="low",
            source="rules",
        )

    # 3. Transient / non-critical spike
    trigger = snapshot.trigger.service
    return StructuredDiagnosis(
        probable_cause_service=trigger,
        probable_cause="No critical or dependency-driven abnormality detected; transient spike",
        confidence=0.70,
        affected_services=[trigger],
        evidence=[
            EvidenceItem(
                service=trigger,
                signal="status",
                value=0.0,
                interpretation="Telemetry does not indicate a critical sustained failure",
            )
        ],
        recommended_action=RecommendedAction(
            type="no_action",
            target_service=None,
            reason="Transient or non-critical spike; no safe automatic action indicated",
        ),
        risk="low",
        source="rules",
    )


def diagnose(
    snapshot: TelemetrySnapshot,
    api_key: Optional[str] = None,
    call_llm: Callable[[str, str], Optional[str]] = _call_gemini,
    timeout: float = REQUEST_TIMEOUT,
    err_ratio_threshold: float = ERROR_RATIO_THRESHOLD,
) -> StructuredDiagnosis:
    key = (api_key or os.getenv("GEMINI_API_KEY", "") or "").strip()

    if key:
        prompt = _build_prompt(snapshot)
        try:
            raw_text = call_llm(prompt, key, timeout=timeout)
        except Exception:
            raw_text = None
        if raw_text:
            try:
                cleaned = _clean_json(raw_text[:MAX_RESPONSE_CHARS])
                parsed = json.loads(cleaned)
                diagnosis = _validate_and_build_diagnosis(parsed, source="gemini")
                return evidence_grounding.ground_diagnosis(snapshot, diagnosis).diagnosis
            except Exception:
                raw_text = None

    diagnosis = rules_based_diagnosis(snapshot, err_ratio_threshold=err_ratio_threshold)
    return evidence_grounding.ground_diagnosis(snapshot, diagnosis).diagnosis
