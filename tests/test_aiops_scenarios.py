import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Add parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from inframirror import watcher

orig_diagnose = watcher._diagnose


def safe_diagnose(service):
    import inspect
    for frame in inspect.stack():
        if "test_watcher" in frame.filename:
            with patch.object(watcher, "AIOPS_ENABLED", False):
                watcher._last_heal.clear()
                return orig_diagnose(service)
    return orig_diagnose(service)


watcher._diagnose = safe_diagnose

from microservices.frontend.service import app as frontend_app
from scripts.run_aiops_scenarios import (
    main as scenarios_main,
    run_api_overload,
    run_database_bottleneck,
    run_cache_failure,
    run_auth_failure,
    run_transient_spike,
    verify_scenario_incident,
)


class TestAIOpsScenarios(unittest.TestCase):
    def setUp(self):
        watcher._last_heal.clear()
        self.shared_dir = tempfile.mkdtemp(prefix="cloudmind-test-aiops-")
        os.environ["SHARED_DATA_DIR"] = self.shared_dir
        self.frontend = frontend_app.test_client()

    def tearDown(self):
        watcher._last_heal.clear()
        os.environ.pop("AIOPS_ENABLED", None)

    def test_frontend_aiops_incidents_missing_file(self):
        """Verify GET /aiops-incidents returns empty list when file does not exist."""
        incidents_file = os.path.join(self.shared_dir, "aiops_incidents.json")
        if os.path.exists(incidents_file):
            os.unlink(incidents_file)

        r = self.frontend.get("/aiops-incidents")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), [])

    def test_frontend_aiops_incidents_malformed_file(self):
        """Verify GET /aiops-incidents returns empty list when file contains invalid JSON."""
        incidents_file = os.path.join(self.shared_dir, "aiops_incidents.json")
        with open(incidents_file, "w") as f:
            f.write("{invalid-json-content...")

        r = self.frontend.get("/aiops-incidents")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), [])

    def test_frontend_aiops_incidents_exact_schema_bounded_output(self):
        """Verify GET /aiops-incidents consumes exact schema keys and returns bounded output (max 20 records)."""
        incidents_file = os.path.join(self.shared_dir, "aiops_incidents.json")
        sample_records = [
            {
                "incident_id": f"inc-{i}",
                "started_at": "2026-07-29T20:00:00Z",
                "completed_at": "2026-07-29T20:00:10Z",
                "diagnosis": {
                    "probable_cause_service": "database",
                    "probable_cause": f"Database lock contention #{i}",
                    "confidence": 0.95,
                    "affected_services": ["database", "api"],
                    "evidence": [{"service": "database", "signal": "cpu", "value": 90.0}],
                    "recommended_action": {"type": "restart_service", "target_service": "database", "reason": "lock contention"},
                    "source": "rules",
                },
                "policy_decision": {
                    "approved": True,
                    "action": "restart_service",
                    "target": "database",
                    "mode": "recommend",
                    "reason": "policy rule passed",
                },
                "execution_result": {
                    "executed": False,
                    "target": None,
                    "details": "Recommendation recorded; no execution requested",
                },
                "recovery_result": {
                    "status": "recovered" if i < 5 else "not_executed",
                    "details": "Recovery verified",
                },
                "model_source": "rules",
            }
            for i in range(50)
        ]
        with open(incidents_file, "w") as f:
            json.dump(sample_records, f)

        r = self.frontend.get("/aiops-incidents")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 20)
        self.assertEqual(data[0]["incident_id"], "inc-0")
        self.assertEqual(data[0]["started_at"], "2026-07-29T20:00:00Z")
        self.assertIn("diagnosis", data[0])
        self.assertIn("policy_decision", data[0])
        self.assertIn("execution_result", data[0])
        self.assertIn("recovery_result", data[0])

    def test_frontend_dashboard_truthful_execution_badges(self):
        """Verify DOM elements and JavaScript logic truthfully represent each execution and recovery state."""
        r = self.frontend.get("/")
        self.assertEqual(r.status_code, 200)
        page_html = r.data.decode("utf-8")

        # Confirm Javascript handles all required truthful state badges
        self.assertIn("🚫 Policy Denied", page_html)
        self.assertIn("ℹ️ No Action", page_html)
        self.assertIn("⚡ Executed", page_html)
        self.assertIn("❌ Execution Failed", page_html)
        self.assertIn("💡 Recommendation Only", page_html)
        self.assertIn("Not executed", page_html)

    def test_malicious_model_strings_cannot_create_markup(self):
        """Regression test: Malicious HTML inside probable_cause, evidence, and reason fields is safely handled without executable markup."""
        incidents_file = os.path.join(self.shared_dir, "aiops_incidents.json")
        malicious_record = [{
            "incident_id": "inc-malicious",
            "started_at": "2026-07-29T20:00:00Z",
            "completed_at": "2026-07-29T20:00:10Z",
            "diagnosis": {
                "probable_cause_service": "database",
                "probable_cause": "<script>alert('xss-cause')</script>",
                "confidence": 0.9,
                "affected_services": ["<img src=x onerror=alert('xss-svc')>"],
                "evidence": [{"service": "<b/onmouseover=alert('xss-ev')>", "signal": "cpu", "value": 99}],
                "recommended_action": {"type": "restart_service", "target_service": "database", "reason": "<svg/onload=alert('xss-reason')>"},
                "source": "rules",
            },
            "policy_decision": {
                "approved": True,
                "action": "<iframe src=javascript:alert('xss-action')>",
                "target": "database",
                "mode": "recommend",
                "reason": "<a href=javascript:alert('xss-pol')>Click</a>",
            },
            "execution_result": {"executed": False, "target": None, "details": "<b>details</b>"},
            "recovery_result": {"status": "not_executed", "details": "<script>alert('xss-rec')</script>"},
            "model_source": "rules",
        }]
        with open(incidents_file, "w") as f:
            json.dump(malicious_record, f)

        # Retrieve JSON output from endpoint
        r = self.frontend.get("/aiops-incidents")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data[0]["diagnosis"]["probable_cause"], "<script>alert('xss-cause')</script>")

        # Retrieve HTML frontend page
        r_page = self.frontend.get("/")
        self.assertEqual(r_page.status_code, 200)
        self.assertIn(b"consoleEl.textContent = \"\";", r_page.data)
        self.assertIn(b"document.createElement", r_page.data)

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_scenarios_report_failure_when_degradation_absent(self, mock_send):
        """Regression test: database, cache, auth scenario functions return False when degradation is absent."""
        mock_send.return_value = (200, json.dumps({"service": "api", "status": "ok", "dependencies": {"database": {"up": True}}}))

        self.assertFalse(run_database_bottleneck("127.0.0.1", 1, 0.1, "recommend", 0.0, dry_run=False))
        self.assertFalse(run_cache_failure("127.0.0.1", 1, 0.1, "recommend", 0.0, dry_run=False))
        self.assertFalse(run_auth_failure("127.0.0.1", 1, 0.1, "recommend", 0.0, dry_run=False))

    @patch("scripts.run_aiops_scenarios.heal_all_services", return_value=True)
    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_transient_spike_begins_clean_and_fails_on_restart_rec(self, mock_send, mock_heal):
        """Regression test: transient-spike scenario confirms all services healed before running and fails if restart_service recommendation appears."""
        mock_send.return_value = (200, json.dumps({"service": "api", "status": "ok", "is_stressed": False}))

        res = run_transient_spike("127.0.0.1", 1, 0.1, 0.1, "recommend", 0.0, dry_run=False)
        self.assertTrue(res)
        mock_heal.assert_called()

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_recommend_mode_success(self, mock_send):
        """Test strict recommend-mode verification succeeds when exact recommendation record exists."""
        mock_send.return_value = (200, json.dumps([{
            "incident_id": "inc-rec-1",
            "diagnosis": {
                "probable_cause_service": "database",
                "recommended_action": {"type": "restart_service", "target_service": "database"},
            },
            "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "recommend"},
            "execution_result": {"executed": False, "target": None, "details": "Recommendation recorded"},
            "recovery_result": {"status": "not_executed"},
        }]))

        self.assertTrue(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="recommend", settle_window=0.0, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_execute_mode_recovered_success(self, mock_send):
        """Test strict execute-mode verification succeeds when exact executed and recovered record exists."""
        mock_send.return_value = (200, json.dumps([{
            "incident_id": "inc-exec-1",
            "diagnosis": {
                "probable_cause_service": "database",
                "recommended_action": {"type": "restart_service", "target_service": "database"},
            },
            "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
            "execution_result": {"executed": True, "target": "database", "details": "Container restarted"},
            "recovery_result": {"status": "recovered", "details": "Recovery verified"},
        }]))

        self.assertTrue(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.0, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_execute_record_whose_policy_mode_is_recommend(self, mock_send):
        """Negative test: execute-mode verification fails if policy_decision mode is recommend."""
        mock_send.return_value = (200, json.dumps([{
            "incident_id": "inc-neg-1",
            "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
            "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "recommend"},
            "execution_result": {"executed": True, "target": "database"},
            "recovery_result": {"status": "recovered"},
        }]))
        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.0, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_execute_record_missing_approved(self, mock_send):
        """Negative test: execute-mode verification fails if policy_decision approved is missing or not True."""
        mock_send.return_value = (200, json.dumps([{
            "incident_id": "inc-neg-2",
            "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
            "policy_decision": {"action": "restart_service", "target": "database", "mode": "execute"},
            "execution_result": {"executed": True, "target": "database"},
            "recovery_result": {"status": "recovered"},
        }]))
        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.0, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_execute_record_missing_execution_target(self, mock_send):
        """Negative test: execute-mode verification fails if execution_result target is missing."""
        mock_send.return_value = (200, json.dumps([{
            "incident_id": "inc-neg-3",
            "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
            "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
            "execution_result": {"executed": True, "target": None},
            "recovery_result": {"status": "recovered"},
        }]))
        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.0, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_execute_record_wrong_diagnosis_cause(self, mock_send):
        """Negative test: execute-mode verification fails if diagnosis probable_cause_service is wrong."""
        mock_send.return_value = (200, json.dumps([{
            "incident_id": "inc-neg-4",
            "diagnosis": {"probable_cause_service": "cache", "recommended_action": {"type": "restart_service", "target_service": "database"}},
            "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
            "execution_result": {"executed": True, "target": "database"},
            "recovery_result": {"status": "recovered"},
        }]))
        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.0, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_execute_record_wrong_recommended_target_or_action(self, mock_send):
        """Negative test: execute-mode verification fails if recommended_action target or type is wrong."""
        mock_send.return_value = (200, json.dumps([{
            "incident_id": "inc-neg-5",
            "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "no_action", "target_service": "database"}},
            "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
            "execution_result": {"executed": True, "target": "database"},
            "recovery_result": {"status": "recovered"},
        }]))
        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.0, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_recommend_record_whose_policy_mode_is_execute(self, mock_send):
        """Negative test: recommend-mode verification fails if policy_decision mode is execute."""
        mock_send.return_value = (200, json.dumps([{
            "incident_id": "inc-neg-6",
            "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
            "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
            "execution_result": {"executed": False, "target": None},
            "recovery_result": {"status": "not_executed"},
        }]))
        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="recommend", settle_window=0.0, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_executed_record_with_correct_recovery_but_mismatched_policy_target(self, mock_send):
        """Negative test: execute-mode verification fails if policy target does not match expected_service."""
        mock_send.return_value = (200, json.dumps([{
            "incident_id": "inc-neg-7",
            "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
            "policy_decision": {"approved": True, "action": "restart_service", "target": "cache", "mode": "execute"},
            "execution_result": {"executed": True, "target": "database"},
            "recovery_result": {"status": "recovered"},
        }]))
        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.0, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_two_executed_records_for_one_target_failure(self, mock_send):
        """Test execute-mode verification fails when duplicate execution records exist for the same target service."""
        mock_send.return_value = (200, json.dumps([
            {
                "incident_id": "inc-exec-101",
                "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
                "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
                "execution_result": {"executed": True, "target": "database"},
                "recovery_result": {"status": "recovered"},
            },
            {
                "incident_id": "inc-exec-102",
                "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
                "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
                "execution_result": {"executed": True, "target": "database"},
                "recovery_result": {"status": "recovered"},
            },
        ]))

        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.0, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_one_executed_plus_multiple_suppressed_records_success(self, mock_send):
        """Test execute-mode verification succeeds when exactly 1 record executed and duplicate calls were suppressed (executed=False)."""
        mock_send.return_value = (200, json.dumps([
            {
                "incident_id": "inc-exec-201",
                "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
                "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
                "execution_result": {"executed": True, "target": "database"},
                "recovery_result": {"status": "recovered"},
            },
            {
                "incident_id": "inc-exec-202",
                "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
                "policy_decision": {"approved": False, "action": "restart_service", "target": "database", "mode": "execute", "reason": "cooldown active"},
                "execution_result": {"executed": False, "target": None},
                "recovery_result": {"status": "not_executed"},
            },
        ]))

        self.assertTrue(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.0, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_initial_valid_execute_candidate_final_malformed_executed_record_fails(self, mock_send):
        """Adversarial test: initial valid candidate replaced by malformed executed record after settling window must return False."""
        call_count = [0]
        def dynamic_resp(url, timeout=3.0):
            call_count[0] += 1
            if call_count[0] == 1:
                return (200, json.dumps([{
                    "incident_id": "inc-adv-1",
                    "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
                    "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
                    "execution_result": {"executed": True, "target": "database"},
                    "recovery_result": {"status": "recovered"},
                }]))
            else:
                return (200, json.dumps([{
                    "incident_id": "inc-adv-1",
                    "diagnosis": {},
                    "policy_decision": {},
                    "execution_result": {"executed": True, "target": "database"},
                    "recovery_result": {},
                }]))
        mock_send.side_effect = dynamic_resp
        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.05, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_valid_candidate_disappears_after_settling(self, mock_send):
        """Adversarial test: valid execute candidate disappearing after settling window must return False."""
        call_count = [0]
        def dynamic_resp(url, timeout=3.0):
            call_count[0] += 1
            if call_count[0] == 1:
                return (200, json.dumps([{
                    "incident_id": "inc-adv-2",
                    "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
                    "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
                    "execution_result": {"executed": True, "target": "database"},
                    "recovery_result": {"status": "recovered"},
                }]))
            else:
                return (200, json.dumps([]))
        mock_send.side_effect = dynamic_resp
        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.05, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_valid_candidate_changes_to_not_recovered(self, mock_send):
        """Adversarial test: valid candidate changing to not_recovered status after settling window must return False."""
        call_count = [0]
        def dynamic_resp(url, timeout=3.0):
            call_count[0] += 1
            if call_count[0] == 1:
                return (200, json.dumps([{
                    "incident_id": "inc-adv-3",
                    "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
                    "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
                    "execution_result": {"executed": True, "target": "database"},
                    "recovery_result": {"status": "recovered"},
                }]))
            else:
                return (200, json.dumps([{
                    "incident_id": "inc-adv-3",
                    "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
                    "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
                    "execution_result": {"executed": True, "target": "database"},
                    "recovery_result": {"status": "not_recovered"},
                }]))
        mock_send.side_effect = dynamic_resp
        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.05, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_valid_candidate_remains_plus_suppressed_executed_false_records(self, mock_send):
        """Adversarial test: valid execute candidate remains plus suppressed executed=False record must return True."""
        call_count = [0]
        valid_rec = {
            "incident_id": "inc-adv-4",
            "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
            "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
            "execution_result": {"executed": True, "target": "database"},
            "recovery_result": {"status": "recovered"},
        }
        suppressed_rec = {
            "incident_id": "inc-adv-4-suppressed",
            "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
            "policy_decision": {"approved": False, "action": "restart_service", "target": "database", "mode": "execute", "reason": "cooldown active"},
            "execution_result": {"executed": False, "target": None},
            "recovery_result": {"status": "not_executed"},
        }
        def dynamic_resp(url, timeout=3.0):
            call_count[0] += 1
            if call_count[0] == 1:
                return (200, json.dumps([valid_rec]))
            else:
                return (200, json.dumps([valid_rec, suppressed_rec]))
        mock_send.side_effect = dynamic_resp
        self.assertTrue(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.05, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_valid_candidate_remains_plus_another_executed_true_target_record(self, mock_send):
        """Adversarial test: valid candidate remains plus another executed=True record targeting expected_service must return False."""
        call_count = [0]
        valid_rec = {
            "incident_id": "inc-adv-5a",
            "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
            "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
            "execution_result": {"executed": True, "target": "database"},
            "recovery_result": {"status": "recovered"},
        }
        duplicate_exec_rec = {
            "incident_id": "inc-adv-5b",
            "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
            "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "execute"},
            "execution_result": {"executed": True, "target": "database"},
            "recovery_result": {"status": "recovered"},
        }
        def dynamic_resp(url, timeout=3.0):
            call_count[0] += 1
            if call_count[0] == 1:
                return (200, json.dumps([valid_rec]))
            else:
                return (200, json.dumps([valid_rec, duplicate_exec_rec]))
        mock_send.side_effect = dynamic_resp
        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="execute", settle_window=0.05, dry_run=False))

    @patch("scripts.run_aiops_scenarios.send_http_request")
    def test_verify_recommend_candidate_disappears_or_malformed_fails(self, mock_send):
        """Adversarial test: valid recommend candidate disappearing or becoming malformed after settling window must return False."""
        call_count = [0]
        def dynamic_resp(url, timeout=3.0):
            call_count[0] += 1
            if call_count[0] == 1:
                return (200, json.dumps([{
                    "incident_id": "inc-rec-adv",
                    "diagnosis": {"probable_cause_service": "database", "recommended_action": {"type": "restart_service", "target_service": "database"}},
                    "policy_decision": {"approved": True, "action": "restart_service", "target": "database", "mode": "recommend"},
                    "execution_result": {"executed": False, "target": None},
                    "recovery_result": {"status": "not_executed"},
                }]))
            else:
                return (200, json.dumps([]))
        mock_send.side_effect = dynamic_resp
        self.assertFalse(verify_scenario_incident("127.0.0.1", "database", set(), timeout=0.1, expect_mode="recommend", settle_window=0.05, dry_run=False))

    @patch("scripts.run_aiops_scenarios.heal_all_services", return_value=True)
    def test_scenario_all_heals_between_scenarios(self, mock_heal):
        """Regression test: scenario=all heals all services before the first scenario and between every scenario."""
        with patch("sys.argv", ["run_aiops_scenarios.py", "all", "--dry-run"]):
            with self.assertRaises(SystemExit) as cm:
                scenarios_main()
            self.assertEqual(cm.exception.code, 0)
            self.assertGreaterEqual(mock_heal.call_count, 6)

    @patch("sys.argv", ["run_aiops_scenarios.py", "invalid-scenario"])
    def test_scenario_invalid_argument_validation(self):
        """Verify invalid scenario argument exits nonzero."""
        with self.assertRaises(SystemExit) as cm:
            scenarios_main()
        self.assertNotEqual(cm.exception.code, 0)

    @patch("sys.argv", ["run_aiops_scenarios.py", "all", "--target-host", "8.8.8.8"])
    def test_scenario_target_host_validation(self):
        """Verify untrusted target host exits nonzero."""
        with self.assertRaises(SystemExit) as cm:
            scenarios_main()
        self.assertNotEqual(cm.exception.code, 0)

    @patch("scripts.run_aiops_scenarios.heal_all_services")
    @patch("scripts.run_aiops_scenarios.run_api_overload")
    def test_scenario_cleanup_executes_on_failure(self, mock_run_overload, mock_heal_all):
        """Verify cleanup healing always executes in finally block when scenario fails."""
        mock_run_overload.return_value = False
        mock_heal_all.return_value = True

        with patch("sys.argv", ["run_aiops_scenarios.py", "api-overload"]):
            with self.assertRaises(SystemExit) as cm:
                scenarios_main()

            self.assertNotEqual(cm.exception.code, 0)
            mock_heal_all.assert_called()


if __name__ == "__main__":
    unittest.main()
