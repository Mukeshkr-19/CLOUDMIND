# telemetry_collector.py – Cross-service telemetry snapshot collector
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover - tests mock this
    requests = None  # type: ignore

try:
    from .aiops_models import (
        ALLOWED_SERVICES,
        DependencyInfo,
        ServiceTelemetry,
        TelemetrySnapshot,
        Trigger,
        new_incident_id,
        utc_now_iso,
    )
except ImportError:
    from aiops_models import (
        ALLOWED_SERVICES,
        DependencyInfo,
        ServiceTelemetry,
        TelemetrySnapshot,
        Trigger,
        new_incident_id,
        utc_now_iso,
    )


PROM_URL = os.getenv("PROM_URL", "http://prometheus:9090")


def _extract_values(data: List[Dict[str, Any]]) -> List[float]:
    """Extract numeric sample values from a Prometheus instant query result."""
    values: List[float] = []
    for item in data:
        value = item.get("value")
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                values.append(float(value[1]))
            except (TypeError, ValueError):
                continue
    return values


def _max_value(data: List[Dict[str, Any]]) -> Optional[float]:
    values = _extract_values(data)
    return max(values) if values else None


def _sum_value(data: List[Dict[str, Any]]) -> Optional[float]:
    values = _extract_values(data)
    return sum(values) if values else None


def _any_true(data: List[Dict[str, Any]]) -> Optional[bool]:
    """True if any replica reports a non-zero value; unknown if no data."""
    values = _extract_values(data)
    if not values:
        return None
    return any(v != 0.0 for v in values)


def _all_up(data: List[Dict[str, Any]]) -> Optional[bool]:
    """True only if all returned replicas are up; false if any is down."""
    values = _extract_values(data)
    if not values:
        return None
    return all(v != 0.0 for v in values)


def _query_instant(expr: str, prom_url: str = PROM_URL, timeout: float = 4.0) -> List[Dict[str, Any]]:
    if requests is None:
        return []
    try:
        resp = requests.get(
            f"{prom_url}/api/v1/query",
            params={"query": expr},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {}).get("result", [])
        return data
    except Exception:
        return []


def _collect_service_telemetry(service: str, prom_url: str = PROM_URL) -> ServiceTelemetry:
    cpu_result = _query_instant(f'service_cpu_percent{{service="{service}"}}', prom_url)
    lat_result = _query_instant(f'service_latency_ms{{service="{service}"}}', prom_url)
    req_result = _query_instant(
        f'rate(service_requests_total{{service="{service}"}}[1m])', prom_url
    )
    err_result = _query_instant(
        f'rate(service_request_errors_total{{service="{service}"}}[1m])', prom_url
    )
    up_result = _query_instant(f'up{{job="{service}"}}', prom_url)
    incident_result = _query_instant(
        f'service_incident_active{{service="{service}"}}', prom_url
    )
    # attempts is required to calculate error ratio
    attempts_result = _query_instant(
        f'rate(service_request_attempts_total{{service="{service}"}}[1m])', prom_url
    )

    cpu = _max_value(cpu_result)
    latency = _max_value(lat_result)
    request_rate = _sum_value(req_result)
    available = _all_up(up_result)
    incident_active = _any_true(incident_result)

    # error_rate = sum(errors) / sum(attempts) when both are present; otherwise unknown
    error_count = _sum_value(err_result)
    attempts = _sum_value(attempts_result)
    error_rate: Optional[float] = None
    if error_count is not None and attempts is not None and attempts > 0.0:
        error_rate = min(error_count / attempts, 1.0)

    dependencies: Dict[str, DependencyInfo] = {}
    dep_up_data = _query_instant(
        f'service_dependency_up{{service="{service}"}}', prom_url
    )
    dep_lat_data = _query_instant(
        f'service_dependency_latency_ms{{service="{service}"}}', prom_url
    )

    # Aggregate dependency_up per dependency: false if any replica reports down.
    dep_up_state: Dict[str, bool] = {}
    for item in dep_up_data:
        labels = item.get("metric", {})
        dependency = labels.get("dependency")
        if dependency is None:
            continue
        values = _extract_values([item])
        if not values:
            continue
        if any(v == 0.0 for v in values):
            dep_up_state[dependency] = False
        elif dependency not in dep_up_state:
            dep_up_state[dependency] = True

    # Aggregate dependency latency per dependency: max across replicas.
    dep_lat_map: Dict[str, float] = {}
    for item in dep_lat_data:
        labels = item.get("metric", {})
        dependency = labels.get("dependency")
        if dependency is None:
            continue
        values = _extract_values([item])
        if not values:
            continue
        max_lat = max(values)
        if dependency not in dep_lat_map or max_lat > dep_lat_map[dependency]:
            dep_lat_map[dependency] = max_lat

    for dep in ALLOWED_SERVICES:
        if dep == service:
            continue
        up = dep_up_state.get(dep)
        lat = dep_lat_map.get(dep)
        if up is not None or lat is not None:
            dependencies[dep] = DependencyInfo(up=up, latency_ms=lat)

    return ServiceTelemetry(
        cpu_percent=cpu,
        latency_ms=latency,
        request_rate=request_rate,
        error_rate=error_rate,
        available=available,
        incident_active=incident_active,
        dependencies=dependencies,
    )


def _collect_active_alerts(prom_url: str = PROM_URL) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    if requests is None:
        return alerts
    try:
        resp = requests.get(f"{prom_url}/api/v1/alerts", timeout=4.0)
        resp.raise_for_status()
        data = resp.json().get("data", {}).get("alerts", [])
        for alert in data:
            if alert.get("state") == "firing":
                alerts.append({
                    "labels": alert.get("labels", {}),
                    "annotations": alert.get("annotations", {}),
                })
    except Exception:
        pass
    return alerts


def collect_telemetry_snapshot(
    trigger_service: str,
    alertname: str = "Unknown",
    reason: str = "",
    prom_url: str = PROM_URL,
    incident_id: Optional[str] = None,
    observed_at: Optional[str] = None,
) -> TelemetrySnapshot:
    if trigger_service not in ALLOWED_SERVICES:
        raise ValueError(f"Unknown service: {trigger_service}")

    services: Dict[str, ServiceTelemetry] = {}
    for service in ALLOWED_SERVICES:
        services[service] = _collect_service_telemetry(service, prom_url)

    active_alerts = _collect_active_alerts(prom_url)

    trigger = Trigger(service=trigger_service, alertname=alertname, reason=reason)

    return TelemetrySnapshot(
        incident_id=incident_id or new_incident_id(),
        observed_at=observed_at or utc_now_iso(),
        trigger=trigger,
        services=services,
        active_alerts=active_alerts,
    )
