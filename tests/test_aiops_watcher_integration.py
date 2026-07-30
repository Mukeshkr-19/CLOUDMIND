import unittest
import sys
import os
import tempfile
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from inframirror import watcher
from inframirror import incident_store as inc_store


class TestAIOpsWatcherIntegration(unittest.TestCase):
    def tearDown(self):
        watcher._last_heal.clear()
        watcher._last_dialogue.clear()
        watcher._in_flight.clear()
        watcher._completed.clear()

    def test_no_duplicate_restart_between_polling_and_webhook(self):
        # AIOps mode should centralize decision making so a single incident is handled once.
        # Invoke the workflow twice for the same service within the cooldown window and
        # assert the trusted healing function is only called once.
        fake_diagnosis = watcher.aiops_models.StructuredDiagnosis(
            probable_cause_service="api",
            probable_cause="CPU high",
            confidence=0.9,
            affected_services=["api"],
            evidence=[watcher.aiops_models.EvidenceItem(service="api", signal="cpu", value=90.0, interpretation="high")],
            recommended_action=watcher.aiops_models.RecommendedAction(type="restart_service", target_service="api", reason="test"),
            risk="low",
            source="rules",
        )
        fake_policy = watcher.aiops_models.PolicyDecision(
            approved=True, action="restart_service", target="api", mode="execute", reason="ok", confidence_threshold=0.75
        )
        fake_recovery = watcher.aiops_models.RecoveryResult(status="recovered", details="ok")
        with patch.object(watcher.incident_intelligence, "diagnose", return_value=fake_diagnosis), \
             patch.object(watcher.policy_engine, "evaluate_policy", return_value=fake_policy), \
             patch.object(watcher.incident_store, "persist_incident"), \
             patch.object(watcher, "HEALING", True), \
             patch.object(watcher, "AIOPS_EXECUTION_MODE", "execute"), \
             patch.object(watcher, "AIOPS_EXECUTION_GRACE_SEC", 0), \
             patch.object(watcher.policy_engine, "has_supporting_abnormal_telemetry", return_value=True), \
             patch.object(watcher, "_maybe_heal", return_value=True) as maybe_heal, \
             patch.object(watcher.recovery_verifier, "verify_recovery", return_value=fake_recovery):
            watcher._run_aiops_workflow("api", "Test", "first")
            watcher._run_aiops_workflow("api", "Test", "second")
        self.assertEqual(maybe_heal.call_count, 1, "Trusted healing should only run once within cooldown")

    def test_aiops_mode_runs_workflow(self):
        # Simulate abnormal signals so the AIOps workflow is actually triggered.
        def fake_signals(service):
            return [(None, 95.0)], [(None, 500.0)], [(None, 1.0)], [(None, 1.0)], 0.5
        with patch.object(watcher, "AIOPS_ENABLED", True), \
             patch.object(watcher, "HEALING", False), \
             patch.object(watcher, "_quick_signals", side_effect=fake_signals), \
             patch.object(watcher, "_has_unhealthy_dependency", return_value=(False, None)), \
             patch.object(watcher, "_run_aiops_workflow") as workflow:
            watcher._diagnose("api")
            workflow.assert_called_once()

    def test_whisper_resolved_alert_still_ignored(self):
        payload = {
            "status": "resolved",
            "alerts": [{"status": "resolved", "labels": {"service": "api"}}],
        }
        with patch.dict(os.environ, {"WHISPER_TOKEN": "test-token"}):
            response = watcher.app.test_client().post(
                "/whisper",
                json=payload,
                headers={"Authorization": "Bearer test-token"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reason"], "alert resolved")

    def test_whisper_auth_required(self):
        with patch.dict(os.environ, {"WHISPER_TOKEN": "test-token"}):
            response = watcher.app.test_client().post(
                "/whisper",
                json={"service": "api", "cpu": 90, "latency": 350},
            )
        self.assertEqual(response.status_code, 401)

    def test_incident_persistence(self):
        tmp_dir = tempfile.mkdtemp()
        path = os.path.join(tmp_dir, "aiops_incidents.json")
        with patch.dict(os.environ, {"SHARED_DATA_DIR": tmp_dir}):
            from inframirror.aiops_models import TelemetrySnapshot, Trigger, ServiceTelemetry, StructuredDiagnosis, RecommendedAction, ExecutionResult, RecoveryResult
            from inframirror.policy_engine import evaluate_policy
            from inframirror.incident_store import persist_incident

            snapshot = TelemetrySnapshot(
                incident_id="inc-1",
                observed_at="2024-01-01T00:00:00+00:00",
                trigger=Trigger(service="api", alertname="test", reason="test"),
                services={"api": ServiceTelemetry(cpu_percent=90.0, latency_ms=100.0)},
                active_alerts=[],
            )
            diagnosis = StructuredDiagnosis(
                probable_cause_service="api",
                probable_cause="test",
                confidence=0.9,
                affected_services=["api"],
                evidence=[],
                recommended_action=RecommendedAction(type="restart_service", target_service="api", reason="test"),
                risk="low",
                source="rules",
            )
            policy = evaluate_policy(snapshot, diagnosis, mode="recommend")
            record = inc_store.IncidentRecord.from_diagnosis(
                snapshot=snapshot,
                diagnosis=diagnosis,
                policy_decision=policy,
                execution_result=ExecutionResult(executed=False, target=None, details=""),
                recovery_result=RecoveryResult(status="not_executed", details=""),
            )
            persist_incident(record, path=path)
            # Load and verify
            data = inc_store.load_incidents(path=path)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["incident_id"], "inc-1")

            # Add more records and verify cap/ordering
            for i in range(110):
                snapshot_i = TelemetrySnapshot(
                    incident_id=f"inc-{i+2}",
                    observed_at="2024-01-01T00:00:00+00:00",
                    trigger=Trigger(service="api", alertname="test", reason="test"),
                    services={"api": ServiceTelemetry(cpu_percent=90.0, latency_ms=100.0)},
                    active_alerts=[],
                )
                record_i = inc_store.IncidentRecord.from_diagnosis(
                    snapshot=snapshot_i,
                    diagnosis=diagnosis,
                    policy_decision=policy,
                    execution_result=ExecutionResult(executed=False, target=None, details=""),
                    recovery_result=RecoveryResult(status="not_executed", details=""),
                )
                persist_incident(record_i, path=path)
            data = inc_store.load_incidents(path=path)
            self.assertEqual(len(data), 100)
            self.assertEqual(data[0]["incident_id"], "inc-111")


if __name__ == "__main__":
    unittest.main()
