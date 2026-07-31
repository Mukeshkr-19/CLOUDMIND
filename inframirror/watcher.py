# watcher.py – Phase 5.5 Smart Diagnostics + Auto-Healing & Dialogue Engine
from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import docker
import requests
from flask import Flask, Response, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from prometheus_client.parser import text_string_to_metric_families

try:
    from . import aiops_metrics, aiops_models, llm_engine, telemetry_collector, incident_intelligence, policy_engine, incident_store, recovery_verifier, remediation_guard
except ImportError:
    import aiops_metrics
    import aiops_models
    import llm_engine
    import telemetry_collector
    import incident_intelligence
    import policy_engine
    import incident_store
    import recovery_verifier
    import remediation_guard

# ----------------------------
# Config (env overrides)
# ----------------------------
SERVICES = {
    "frontend": "http://frontend:5050/metrics",
    "api":      "http://api:5051/metrics",
    "database": "http://database:5052/metrics",
    "cache":    "http://cache:5053/metrics",
    "auth":     "http://auth:5054/metrics",
}

SERVICE_BY_INSTANCE = {
    "frontend:5050": "frontend",
    "api:5051": "api",
    "database:5052": "database",
    "cache:5053": "cache",
    "auth:5054": "auth",
}

PROM_URL  = os.getenv("PROM_URL", "http://prometheus:9090")
HEALING   = os.getenv("HEALING_ENABLED", "false").lower() == "true"
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
WHISPER_TOKEN = os.getenv("WHISPER_TOKEN", "").strip()

# AIOps config
AIOPS_ENABLED = os.getenv("AIOPS_ENABLED", "false").lower() == "true"
_raw_aiops_mode = os.getenv("AIOPS_EXECUTION_MODE", "recommend").lower()
if _raw_aiops_mode not in {"recommend", "execute"}:
    _raw_aiops_mode = "recommend"
# Effective execution mode may be execute only when healing is enabled.
AIOPS_EXECUTION_MODE = "execute" if _raw_aiops_mode == "execute" and HEALING else "recommend"


def _effective_execution_mode(monotonic_func: Optional[Callable[[], float]] = None) -> str:
    """Return effective execution mode honoring grace period, HEALING and AIOPS_EXECUTION_MODE.

    During the startup grace window, mode is always 'recommend' so residual
    rolling telemetry cannot trigger immediate Docker restarts.
    """
    now = (monotonic_func or _monotonic_time_func)()
    if now - _startup_monotonic < AIOPS_EXECUTION_GRACE_SEC:
        return "recommend"
    if AIOPS_EXECUTION_MODE == "execute" and HEALING:
        return "execute"
    return "recommend"


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
    return value


AIOPS_CONFIDENCE_THRESHOLD = _env_float("AIOPS_CONFIDENCE_THRESHOLD", 0.75, 0.0, 1.0)
AIOPS_RECOVERY_TIMEOUT_SEC = _env_float("AIOPS_RECOVERY_TIMEOUT_SEC", 45.0, 1.0, 600.0)
AIOPS_RECOVERY_POLL_SEC = _env_float("AIOPS_RECOVERY_POLL_SEC", 3.0, 0.5, 120.0)
AIOPS_REQUIRED_HEALTHY_SAMPLES = _env_int("AIOPS_REQUIRED_HEALTHY_SAMPLES", 2, 1, 20)

# heuristics
CPU_SOFT      = float(os.getenv("CPU_SOFT_THRESHOLD", "70"))
CPU_HARD      = float(os.getenv("CPU_HARD_THRESHOLD", "85"))
LAT_WARN_MS   = float(os.getenv("LAT_WARN_MS", "250"))
LAT_PAIN_MS   = float(os.getenv("LAT_PAIN_MS", "350"))
COOLDOWN_SEC  = int(os.getenv("HEALING_COOLDOWN_SEC", "150"))
INCIDENT_DIALOGUE_COOLDOWN_SEC = int(os.getenv("INCIDENT_DIALOGUE_COOLDOWN_SEC", "60"))
INCIDENT_DEDUP_SEC = int(os.getenv("INCIDENT_DEDUP_SEC", "300"))
AIOPS_MAX_WORKERS = _env_int("AIOPS_MAX_WORKERS", 5, 1, 50)
AIOPS_QUEUE_CAPACITY = _env_int("AIOPS_QUEUE_CAPACITY", 10, 0, 500)
ERROR_RATIO_THRESHOLD = _env_float("AIOPS_ERROR_RATIO_THRESHOLD", 0.10, 0.0, 1.0)
AIOPS_EXECUTION_GRACE_SEC = _env_int("AIOPS_EXECUTION_GRACE_SEC", 30, 0, 300)
AIOPS_PROBE_TIMEOUT_SEC = _env_float("AIOPS_PROBE_TIMEOUT_SEC", 2.0, 0.5, 10.0)
AIOPS_MAX_RESTARTS_PER_SERVICE_PER_HOUR = _env_int("AIOPS_MAX_RESTARTS_PER_SERVICE_PER_HOUR", 3, 1, 20)
AIOPS_MAX_FAILED_RECOVERIES = _env_int("AIOPS_MAX_FAILED_RECOVERIES", 2, 1, 20)
AIOPS_CIRCUIT_BREAKER_RESET_SEC = _env_int("AIOPS_CIRCUIT_BREAKER_RESET_SEC", 900, 30, 86400)

# Process startup monotonic anchor for execution grace window.
_startup_monotonic = time.monotonic()
# Injectable monotonic clock for tests.
_monotonic_time_func: Callable[[], float] = time.monotonic

client = None
_last_heal = {}  # service -> datetime
_last_dialogue = {}  # service -> datetime
_in_flight: Dict[str, datetime] = {}
_completed: Dict[str, datetime] = {}
_completed_info: Dict[str, Tuple[str, str, Optional[str], Optional[str], Optional[str]]] = {}
# Per-target remediation lease. Target -> lease start monotonic time.
_target_leases: Dict[str, float] = {}
state_lock = threading.Lock()
_aiops_executor: Optional[ThreadPoolExecutor] = None
_work_semaphore: Optional[threading.BoundedSemaphore] = None
_remediation_guard = remediation_guard.RemediationGuard(
    max_restarts_per_hour=AIOPS_MAX_RESTARTS_PER_SERVICE_PER_HOUR,
    max_failed_recoveries=AIOPS_MAX_FAILED_RECOVERIES,
    circuit_breaker_reset_sec=AIOPS_CIRCUIT_BREAKER_RESET_SEC,
)


def _get_semaphore() -> threading.BoundedSemaphore:
    global _work_semaphore
    if _work_semaphore is None:
        _work_semaphore = threading.BoundedSemaphore(AIOPS_MAX_WORKERS + AIOPS_QUEUE_CAPACITY)
    return _work_semaphore


def _get_executor() -> ThreadPoolExecutor:
    global _aiops_executor
    if _aiops_executor is None:
        _aiops_executor = ThreadPoolExecutor(max_workers=AIOPS_MAX_WORKERS, thread_name_prefix="aiops-")
    return _aiops_executor


def _shutdown_executor():
    global _aiops_executor
    if _aiops_executor is not None:
        _aiops_executor.shutdown(wait=False)
        _aiops_executor = None
    # Keep _work_semaphore alive so that any in-flight done callbacks can
    # release without raising on a freshly-created semaphore.


if not getattr(atexit, "_cloudmind_watcher_registered", False):
    atexit.register(_shutdown_executor)
    atexit._cloudmind_watcher_registered = True  # type: ignore[attr-defined]


def _docker_client():
    global client
    if client is None:
        client = docker.from_env()
    return client


def _current_webhook() -> str:
    return os.getenv("DISCORD_WEBHOOK_URL", "").strip()


def _authorized_request() -> bool:
    token = os.getenv("WHISPER_TOKEN", WHISPER_TOKEN).strip()
    if not token:
        return False
    auth = request.headers.get("Authorization", "")
    header_token = request.headers.get("X-CloudMind-Token", "")
    bearer = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    return token in {bearer, header_token}


def _max_value(series) -> Optional[float]:
    return max((value for _, value in series), default=None)


def _sum_value(series) -> Optional[float]:
    return sum((value for _, value in series), 0.0) if series else None


def _up_value(series) -> Optional[float]:
    """Conservative replica availability: 0.0 if any replica is down, 1.0 if all are up, None if no data."""
    if not series:
        return None
    values = [value for _, value in series]
    if not values:
        return None
    if any(v == 0.0 for v in values):
        return 0.0
    return 1.0


def _service_container(service: str):
    docker_client = _docker_client()
    containers = docker_client.containers.list(
        all=True,
        filters={"label": f"com.docker.compose.service={service}"}
    )
    if containers:
        return sorted(containers, key=lambda c: c.name)[0]

    name = f"cloudmind-{service}-1"
    try:
        return docker_client.containers.get(name)
    except Exception:
        return None


def _should_emit_dialogue(service: str, now: datetime) -> bool:
    with state_lock:
        last = _last_dialogue.get(service)
        if last and (now - last) < timedelta(seconds=INCIDENT_DIALOGUE_COOLDOWN_SEC):
            return False
        _last_dialogue[service] = now
        return True


def _send_discord_embed(payload: dict):
    webhook = _current_webhook()
    if not webhook:
        return
    try:
        requests.post(webhook, json=payload, timeout=3)
    except Exception as e:
        print(f"[❌] Discord Webhook error: {e}")


def _prom_query(expr: str):
    """Instant query to Prometheus; returns list of (labels, value) or []."""
    try:
        r = requests.get(f"{PROM_URL}/api/v1/query", params={"query": expr}, timeout=4)
        r.raise_for_status()
        data = r.json().get("data", {}).get("result", [])
        out = []
        for item in data:
            val = item.get("value")
            if isinstance(val, (list, tuple)) and len(val) == 2:
                try:
                    out.append((item.get("metric", {}), float(val[1])))
                except Exception:
                    continue
        return out
    except Exception:
        return []


def _maybe_heal(service: str, reason: str) -> bool:
    """Restart the container first; only report success after the restart call."""
    container = _service_container(service)
    if not container:
        print(f"[] No Docker container found for service={service}, skip healing")
        return False

    try:
        print(f"[💊] HEALING ACTION: Restarting container {container.name} ({reason})...")
        container.restart()
        print(f"[✅] Restart executed for {container.name}")
        return True
    except Exception as e:
        print(f"[❌] Healing failed for {service}: {e}")
        return False


def _send_recovered_notification(service: str, details: str):
    if not _current_webhook():
        return
    payload = {
        "embeds": [{
            "title": f"✅ [RECOVERY VERIFIED] SERVICE: {service.upper()}",
            "description": f"Recovery verification passed for `{service}`.",
            "color": 65280,
            "fields": [
                {"name": "Details", "value": details[:1024] or "No details", "inline": False},
            ],
            "footer": {"text": "SRE Auto-Remediation Engine | Recovery verified"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }
    _send_discord_embed(payload)


def _safe_signature(
    service: str,
    alertname: str,
    dependency: Optional[str] = None,
    fingerprint: Optional[str] = None,
    starts_at: Optional[str] = None,
) -> str:
    """Stable signature for incident deduplication."""
    key = (
        f"{service}:{alertname}:{dependency or ''}:"
        f"{fingerprint or ''}:{starts_at or ''}"
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _prune_dedup_maps(now: datetime) -> None:
    stale = [s for s, t in _completed.items() if (now - t).total_seconds() > INCIDENT_DEDUP_SEC]
    for s in stale:
        _completed.pop(s, None)
        _completed_info.pop(s, None)


def _try_acquire_event(
    service: str,
    alertname: str,
    dependency: Optional[str] = None,
    fingerprint: Optional[str] = None,
    starts_at: Optional[str] = None,
) -> bool:
    """Acquire an in-flight signature. Returns True if the caller should proceed."""
    now = datetime.now(timezone.utc)
    sig = _safe_signature(service, alertname, dependency, fingerprint, starts_at)
    with state_lock:
        _prune_dedup_maps(now)
        if sig in _in_flight:
            return False
        # A verified healthy transition removes the completed signature, so a
        # re-fire naturally proceeds. If it is still present, treat as duplicate.
        if sig in _completed:
            return False
        _in_flight[sig] = now
        return True


def _release_event(
    service: str,
    alertname: str,
    dependency: Optional[str] = None,
    fingerprint: Optional[str] = None,
    starts_at: Optional[str] = None,
    completed: bool = True,
):
    sig = _safe_signature(service, alertname, dependency, fingerprint, starts_at)
    with state_lock:
        _in_flight.pop(sig, None)
        if completed:
            _completed[sig] = datetime.now(timezone.utc)
            _completed_info[sig] = (service, alertname, dependency, fingerprint, starts_at)


def _record_healthy_transition(
    service: str,
    alertname: str,
    dependency: Optional[str] = None,
    fingerprint: Optional[str] = None,
    starts_at: Optional[str] = None,
):
    """Remove the completed signature so the same incident can re-fire."""
    sig = _safe_signature(service, alertname, dependency, fingerprint, starts_at)
    with state_lock:
        _completed.pop(sig, None)
        _completed_info.pop(sig, None)


def _record_service_healthy(service: str):
    """Clear all completed signatures for a service after a verified healthy transition."""
    with state_lock:
        for sig, (svc, alertname, dep, fp, sa) in list(_completed_info.items()):
            if svc == service:
                _completed.pop(sig, None)
                _completed_info.pop(sig, None)


def _is_duplicate_event(
    service: str,
    alertname: str,
    dependency: Optional[str] = None,
    fingerprint: Optional[str] = None,
    starts_at: Optional[str] = None,
) -> bool:
    return not _try_acquire_event(service, alertname, dependency, fingerprint, starts_at)


def _acquire_target_lease(target: str, now: datetime) -> bool:
    """Atomically reserve a target for remediation. Returns True if reserved."""
    now_mono = _monotonic_time_func()
    with state_lock:
        # Prune stale leases opportunistically (should not happen with finally).
        _prune_target_leases(now_mono)
        if target in _target_leases:
            return False
        # Also respect cooldown under the same lock so concurrent workflows see
        # the same cooldown state as the lease winner will.
        last = _last_heal.get(target)
        if last and (now - last).total_seconds() < COOLDOWN_SEC:
            return False
        _target_leases[target] = now_mono
        return True


def _release_target_lease(target: str) -> None:
    with state_lock:
        _target_leases.pop(target, None)


def _prune_target_leases(now_mono: float) -> None:
    # Defensive: leases should be released promptly; clear anything older than
    # a generous execution ceiling to prevent unbounded growth.
    cutoff = now_mono - max(AIOPS_RECOVERY_TIMEOUT_SEC + 60.0, 600.0)
    stale = [t for t, t0 in _target_leases.items() if t0 < cutoff]
    for t in stale:
        _target_leases.pop(t, None)


def _is_abnormal(
    cpu: Optional[float],
    lat: Optional[float],
    up: Optional[float],
    incident_active: Optional[float],
    err_ratio: Optional[float],
    dep_down: bool = False,
) -> bool:
    if up is not None and up == 0.0:
        return True
    if cpu is not None and cpu >= CPU_HARD:
        return True
    if lat is not None and lat >= LAT_PAIN_MS:
        return True
    if incident_active is not None and incident_active != 0.0:
        return True
    if err_ratio is not None and err_ratio >= ERROR_RATIO_THRESHOLD:
        return True
    if dep_down:
        return True
    return False


def _categorize_error(error: Exception) -> str:
    name = type(error).__name__
    if name in {"ConnectionError", "Timeout"}:
        return "telemetry_error"
    if name in {"ValueError", "TypeError", "KeyError"}:
        return "validation_error"
    return "workflow_error"


def _probe_api_dependency(dependency: str) -> bool:
    """Ask the internal API whether a dependency is currently considered up.

    Returns True only when the API reports dependencies[dependency].up == True.
    Returns False on timeout, malformed JSON, missing fields, or exceptions.
    A 503 response may still contain valid JSON, so the dependency field is
    inspected rather than the HTTP status alone.
    """
    if dependency not in aiops_models.ALLOWED_SERVICES:
        return False
    try:
        r = requests.get("http://api:5051/work", timeout=AIOPS_PROBE_TIMEOUT_SEC)
        try:
            payload = r.json()
        except Exception:
            print(f"[⚠️] Dependency probe for {dependency}: malformed JSON response")
            return False
        if not isinstance(payload, dict):
            print(f"[⚠️] Dependency probe for {dependency}: response root is not an object")
            return False
        deps = payload.get("dependencies")
        if not isinstance(deps, dict):
            print(f"[⚠️] Dependency probe for {dependency}: dependencies field missing or invalid")
            return False
        dep_info = deps.get(dependency)
        if not isinstance(dep_info, dict):
            print(f"[⚠️] Dependency probe for {dependency}: dependency entry missing or invalid")
            return False
        return dep_info.get("up") is True
    except requests.Timeout:
        print(f"[⚠️] Dependency probe for {dependency}: request timed out")
        return False
    except requests.RequestException as exc:
        print(f"[⚠️] Dependency probe for {dependency}: request error ({type(exc).__name__})")
        return False
    except Exception:
        print(f"[⚠️] Dependency probe for {dependency}: unexpected probe error")
        return False


def _run_aiops_workflow(
    trigger_service: str,
    alertname: str,
    reason: str,
    cpu: Optional[float] = None,
    latency: Optional[float] = None,
    dependency: Optional[str] = None,
    fingerprint: Optional[str] = None,
    starts_at: Optional[str] = None,
):
    """Runs the complete AIOps incident workflow and persists the decision trail."""
    effective_mode = _effective_execution_mode()
    now = datetime.now(timezone.utc)

    if _is_duplicate_event(trigger_service, alertname, dependency, fingerprint, starts_at):
        print(f"[] AIOps event duplicate for {trigger_service}/{alertname}; skipping")
        incident_store.record_duplicate(
            _safe_signature(trigger_service, alertname, dependency, fingerprint, starts_at)
        )
        return

    persisted = False
    snapshot = None
    diagnosis = None
    policy_decision = None
    execution_result = aiops_models.ExecutionResult(
        executed=False,
        target=None,
        details="Workflow did not complete",
    )
    recovery_result = aiops_models.RecoveryResult(
        status="inconclusive",
        details="Workflow did not complete",
    )
    errors: List[str] = []
    guard_state: Dict[str, Any] = {}
    incident_signature = _safe_signature(trigger_service, alertname, dependency, fingerprint, starts_at)

    try:
        snapshot = telemetry_collector.collect_telemetry_snapshot(
            trigger_service=trigger_service,
            alertname=alertname,
            reason=reason,
        )
        aiops_metrics.INCIDENTS.labels(target=trigger_service).inc()
        diagnosis_started = time.monotonic()
        diagnosis = incident_intelligence.diagnose(snapshot, api_key=GEMINI_KEY, err_ratio_threshold=ERROR_RATIO_THRESHOLD)
        aiops_metrics.DIAGNOSIS_DURATION.observe(max(0.0, time.monotonic() - diagnosis_started))
        aiops_metrics.DIAGNOSES.labels(source=diagnosis.source).inc()

        policy_decision = policy_engine.evaluate_policy(
            snapshot,
            diagnosis,
            mode=effective_mode,
            confidence_threshold=AIOPS_CONFIDENCE_THRESHOLD,
            last_heal=_last_heal,
            cooldown_seconds=COOLDOWN_SEC,
            err_ratio_threshold=ERROR_RATIO_THRESHOLD,
        )
        aiops_metrics.POLICY.labels(
            decision="approved" if policy_decision.approved else "denied",
            action=policy_decision.action,
            mode=policy_decision.mode,
        ).inc()
        if not policy_decision.approved:
            aiops_metrics.DENIALS.labels(
                reason_category=aiops_metrics.reason_category(policy_decision.reason),
                target=diagnosis.recommended_action.target_service or trigger_service,
            ).inc()

        if not policy_decision.approved:
            execution_result = aiops_models.ExecutionResult(
                executed=False,
                target=None,
                details="Policy denied action",
            )
            recovery_result = aiops_models.RecoveryResult(
                status="not_executed",
                details="Policy denied action; recovery not applicable",
            )
            errors.append(policy_decision.reason)
        elif policy_decision.action == "no_action":
            execution_result = aiops_models.ExecutionResult(
                executed=False,
                target=None,
                details="Policy selected no_action",
            )
            recovery_result = aiops_models.RecoveryResult(
                status="not_executed",
                details="no_action selected; recovery not applicable",
            )
        elif policy_decision.action == "restart_service":
            if effective_mode == "execute":
                target = policy_decision.target
                # Revalidate immediately before acquiring the target lease.
                revalidated = policy_engine.evaluate_policy(
                    snapshot,
                    diagnosis,
                    mode="execute",
                    confidence_threshold=AIOPS_CONFIDENCE_THRESHOLD,
                    last_heal=_last_heal,
                    cooldown_seconds=COOLDOWN_SEC,
                    err_ratio_threshold=ERROR_RATIO_THRESHOLD,
                )
                if not revalidated.approved or target != revalidated.target or target not in aiops_models.ALLOWED_SERVICES:
                    execution_result = aiops_models.ExecutionResult(
                        executed=False,
                        target=target,
                        details="Execution suppressed: stale or no-longer-actionable evidence",
                    )
                    recovery_result = aiops_models.RecoveryResult(
                        status="not_executed",
                        details="Execution suppressed: stale or no-longer-actionable evidence",
                    )
                    errors.append("execution_revalidation_failed")
                elif not policy_engine.has_supporting_abnormal_telemetry(snapshot, target, err_ratio_threshold=ERROR_RATIO_THRESHOLD):
                    execution_result = aiops_models.ExecutionResult(
                        executed=False,
                        target=target,
                        details="Execution suppressed: target no longer shows abnormal supporting telemetry",
                    )
                    recovery_result = aiops_models.RecoveryResult(
                        status="not_executed",
                        details="Execution suppressed: target no longer shows abnormal supporting telemetry",
                    )
                    errors.append("execution_revalidation_failed")
                elif _acquire_target_lease(target, now):
                    try:
                        guard_decision = (
                            _remediation_guard.reserve_restart(target)
                            if policy_decision.evidence_assessment
                            else remediation_guard.GuardDecision(True, "legacy_policy_test", {})
                        )
                        guard_state = guard_decision.state
                        if guard_state:
                            aiops_metrics.BUDGET_REMAINING.labels(target=target).set(guard_state["restart_budget_remaining"])
                            aiops_metrics.CIRCUIT_OPEN.labels(target=target).set(1 if guard_state["circuit_breaker_open"] else 0)
                        if not guard_decision.allowed:
                            execution_result = aiops_models.ExecutionResult(
                                executed=False,
                                target=target,
                                details=f"Execution suppressed: {guard_decision.reason}",
                            )
                            recovery_result = aiops_models.RecoveryResult(
                                status="not_executed",
                                details=f"Execution suppressed: {guard_decision.reason}",
                            )
                            errors.append(guard_decision.reason)
                        else:
                            success = _maybe_heal(target, f"AIOps policy approved {diagnosis.probable_cause}")
                            aiops_metrics.REMEDIATIONS.labels(target=target, result="executed" if success else "failed").inc()
                            execution_result = aiops_models.ExecutionResult(
                                executed=success,
                                target=target,
                                details="Restart executed" if success else "Restart failed",
                            )
                            if success:
                                restart_time = datetime.now(timezone.utc)
                                with state_lock:
                                    _last_heal[target] = restart_time
                                dependency_for_recovery = dependency if dependency and trigger_service == "api" else None
                                recovery_started = time.monotonic()
                                recovery_result = recovery_verifier.verify_recovery(
                                    target=target,
                                    cpu_threshold=CPU_HARD,
                                    lat_threshold=LAT_PAIN_MS,
                                    max_attempts=int(AIOPS_RECOVERY_TIMEOUT_SEC / AIOPS_RECOVERY_POLL_SEC),
                                    interval_seconds=AIOPS_RECOVERY_POLL_SEC,
                                    required_consecutive=AIOPS_REQUIRED_HEALTHY_SAMPLES,
                                    sleep_func=time.sleep,
                                    query_func=_prom_query,
                                    dependency=dependency_for_recovery,
                                    dependency_probe_func=_probe_api_dependency if dependency_for_recovery else None,
                                )
                                aiops_metrics.RECOVERY_DURATION.observe(max(0.0, time.monotonic() - recovery_started))
                                aiops_metrics.RECOVERY.labels(target=target, result=recovery_result.status).inc()
                                if policy_decision.evidence_assessment:
                                    guard_state = _remediation_guard.record_recovery(target, recovery_result.status == "recovered")
                                    aiops_metrics.BUDGET_REMAINING.labels(target=target).set(guard_state["restart_budget_remaining"])
                                    aiops_metrics.CIRCUIT_OPEN.labels(target=target).set(1 if guard_state["circuit_breaker_open"] else 0)
                                if recovery_result.status == "recovered":
                                    _send_recovered_notification(target, recovery_result.details)
                            else:
                                recovery_result = aiops_models.RecoveryResult(
                                    status="not_recovered",
                                    details="Recovery verification skipped because restart failed",
                                )
                                if policy_decision.evidence_assessment:
                                    guard_state = _remediation_guard.record_recovery(target, False)
                                errors.append("Restart execution failed")
                    finally:
                        _release_target_lease(target)
                else:
                    execution_result = aiops_models.ExecutionResult(
                        executed=False,
                        target=target,
                        details="Execution suppressed: target remediation already in progress or in cooldown",
                    )
                    recovery_result = aiops_models.RecoveryResult(
                        status="not_executed",
                        details="Execution suppressed: target remediation already in progress or in cooldown",
                    )
                    errors.append("target_lease_unavailable")
            else:
                execution_result = aiops_models.ExecutionResult(
                    executed=False,
                    target=policy_decision.target,
                    details="Recommendation recorded; execution deferred",
                )
                recovery_result = aiops_models.RecoveryResult(
                    status="not_executed",
                    details="Recommend mode; execution deferred",
                )

        # Generate incident dialogue using probable cause and real telemetry
        if _should_emit_dialogue(diagnosis.probable_cause_service, now):
            llm_engine.generate_aiops_incident_dialogue(
                probable_cause_service=diagnosis.probable_cause_service,
                diagnosis=diagnosis.probable_cause,
                snapshot_data=snapshot.to_dict(),
                policy_decision=policy_decision.to_dict(),
                execution_result=execution_result.to_dict(),
                recovery_result=recovery_result.to_dict(),
                gemini_key=GEMINI_KEY,
            )

        record = aiops_models.IncidentRecord.from_diagnosis(
            snapshot=snapshot,
            diagnosis=diagnosis,
            policy_decision=policy_decision,
            execution_result=execution_result,
            recovery_result=recovery_result,
            completed_at=aiops_models.utc_now_iso(),
            incident_fingerprint=incident_signature,
            restart_budget_state=guard_state,
            circuit_breaker_state={
                "open": bool(guard_state.get("circuit_breaker_open", False)),
                "reset_in_sec": guard_state.get("circuit_breaker_reset_in_sec", 0.0),
            },
        )
        if errors:
            record = aiops_models.IncidentRecord(
                incident_id=record.incident_id,
                started_at=record.started_at,
                completed_at=record.completed_at,
                trigger=record.trigger,
                snapshot=record.snapshot,
                diagnosis=record.diagnosis,
                policy_decision=record.policy_decision,
                execution_result=record.execution_result,
                recovery_result=record.recovery_result,
                model_source=record.model_source,
                errors=errors,
            )
        incident_store.persist_incident(record)
        persisted = True
    except Exception as e:
        print(f"[❌] AIOps workflow failed: {e}")
        errors.append(_categorize_error(e))
        try:
            fallback_diagnosis = aiops_models.StructuredDiagnosis(
                probable_cause_service=trigger_service,
                probable_cause="Workflow failed before diagnosis completed",
                confidence=0.0,
                affected_services=[trigger_service],
                evidence=[],
                recommended_action=aiops_models.RecommendedAction(type="no_action", target_service=None, reason="workflow error"),
                risk="low",
                source="rules",
            ) if diagnosis is None else diagnosis
            if policy_decision is None:
                fallback_policy = aiops_models.PolicyDecision(
                    approved=False,
                    action="no_action",
                    target=None,
                    mode=effective_mode if effective_mode in {"recommend", "execute"} else "recommend",
                    reason="Workflow error",
                    confidence_threshold=AIOPS_CONFIDENCE_THRESHOLD,
                )
            else:
                fallback_policy = aiops_models.PolicyDecision(
                    approved=policy_decision.approved,
                    action=policy_decision.action,
                    target=policy_decision.target,
                    mode=effective_mode if effective_mode in {"recommend", "execute"} else "recommend",
                    reason=policy_decision.reason,
                    confidence_threshold=policy_decision.confidence_threshold,
                )
            record = aiops_models.IncidentRecord.from_diagnosis(
                snapshot=snapshot or aiops_models.TelemetrySnapshot(
                    incident_id=aiops_models.new_incident_id(),
                    observed_at=aiops_models.utc_now_iso(),
                    trigger=aiops_models.Trigger(service=trigger_service, alertname=alertname, reason=reason),
                    services={},
                    active_alerts=[],
                ),
                diagnosis=fallback_diagnosis,
                policy_decision=fallback_policy,
                execution_result=execution_result,
                recovery_result=recovery_result,
                completed_at=aiops_models.utc_now_iso(),
            )
            if errors:
                record = aiops_models.IncidentRecord(
                    incident_id=record.incident_id,
                    started_at=record.started_at,
                    completed_at=record.completed_at,
                    trigger=record.trigger,
                    snapshot=record.snapshot,
                    diagnosis=record.diagnosis,
                    policy_decision=record.policy_decision,
                    execution_result=record.execution_result,
                    recovery_result=record.recovery_result,
                    model_source=record.model_source,
                    errors=errors,
                    incident_fingerprint=record.incident_fingerprint,
                    duplicate_count=record.duplicate_count,
                    first_seen=record.first_seen,
                    last_seen=record.last_seen,
                    restart_budget_state=record.restart_budget_state,
                    circuit_breaker_state=record.circuit_breaker_state,
                )
            incident_store.persist_incident(record)
            persisted = True
        except Exception as persist_error:
            print(f"[❌] Failed to persist AIOps error record: {persist_error}")
    finally:
        _release_event(trigger_service, alertname, dependency, fingerprint, starts_at, completed=persisted)


def _quick_signals(service: str) -> Tuple[
    List[Tuple[Dict[str, Any], float]],
    List[Tuple[Dict[str, Any], float]],
    List[Tuple[Dict[str, Any], float]],
    List[Tuple[Dict[str, Any], float]],
    Optional[float],
]:
    cpu = _prom_query(f'service_cpu_percent{{service="{service}"}}')
    lat = _prom_query(f'service_latency_ms{{service="{service}"}}')
    up = _prom_query(f'up{{job="{service}"}}')
    incident = _prom_query(f'service_incident_active{{service="{service}"}}')

    # Error ratio = sum(errors) / sum(attempts). Missing denominator means unknown.
    err_samples = _prom_query(f'rate(service_request_errors_total{{service="{service}"}}[1m])')
    attempts_samples = _prom_query(f'rate(service_request_attempts_total{{service="{service}"}}[1m])')
    err_value = _sum_value(err_samples)
    attempts_value = _sum_value(attempts_samples)
    if err_value is not None and attempts_value is not None and attempts_value > 0.0:
        err_ratio = err_value / attempts_value
    else:
        err_ratio = None

    return cpu, lat, up, incident, err_ratio


def _has_unhealthy_dependency(service: str) -> Tuple[bool, Optional[str]]:
    dep_up_data = _prom_query(f'service_dependency_up{{service="{service}"}}')
    dep_lat_data = _prom_query(f'service_dependency_latency_ms{{service="{service}"}}')

    # Aggregate dependency_up across replicas: false if any replica reports down.
    dep_up_state: Dict[str, bool] = {}
    for labels, value in dep_up_data:
        dep = labels.get("dependency")
        if dep is None:
            continue
        if value == 0.0:
            dep_up_state[dep] = False
        elif dep not in dep_up_state:
            dep_up_state[dep] = True

    # Aggregate dependency latency across replicas: max per dependency.
    dep_lat_state: Dict[str, float] = {}
    for labels, value in dep_lat_data:
        dep = labels.get("dependency")
        if dep is None:
            continue
        if dep not in dep_lat_state or value > dep_lat_state[dep]:
            dep_lat_state[dep] = value

    for dep, up in dep_up_state.items():
        if not up:
            return True, dep
    for dep, lat in dep_lat_state.items():
        if lat >= LAT_PAIN_MS:
            return True, dep
    return False, None


def _diagnose(service: str):
    """Pulls quick signals from Prometheus and prints a diagnostic line."""
    signals = _quick_signals(service)
    cpu, lat, up, incident, err_ratio = signals[:5]
    cpu_v = _max_value(cpu)
    lat_v = _max_value(lat)
    up_v = _up_value(up)
    incident_v = _max_value(incident)

    dep_down, dep_name = _has_unhealthy_dependency(service)

    if not _is_abnormal(cpu_v, lat_v, up_v, incident_v, err_ratio, dep_down):
        print(f"[✅] {service} healthy; no AIOps action")
        _record_service_healthy(service)
        return

    if AIOPS_ENABLED:
        _run_aiops_workflow(
            trigger_service=service,
            alertname="ThresholdDiagnosis",
            reason=f"Legacy watcher threshold check detected abnormality for {service}",
            cpu=cpu_v,
            latency=lat_v,
            dependency=dep_name,
        )
        return

    # Legacy threshold-driven behavior
    if lat_v is None:
        lat_v = LAT_PAIN_MS if cpu_v and cpu_v >= CPU_HARD else 80
        print(f"[️] Missing latency sample for {service}; using fallback {lat_v:.0f}ms")

    trigger_heal = False
    reason = ""

    if up_v == 0:
        trigger_heal = True
        reason = "Prometheus scrape target is down"

    if cpu_v is not None and cpu_v >= CPU_HARD:
        trigger_heal = True
        reason = f"CPU {cpu_v:.1f}% ≥ {CPU_HARD:.0f}%"

    if trigger_heal:
        now = datetime.now(timezone.utc)
        with state_lock:
            last = _last_heal.get(service)
        if last and (now - last) < timedelta(seconds=COOLDOWN_SEC):
            print(f"[⏳] Healing cooldown active for {service} (skipping action)")
            if _should_emit_dialogue(service, now):
                llm_engine.generate_incident_dialogue(
                    service,
                    cpu_v,
                    lat_v,
                    gemini_key=GEMINI_KEY,
                    send_discord=False,
                )
            return

        if _should_emit_dialogue(service, now):
            llm_engine.generate_incident_dialogue(service, cpu_v, lat_v, gemini_key=GEMINI_KEY)

        if HEALING:
            success = _maybe_heal(service, reason)
            if success:
                with state_lock:
                    _last_heal[service] = now
        else:
            print(f"[💊] Healing simulated (HEALING_ENABLED=false) for {service}: {reason}")


def _cluster_is_healthy() -> bool:
    """Return True only if no service is currently signaling an abnormal condition."""
    for service in SERVICES:
        cpu, lat, up, incident, err_ratio = _quick_signals(service)
        dep_down, _ = _has_unhealthy_dependency(service)
        if _is_abnormal(_max_value(cpu), _max_value(lat), _up_value(up), _max_value(incident), err_ratio, dep_down):
            return False
    return True


def interpret_metrics(service, metrics_text):
    """Original metrics interpretation + calls SRE diagnostic engine."""
    cpu = None
    for family in text_string_to_metric_families(metrics_text):
        if family.name == "service_cpu_percent":
            for sample in family.samples:
                if sample.labels.get("service") == service:
                    cpu = sample.value
                    break

    if cpu is None:
        print(f"[⚠️] No CPU metric found for {service}; checking Prometheus target state")
        _diagnose(service)
        return

    if cpu < 50:
        mood = "😊 calm and stable"
    elif cpu < 80:
        mood = "😟 feeling pressure"
    else:
        mood = " overloaded!"

    print(f"[] {service} CPU={cpu:.1f}% → {mood}")
    _diagnose(service)


def watch():
    print(" InfraMirror AI Whisper Brain activated...")
    time.sleep(5)

    last_ambient_time = time.time() - 10

    while True:
        for service, url in SERVICES.items():
            try:
                r = requests.get(url, timeout=3)
                if r.status_code == 200:
                    interpret_metrics(service, r.text)
                else:
                    print(f"[❌] {service} metrics returned {r.status_code}")
            except Exception as e:
                print(f"[❌] Failed to scrape {service} metrics from {url}: {e}")

        # Steady-state ambient dialogue when cluster is healthy
        try:
            if (time.time() - last_ambient_time) >= 25 and _cluster_is_healthy():
                llm_engine.generate_healthy_dialogue(gemini_key=GEMINI_KEY)
                last_ambient_time = time.time()
        except Exception as e:
            print(f"[⚠️] Ambient check failed: {e}")

        time.sleep(5)


# SRE Webhook /whisper Receiver
app = Flask(__name__)


@app.route("/metrics", methods=["GET"])
def aiops_prometheus_metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route("/aiops/circuit-breakers/<target>/reset", methods=["POST"])
def reset_circuit_breaker(target: str):
    if not _authorized_request():
        return jsonify({"status": "rejected", "reason": "unauthorized"}), 401
    if target not in aiops_models.ALLOWED_SERVICES:
        return jsonify({"status": "rejected", "reason": "invalid service"}), 400
    state = _remediation_guard.reset(target)
    aiops_metrics.CIRCUIT_OPEN.labels(target=target).set(0)
    return jsonify({"status": "reset", "target": target, "state": state}), 200


# Allowlist for alertname and dependency identifiers
_ALERTNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _is_valid_alertname(alertname: Any) -> bool:
    return isinstance(alertname, str) and bool(_ALERTNAME_RE.match(alertname))


def _is_valid_dependency(dependency: Any) -> Optional[str]:
    if dependency is None:
        return None
    if isinstance(dependency, str) and dependency in aiops_models.ALLOWED_SERVICES:
        return dependency
    return None


def _is_finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
        if not math.isfinite(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _parse_cpu(value: Any) -> Optional[float]:
    """Validate a CPU percentage value. Returns float or raises ValueError."""
    v = _is_finite_number(value)
    if v is None:
        raise ValueError("cpu must be a finite number between 0 and 100")
    if v < 0 or v > 100:
        raise ValueError("cpu out of range")
    return v


def _parse_latency(value: Any) -> Optional[float]:
    """Validate a latency value in milliseconds. Returns float or raises ValueError."""
    v = _is_finite_number(value)
    if v is None:
        raise ValueError("latency must be a finite number between 0 and 60000")
    if v < 0 or v > 60000:
        raise ValueError("latency out of range")
    return v


def _release_semaphore_slot(sem: threading.BoundedSemaphore):
    try:
        sem.release()
    except ValueError:
        # Semaphore was already at capacity (e.g., after shutdown).
        pass


def _sanitize_payload_for_log(data: Dict[str, Any]) -> str:
    """Return a short sanitized string for logging; do not include raw alert JSON."""
    service = data.get("service")
    alertname = data.get("alertname")
    status = data.get("status")
    alerts_count = len(data.get("alerts", [])) if isinstance(data.get("alerts"), list) else 0
    summary = {
        "service": service,
        "alertname": alertname,
        "status": status,
        "alerts_count": alerts_count,
    }
    return json.dumps(summary, default=str)[:256]


@app.route("/whisper", methods=["POST"])
def receive_whisper_alert():
    """Receives Prometheus alert webhook callbacks and triggers AI remediation."""
    if not _authorized_request():
        return jsonify({"status": "rejected", "reason": "unauthorized"}), 401

    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return jsonify({"status": "rejected", "reason": "payload must be a JSON object"}), 400

    data = raw
    print(f"\n[🚨 Alert Webhook] Received telemetry alert: {_sanitize_payload_for_log(data)}")

    alerts = data.get("alerts")
    if alerts is not None and not isinstance(alerts, list):
        return jsonify({"status": "rejected", "reason": "alerts must be a list"}), 400

    # Validate grouped alert structure before accessing members.
    validated_alerts: List[Dict[str, Any]] = []
    if alerts:
        for alert in alerts:
            if not isinstance(alert, dict):
                return jsonify({"status": "rejected", "reason": "each alert must be an object"}), 400
            labels = alert.get("labels")
            if not isinstance(labels, dict):
                return jsonify({"status": "rejected", "reason": "alert labels must be an object"}), 400
            validated_alerts.append(alert)

    status = data.get("status")
    all_resolved = bool(validated_alerts) and all(
        alert.get("status") == "resolved" for alert in validated_alerts
    )
    if status == "resolved" or all_resolved:
        for alert in validated_alerts:
            labels = alert.get("labels", {})
            svc = labels.get("service") or SERVICE_BY_INSTANCE.get(labels.get("instance", ""))
            dep = _is_valid_dependency(labels.get("dependency"))
            aname = labels.get("alertname")
            if svc and aname:
                _record_healthy_transition(
                    svc,
                    aname,
                    dep,
                    fingerprint=alert.get("fingerprint"),
                    starts_at=alert.get("startsAt"),
                )
                _record_service_healthy(svc)
        if status == "resolved":
            return jsonify({"status": "ignored", "reason": "alert resolved"}), 200
        return jsonify({"status": "ignored", "reason": "all alerts resolved"}), 200

    alertname: str = "Unknown"
    service: Optional[str] = None
    dependency: Optional[str] = None
    cpu: Optional[float] = None
    latency: Optional[float] = None
    fingerprint: Optional[str] = None
    starts_at: Optional[str] = None

    # Prefer Alertmanager-style alerts array
    if validated_alerts:
        first = validated_alerts[0]
        labels = first.get("labels", {})
        alertname = labels.get("alertname") or data.get("alertname") or "Unknown"
        service = labels.get("service") or SERVICE_BY_INSTANCE.get(labels.get("instance", ""))
        dependency = labels.get("dependency")
        fingerprint = first.get("fingerprint")
        starts_at = first.get("startsAt")
        # Direct payload values may supplement missing Prometheus values
        try:
            if "cpu" in data:
                cpu = _parse_cpu(data["cpu"])
            if "latency" in data:
                latency = _parse_latency(data["latency"])
        except ValueError as exc:
            return jsonify({"status": "rejected", "reason": str(exc)}), 400
    else:
        # Direct /whisper payload format: {"service": "database", "cpu": 92.4, "latency": 380}
        service = data.get("service")
        alertname = data.get("alertname") or "Unknown"
        dependency = data.get("dependency")
        fingerprint = data.get("fingerprint")
        starts_at = data.get("startsAt")
        try:
            if "cpu" in data:
                cpu = _parse_cpu(data["cpu"])
            if "latency" in data:
                latency = _parse_latency(data["latency"])
        except ValueError as exc:
            return jsonify({"status": "rejected", "reason": str(exc)}), 400

    if not service or service not in SERVICES:
        return jsonify({"status": "rejected", "reason": "invalid service"}), 400

    # Validate alertname and dependency
    if not _is_valid_alertname(alertname):
        return jsonify({"status": "rejected", "reason": "invalid alertname"}), 400
    validated_dependency = _is_valid_dependency(dependency)

    print(f"[🤖 AI Engine] Processing webhook alert for service: {service.upper()}...")

    if AIOPS_ENABLED:
        sem = _get_semaphore()
        if not sem.acquire(blocking=False):
            return jsonify({"status": "rejected", "reason": "worker capacity unavailable"}), 429
        try:
            executor = _get_executor()
            future = executor.submit(
                _run_aiops_workflow,
                trigger_service=service,
                alertname=alertname,
                reason=f"Authenticated /whisper alert ({alertname}) for {service}",
                cpu=cpu,
                latency=latency,
                dependency=validated_dependency,
                fingerprint=fingerprint,
                starts_at=starts_at,
            )
        except Exception:
            _release_semaphore_slot(sem)
            return jsonify({"status": "rejected", "reason": "background work unavailable"}), 503
        future.add_done_callback(lambda f, semaphore=sem: _release_semaphore_slot(semaphore))
        return jsonify({"status": "accepted", "service": service, "remediation": "queued"}), 202

    # Legacy webhook behavior: missing telemetry is not invented; when absent,
    # only the healing action is taken without a misleading dialogue.
    if cpu is None or latency is None:
        print(f"[️] Legacy /whisper for {service} missing telemetry; skipping dialogue")
    _process_whisper_alert(service, cpu, latency)
    return jsonify({"status": "accepted", "service": service, "remediation": "queued"}), 202


def _process_whisper_alert(service: str, cpu: Optional[float], latency: Optional[float]):
    """Runs slower dialogue + healing work away from the webhook response path."""
    now = datetime.now(timezone.utc)
    with state_lock:
        last = _last_heal.get(service)
    if last and (now - last) < timedelta(seconds=COOLDOWN_SEC):
        print(f"[] Webhook healing cooldown active for {service}")
        if _should_emit_dialogue(service, now) and cpu is not None and latency is not None:
            llm_engine.generate_incident_dialogue(
                service,
                cpu,
                latency,
                gemini_key=GEMINI_KEY,
                send_discord=False,
            )
        return

    if _should_emit_dialogue(service, now) and cpu is not None and latency is not None:
        llm_engine.generate_incident_dialogue(service, cpu, latency, gemini_key=GEMINI_KEY)

    if HEALING:
        reason = f"Webhook Alert: CPU={cpu if cpu is not None else 'unknown'}% Latency={latency if latency is not None else 'unknown'}ms"
        success = _maybe_heal(service, reason)
        if success:
            with state_lock:
                _last_heal[service] = now
    else:
        print(f"[💊] Webhook Healing simulated for {service}")


def run_webhook_server():
    print("🚀 Exposing /whisper Webhook receiver on port 5055...")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=5055)
    except ImportError:
        print("[⚠️] Waitress not installed; falling back to Flask server")
        app.run(host="0.0.0.0", port=5055, debug=False, use_reloader=False)


if __name__ == "__main__":
    # Start Flask /whisper webhook receiver in a background thread
    threading.Thread(target=run_webhook_server, daemon=True).start()

    # Run the main SRE prometheus watcher loop
    watch()
