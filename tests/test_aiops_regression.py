import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from inframirror import aiops_models
from inframirror.aiops_models import (
    ALLOWED_SERVICES,
    DependencyInfo,
    ServiceTelemetry,
    TelemetrySnapshot,
    Trigger,
    StructuredDiagnosis,
    RecommendedAction,
    EvidenceItem,
)
from inframirror import incident_intelligence as ii
from inframirror import policy_engine as pe
from inframirror import incident_store
from inframirror import recovery_verifier as rv
from inframirror import watcher


class TestAIOpsRegression(unittest.TestCase):
    def tearDown(self):
        watcher._last_heal.clear()
        watcher._last_dialogue.clear()
        watcher._in_flight.clear()
        watcher._completed.clear()

    def _snapshot(self, services=None, trigger="api"):
        return TelemetrySnapshot(
            incident_id="test-id",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service=trigger, alertname="Test", reason="test"),
            services=services or {},
            active_alerts=[],
        )

    # 1. Healthy polling creates no incident and makes no model call.
    def test_healthy_polling_no_incident(self):
        def fake_signals(service):
            return [(None, 20.0)], [(None, 50.0)], [(None, 1.0)], [(None, 0.0)], 0.01
        with patch.object(watcher, "AIOPS_ENABLED", True), \
             patch.object(watcher, "_quick_signals", side_effect=fake_signals), \
             patch.object(watcher, "_has_unhealthy_dependency", return_value=(False, None)), \
             patch.object(watcher, "_run_aiops_workflow") as workflow, \
             patch.object(watcher.llm_engine, "generate_incident_dialogue"):
            watcher._diagnose("api")
            workflow.assert_not_called()

    # 2. Duplicate polling/webhook events execute once in recommend and execute modes.
    def _test_duplicate_event(self, mode):
        fake_diagnosis = StructuredDiagnosis(
            probable_cause_service="api",
            probable_cause="CPU high",
            confidence=0.9,
            affected_services=["api"],
            evidence=[EvidenceItem(service="api", signal="cpu", value=90.0, interpretation="high")],
            recommended_action=RecommendedAction(type="restart_service", target_service="api", reason="test"),
            risk="low",
            source="rules",
        )
        fake_policy = aiops_models.PolicyDecision(
            approved=True, action="restart_service", target="api", mode=mode, reason="ok", confidence_threshold=0.75
        )
        fake_recovery = aiops_models.RecoveryResult(status="recovered", details="ok")
        with patch.object(watcher.incident_intelligence, "diagnose", return_value=fake_diagnosis), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=fake_policy), \
             patch.object(watcher.incident_store, "persist_incident"), \
             patch.object(watcher, "HEALING", True), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", mode), \
             patch.object(watcher, "AIOPS_EXECUTION_GRACE_SEC", 0), \
             patch.object(watcher.policy_engine, "has_supporting_abnormal_telemetry", return_value=True), \
             patch.object(watcher, "_maybe_heal", return_value=True) as maybe_heal, \
             patch.object(watcher.recovery_verifier, "verify_recovery", return_value=fake_recovery), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "first")
            watcher._run_aiops_workflow("api", "Test", "second")
        self.assertEqual(maybe_heal.call_count, 0 if mode == "recommend" else 1)

    def test_duplicate_event_recommend_mode(self):
        self._test_duplicate_event("recommend")

    def test_duplicate_event_execute_mode(self):
        self._test_duplicate_event("execute")

    # 3. Compose variable name AIOPS_EXECUTION_MODE is honored when healing enabled.
    def test_aiops_execution_mode_env(self):
        with patch.dict(os.environ, {"AIOPS_EXECUTION_MODE": "execute", "HEALING_ENABLED": "true"}):
            import importlib
            from inframirror import watcher as w
            w._shutdown_executor()
            importlib.reload(w)
            self.assertEqual(w.AIOPS_EXECUTION_MODE, "execute")
        with patch.dict(os.environ, {"AIOPS_EXECUTION_MODE": "invalid", "HEALING_ENABLED": "true"}):
            w._shutdown_executor()
            importlib.reload(w)
            self.assertEqual(w.AIOPS_EXECUTION_MODE, "recommend")
        with patch.dict(os.environ, {"AIOPS_EXECUTION_MODE": "execute", "HEALING_ENABLED": "false"}):
            w._shutdown_executor()
            importlib.reload(w)
            self.assertEqual(w.AIOPS_EXECUTION_MODE, "recommend")

    # 4. Confidence and recovery env values are honored.
    def test_env_values_honored(self):
        with patch.dict(os.environ, {
            "AIOPS_CONFIDENCE_THRESHOLD": "0.8",
            "AIOPS_RECOVERY_TIMEOUT_SEC": "90",
            "AIOPS_RECOVERY_POLL_SEC": "10",
            "AIOPS_REQUIRED_HEALTHY_SAMPLES": "3",
            "AIOPS_PROBE_TIMEOUT_SEC": "5.0",
        }):
            import importlib
            from inframirror import watcher as w
            importlib.reload(w)
            self.assertEqual(w.AIOPS_CONFIDENCE_THRESHOLD, 0.8)
            self.assertEqual(w.AIOPS_RECOVERY_TIMEOUT_SEC, 90.0)
            self.assertEqual(w.AIOPS_RECOVERY_POLL_SEC, 10.0)
            self.assertEqual(w.AIOPS_REQUIRED_HEALTHY_SAMPLES, 3)
            self.assertEqual(w.AIOPS_PROBE_TIMEOUT_SEC, 5.0)

    def test_env_invalid_defaults(self):
        with patch.dict(os.environ, {
            "AIOPS_CONFIDENCE_THRESHOLD": "bad",
            "AIOPS_RECOVERY_TIMEOUT_SEC": "9999",
            "AIOPS_RECOVERY_POLL_SEC": "0.1",
            "AIOPS_REQUIRED_HEALTHY_SAMPLES": "-1",
            "AIOPS_PROBE_TIMEOUT_SEC": "100",
        }):
            import importlib
            from inframirror import watcher as w
            importlib.reload(w)
            self.assertEqual(w.AIOPS_CONFIDENCE_THRESHOLD, 0.75)
            self.assertEqual(w.AIOPS_RECOVERY_TIMEOUT_SEC, 45.0)
            self.assertEqual(w.AIOPS_RECOVERY_POLL_SEC, 3.0)
            self.assertEqual(w.AIOPS_REQUIRED_HEALTHY_SAMPLES, 2)
            self.assertEqual(w.AIOPS_PROBE_TIMEOUT_SEC, 2.0)

    # 5. Production recovery uses injected/real interval correctly.
    def test_production_recovery_uses_real_interval(self):
        import time
        responses = {
            'up{job="api"}': [{"value": (None, 1.0)}],
            'service_cpu_percent{service="api"}': [{"value": (None, 50.0)}],
            'service_latency_ms{service="api"}': [{"value": (None, 100.0)}],
        }
        with patch("time.sleep") as sleep_mock:
            result = rv.verify_recovery(
                target="api",
                max_attempts=2,
                interval_seconds=1.5,
                required_consecutive=2,
                sleep_func=time.sleep,
                query_func=lambda expr: responses.get(expr, []),
            )
            self.assertEqual(result.status, "recovered")
            sleep_mock.assert_called_with(1.5)

    # 6. Dependency recovery checks dependency_up.
    def test_dependency_recovery_checks_dependency_up(self):
        responses = {
            'up{job="database"}': [{"value": (None, 1.0)}],
            'service_cpu_percent{service="database"}': [{"value": (None, 50.0)}],
            'service_latency_ms{service="database"}': [{"value": (None, 100.0)}],
            'service_dependency_up{service="api",dependency="database"}': [{"value": (None, 1.0)}],
        }
        result = rv.verify_recovery(
            target="database",
            max_attempts=5,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=lambda expr: responses.get(expr, []),
            dependency="database",
        )
        self.assertEqual(result.status, "recovered")

    def test_dependency_recovery_fails_when_dependency_down(self):
        responses = {
            'up{job="database"}': [{"value": (None, 1.0)}],
            'service_cpu_percent{service="database"}': [{"value": (None, 50.0)}],
            'service_latency_ms{service="database"}': [{"value": (None, 100.0)}],
            'service_dependency_up{service="api",dependency="database"}': [{"value": (None, 0.0)}],
        }
        result = rv.verify_recovery(
            target="database",
            max_attempts=3,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=lambda expr: responses.get(expr, []),
            dependency="database",
        )
        self.assertEqual(result.status, "not_recovered")

    # 7. Missing webhook telemetry remains unknown.
    def test_webhook_missing_telemetry_unknown(self):
        payload = {
            "service": "api",
            "alertname": "HighLatency",
        }
        with patch.dict(os.environ, {"WHISPER_TOKEN": "test-token"}):
            with patch.object(watcher, "AIOPS_ENABLED", True), \
                 patch.object(watcher, "_get_executor") as mock_executor:
                mock_future = MagicMock()
                mock_executor.return_value.submit.return_value = mock_future
                response = watcher.app.test_client().post(
                    "/whisper",
                    json=payload,
                    headers={"Authorization": "Bearer test-token"},
                )
                self.assertEqual(response.status_code, 202)
                _, kwargs = mock_executor.return_value.submit.call_args
                self.assertIsNone(kwargs.get("cpu"))
                self.assertIsNone(kwargs.get("latency"))

    # 8. Recommendation/no_action dialogue never claims execution.
    def test_recommend_mode_dialogue_no_execution_claim(self):
        snapshot = self._snapshot({"api": ServiceTelemetry(cpu_percent=90.0, latency_ms=100.0)})
        policy = aiops_models.PolicyDecision(
            approved=True, action="restart_service", target="api", mode="recommend", reason="ok", confidence_threshold=0.75
        )
        dialogue = watcher.llm_engine.generate_aiops_incident_dialogue(
            probable_cause_service="api",
            diagnosis="api overload",
            snapshot_data=snapshot.to_dict(),
            policy_decision=policy.to_dict(),
            execution_result=aiops_models.ExecutionResult(executed=False, target=None, details="Recommendation recorded").to_dict(),
            recovery_result=aiops_models.RecoveryResult(status="not_executed", details="").to_dict(),
            send_discord=False,
            persist=False,
        )
        self.assertTrue(
            "recommends" in dialogue.lower() or "awaiting operator approval" in dialogue.lower(),
            f"Expected recommendation language, got: {dialogue}",
        )

    def test_no_action_dialogue_no_execution_claim(self):
        snapshot = self._snapshot({"api": ServiceTelemetry(cpu_percent=20.0, latency_ms=50.0)})
        policy = aiops_models.PolicyDecision(
            approved=True, action="no_action", target=None, mode="recommend", reason="transient", confidence_threshold=0.75
        )
        dialogue = watcher.llm_engine.generate_aiops_incident_dialogue(
            probable_cause_service="api",
            diagnosis="transient",
            snapshot_data=snapshot.to_dict(),
            policy_decision=policy.to_dict(),
            execution_result=aiops_models.ExecutionResult(executed=False, target=None, details="No action").to_dict(),
            recovery_result=aiops_models.RecoveryResult(status="not_executed", details="").to_dict(),
            send_discord=False,
            persist=False,
        )
        self.assertIn("no remediation action required", dialogue.lower())

    # 9. Error rate is a ratio.
    def test_error_rate_is_ratio(self):
        from inframirror import telemetry_collector as tc
        err_data = [{"value": (None, 10.0)}]
        attempts_data = [{"value": (None, 100.0)}]
        with patch.object(tc, "_query_instant", side_effect=lambda expr, prom_url=None: err_data if "errors" in expr else attempts_data):
            svc = tc._collect_service_telemetry("api", prom_url="http://test")
            self.assertEqual(svc.error_rate, 0.1)

    # 10. incident_active can identify direct API overload.
    def test_incident_active_direct_overload(self):
        snapshot = self._snapshot({
            "api": ServiceTelemetry(incident_active=True),
            "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=40.0),
            "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        diagnosis = ii.rules_based_diagnosis(snapshot)
        self.assertEqual(diagnosis.probable_cause_service, "api")
        self.assertEqual(diagnosis.recommended_action.type, "restart_service")

    # 11. Rules selection is deterministic.
    def test_rules_selection_deterministic(self):
        snapshot = self._snapshot({
            "api": ServiceTelemetry(cpu_percent=92.0, latency_ms=120.0),
            "frontend": ServiceTelemetry(cpu_percent=95.0, latency_ms=200.0),
            "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        results = [ii.rules_based_diagnosis(snapshot).probable_cause_service for _ in range(10)]
        self.assertEqual(len(set(results)), 1)

    # 12. Recursive secret sanitization works.
    def test_recursive_secret_sanitization(self):
        tmp_dir = tempfile.mkdtemp()
        path = os.path.join(tmp_dir, "aiops_incidents.json")
        snapshot = TelemetrySnapshot(
            incident_id="sec-1",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service="api", alertname="test", reason="test"),
            services={"api": ServiceTelemetry(cpu_percent=10.0, latency_ms=50.0)},
            active_alerts=[],
        )
        diagnosis = StructuredDiagnosis(
            probable_cause_service="api",
            probable_cause="test",
            confidence=0.9,
            affected_services=["api"],
            evidence=[],
            recommended_action=RecommendedAction(type="no_action", target_service=None, reason="test"),
            risk="low",
            source="rules",
        )
        policy = pe.evaluate_policy(snapshot, diagnosis, mode="recommend")
        record = aiops_models.IncidentRecord.from_diagnosis(
            snapshot=snapshot,
            diagnosis=diagnosis,
            policy_decision=policy,
            execution_result=aiops_models.ExecutionResult(executed=False, target=None, details=""),
            recovery_result=aiops_models.RecoveryResult(status="not_executed", details=""),
        )
        record_dict = record.to_dict()
        record_dict["snapshot"]["metadata"] = {
            "API_KEY": "super-secret",
            "nested": {"password": "hunter2"},
        }
        record_dict["raw_model_output"] = "should be redacted"
        sanitized = incident_store._sanitize_for_storage(record_dict)
        self.assertEqual(sanitized["snapshot"]["metadata"]["API_KEY"], "[REDACTED]")
        self.assertEqual(sanitized["snapshot"]["metadata"]["nested"]["password"], "[REDACTED]")
        self.assertEqual(sanitized["raw_model_output"], "[REDACTED]")

    # 13. Background work is bounded.
    def test_background_workers_bounded(self):
        with patch.dict(os.environ, {"AIOPS_MAX_WORKERS": "3"}):
            import importlib
            from inframirror import watcher as w
            w._shutdown_executor()
            importlib.reload(w)
            executor = w._get_executor()
            self.assertEqual(executor._max_workers, 3)

    # New regression tests for final correction pass.

    # 1. Real executor backpressure returns 429 when workers and queue are full.
    def test_executor_backpressure_returns_429(self):
        env = {
            "WHISPER_TOKEN": "test-token",
            "AIOPS_MAX_WORKERS": "1",
            "AIOPS_QUEUE_CAPACITY": "0",
        }
        with patch.dict(os.environ, env):
            import importlib
            from inframirror import watcher as w
            w._shutdown_executor()
            importlib.reload(w)

            blocker = threading.Event()
            def blocking_workflow(*args, **kwargs):
                blocker.wait(timeout=5)

            with patch.object(w, "AIOPS_ENABLED", True), \
                 patch.object(w, "_run_aiops_workflow", side_effect=blocking_workflow):
                client = w.app.test_client()
                # First request accepts the only worker slot.
                r1 = client.post(
                    "/whisper",
                    json={"service": "api", "alertname": "HighLatency"},
                    headers={"Authorization": "Bearer test-token"},
                )
                self.assertEqual(r1.status_code, 202)
                # Second request must be rejected because the worker is busy.
                r2 = client.post(
                    "/whisper",
                    json={"service": "api", "alertname": "HighLatency2"},
                    headers={"Authorization": "Bearer test-token"},
                )
                self.assertEqual(r2.status_code, 429)
                blocker.set()
                w._shutdown_executor()

    # 2. HEALING_ENABLED=false forces recommendation behavior.
    def test_healing_disabled_forces_recommend(self):
        fake_diagnosis = StructuredDiagnosis(
            probable_cause_service="api",
            probable_cause="CPU high",
            confidence=0.9,
            affected_services=["api"],
            evidence=[EvidenceItem(service="api", signal="cpu", value=90.0, interpretation="high")],
            recommended_action=RecommendedAction(type="restart_service", target_service="api", reason="test"),
            risk="low",
            source="rules",
        )
        fake_policy = aiops_models.PolicyDecision(
            approved=True, action="restart_service", target="api", mode="execute", reason="ok", confidence_threshold=0.75
        )
        with patch.object(watcher, "HEALING", False), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "recommend"), \
             patch.object(watcher.incident_intelligence, "diagnose", return_value=fake_diagnosis), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=fake_policy), \
             patch.object(watcher.incident_store, "persist_incident"), \
             patch.object(watcher, "_maybe_heal") as maybe_heal, \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")
            maybe_heal.assert_not_called()

    # 3. Quick-signal error ratio: healthy low error ratio.
    def test_healthy_low_error_ratio(self):
        def fake_signals(service):
            # CPU, lat, up, incident, error_ratio
            return [(None, 20.0)], [(None, 50.0)], [(None, 1.0)], [(None, 0.0)], 0.05
        with patch.object(watcher, "AIOPS_ENABLED", True), \
             patch.object(watcher, "_quick_signals", side_effect=fake_signals), \
             patch.object(watcher, "_has_unhealthy_dependency", return_value=(False, None)), \
             patch.object(watcher, "_run_aiops_workflow") as workflow:
            watcher._diagnose("api")
            workflow.assert_not_called()

    # 4. Quick-signal error ratio: abnormal high error ratio.
    def test_abnormal_high_error_ratio(self):
        def fake_signals(service):
            return [(None, 20.0)], [(None, 50.0)], [(None, 1.0)], [(None, 0.0)], 0.15
        with patch.object(watcher, "AIOPS_ENABLED", True), \
             patch.object(watcher, "_quick_signals", side_effect=fake_signals), \
             patch.object(watcher, "_has_unhealthy_dependency", return_value=(False, None)), \
             patch.object(watcher, "_run_aiops_workflow") as workflow:
            watcher._diagnose("api")
            workflow.assert_called_once()

    # 5. Ambient health truthfulness: dependency down suppresses healthy dialogue.
    def test_ambient_health_dependency_down_no_dialogue(self):
        def fake_signals(service):
            # CPU healthy, but dependency down via _has_unhealthy_dependency
            return [(None, 20.0)], [(None, 50.0)], [(None, 1.0)], [(None, 0.0)], 0.01
        with patch.object(watcher, "_quick_signals", side_effect=fake_signals), \
             patch.object(watcher, "_has_unhealthy_dependency", return_value=(True, "database")), \
             patch.object(watcher.llm_engine, "generate_healthy_dialogue") as healthy_dialogue:
            result = watcher._cluster_is_healthy()
            self.assertFalse(result)
            healthy_dialogue.assert_not_called()

    # 6. Webhook validation rejects invalid alertname and out-of-range metrics;
    # invalid dependencies are sanitized to None and accepted.
    def test_webhook_validation_rejects_invalid_inputs(self):
        with patch.dict(os.environ, {"WHISPER_TOKEN": "test-token"}):
            # Invalid alertname
            response = watcher.app.test_client().post(
                "/whisper",
                json={"service": "api", "alertname": "bad name!", "cpu": 50.0, "latency": 100.0},
                headers={"Authorization": "Bearer test-token"},
            )
            self.assertEqual(response.status_code, 400)
            # Out of range metrics
            response = watcher.app.test_client().post(
                "/whisper",
                json={"service": "api", "alertname": "Test", "cpu": 150.0, "latency": 100.0},
                headers={"Authorization": "Bearer test-token"},
            )
            self.assertEqual(response.status_code, 400)
            response = watcher.app.test_client().post(
                "/whisper",
                json={"service": "api", "alertname": "Test", "cpu": 50.0, "latency": -1.0},
                headers={"Authorization": "Bearer test-token"},
            )
            self.assertEqual(response.status_code, 400)
            # Invalid dependency is sanitized to None and request accepted
            fresh_semaphore = threading.BoundedSemaphore(1)
            with patch.object(watcher, "AIOPS_ENABLED", True), \
                 patch.object(watcher, "_get_executor") as mock_executor, \
                 patch.object(watcher, "_get_semaphore", return_value=fresh_semaphore):
                mock_future = MagicMock()
                mock_executor.return_value.submit.return_value = mock_future
                response = watcher.app.test_client().post(
                    "/whisper",
                    json={"service": "api", "alertname": "Test", "dependency": "hacker", "cpu": 50.0, "latency": 100.0},
                    headers={"Authorization": "Bearer test-token"},
                )
                self.assertEqual(response.status_code, 202)
                _, kwargs = mock_executor.return_value.submit.call_args
                self.assertIsNone(kwargs.get("dependency"))

    # 7. Dedup lifecycle: explicit clear state and no cross-test pollution.
    def test_dedup_lifecycle_clears_state(self):
        self.assertEqual(len(watcher._in_flight), 0)
        self.assertEqual(len(watcher._completed), 0)

    # 8. Model and telemetry bounds reject invalid values.
    def test_telemetry_bounds(self):
        with self.assertRaises(ValueError):
            ServiceTelemetry(cpu_percent=150.0)
        with self.assertRaises(ValueError):
            ServiceTelemetry(latency_ms=-1.0)
        with self.assertRaises(ValueError):
            ServiceTelemetry(error_rate=1.5)
        with self.assertRaises(ValueError):
            ServiceTelemetry(dependencies={"hacker": DependencyInfo(up=True)})

    def test_telemetry_nan_inf_rejected(self):
        import math
        with self.assertRaises(ValueError):
            ServiceTelemetry(cpu_percent=float("nan"))
        with self.assertRaises(ValueError):
            ServiceTelemetry(latency_ms=float("inf"))


if __name__ == "__main__":
    unittest.main()
