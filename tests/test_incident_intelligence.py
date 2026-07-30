import unittest
import sys
import os
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from inframirror.aiops_models import (
    DependencyInfo,
    ServiceTelemetry,
    TelemetrySnapshot,
    Trigger,
)
from inframirror import incident_intelligence as ii


class TestIncidentIntelligence(unittest.TestCase):
    def _snapshot(self, services=None, trigger="api"):
        return TelemetrySnapshot(
            incident_id="test-id",
            observed_at="2024-01-01T00:00:00+00:00",
            trigger=Trigger(service=trigger, alertname="Test", reason="test"),
            services=services or {},
            active_alerts=[],
        )

    def test_database_dependency_causes_api_latency(self):
        snapshot = self._snapshot({
            "api": ServiceTelemetry(
                cpu_percent=45.0,
                latency_ms=410.0,
                dependencies={"database": DependencyInfo(up=False, latency_ms=450.0)},
            ),
            "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=40.0),
            "database": ServiceTelemetry(cpu_percent=30.0, latency_ms=430.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        diagnosis = ii.rules_based_diagnosis(snapshot)
        self.assertEqual(diagnosis.probable_cause_service, "database")
        self.assertEqual(diagnosis.recommended_action.type, "restart_service")
        self.assertEqual(diagnosis.recommended_action.target_service, "database")
        self.assertEqual(diagnosis.source, "rules")

    def test_direct_api_overload_selects_api(self):
        snapshot = self._snapshot({
            "api": ServiceTelemetry(cpu_percent=92.0, latency_ms=120.0),
            "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=40.0),
            "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        diagnosis = ii.rules_based_diagnosis(snapshot)
        self.assertEqual(diagnosis.probable_cause_service, "api")
        self.assertEqual(diagnosis.recommended_action.type, "restart_service")
        self.assertEqual(diagnosis.recommended_action.target_service, "api")

    def test_transient_spike_selects_no_action(self):
        snapshot = self._snapshot({
            "api": ServiceTelemetry(cpu_percent=40.0, latency_ms=100.0),
            "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=40.0),
            "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        diagnosis = ii.rules_based_diagnosis(snapshot)
        self.assertEqual(diagnosis.recommended_action.type, "no_action")
        self.assertIsNone(diagnosis.recommended_action.target_service)

    def test_diagnose_with_valid_llm_response(self):
        def fake_llm(prompt, key, timeout=8.0):
            return json.dumps({
                "probable_cause_service": "api",
                "probable_cause": "CPU high",
                "confidence": 0.9,
                "affected_services": ["api"],
                "evidence": [{"service": "api", "signal": "cpu", "value": 90.0, "interpretation": "high"}],
                "recommended_action": {"type": "restart_service", "target_service": "api", "reason": "fix"},
                "risk": "low",
            })

        snapshot = self._snapshot({
            "api": ServiceTelemetry(cpu_percent=90.0, latency_ms=120.0),
            "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=40.0),
            "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        diagnosis = ii.diagnose(snapshot, api_key="fake-key", call_llm=fake_llm)
        self.assertEqual(diagnosis.source, "gemini")
        self.assertEqual(diagnosis.probable_cause_service, "api")

    def test_diagnose_with_fenced_json_response(self):
        def fake_llm(prompt, key, timeout=8.0):
            return "```json\n" + json.dumps({
                "probable_cause_service": "api",
                "probable_cause": "CPU high",
                "confidence": 0.9,
                "affected_services": ["api"],
                "evidence": [{"service": "api", "signal": "cpu", "value": 90.0, "interpretation": "high"}],
                "recommended_action": {"type": "restart_service", "target_service": "api", "reason": "fix"},
                "risk": "low",
            }) + "\n```"

        snapshot = self._snapshot({
            "api": ServiceTelemetry(cpu_percent=90.0, latency_ms=120.0),
            "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=40.0),
            "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        diagnosis = ii.diagnose(snapshot, api_key="fake-key", call_llm=fake_llm)
        self.assertEqual(diagnosis.source, "gemini")

    def test_diagnose_malformed_json_fallback(self):
        def fake_llm(prompt, key, timeout=8.0):
            return "this is not json {{"

        snapshot = self._snapshot({
            "api": ServiceTelemetry(cpu_percent=92.0, latency_ms=120.0),
            "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=40.0),
            "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        diagnosis = ii.diagnose(snapshot, api_key="fake-key", call_llm=fake_llm)
        self.assertEqual(diagnosis.source, "rules")

    def test_diagnose_empty_api_key_fallback(self):
        def fake_llm(prompt, key, timeout=8.0):
            return None

        snapshot = self._snapshot({
            "api": ServiceTelemetry(cpu_percent=92.0, latency_ms=120.0),
            "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=40.0),
            "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        diagnosis = ii.diagnose(snapshot, api_key="", call_llm=fake_llm)
        self.assertEqual(diagnosis.source, "rules")

    def test_diagnose_timeout_fallback(self):
        def fake_llm(prompt, key, timeout=8.0):
            raise TimeoutError("slow")

        snapshot = self._snapshot({
            "api": ServiceTelemetry(cpu_percent=92.0, latency_ms=120.0),
            "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=40.0),
            "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        diagnosis = ii.diagnose(snapshot, api_key="fake-key", call_llm=fake_llm)
        self.assertEqual(diagnosis.source, "rules")

    def test_diagnose_rejects_unknown_action(self):
        def fake_llm(prompt, key, timeout=8.0):
            return json.dumps({
                "probable_cause_service": "api",
                "probable_cause": "CPU high",
                "confidence": 0.9,
                "affected_services": ["api"],
                "evidence": [{"service": "api", "signal": "cpu", "value": 90.0, "interpretation": "high"}],
                "recommended_action": {"type": "delete_service", "target_service": "api", "reason": "fix"},
                "risk": "low",
            })

        snapshot = self._snapshot({
            "api": ServiceTelemetry(cpu_percent=92.0, latency_ms=120.0),
            "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=40.0),
            "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        diagnosis = ii.diagnose(snapshot, api_key="fake-key", call_llm=fake_llm)
        self.assertEqual(diagnosis.source, "rules")

    def test_diagnose_rejects_unknown_service(self):
        def fake_llm(prompt, key, timeout=8.0):
            return json.dumps({
                "probable_cause_service": "worker",
                "probable_cause": "CPU high",
                "confidence": 0.9,
                "affected_services": ["api"],
                "evidence": [{"service": "api", "signal": "cpu", "value": 90.0, "interpretation": "high"}],
                "recommended_action": {"type": "restart_service", "target_service": "api", "reason": "fix"},
                "risk": "low",
            })

        snapshot = self._snapshot({
            "api": ServiceTelemetry(cpu_percent=92.0, latency_ms=120.0),
            "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=40.0),
            "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        diagnosis = ii.diagnose(snapshot, api_key="fake-key", call_llm=fake_llm)
        self.assertEqual(diagnosis.source, "rules")

    def test_diagnose_rejects_nan_value(self):
        def fake_llm(prompt, key, timeout=8.0):
            return json.dumps({
                "probable_cause_service": "api",
                "probable_cause": "CPU high",
                "confidence": 0.9,
                "affected_services": ["api"],
                "evidence": [{"service": "api", "signal": "cpu", "value": float("nan"), "interpretation": "high"}],
                "recommended_action": {"type": "restart_service", "target_service": "api", "reason": "fix"},
                "risk": "low",
            })

        snapshot = self._snapshot({
            "api": ServiceTelemetry(cpu_percent=92.0, latency_ms=120.0),
            "frontend": ServiceTelemetry(cpu_percent=20.0, latency_ms=40.0),
            "database": ServiceTelemetry(cpu_percent=15.0, latency_ms=40.0),
            "cache": ServiceTelemetry(cpu_percent=10.0, latency_ms=20.0),
            "auth": ServiceTelemetry(cpu_percent=8.0, latency_ms=25.0),
        })
        diagnosis = ii.diagnose(snapshot, api_key="fake-key", call_llm=fake_llm)
        self.assertEqual(diagnosis.source, "rules")


if __name__ == "__main__":
    unittest.main()
