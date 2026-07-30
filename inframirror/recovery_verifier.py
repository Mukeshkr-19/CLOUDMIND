# recovery_verifier.py – Bounded recovery verification with testable injection
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional

try:
    from .aiops_models import RecoveryResult, ALLOWED_SERVICES
except ImportError:
    from aiops_models import RecoveryResult, ALLOWED_SERVICES


CPU_HARD = float(os.getenv("CPU_HARD_THRESHOLD", "85"))
LAT_PAIN_MS = float(os.getenv("LAT_PAIN_MS", "350"))


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


AIOPS_RECOVERY_TIMEOUT_SEC = _env_float("AIOPS_RECOVERY_TIMEOUT_SEC", 45.0, 1.0, 600.0)
AIOPS_RECOVERY_POLL_SEC = _env_float("AIOPS_RECOVERY_POLL_SEC", 3.0, 0.5, 120.0)
AIOPS_REQUIRED_HEALTHY_SAMPLES = _env_int("AIOPS_REQUIRED_HEALTHY_SAMPLES", 2, 1, 20)


def _default_sleep(seconds: float) -> None:
    time.sleep(seconds)


def _extract_values(data: Any) -> List[float]:
    """Extract numeric sample values from a Prometheus instant query result."""
    values: List[float] = []
    if not isinstance(data, list):
        return values
    for item in data:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                values.append(float(item[1]))
            except (TypeError, ValueError):
                continue
        elif isinstance(item, dict):
            value = item.get("value")
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                try:
                    values.append(float(value[1]))
                except (TypeError, ValueError):
                    continue
    return values


def _max_value(data: Any) -> Optional[float]:
    values = _extract_values(data)
    return max(values) if values else None


def _all_up(data: Any) -> Optional[float]:
    """Return 1.0 if all returned replicas are up, 0.0 if any is down, None if no data."""
    values = _extract_values(data)
    if not values:
        return None
    return 0.0 if any(v == 0.0 for v in values) else 1.0


def verify_recovery(
    target: str,
    cpu_threshold: float = CPU_HARD,
    lat_threshold: float = LAT_PAIN_MS,
    max_attempts: int = int(AIOPS_RECOVERY_TIMEOUT_SEC / AIOPS_RECOVERY_POLL_SEC),
    interval_seconds: float = AIOPS_RECOVERY_POLL_SEC,
    required_consecutive: int = AIOPS_REQUIRED_HEALTHY_SAMPLES,
    sleep_func: Callable[[float], None] = _default_sleep,
    query_func: Callable[[str], Any] = lambda q: [],
    dependency: Optional[str] = None,
    dependency_probe_func: Optional[Callable[[str], bool]] = None,
) -> RecoveryResult:
    if not target:
        return RecoveryResult(
            status="not_executed",
            details="No target service was provided for recovery verification",
        )

    # Normalize configuration safely
    if max_attempts < 1:
        max_attempts = 1
    if required_consecutive < 1:
        required_consecutive = 1
    total_timeout = max(max_attempts * interval_seconds, interval_seconds)
    if interval_seconds <= 0 or interval_seconds > total_timeout:
        interval_seconds = max(0.1, total_timeout / 2)

    if dependency is not None and dependency not in ALLOWED_SERVICES:
        return RecoveryResult(
            status="inconclusive",
            details=f"Dependency '{dependency}' is not an allowed service",
        )

    samples: List[Dict[str, Any]] = []
    consecutive = 0
    for attempt in range(max_attempts):
        if attempt > 0:
            sleep_func(interval_seconds)

        up_result = query_func(f'up{{job="{target}"}}')
        cpu_result = query_func(f'service_cpu_percent{{service="{target}"}}')
        lat_result = query_func(f'service_latency_ms{{service="{target}"}}')

        up_value = _all_up(up_result)
        cpu_value = _max_value(cpu_result)
        lat_value = _max_value(lat_result)

        up_ok = up_value is not None and up_value != 0.0
        cpu_ok = cpu_value is not None and cpu_value < cpu_threshold
        lat_ok = lat_value is not None and lat_value < lat_threshold

        sample: Dict[str, Any] = {
            "target": target,
            "up": up_value,
            "cpu": cpu_value,
            "latency": lat_value,
            "up_ok": up_ok,
            "cpu_ok": cpu_ok,
            "lat_ok": lat_ok,
        }

        healthy = up_ok and cpu_ok and lat_ok

        # For dependency-caused incidents, also verify the API's view of that dependency.
        if dependency:
            sample["dependency"] = dependency

            # Active probe: ask the API directly whether the dependency is up.
            # Only the exact boolean True is considered healthy. Any other value
            # (False, None, strings, numbers, dicts, etc.) or an exception makes
            # this sample unhealthy. active_probe_ok is always a real bool.
            active_probe_ok: bool = False
            if dependency_probe_func is not None:
                active_probe_result_valid: bool = True
                try:
                    probe_result = dependency_probe_func(dependency)
                except Exception:
                    active_probe_result_valid = False
                    active_probe_ok = False
                else:
                    active_probe_result_valid = isinstance(probe_result, bool)
                    active_probe_ok = probe_result is True
                sample["active_probe_ok"] = active_probe_ok
                sample["active_probe_result_valid"] = active_probe_result_valid

            dep_query = f'service_dependency_up{{service="api",dependency="{dependency}"}}'
            dep_up_result = query_func(dep_query)
            dep_value = _all_up(dep_up_result)
            dep_metric_ok = dep_value is not None and dep_value != 0.0
            sample["dependency_up"] = dep_value
            sample["dependency_metric_ok"] = dep_metric_ok

            # A provided active probe must also succeed in addition to the metric.
            if dependency_probe_func is not None:
                healthy = healthy and active_probe_ok and dep_metric_ok
            else:
                healthy = healthy and dep_metric_ok

        samples.append(sample)

        if healthy:
            consecutive += 1
            if consecutive >= required_consecutive:
                return RecoveryResult(
                    status="recovered",
                    details=f"Target {target} recovered after {attempt + 1} samples",
                )
        else:
            consecutive = 0

    summary = {
        "target": target,
        "max_attempts": max_attempts,
        "required_consecutive": required_consecutive,
        "last_sample": samples[-1] if samples else None,
        "sample_count": len(samples),
    }
    return RecoveryResult(
        status="not_recovered",
        details=f"Target {target} did not satisfy recovery conditions: {summary}",
    )
