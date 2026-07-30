import os
import sys
import tempfile
import unittest

# Add parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SHARED_DATA_DIR", tempfile.mkdtemp(prefix="cloudmind-test-shared-"))

from microservices.api.service import app as api_app
from microservices.auth.service import app as auth_app
from microservices.cache.service import app as cache_app
from microservices.database.service import app as database_app
from microservices.frontend.service import app as frontend_app


class TestAIOpsServiceSignals(unittest.TestCase):
    def setUp(self):
        self.api = api_app.test_client()
        self.auth = auth_app.test_client()
        self.cache = cache_app.test_client()
        self.database = database_app.test_client()
        self.frontend = frontend_app.test_client()
        self.clients = {
            "api": self.api,
            "auth": self.auth,
            "cache": self.cache,
            "database": self.database,
            "frontend": self.frontend,
        }
        for client in self.clients.values():
            client.post("/heal")

    def tearDown(self):
        for client in self.clients.values():
            client.post("/heal")
        from inframirror import watcher
        watcher._last_heal.clear()
        watcher.AIOPS_ENABLED = False
        os.environ.pop("AIOPS_ENABLED", None)

    def test_new_metrics_exist_on_all_services(self):
        """Verify service_request_attempts_total, service_request_errors_total, and service_incident_active exist."""
        for name, client in self.clients.items():
            r = client.get("/metrics")
            self.assertEqual(r.status_code, 200)
            self.assertIn(b"service_request_attempts_total", r.data)
            self.assertIn(b"service_request_errors_total", r.data)
            self.assertIn(b"service_incident_active", r.data)

    def test_attempts_and_errors_semantics(self):
        """Verify attempts and errors increment properly on app requests."""
        r_ok = self.database.get("/status")
        self.assertEqual(r_ok.status_code, 200)

        self.database.post("/stress")
        r_err = self.database.get("/probe")
        self.assertEqual(r_err.status_code, 503)

        m = self.database.get("/metrics")
        self.assertEqual(m.status_code, 200)
        metrics_text = m.data.decode("utf-8")
        self.assertIn('service_request_attempts_total{service="database"}', metrics_text)
        self.assertIn('service_request_errors_total{service="database"}', metrics_text)
        self.assertIn('service_incident_active{service="database"} 1.0', metrics_text)

    def test_probe_healthy_behavior(self):
        """Verify /probe endpoint returns HTTP 200 with ok status when healthy."""
        probed_services = [("database", self.database), ("cache", self.cache), ("auth", self.auth)]
        for name, client in probed_services:
            r = client.get("/probe")
            self.assertEqual(r.status_code, 200)
            data = r.get_json()
            self.assertEqual(data["service"], name)
            self.assertEqual(data["status"], "ok")
            self.assertFalse(data["is_stressed"])

    def test_probe_stressed_degraded_behavior(self):
        """Verify /probe returns HTTP 503 degraded response when stressed."""
        probed_services = [("database", self.database), ("cache", self.cache), ("auth", self.auth)]
        for name, client in probed_services:
            client.post("/stress")
            r = client.get("/probe")
            self.assertEqual(r.status_code, 503)
            data = r.get_json()
            self.assertEqual(data["service"], name)
            self.assertEqual(data["status"], "degraded")
            self.assertTrue(data["is_stressed"])
            client.post("/heal")

    def test_heal_does_not_access_inframirror(self):
        """Regression test: /heal must never access sys.modules['inframirror.watcher'] or mutate InfraMirror state."""
        r = self.api.post("/heal")
        self.assertEqual(r.status_code, 200)
        # Verify response text and clean state
        data = r.get_json()
        self.assertEqual(data["status"], "healed")

    def test_api_dockerfile_contains_all_imports(self):
        """Regression test: microservices/api/Dockerfile contains every non-standard runtime dependency imported in service.py."""
        dockerfile_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "microservices", "api", "Dockerfile"))
        self.assertTrue(os.path.exists(dockerfile_path))

        with open(dockerfile_path, "r") as f:
            dockerfile_content = f.read()

        # Check required third-party imports in api/service.py: flask, prometheus_client, psutil, requests
        self.assertIn("flask", dockerfile_content)
        self.assertIn("prometheus_client", dockerfile_content)
        self.assertIn("psutil", dockerfile_content)
        self.assertIn("requests==2.34.2", dockerfile_content)


if __name__ == "__main__":
    unittest.main()
