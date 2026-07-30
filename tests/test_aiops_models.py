import math
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from inframirror.aiops_models import (
    ALLOWED_SERVICES,
    ALLOWED_ACTIONS,
    DependencyInfo,
    ServiceTelemetry,
    TelemetrySnapshot,
    Trigger,
    EvidenceItem,
    RecommendedAction,
    StructuredDiagnosis,
    PolicyDecision,
    RecoveryResult,
    ExecutionResult,
    IncidentRecord,
)


class TestAIOpsModels(unittest.TestCase):
    def _valid_snapshot(self):
        return TelemetrySnapshot(
            incident_id="abc-123",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service="api", alertname="HighLatency", reason="test"),
            services={
                "api": ServiceTelemetry(cpu_percent=10.0, latency_ms=50.0),
                "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=30.0),
                "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
                "cache": ServiceTelemetry(cpu_percent=5.0, latency_ms=20.0),
                "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
            },
            active_alerts=[],
        )

    def test_dependency_info_valid(self):
        dep = DependencyInfo(up=True, latency_ms=10.0)
        self.assertTrue(dep.up)
        self.assertEqual(dep.latency_ms, 10.0)

    def test_service_telemetry_rejects_nan(self):
        with self.assertRaises(ValueError):
            ServiceTelemetry(cpu_percent=float("nan"))

    def test_service_telemetry_rejects_inf(self):
        with self.assertRaises(ValueError):
            ServiceTelemetry(latency_ms=float("inf"))

    def test_telemetry_snapshot_unknown_service(self):
        with self.assertRaises(ValueError):
            TelemetrySnapshot(
                incident_id="x",
                observed_at="2024-01-01T00:00:00+00:00",
                trigger=Trigger(service="api"),
                services={"unknown": ServiceTelemetry()},
                active_alerts=[],
            )

    def test_telemetry_snapshot_bad_iso(self):
        with self.assertRaises(ValueError):
            TelemetrySnapshot(
                incident_id="x",
                observed_at="not-a-date",
                trigger=Trigger(service="api"),
                services={},
                active_alerts=[],
            )

    def test_structured_diagnosis_valid(self):
        diag = StructuredDiagnosis(
            probable_cause_service="api",
            probable_cause="api overloaded",
            confidence=0.9,
            affected_services=["api"],
            evidence=[EvidenceItem(service="api", signal="cpu", value=90.0, interpretation="high")],
            recommended_action=RecommendedAction(type="restart_service", target_service="api", reason="fix"),
            risk="low",
            source="gemini",
        )
        self.assertEqual(diag.probable_cause_service, "api")

    def test_diagnosis_rejects_unknown_service(self):
        with self.assertRaises(ValueError):
            StructuredDiagnosis(
                probable_cause_service="bad",
                probable_cause="x",
                confidence=0.9,
                affected_services=[],
                evidence=[],
                recommended_action=RecommendedAction(type="no_action", target_service=None, reason="x"),
                risk="low",
                source="rules",
            )

    def test_diagnosis_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            StructuredDiagnosis(
                probable_cause_service="api",
                probable_cause="x",
                confidence=0.9,
                affected_services=[],
                evidence=[],
                recommended_action=RecommendedAction(type="bad_action", target_service=None, reason="x"),
                risk="low",
                source="rules",
            )

    def test_diagnosis_rejects_restart_without_target(self):
        with self.assertRaises(ValueError):
            StructuredDiagnosis(
                probable_cause_service="api",
                probable_cause="x",
                confidence=0.9,
                affected_services=[],
                evidence=[],
                recommended_action=RecommendedAction(type="restart_service", target_service=None, reason="x"),
                risk="low",
                source="rules",
            )

    def test_diagnosis_rejects_no_action_with_target(self):
        with self.assertRaises(ValueError):
            StructuredDiagnosis(
                probable_cause_service="api",
                probable_cause="x",
                confidence=0.9,
                affected_services=[],
                evidence=[],
                recommended_action=RecommendedAction(type="no_action", target_service="api", reason="x"),
                risk="low",
                source="rules",
            )

    def test_diagnosis_rejects_invalid_confidence(self):
        with self.assertRaises(ValueError):
            StructuredDiagnosis(
                probable_cause_service="api",
                probable_cause="x",
                confidence=1.5,
                affected_services=[],
                evidence=[],
                recommended_action=RecommendedAction(type="no_action", target_service=None, reason="x"),
                risk="low",
                source="rules",
            )

    def test_diagnosis_rejects_nan_evidence(self):
        with self.assertRaises(ValueError):
            StructuredDiagnosis(
                probable_cause_service="api",
                probable_cause="x",
                confidence=0.9,
                affected_services=[],
                evidence=[EvidenceItem(service="api", signal="cpu", value=float("nan"), interpretation="bad")],
                recommended_action=RecommendedAction(type="no_action", target_service=None, reason="x"),
                risk="low",
                source="rules",
            )

    def test_policy_decision_valid(self):
        decision = PolicyDecision(approved=True, action="restart_service", target="api", mode="execute", reason="ok", confidence_threshold=0.75)
        self.assertTrue(decision.approved)

    def test_policy_decision_rejects_no_action_with_target(self):
        with self.assertRaises(ValueError):
            PolicyDecision(approved=False, action="no_action", target="api", mode="recommend", reason="bad", confidence_threshold=0.75)

    def test_recovery_result_valid(self):
        result = RecoveryResult(status="recovered", details="ok")
        self.assertEqual(result.status, "recovered")

    def test_recovery_result_invalid_status(self):
        with self.assertRaises(ValueError):
            RecoveryResult(status="broken", details="bad")

    def test_incident_record_round_trip(self):
        snapshot = self._valid_snapshot()
        from inframirror.aiops_models import utc_now_iso
        record = IncidentRecord(
            incident_id="x",
            started_at=utc_now_iso(),
            completed_at=utc_now_iso(),
            trigger={"service": "api"},
            snapshot=snapshot.to_dict(),
            diagnosis={},
            policy_decision={},
            execution_result={"executed": False},
            recovery_result={"status": "not_executed"},
            model_source="rules",
            errors=[],
        )
        self.assertEqual(record.model_source, "rules")


if __name__ == "__main__":
    unittest.main()
