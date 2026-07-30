import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

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
from inframirror import watcher
from inframirror import telemetry_collector
from inframirror import recovery_verifier as rv


def _clear_watcher_state(mod):
    mod._last_heal.clear()
    mod._last_dialogue.clear()
    mod._in_flight.clear()
    mod._completed.clear()
    mod._completed_info.clear()
    mod._target_leases.clear()
    mod._startup_monotonic = time.monotonic()


class TestAuditFixes(unittest.TestCase):
    def tearDown(self):
        _clear_watcher_state(watcher)

    # ------------------------------------------------------------------
    # 1. Malformed webhook payloads return 400, never 500
    # ------------------------------------------------------------------
    def _post(self, payload, token="test-token"):
        with patch.dict(os.environ, {"WHISPER_TOKEN": token}):
            return watcher.app.test_client().post(
                "/whisper",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )

    def test_list_root_rejected(self):
        response = self._post(["bad"])
        self.assertEqual(response.status_code, 400)

    def test_string_root_rejected(self):
        response = self._post("alert")
        self.assertEqual(response.status_code, 400)

    def test_dict_alerts_rejected(self):
        response = self._post({"alerts": {"service": "api"}})
        self.assertEqual(response.status_code, 400)

    def test_string_alerts_rejected(self):
        response = self._post({"alerts": "foo"})
        self.assertEqual(response.status_code, 400)

    def test_non_object_alert_member_rejected(self):
        response = self._post({"alerts": [1]})
        self.assertEqual(response.status_code, 400)

    def test_malformed_labels_rejected(self):
        response = self._post({"alerts": [{"labels": 1}]})
        self.assertEqual(response.status_code, 400)

    def test_valid_resolved_payload_ignored(self):
        response = self._post({
            "status": "resolved",
            "alerts": [],
        })
        self.assertEqual(response.status_code, 200)

    def test_valid_grouped_resolved_ignored(self):
        response = self._post({
            "alerts": [
                {
                    "status": "resolved",
                    "labels": {"service": "api", "alertname": "Test"},
                }
            ]
        })
        self.assertEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # 2. Executor submission failure releases capacity
    # ------------------------------------------------------------------
    def test_executor_submit_failure_releases_capacity(self):
        env = {"WHISPER_TOKEN": "test-token", "AIOPS_MAX_WORKERS": "1", "AIOPS_QUEUE_CAPACITY": "0"}
        with patch.dict(os.environ, env):
            import importlib
            from inframirror import watcher as w
            w._shutdown_executor()
            importlib.reload(w)

            try:
                failing_executor = MagicMock()
                failing_executor.submit.side_effect = RuntimeError("executor closed")

                def fake_get_executor():
                    return failing_executor

                with patch.object(w, "AIOPS_ENABLED", True), \
                     patch.object(w, "_get_executor", side_effect=fake_get_executor):
                    r1 = w.app.test_client().post(
                        "/whisper",
                        json={"service": "api", "alertname": "HighLatency"},
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(r1.status_code, 503)

                # After the failure the slot must be released, so a real submission succeeds.
                with patch.object(w, "AIOPS_ENABLED", True), \
                     patch.object(w, "_run_aiops_workflow") as workflow:
                    r2 = w.app.test_client().post(
                        "/whisper",
                        json={"service": "api", "alertname": "HighLatency2"},
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(r2.status_code, 202)
                    workflow.assert_called_once()
            finally:
                _clear_watcher_state(w)
                w._shutdown_executor()

    # ------------------------------------------------------------------
    # 3. Failed workflows are not marked completed without persistence
    # ------------------------------------------------------------------
    def test_successful_persistence_marks_completed(self):
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
            approved=True, action="restart_service", target="api", mode="recommend", reason="ok", confidence_threshold=0.75
        )
        with patch.object(watcher.incident_intelligence, "diagnose", return_value=fake_diagnosis), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=fake_policy), \
             patch.object(watcher.incident_store, "persist_incident") as persist, \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "recommend"), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")
            persist.assert_called_once()
        expected_sig = watcher._safe_signature("api", "Test", None)
        self.assertIn(expected_sig, watcher._completed)

    def test_fallback_persistence_marks_completed(self):
        captured = {}
        def capture(record):
            captured["record"] = record
        with patch.object(watcher.incident_intelligence, "diagnose", side_effect=ValueError("boom")), \
             patch.object(watcher.incident_store, "persist_incident", side_effect=capture), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "recommend"), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")
        self.assertIn("errors", captured["record"].to_dict())
        expected_sig = watcher._safe_signature("api", "Test", None)
        self.assertIn(expected_sig, watcher._completed)

    def test_total_persistence_failure_allows_immediate_retry(self):
        sig = watcher._safe_signature("api", "Test", None)

        def fail(_record):
            raise IOError("disk full")

        with patch.object(watcher.incident_intelligence, "diagnose", side_effect=ValueError("boom")), \
             patch.object(watcher.incident_store, "persist_incident", side_effect=fail), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "recommend"), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")

        # In-flight should be cleared after total failure and signature must not be completed.
        self.assertEqual(len(watcher._in_flight), 0)
        self.assertNotIn(sig, watcher._completed)

        # Should be able to run again immediately.
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
            approved=True, action="restart_service", target="api", mode="recommend", reason="ok", confidence_threshold=0.75
        )
        with patch.object(watcher.incident_intelligence, "diagnose", return_value=fake_diagnosis), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=fake_policy), \
             patch.object(watcher.incident_store, "persist_incident") as persist, \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "recommend"), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")
            persist.assert_called_once()

    # ------------------------------------------------------------------
    # 4. Multi-replica telemetry is order-independent
    # ------------------------------------------------------------------
    def _query_side_effect_for_replicas(self, expr, results):
        # results is a dict mapping metric substring to list of values
        for key, values in results.items():
            if key in expr:
                return [{"metric": {"__name__": key}, "value": (None, v)} for v in values]
        return []

    def test_cpu_latency_max_and_request_rate_sum(self):
        results = {
            "service_cpu_percent": [20.0, 50.0, 30.0],
            "service_latency_ms": [5.0, 100.0, 60.0],
            "service_requests_total": [10.0, 20.0, 30.0],
            "service_request_errors_total": [1.0, 2.0, 3.0],
            "up": [1.0, 1.0],
            "service_incident_active": [0.0, 0.0],
            "service_request_attempts_total": [10.0, 20.0, 30.0],
        }
        with patch.object(telemetry_collector, "_query_instant", side_effect=lambda expr, prom_url=None: self._query_side_effect_for_replicas(expr, results)):
            svc = telemetry_collector._collect_service_telemetry("api", prom_url="http://test")
        self.assertEqual(svc.cpu_percent, 50.0)
        self.assertEqual(svc.latency_ms, 100.0)
        self.assertEqual(svc.request_rate, 60.0)
        self.assertEqual(svc.error_rate, 6.0 / 60.0)
        self.assertTrue(svc.available)
        self.assertFalse(svc.incident_active)

    def test_dependency_down_any_replica_down_healthy_first(self):
        # First replica healthy, second unhealthy => dependency down.
        def query(expr, prom_url=None):
            if "service_dependency_up" in expr:
                return [
                    {"metric": {"dependency": "database"}, "value": (None, 1.0)},
                    {"metric": {"dependency": "database"}, "value": (None, 0.0)},
                ]
            if "service_dependency_latency_ms" in expr:
                return []
            return []

        with patch.object(telemetry_collector, "_query_instant", side_effect=query):
            svc = telemetry_collector._collect_service_telemetry("api", prom_url="http://test")
        self.assertIn("database", svc.dependencies)
        self.assertIs(svc.dependencies["database"].up, False)

    def test_dependency_down_any_replica_unhealthy_first(self):
        # Unhealthy first, then healthy => still down.
        def query(expr, prom_url=None):
            if "service_dependency_up" in expr:
                return [
                    {"metric": {"dependency": "database"}, "value": (None, 0.0)},
                    {"metric": {"dependency": "database"}, "value": (None, 1.0)},
                ]
            if "service_dependency_latency_ms" in expr:
                return []
            return []

        with patch.object(telemetry_collector, "_query_instant", side_effect=query):
            svc = telemetry_collector._collect_service_telemetry("api", prom_url="http://test")
        self.assertIn("database", svc.dependencies)
        self.assertIs(svc.dependencies["database"].up, False)

    def test_recovery_fails_when_one_replica_down(self):
        responses = {
            'up{job="api"}': [{"value": (None, 1.0)}, {"value": (None, 0.0)}],
            'service_cpu_percent{service="api"}': [{"value": (None, 50.0)}],
            'service_latency_ms{service="api"}': [{"value": (None, 100.0)}],
        }
        result = rv.verify_recovery(
            target="api",
            max_attempts=2,
            interval_seconds=0,
            required_consecutive=1,
            sleep_func=lambda s: None,
            query_func=lambda expr: responses.get(expr, []),
        )
        self.assertEqual(result.status, "not_recovered")

    # ------------------------------------------------------------------
    # 5. Non-finite webhook numbers rejected
    # ------------------------------------------------------------------
    def test_nan_cpu_rejected(self):
        response = self._post({"service": "api", "alertname": "Test", "cpu": float("nan")})
        self.assertEqual(response.status_code, 400)

    def test_inf_latency_rejected(self):
        response = self._post({"service": "api", "alertname": "Test", "latency": float("inf")})
        self.assertEqual(response.status_code, 400)

    def test_boolean_cpu_rejected(self):
        response = self._post({"service": "api", "alertname": "Test", "cpu": True})
        self.assertEqual(response.status_code, 400)

    def test_negative_latency_rejected(self):
        response = self._post({"service": "api", "alertname": "Test", "latency": -1})
        self.assertEqual(response.status_code, 400)

    def test_cpu_above_100_rejected(self):
        response = self._post({"service": "api", "alertname": "Test", "cpu": 150})
        self.assertEqual(response.status_code, 400)

    def test_latency_above_60000_rejected(self):
        response = self._post({"service": "api", "alertname": "Test", "latency": 70000})
        self.assertEqual(response.status_code, 400)

    def test_grouped_invalid_cpu_rejected(self):
        response = self._post({
            "alerts": [{"labels": {"service": "api", "alertname": "Test"}}],
            "cpu": float("inf"),
        })
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # 6. Effective execution mode forces recommend when healing disabled
    # ------------------------------------------------------------------
    def test_healing_disabled_forces_recommend_in_record(self):
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
        records = []
        with patch.object(watcher.incident_intelligence, "diagnose", return_value=fake_diagnosis), \
             patch.object(watcher.incident_store, "persist_incident", side_effect=records.append), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"), \
             patch.object(watcher, "HEALING", False), \
             patch.object(watcher, "_maybe_heal") as maybe_heal, \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")
            maybe_heal.assert_not_called()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.policy_decision["mode"], "recommend")
        self.assertFalse(record.execution_result["executed"])

    # ------------------------------------------------------------------
    # 7. Repeatable incidents after healthy transition
    # ------------------------------------------------------------------
    def test_healthy_transition_allows_new_incident(self):
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
            approved=True, action="restart_service", target="api", mode="recommend", reason="ok", confidence_threshold=0.75
        )
        with patch.object(watcher.incident_intelligence, "diagnose", return_value=fake_diagnosis), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=fake_policy), \
             patch.object(watcher.incident_store, "persist_incident"), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "recommend"), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")

        # Same incident is now suppressed.
        with patch.object(watcher.incident_intelligence, "diagnose", return_value=fake_diagnosis), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=fake_policy), \
             patch.object(watcher.incident_store, "persist_incident") as persist, \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "recommend"), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")
            persist.assert_not_called()

        # Record a healthy transition for the same signature.
        watcher._record_healthy_transition("api", "Test", None)

        # Now the same incident should be allowed again.
        with patch.object(watcher.incident_intelligence, "diagnose", return_value=fake_diagnosis), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=fake_policy), \
             patch.object(watcher.incident_store, "persist_incident") as persist, \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "recommend"), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")
            persist.assert_called_once()

    def test_service_healthy_clears_all_signatures_for_service(self):
        sig_a = watcher._safe_signature("api", "AlertA", None)
        sig_b = watcher._safe_signature("api", "AlertB", "database")
        watcher._completed[sig_a] = None
        watcher._completed_info[sig_a] = ("api", "AlertA", None, None, None)
        watcher._completed[sig_b] = None
        watcher._completed_info[sig_b] = ("api", "AlertB", "database", None, None)
        watcher._record_service_healthy("api")
        self.assertNotIn(sig_a, watcher._completed)
        self.assertNotIn(sig_b, watcher._completed)

    # ------------------------------------------------------------------
    # Conservative up aggregation in fast watcher path
    # ------------------------------------------------------------------
    def _patch_quick_signals(self, up_values, cpu=20.0, lat=40.0, incident=0.0, err_ratio=None):
        def fake_quick_signals(service):
            return (
                [(None, cpu)],
                [(None, lat)],
                [(None, v) for v in up_values],
                [(None, incident)],
                err_ratio,
            )
        return patch.object(watcher, "_quick_signals", side_effect=fake_quick_signals)

    def test_up_healthy_first_down_second_triggers_aiops(self):
        with self._patch_quick_signals([1.0, 0.0]), \
             patch.object(watcher, "AIOPS_ENABLED", True), \
             patch.object(watcher, "_run_aiops_workflow") as workflow:
            watcher._diagnose("api")
            workflow.assert_called_once()

    def test_up_down_first_healthy_second_triggers_aiops(self):
        with self._patch_quick_signals([0.0, 1.0]), \
             patch.object(watcher, "AIOPS_ENABLED", True), \
             patch.object(watcher, "_run_aiops_workflow") as workflow:
            watcher._diagnose("api")
            workflow.assert_called_once()

    def test_up_all_healthy_no_aiops(self):
        with self._patch_quick_signals([1.0, 1.0]), \
             patch.object(watcher, "AIOPS_ENABLED", True), \
             patch.object(watcher, "_run_aiops_workflow") as workflow:
            watcher._diagnose("api")
            workflow.assert_not_called()

    def test_up_no_data_no_aiops(self):
        with self._patch_quick_signals([]), \
             patch.object(watcher, "AIOPS_ENABLED", True), \
             patch.object(watcher, "_run_aiops_workflow") as workflow:
            watcher._diagnose("api")
            workflow.assert_not_called()

    # ------------------------------------------------------------------
    # Dedup state hygiene: many unique resolved fingerprints are cleaned
    # ------------------------------------------------------------------
    def test_many_resolved_fingerprints_clean_state(self):
        # Simulate many completed incidents with unique fingerprints, then resolve each one.
        for i in range(50):
            sig = watcher._safe_signature("api", "HighLatency", None, fingerprint=f"fp-{i}", starts_at=f"ts-{i}")
            watcher._completed[sig] = None
            watcher._completed_info[sig] = ("api", "HighLatency", None, f"fp-{i}", f"ts-{i}")

        # Record healthy transition for each unique fingerprint.
        for i in range(50):
            watcher._record_healthy_transition("api", "HighLatency", None, fingerprint=f"fp-{i}", starts_at=f"ts-{i}")

        self.assertEqual(len(watcher._completed), 0)
        self.assertEqual(len(watcher._completed_info), 0)
        self.assertFalse(hasattr(watcher, "_last_healthy_at"))


class TestRuntimeDefects(unittest.TestCase):
    def setUp(self):
        self._orig_startup = watcher._startup_monotonic
        self._orig_grace = watcher.AIOPS_EXECUTION_GRACE_SEC

    def tearDown(self):
        _clear_watcher_state(watcher)
        watcher._startup_monotonic = self._orig_startup
        watcher.AIOPS_EXECUTION_GRACE_SEC = self._orig_grace

    def _make_diagnosis(self, target="database", action="restart_service", confidence=0.9):
        return StructuredDiagnosis(
            probable_cause_service=target,
            probable_cause="overload",
            confidence=confidence,
            affected_services=[target],
            evidence=[EvidenceItem(service=target, signal="cpu", value=90.0, interpretation="high")],
            recommended_action=RecommendedAction(type=action, target_service=target if action == "restart_service" else None, reason="test"),
            risk="low",
            source="rules",
        )

    def _make_policy(self, target="database", mode="execute"):
        return aiops_models.PolicyDecision(
            approved=True, action="restart_service", target=target, mode=mode, reason="ok", confidence_threshold=0.75
        )

    def _snapshot(self):
        return TelemetrySnapshot(
            incident_id="test-id",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service="api", alertname="Test", reason="test"),
            services={},
            active_alerts=[],
        )

    # ------------------------------------------------------------------
    # DEFECT 1: Atomic target-level remediation arbitration
    # ------------------------------------------------------------------
    def test_concurrent_same_target_executes_once(self):
        records = []
        proceed = threading.Event()
        lock = threading.Lock()
        call_count = [0]

        def slow_maybe_heal(target, reason):
            with lock:
                call_count[0] += 1
            # Wait for main thread to allow the winner to proceed.
            proceed.wait(timeout=5)
            return True

        def run_workflow(alertname):
            watcher._run_aiops_workflow("api", alertname, "test")

        patches = [
            patch.object(telemetry_collector, "collect_telemetry_snapshot", return_value=self._snapshot()),
            patch.object(rv, "verify_recovery", return_value=aiops_models.RecoveryResult(status="recovered", details="ok")),
            patch.object(watcher.incident_intelligence, "diagnose", return_value=self._make_diagnosis()),
            patch.object(watcher.policy_engine, "evaluate_policy", return_value=self._make_policy()),
            patch.object(watcher.incident_store, "persist_incident", side_effect=records.append),
            patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"),
            patch.object(watcher, "HEALING", True),
            patch.object(watcher, "AIOPS_EXECUTION_GRACE_SEC", 0),
            patch.object(watcher.policy_engine, "has_supporting_abnormal_telemetry", return_value=True),
            patch.object(watcher, "_maybe_heal", side_effect=slow_maybe_heal),
            patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"),
        ]
        for p in patches:
            p.start()
        try:
            threads = [threading.Thread(target=run_workflow, args=(f"Alert{i}",)) for i in range(3)]
            for t in threads:
                t.start()
            # Give workers time to race for the lease.
            time.sleep(0.2)
            proceed.set()
            for t in threads:
                t.join(timeout=5)
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(call_count[0], 1, "_maybe_heal should run exactly once for the same target")
        executed = [r for r in records if r.execution_result["executed"]]
        self.assertEqual(len(executed), 1, "Only one record may have executed=True")
        suppressed = [r for r in records if not r.execution_result["executed"]]
        self.assertGreaterEqual(len(suppressed), 2)
        for r in suppressed:
            self.assertIn("in progress", r.execution_result["details"].lower())
            self.assertEqual(r.recovery_result["status"], "not_executed")
        self.assertEqual(len(watcher._target_leases), 0)

    def test_concurrent_different_targets_both_execute(self):
        targets_executed = []
        barrier = threading.Barrier(2)
        lock = threading.Lock()

        def maybe_heal(target, reason):
            with lock:
                targets_executed.append(target)
            barrier.wait(timeout=5)
            return True

        def snapshot_for_alertname(trigger_service, alertname, reason):
            target = "database" if alertname == "AlertA" else "cache"
            abnormal_telemetry = ServiceTelemetry(
                cpu_percent=90.0,
                latency_ms=400.0,
                available=True,
                incident_active=True,
            )
            return TelemetrySnapshot(
                incident_id=f"test-{target}",
                observed_at="2024-01-01T00:00:00+00:00",
                trigger=Trigger(service=trigger_service, alertname=alertname, reason=reason),
                services={
                    trigger_service: abnormal_telemetry,
                    target: abnormal_telemetry,
                },
                active_alerts=[],
            )

        def diagnose_for_target(snapshot, *args, **kwargs):
            target = "database" if snapshot.trigger.alertname == "AlertA" else "cache"
            return self._make_diagnosis(target=target)

        def policy_for_target(snapshot, diagnosis, *args, **kwargs):
            target = diagnosis.recommended_action.target_service
            return self._make_policy(target=target)

        def run_workflow(alertname, target):
            watcher._run_aiops_workflow("api", alertname, "test")

        patches = [
            patch.object(telemetry_collector, "collect_telemetry_snapshot", side_effect=snapshot_for_alertname),
            patch.object(rv, "verify_recovery", return_value=aiops_models.RecoveryResult(status="recovered", details="ok")),
            patch.object(watcher.incident_intelligence, "diagnose", side_effect=diagnose_for_target),
            patch.object(watcher.policy_engine, "evaluate_policy", side_effect=policy_for_target),
            patch.object(watcher.incident_store, "persist_incident"),
            patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"),
            patch.object(watcher, "HEALING", True),
            patch.object(watcher, "AIOPS_EXECUTION_GRACE_SEC", 0),
            patch.object(watcher, "_maybe_heal", side_effect=maybe_heal),
            patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"),
        ]
        for p in patches:
            p.start()
        try:
            t1 = threading.Thread(target=run_workflow, args=("AlertA", "database"))
            t2 = threading.Thread(target=run_workflow, args=("AlertB", "cache"))
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(targets_executed.count("database"), 1)
        self.assertEqual(targets_executed.count("cache"), 1)
        self.assertEqual(len(watcher._target_leases), 0)

    def test_target_lease_pruning_keeps_state_bounded(self):
        watcher._target_leases["api"] = time.monotonic() - 1000.0
        watcher._target_leases["database"] = time.monotonic() - 10.0
        watcher._prune_target_leases(time.monotonic())
        self.assertNotIn("api", watcher._target_leases)
        self.assertIn("database", watcher._target_leases)

    def test_restart_exception_releases_lease(self):
        def raise_maybe_heal(target, reason):
            raise RuntimeError("docker boom")

        records = []
        with patch.object(telemetry_collector, "collect_telemetry_snapshot", return_value=self._snapshot()), \
             patch.object(rv, "verify_recovery", return_value=aiops_models.RecoveryResult(status="recovered", details="ok")), \
             patch.object(watcher.incident_intelligence, "diagnose", return_value=self._make_diagnosis()), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=self._make_policy()), \
             patch.object(watcher.incident_store, "persist_incident", side_effect=records.append), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"), \
             patch.object(watcher, "HEALING", True), \
             patch.object(watcher, "AIOPS_EXECUTION_GRACE_SEC", 0), \
             patch.object(watcher.policy_engine, "has_supporting_abnormal_telemetry", return_value=True), \
             patch.object(watcher, "_maybe_heal", side_effect=raise_maybe_heal), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")

        self.assertEqual(len(watcher._target_leases), 0)
        record = records[0]
        self.assertFalse(record.execution_result["executed"])

    # ------------------------------------------------------------------
    # DEFECT 2: Execution startup grace
    # ------------------------------------------------------------------
    def test_execution_mode_grace_period_forces_recommend(self):
        # Simulate a fresh startup with a clock that has not yet advanced past grace.
        watcher._startup_monotonic = 0.0
        watcher.AIOPS_EXECUTION_GRACE_SEC = 30

        def fake_monotonic():
            return 5.0

        with patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"), patch.object(watcher, "HEALING", True):
            self.assertEqual(watcher._effective_execution_mode(monotonic_func=fake_monotonic), "recommend")

    def test_execution_mode_after_grace_allows_execute(self):
        watcher._startup_monotonic = 0.0
        watcher.AIOPS_EXECUTION_GRACE_SEC = 30

        def late_monotonic():
            return 100.0

        with patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"), patch.object(watcher, "HEALING", True):
            self.assertEqual(watcher._effective_execution_mode(monotonic_func=late_monotonic), "execute")

    def test_grace_period_persists_recommend_mode(self):
        records = []
        watcher._startup_monotonic = 0.0
        watcher.AIOPS_EXECUTION_GRACE_SEC = 30

        def early_monotonic():
            return 5.0

        with patch.object(watcher, "_monotonic_time_func", early_monotonic), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"), \
             patch.object(watcher, "HEALING", True), \
             patch.object(telemetry_collector, "collect_telemetry_snapshot", return_value=self._snapshot()), \
             patch.object(watcher.incident_intelligence, "diagnose", return_value=self._make_diagnosis()), \
             patch.object(watcher.policy_engine, "evaluate_policy") as eval_policy, \
             patch.object(watcher.incident_store, "persist_incident", side_effect=records.append), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            eval_policy.return_value = aiops_models.PolicyDecision(
                approved=True, action="restart_service", target="database", mode="recommend", reason="ok", confidence_threshold=0.75
            )
            watcher._run_aiops_workflow("api", "Test", "test")
            # Policy must be evaluated in recommend mode during grace.
            self.assertEqual(eval_policy.call_args.kwargs["mode"], "recommend")

    def test_grace_boundary_single_mode_value(self):
        """A workflow that begins during grace must stay recommend-only even if grace expires while running."""
        records = []
        watcher._startup_monotonic = 0.0
        watcher.AIOPS_EXECUTION_GRACE_SEC = 30
        call_count = [0]

        def shifting_clock():
            call_count[0] += 1
            # First call is inside grace; any later call would be outside grace.
            return 5.0 if call_count[0] == 1 else 100.0

        def policy_respecting_mode(*args, **kwargs):
            mode = kwargs.get("mode", "recommend")
            return aiops_models.PolicyDecision(
                approved=True, action="restart_service", target="database", mode=mode, reason="ok", confidence_threshold=0.75
            )

        with patch.object(watcher, "_monotonic_time_func", shifting_clock), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"), \
             patch.object(watcher, "HEALING", True), \
             patch.object(telemetry_collector, "collect_telemetry_snapshot", return_value=self._snapshot()), \
             patch.object(watcher.incident_intelligence, "diagnose", return_value=self._make_diagnosis()), \
             patch.object(watcher.policy_engine, "evaluate_policy", side_effect=policy_respecting_mode), \
             patch.object(watcher.incident_store, "persist_incident", side_effect=records.append), \
             patch.object(watcher, "_maybe_heal") as maybe_heal, \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")
            maybe_heal.assert_not_called()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].policy_decision["mode"], "recommend")
        self.assertFalse(records[0].execution_result["executed"])
        self.assertEqual(call_count[0], 1, "effective_mode must be evaluated exactly once per workflow")

    # ------------------------------------------------------------------
    # DEFECT 3: Error-ratio consistency
    # ------------------------------------------------------------------
    def _service_telemetry_with_error_rate(self, error_rate):
        return ServiceTelemetry(
            cpu_percent=20.0,
            latency_ms=40.0,
            request_rate=100.0,
            error_rate=error_rate,
            available=True,
            incident_active=False,
            dependencies={},
        )

    def test_error_ratio_0_percent_no_action(self):
        from inframirror import incident_intelligence as ii
        services = {s: self._service_telemetry_with_error_rate(0.0) for s in ALLOWED_SERVICES}
        snapshot = TelemetrySnapshot(
            incident_id="test-0",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service="api", alertname="Test", reason="test"),
            services=services,
            active_alerts=[],
        )
        diag = ii.rules_based_diagnosis(snapshot, err_ratio_threshold=0.10)
        self.assertEqual(diag.recommended_action.type, "no_action")

    def test_error_ratio_1_percent_no_action(self):
        from inframirror import incident_intelligence as ii
        services = {s: self._service_telemetry_with_error_rate(0.0) for s in ALLOWED_SERVICES}
        services["api"] = self._service_telemetry_with_error_rate(0.01)
        snapshot = TelemetrySnapshot(
            incident_id="test-1",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service="api", alertname="Test", reason="test"),
            services=services,
            active_alerts=[],
        )
        diag = ii.rules_based_diagnosis(snapshot, err_ratio_threshold=0.10)
        self.assertEqual(diag.recommended_action.type, "no_action")

    def test_error_ratio_9_9_percent_no_action(self):
        from inframirror import incident_intelligence as ii
        services = {s: self._service_telemetry_with_error_rate(0.0) for s in ALLOWED_SERVICES}
        services["api"] = self._service_telemetry_with_error_rate(0.099)
        snapshot = TelemetrySnapshot(
            incident_id="test-99",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service="api", alertname="Test", reason="test"),
            services=services,
            active_alerts=[],
        )
        diag = ii.rules_based_diagnosis(snapshot, err_ratio_threshold=0.10)
        self.assertEqual(diag.recommended_action.type, "no_action")

    def test_error_ratio_10_percent_triggers_restart(self):
        from inframirror import incident_intelligence as ii
        services = {s: self._service_telemetry_with_error_rate(0.0) for s in ALLOWED_SERVICES}
        services["api"] = self._service_telemetry_with_error_rate(0.10)
        snapshot = TelemetrySnapshot(
            incident_id="test-10",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service="api", alertname="Test", reason="test"),
            services=services,
            active_alerts=[],
        )
        diag = ii.rules_based_diagnosis(snapshot, err_ratio_threshold=0.10)
        self.assertEqual(diag.recommended_action.type, "restart_service")

    def test_error_ratio_25_percent_triggers_restart(self):
        from inframirror import incident_intelligence as ii
        services = {s: self._service_telemetry_with_error_rate(0.0) for s in ALLOWED_SERVICES}
        services["api"] = self._service_telemetry_with_error_rate(0.25)
        snapshot = TelemetrySnapshot(
            incident_id="test-25",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service="api", alertname="Test", reason="test"),
            services=services,
            active_alerts=[],
        )
        diag = ii.rules_based_diagnosis(snapshot, err_ratio_threshold=0.10)
        self.assertEqual(diag.recommended_action.type, "restart_service")

    def test_dependency_failure_overrides_error_ratio(self):
        from inframirror import incident_intelligence as ii
        services = {s: self._service_telemetry_with_error_rate(0.0) for s in ALLOWED_SERVICES}
        api_svc = self._service_telemetry_with_error_rate(0.25)
        api_svc.dependencies["database"] = DependencyInfo(up=False, latency_ms=None)
        services["api"] = api_svc
        snapshot = TelemetrySnapshot(
            incident_id="test-dep",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service="api", alertname="Test", reason="test"),
            services=services,
            active_alerts=[],
        )
        diag = ii.rules_based_diagnosis(snapshot, err_ratio_threshold=0.10)
        self.assertEqual(diag.probable_cause_service, "database")

    def test_unavailable_with_low_error_rate_uses_availability_evidence(self):
        from inframirror import incident_intelligence as ii
        services = {s: self._service_telemetry_with_error_rate(0.0) for s in ALLOWED_SERVICES}
        # api is unavailable with a 1% residual error ratio.
        api_svc = ServiceTelemetry(
            cpu_percent=20.0,
            latency_ms=40.0,
            request_rate=100.0,
            error_rate=0.01,
            available=False,
            incident_active=False,
            dependencies={},
        )
        services["api"] = api_svc
        snapshot = TelemetrySnapshot(
            incident_id="test-avail-evidence",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service="api", alertname="Test", reason="test"),
            services=services,
            active_alerts=[],
        )
        diag = ii.rules_based_diagnosis(snapshot, err_ratio_threshold=0.10)
        self.assertEqual(diag.probable_cause_service, "api")
        self.assertEqual(diag.evidence[0].signal, "available")

    def test_error_rate_evidence_only_above_threshold(self):
        from inframirror import incident_intelligence as ii
        threshold = 0.10
        for error_rate in (0.0, 0.01, 0.05, 0.099):
            services = {s: self._service_telemetry_with_error_rate(0.0) for s in ALLOWED_SERVICES}
            svc = self._service_telemetry_with_error_rate(error_rate)
            svc.dependencies["database"] = DependencyInfo(up=True, latency_ms=None)
            services["api"] = svc
            snapshot = TelemetrySnapshot(
                incident_id=f"test-ev-{error_rate}",
                observed_at="2024-01-01T00:00:00+00:00",
                trigger=Trigger(service="api", alertname="Test", reason="test"),
                services=services,
                active_alerts=[],
            )
            diag = ii.rules_based_diagnosis(snapshot, err_ratio_threshold=threshold)
            self.assertEqual(diag.probable_cause_service, "api")
            if error_rate < threshold:
                self.assertNotEqual(diag.evidence[0].signal, "error_rate")
            else:
                self.assertEqual(diag.evidence[0].signal, "error_rate")

        for error_rate in (0.10, 0.25):
            services = {s: self._service_telemetry_with_error_rate(0.0) for s in ALLOWED_SERVICES}
            services["api"] = self._service_telemetry_with_error_rate(error_rate)
            snapshot = TelemetrySnapshot(
                incident_id=f"test-ev-{error_rate}",
                observed_at="2024-01-01T00:00:00+00:00",
                trigger=Trigger(service="api", alertname="Test", reason="test"),
                services=services,
                active_alerts=[],
            )
            diag = ii.rules_based_diagnosis(snapshot, err_ratio_threshold=threshold)
            self.assertEqual(diag.probable_cause_service, "api")
            self.assertEqual(diag.evidence[0].signal, "error_rate")

    # ------------------------------------------------------------------
    # DEFECT 4: Execution revalidation
    # ------------------------------------------------------------------
    def test_execution_revalidation_suppresses_stale_target(self):
        records = []
        first = [True]

        def changing_policy(*args, **kwargs):
            if first[0]:
                first[0] = False
                return self._make_policy()
            return aiops_models.PolicyDecision(
                approved=False, action="no_action", target=None, mode="execute", reason="stale", confidence_threshold=0.75
            )

        with patch.object(telemetry_collector, "collect_telemetry_snapshot", return_value=self._snapshot()), \
             patch.object(watcher.incident_intelligence, "diagnose", return_value=self._make_diagnosis()), \
             patch.object(watcher.policy_engine, "evaluate_policy", side_effect=changing_policy), \
             patch.object(watcher.incident_store, "persist_incident", side_effect=records.append), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"), \
             patch.object(watcher, "HEALING", True), \
             patch.object(watcher, "AIOPS_EXECUTION_GRACE_SEC", 0), \
             patch.object(watcher, "_maybe_heal") as maybe_heal, \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")
            maybe_heal.assert_not_called()

        record = records[0]
        self.assertFalse(record.execution_result["executed"])
        self.assertIn("stale", record.execution_result["details"].lower())

    def test_execution_revalidation_suppresses_missing_telemetry(self):
        records = []
        # Diagnosis targets cache, but supporting telemetry check fails.
        diag = self._make_diagnosis(target="cache")
        policy = self._make_policy(target="cache")
        with patch.object(telemetry_collector, "collect_telemetry_snapshot", return_value=self._snapshot()), \
             patch.object(watcher.incident_intelligence, "diagnose", return_value=diag), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=policy), \
             patch.object(watcher.policy_engine, "has_supporting_abnormal_telemetry", return_value=False), \
             patch.object(watcher.incident_store, "persist_incident", side_effect=records.append), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"), \
             patch.object(watcher, "HEALING", True), \
             patch.object(watcher, "AIOPS_EXECUTION_GRACE_SEC", 0), \
             patch.object(watcher, "_maybe_heal") as maybe_heal, \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            watcher._run_aiops_workflow("api", "Test", "test")
            maybe_heal.assert_not_called()

        record = records[0]
        self.assertFalse(record.execution_result["executed"])
        self.assertIn("abnormal supporting telemetry", record.execution_result["details"].lower())


    def test_watcher_passes_probe_only_for_api_dependency_incidents(self):
        """Watcher should inject the active API probe only for API dependency incidents."""
        records = []
        diag = self._make_diagnosis(target="database")
        policy = self._make_policy(target="database")
        passed_kwargs = {}

        def capture_verify(*args, **kwargs):
            passed_kwargs.update(kwargs)
            return aiops_models.RecoveryResult(status="recovered", details="ok")

        with patch.object(telemetry_collector, "collect_telemetry_snapshot", return_value=self._snapshot()), \
             patch.object(watcher.incident_intelligence, "diagnose", return_value=diag), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=policy), \
             patch.object(watcher.policy_engine, "has_supporting_abnormal_telemetry", return_value=True), \
             patch.object(watcher.incident_store, "persist_incident", side_effect=records.append), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"), \
             patch.object(watcher, "HEALING", True), \
             patch.object(watcher, "AIOPS_EXECUTION_GRACE_SEC", 0), \
             patch.object(watcher, "_maybe_heal", return_value=True), \
             patch.object(watcher.recovery_verifier, "verify_recovery", side_effect=capture_verify), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            # API dependency incident should pass the real probe.
            watcher._run_aiops_workflow("api", "Test", "test", dependency="database")
            self.assertIs(passed_kwargs.get("dependency_probe_func"), watcher._probe_api_dependency)
            self.assertEqual(passed_kwargs.get("dependency"), "database")

    def test_watcher_does_not_pass_probe_for_non_api_dependency_incidents(self):
        """Non-API incidents should not inject the API dependency probe."""
        records = []
        diag = self._make_diagnosis(target="database")
        policy = self._make_policy(target="database")
        passed_kwargs = {}

        def capture_verify(*args, **kwargs):
            passed_kwargs.update(kwargs)
            return aiops_models.RecoveryResult(status="recovered", details="ok")

        with patch.object(telemetry_collector, "collect_telemetry_snapshot", return_value=self._snapshot()), \
             patch.object(watcher.incident_intelligence, "diagnose", return_value=diag), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=policy), \
             patch.object(watcher.policy_engine, "has_supporting_abnormal_telemetry", return_value=True), \
             patch.object(watcher.incident_store, "persist_incident", side_effect=records.append), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"), \
             patch.object(watcher, "HEALING", True), \
             patch.object(watcher, "AIOPS_EXECUTION_GRACE_SEC", 0), \
             patch.object(watcher, "_maybe_heal", return_value=True), \
             patch.object(watcher.recovery_verifier, "verify_recovery", side_effect=capture_verify), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            # Database incident (not API) should not pass the API probe.
            watcher._run_aiops_workflow("database", "Test", "test", dependency="api")
            self.assertIsNone(passed_kwargs.get("dependency_probe_func"))

    def test_watcher_no_probe_for_api_without_dependency(self):
        """API incidents without a dependency label should not pass the API probe."""
        records = []
        diag = self._make_diagnosis(target="api")
        policy = self._make_policy(target="api")
        passed_kwargs = {}

        def capture_verify(*args, **kwargs):
            passed_kwargs.update(kwargs)
            return aiops_models.RecoveryResult(status="recovered", details="ok")

        with patch.object(telemetry_collector, "collect_telemetry_snapshot", return_value=self._snapshot()), \
             patch.object(watcher.incident_intelligence, "diagnose", return_value=diag), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=policy), \
             patch.object(watcher.policy_engine, "has_supporting_abnormal_telemetry", return_value=True), \
             patch.object(watcher.incident_store, "persist_incident", side_effect=records.append), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"), \
             patch.object(watcher, "HEALING", True), \
             patch.object(watcher, "AIOPS_EXECUTION_GRACE_SEC", 0), \
             patch.object(watcher, "_maybe_heal", return_value=True), \
             patch.object(watcher.recovery_verifier, "verify_recovery", side_effect=capture_verify), \
             patch.object(watcher.llm_engine, "generate_aiops_incident_dialogue"):
            # API incident with no dependency label should not pass the API probe.
            watcher._run_aiops_workflow("api", "Test", "test")
            self.assertIsNone(passed_kwargs.get("dependency_probe_func"))
            self.assertIsNone(passed_kwargs.get("dependency"))

    def test_probe_api_dependency_parses_work_endpoint(self):
        """_probe_api_dependency inspects dependencies[dependency].up."""
        import requests

        class FakeResponse:
            status_code = 200
            def json(self):
                return {
                    "dependencies": {
                        "database": {"up": True},
                        "cache": {"up": False},
                    }
                }

        with patch.object(requests, "get", return_value=FakeResponse()) as mock_get:
            self.assertTrue(watcher._probe_api_dependency("database"))
            self.assertFalse(watcher._probe_api_dependency("cache"))
            mock_get.assert_called_with("http://api:5051/work", timeout=2)

    def test_probe_api_dependency_handles_503_with_valid_json(self):
        """503 responses with valid dependency JSON must be inspected."""
        import requests

        class FakeResponse:
            status_code = 503
            def json(self):
                return {"dependencies": {"database": {"up": True}}}

        with patch.object(requests, "get", return_value=FakeResponse()):
            self.assertTrue(watcher._probe_api_dependency("database"))

    def test_probe_api_dependency_rejects_untrusted_dependency(self):
        """Only allowlisted dependencies may be probed."""
        import requests

        class FakeResponse:
            status_code = 200
            def json(self):
                return {"dependencies": {"hacker": {"up": True}}}

        with patch.object(requests, "get", return_value=FakeResponse()) as mock_get:
            self.assertFalse(watcher._probe_api_dependency("hacker"))
            mock_get.assert_not_called()

    def test_probe_api_dependency_returns_false_on_failure(self):
        """Timeouts, bad JSON, missing fields, and exceptions return False."""
        import requests

        class BadJSON:
            def json(self):
                raise ValueError("not json")

        class MissingField:
            def json(self):
                return {"dependencies": {}}

        with patch.object(requests, "get", side_effect=requests.Timeout("timeout")):
            self.assertFalse(watcher._probe_api_dependency("database"))

        with patch.object(requests, "get", return_value=BadJSON()):
            self.assertFalse(watcher._probe_api_dependency("database"))

        with patch.object(requests, "get", return_value=MissingField()):
            self.assertFalse(watcher._probe_api_dependency("database"))


if __name__ == "__main__":
    unittest.main()
