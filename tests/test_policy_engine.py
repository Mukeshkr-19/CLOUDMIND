import unittest
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from inframirror.aiops_models import (
    ServiceTelemetry,
    TelemetrySnapshot,
    Trigger,
    StructuredDiagnosis,
    RecommendedAction,
    EvidenceItem,
)
from inframirror import policy_engine as pe


class TestPolicyEngine(unittest.TestCase):
    def _snapshot(self, services=None, trigger="api"):
        return TelemetrySnapshot(
            incident_id="test-id",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service=trigger, alertname="Test", reason="test"),
            services=services or {},
            active_alerts=[],
        )

    def _diagnosis(self, action="restart_service", target="api", risk="low", confidence=0.9):
        return StructuredDiagnosis(
            probable_cause_service="api",
            probable_cause="api overloaded",
            confidence=confidence,
            affected_services=["api"],
            evidence=[EvidenceItem(service="api", signal="cpu", value=90.0, interpretation="high")],
            recommended_action=RecommendedAction(type=action, target_service=target, reason="test"),
            risk=risk,
            source="gemini",
        )

    def test_confidence_boundary_approved(self):
        snapshot = self._snapshot({"api": ServiceTelemetry(cpu_percent=90.0, latency_ms=120.0)})
        diag = self._diagnosis(confidence=0.75)
        decision = pe.evaluate_policy(snapshot, diag, mode="recommend")
        self.assertTrue(decision.approved)

    def test_confidence_boundary_denied(self):
        snapshot = self._snapshot({"api": ServiceTelemetry(cpu_percent=90.0, latency_ms=120.0)})
        diag = self._diagnosis(confidence=0.74)
        decision = pe.evaluate_policy(snapshot, diag, mode="recommend")
        self.assertFalse(decision.approved)
        self.assertEqual(decision.action, "no_action")

    def test_high_risk_denial(self):
        snapshot = self._snapshot({"api": ServiceTelemetry(cpu_percent=90.0, latency_ms=120.0)})
        diag = self._diagnosis(risk="high")
        decision = pe.evaluate_policy(snapshot, diag, mode="recommend")
        self.assertFalse(decision.approved)

    def test_no_action_with_target_rejected(self):
        snapshot = self._snapshot({"api": ServiceTelemetry(cpu_percent=90.0, latency_ms=120.0)})
        # Use a valid diagnosis then monkey-patch the recommended action to an contradictory state
        # because StructuredDiagnosis itself forbids constructing such an value.
        diag = self._diagnosis(action="no_action", target=None)
        object.__setattr__(diag.recommended_action, "type", "no_action")
        object.__setattr__(diag.recommended_action, "target_service", "api")
        decision = pe.evaluate_policy(snapshot, diag, mode="recommend")
        self.assertFalse(decision.approved)

    def test_unknown_target_denial(self):
        snapshot = self._snapshot({"api": ServiceTelemetry(cpu_percent=90.0, latency_ms=120.0)})
        # Use a valid diagnosis then monkey-patch the target to an unknown service
        # because StructuredDiagnosis itself forbids constructing such a value.
        diag = self._diagnosis(target="api")
        object.__setattr__(diag.recommended_action, "target_service", "worker")
        decision = pe.evaluate_policy(snapshot, diag, mode="recommend")
        self.assertFalse(decision.approved)

    def test_recommend_mode_never_executes(self):
        snapshot = self._snapshot({"api": ServiceTelemetry(cpu_percent=90.0, latency_ms=120.0)})
        diag = self._diagnosis()
        decision = pe.evaluate_policy(snapshot, diag, mode="recommend")
        self.assertTrue(decision.approved)
        self.assertEqual(decision.mode, "recommend")

    def test_execute_mode_approves_safe_action(self):
        snapshot = self._snapshot({"api": ServiceTelemetry(cpu_percent=90.0, latency_ms=120.0)})
        diag = self._diagnosis()
        decision = pe.evaluate_policy(snapshot, diag, mode="execute")
        self.assertTrue(decision.approved)
        self.assertEqual(decision.mode, "execute")

    def test_cooldown_denies_execute(self):
        snapshot = self._snapshot({"api": ServiceTelemetry(cpu_percent=90.0, latency_ms=120.0)})
        diag = self._diagnosis()
        last_heal = {"api": datetime.now(timezone.utc)}
        decision = pe.evaluate_policy(snapshot, diag, mode="execute", last_heal=last_heal, cooldown_seconds=300)
        self.assertFalse(decision.approved)
        self.assertIn("Cooldown", decision.reason)

    def test_no_evidence_denied(self):
        snapshot = self._snapshot({"api": ServiceTelemetry(cpu_percent=90.0, latency_ms=120.0)})
        diag = StructuredDiagnosis(
            probable_cause_service="api",
            probable_cause="x",
            confidence=0.9,
            affected_services=["api"],
            evidence=[],
            recommended_action=RecommendedAction(type="restart_service", target_service="api", reason="x"),
            risk="low",
            source="gemini",
        )
        decision = pe.evaluate_policy(snapshot, diag, mode="recommend")
        self.assertFalse(decision.approved)

    def test_missing_abnormal_telemetry_denied(self):
        snapshot = self._snapshot({"api": ServiceTelemetry(cpu_percent=10.0, latency_ms=50.0)})
        diag = self._diagnosis()
        decision = pe.evaluate_policy(snapshot, diag, mode="recommend")
        self.assertFalse(decision.approved)


if __name__ == "__main__":
    unittest.main()
