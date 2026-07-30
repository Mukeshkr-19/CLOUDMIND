import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Add parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SHARED_DATA_DIR", tempfile.mkdtemp(prefix="cloudmind-test-shared-"))

from microservices.api.service import app as api_app


class TestAIOpsDependencyFlow(unittest.TestCase):
    def setUp(self):
        self.api = api_app.test_client()
        self.api.post("/heal")

    def tearDown(self):
        self.api.post("/heal")
        from inframirror import watcher
        watcher._last_heal.clear()
        watcher.AIOPS_ENABLED = False
        os.environ.pop("AIOPS_ENABLED", None)

    @patch("requests.get")
    def test_work_all_dependencies_healthy(self, mock_get):
        """Verify API /work returns 200 when all dependencies return 200 OK."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        r = self.api.get("/work")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(len(data["dependencies"]), 3)
        self.assertTrue(data["dependencies"]["database"]["up"])
        self.assertTrue(data["dependencies"]["cache"]["up"])
        self.assertTrue(data["dependencies"]["auth"]["up"])

    @patch("requests.get")
    def test_three_probes_execute_concurrently(self, mock_get):
        """Regression test: Probing 3 dependencies with 0.5s delay takes < 1.0s total due to ThreadPoolExecutor concurrency."""
        def slow_probe(url, timeout=1.5):
            time.sleep(0.5)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            return mock_resp

        mock_get.side_effect = slow_probe

        start_t = time.perf_counter()
        r = self.api.get("/work")
        elapsed_total = time.perf_counter() - start_t

        self.assertEqual(r.status_code, 200)
        # Sequential execution of 3 x 0.5s probes would take > 1.5s (+ normal latency).
        # Concurrent execution completes in ~0.5s - 0.7s total (plus normal latency sleep ~0.1s).
        self.assertLess(elapsed_total, 1.2, f"Expected concurrent execution under 1.2s, took {elapsed_total:.2f}s")

    @patch("requests.get")
    def test_total_work_latency_is_bounded(self, mock_get):
        """Regression test: Total /work request latency remains bounded under 2.5s even if probes hang."""
        import requests as req_lib
        def hanging_probe(url, timeout=1.5):
            time.sleep(2.0)
            raise req_lib.exceptions.Timeout("Request timed out")

        mock_get.side_effect = hanging_probe

        start_t = time.perf_counter()
        r = self.api.get("/work")
        elapsed_total = time.perf_counter() - start_t

        self.assertEqual(r.status_code, 503)
        self.assertLess(elapsed_total, 2.5, f"Expected bounded total latency < 2.5s, took {elapsed_total:.2f}s")

    @patch("requests.get")
    def test_raw_exceptions_are_not_returned(self, mock_get):
        """Regression test: /work error fields contain sanitized error categories, never raw exception text or stack traces."""
        import requests as req_lib

        # Test timeout exception
        mock_get.side_effect = req_lib.exceptions.Timeout("ConnectionRefusedError(111, 'Connection refused at 10.0.0.1:5052')")
        r = self.api.get("/work")
        data = r.get_json()
        self.assertEqual(r.status_code, 503)
        db_err = data["dependencies"]["database"]["error"]
        self.assertEqual(db_err, "timeout")
        self.assertNotIn("ConnectionRefusedError", db_err)
        self.assertNotIn("10.0.0.1", db_err)

        # Test connection exception
        mock_get.side_effect = req_lib.exceptions.ConnectionError("HTTPConnectionPool(host='database', port=5052): Max retries exceeded")
        r = self.api.get("/work")
        data = r.get_json()
        self.assertEqual(r.status_code, 503)
        db_err = data["dependencies"]["database"]["error"]
        self.assertEqual(db_err, "unavailable")
        self.assertNotIn("HTTPConnectionPool", db_err)

    @patch("requests.get")
    def test_work_database_failure(self, mock_get):
        """Verify API /work returns HTTP 503 when database dependency fails."""
        def side_effect(url, timeout=1.5):
            mock_resp = MagicMock()
            if "5052" in url or "database" in url:
                mock_resp.status_code = 503
            else:
                mock_resp.status_code = 200
            return mock_resp

        mock_get.side_effect = side_effect

        r = self.api.get("/work")
        self.assertEqual(r.status_code, 503)
        data = r.get_json()
        self.assertEqual(data["status"], "degraded")
        self.assertFalse(data["dependencies"]["database"]["up"])
        self.assertEqual(data["dependencies"]["database"]["error"], "unavailable")

    @patch("requests.get")
    def test_work_cache_failure(self, mock_get):
        """Verify API /work returns HTTP 503 when cache dependency fails."""
        def side_effect(url, timeout=1.5):
            mock_resp = MagicMock()
            if "5053" in url or "cache" in url:
                mock_resp.status_code = 503
            else:
                mock_resp.status_code = 200
            return mock_resp

        mock_get.side_effect = side_effect

        r = self.api.get("/work")
        self.assertEqual(r.status_code, 503)
        data = r.get_json()
        self.assertEqual(data["status"], "degraded")
        self.assertFalse(data["dependencies"]["cache"]["up"])

    @patch("requests.get")
    def test_work_auth_failure(self, mock_get):
        """Verify API /work returns HTTP 503 when auth dependency fails."""
        def side_effect(url, timeout=1.5):
            mock_resp = MagicMock()
            if "5054" in url or "auth" in url:
                mock_resp.status_code = 503
            else:
                mock_resp.status_code = 200
            return mock_resp

        mock_get.side_effect = side_effect

        r = self.api.get("/work")
        self.assertEqual(r.status_code, 503)
        data = r.get_json()
        self.assertEqual(data["status"], "degraded")
        self.assertFalse(data["dependencies"]["auth"]["up"])

    @patch("requests.get")
    def test_missing_dependency_configuration_fails_safely(self, mock_get):
        """Verify missing/empty dependency URL env var fails safely with 'missing_configuration' error category."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            r = self.api.get("/work")
            self.assertEqual(r.status_code, 503)
            data = r.get_json()
            self.assertEqual(data["status"], "degraded")
            self.assertFalse(data["dependencies"]["database"]["up"])
            self.assertEqual(data["dependencies"]["database"]["error"], "missing_configuration")

    def test_named_timeout_constants_exist(self):
        """Verify PROBE_TIMEOUT_SECONDS and TOTAL_WORK_TIMEOUT_SECONDS exist in API service module."""
        from microservices.api import service
        self.assertTrue(hasattr(service, "PROBE_TIMEOUT_SECONDS"))
        self.assertTrue(hasattr(service, "TOTAL_WORK_TIMEOUT_SECONDS"))
        self.assertEqual(service.PROBE_TIMEOUT_SECONDS, 1.5)
        self.assertEqual(service.TOTAL_WORK_TIMEOUT_SECONDS, 2.0)


if __name__ == "__main__":
    unittest.main()
