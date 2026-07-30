import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from inframirror import recovery_verifier as rv


class TestRecoveryVerifier(unittest.TestCase):
    def test_recovery_success_after_required_samples(self):
        responses = {
            'up{job="api"}': [{"value": (None, 1.0)}],
            'service_cpu_percent{service="api"}': [{"value": (None, 50.0)}],
            'service_latency_ms{service="api"}': [{"value": (None, 100.0)}],
        }
        def query(expr):
            return responses.get(expr, [])

        result = rv.verify_recovery(
            target="api",
            max_attempts=5,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=query,
        )
        self.assertEqual(result.status, "recovered")

    def test_recovery_failure_and_timeout(self):
        responses = {
            'up{job="api"}': [{"value": (None, 0.0)}],
            'service_cpu_percent{service="api"}': [{"value": (None, 90.0)}],
            'service_latency_ms{service="api"}': [{"value": (None, 400.0)}],
        }
        def query(expr):
            return responses.get(expr, [])

        result = rv.verify_recovery(
            target="api",
            max_attempts=3,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=query,
        )
        self.assertEqual(result.status, "not_recovered")

    def test_not_executed_without_target(self):
        result = rv.verify_recovery(
            target="",
            max_attempts=3,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=lambda q: [],
        )
        self.assertEqual(result.status, "not_executed")

    def test_invalid_config_normalizes_to_safe_defaults(self):
        # max_attempts < 1 is normalized to 1; interval_seconds is normalized safely.
        result = rv.verify_recovery(
            target="api",
            max_attempts=0,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=lambda q: [],
        )
        self.assertEqual(result.status, "not_recovered")

    # ------------------------------------------------------------------
    # Active dependency probe tests
    # ------------------------------------------------------------------
    def test_active_probe_refreshes_stale_dependency_to_healthy(self):
        """Probe refreshes a stale gauge from 0 to 1 and recovery succeeds.

        The probe returns the API's real-time view. The Prometheus gauge
        may still reflect the pre-refresh state on the first attempt because
        a scrape has not yet occurred; the second attempt observes the
        refreshed gauge.
        """
        state = {"gauge_value": 0.0, "probe_calls": 0}

        def probe(dependency):
            state["probe_calls"] += 1
            # Simulate the gauge still showing the pre-refresh state on the
            # first sample, then refreshed on subsequent samples.
            if state["probe_calls"] > 1:
                state["gauge_value"] = 1.0
            return True

        def query(expr):
            if expr == 'up{job="database"}':
                return [{"value": (None, 1.0)}]
            if expr == 'service_cpu_percent{service="database"}':
                return [{"value": (None, 50.0)}]
            if expr == 'service_latency_ms{service="database"}':
                return [{"value": (None, 100.0)}]
            if expr == 'service_dependency_up{service="api",dependency="database"}':
                return [{"value": (None, state["gauge_value"])}]
            return []

        result = rv.verify_recovery(
            target="database",
            max_attempts=5,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=query,
            dependency="database",
            dependency_probe_func=probe,
        )
        self.assertEqual(result.status, "recovered")

    def test_active_probe_runs_before_dependency_metric(self):
        """Probe must be evaluated before the Prometheus dependency gauge."""
        order = []

        def probe(dependency):
            order.append("probe")
            return True

        def query(expr):
            if "service_dependency_up" in expr:
                order.append("metric")
                return [{"value": (None, 1.0)}]
            return [{"value": (None, 1.0)}]

        rv.verify_recovery(
            target="database",
            max_attempts=1,
            interval_seconds=0,
            required_consecutive=1,
            sleep_func=lambda s: None,
            query_func=query,
            dependency="database",
            dependency_probe_func=probe,
        )
        self.assertEqual(order, ["probe", "metric"])

    def test_active_probe_false_overrides_healthy_gauge(self):
        """Probe returning False must prevent recovery even if the gauge is 1."""
        def query(expr):
            if "service_dependency_up" in expr:
                return [{"value": (None, 1.0)}]
            return [{"value": (None, 1.0)}]

        result = rv.verify_recovery(
            target="database",
            max_attempts=3,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=query,
            dependency="database",
            dependency_probe_func=lambda d: False,
        )
        self.assertEqual(result.status, "not_recovered")

    def test_active_probe_exception_treated_as_not_recovered(self):
        """A probe that raises must be handled gracefully without crashing."""
        def query(expr):
            if "service_dependency_up" in expr:
                return [{"value": (None, 1.0)}]
            return [{"value": (None, 1.0)}]

        def raising_probe(dependency):
            raise ValueError("boom")

        result = rv.verify_recovery(
            target="database",
            max_attempts=2,
            interval_seconds=0,
            required_consecutive=1,
            sleep_func=lambda s: None,
            query_func=query,
            dependency="database",
            dependency_probe_func=raising_probe,
        )
        self.assertEqual(result.status, "not_recovered")

    def test_active_probe_none_is_unhealthy(self):
        """A supplied probe returning None must be treated as unhealthy."""
        def query(expr):
            if "service_dependency_up" in expr:
                return [{"value": (None, 1.0)}]
            return [{"value": (None, 1.0)}]

        result = rv.verify_recovery(
            target="database",
            max_attempts=3,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=query,
            dependency="database",
            dependency_probe_func=lambda d: None,
        )
        self.assertEqual(result.status, "not_recovered")

    def test_active_probe_string_true_is_unhealthy(self):
        """A supplied probe returning a string must be treated as unhealthy."""
        def query(expr):
            if "service_dependency_up" in expr:
                return [{"value": (None, 1.0)}]
            return [{"value": (None, 1.0)}]

        result = rv.verify_recovery(
            target="database",
            max_attempts=3,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=query,
            dependency="database",
            dependency_probe_func=lambda d: "true",
        )
        self.assertEqual(result.status, "not_recovered")

    def test_active_probe_integer_one_is_unhealthy(self):
        """A supplied probe returning 1 must be treated as unhealthy."""
        def query(expr):
            if "service_dependency_up" in expr:
                return [{"value": (None, 1.0)}]
            return [{"value": (None, 1.0)}]

        result = rv.verify_recovery(
            target="database",
            max_attempts=3,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=query,
            dependency="database",
            dependency_probe_func=lambda d: 1,
        )
        self.assertEqual(result.status, "not_recovered")

    def test_dependency_gauge_stale_without_probe_is_not_recovered(self):
        """Without a probe, a stale dependency gauge blocks recovery."""
        def query(expr):
            if "service_dependency_up" in expr:
                return [{"value": (None, 0.0)}]
            return [{"value": (None, 1.0)}]

        result = rv.verify_recovery(
            target="database",
            max_attempts=3,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=query,
            dependency="database",
        )
        self.assertEqual(result.status, "not_recovered")

    def test_no_probe_uses_dependency_metric_only(self):
        """Without a probe, recovery relies on the Prometheus dependency gauge."""
        def query(expr):
            if "service_dependency_up" in expr:
                return [{"value": (None, 1.0)}]
            return [{"value": (None, 1.0)}]

        result = rv.verify_recovery(
            target="database",
            max_attempts=3,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=query,
            dependency="database",
        )
        self.assertEqual(result.status, "recovered")

    def test_active_probe_true_gauge_stale_not_recovered(self):
        """A healthy active probe cannot override a stale Prometheus gauge."""
        def query(expr):
            if "service_dependency_up" in expr:
                return [{"value": (None, 0.0)}]
            return [{"value": (None, 1.0)}]

        result = rv.verify_recovery(
            target="database",
            max_attempts=3,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=query,
            dependency="database",
            dependency_probe_func=lambda d: True,
        )
        self.assertEqual(result.status, "not_recovered")

    def test_two_consecutive_healthy_samples_required(self):
        """Both probe and gauge must be healthy for required_consecutive samples."""
        calls = {"count": 0}

        def probe(dependency):
            return True

        def query(expr):
            if "service_dependency_up" in expr:
                calls["count"] += 1
                # Return 1.0 every call; need 2 consecutive healthy samples.
                return [{"value": (None, 1.0)}]
            return [{"value": (None, 1.0)}]

        result = rv.verify_recovery(
            target="database",
            max_attempts=5,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=query,
            dependency="database",
            dependency_probe_func=probe,
        )
        self.assertEqual(result.status, "recovered")

    def test_non_dependency_target_does_not_invoke_probe(self):
        """A target without a dependency should not call the probe function."""
        invoked = [False]

        def probe(dependency):
            invoked[0] = True
            return True

        def query(expr):
            return [{"value": (None, 1.0)}]

        result = rv.verify_recovery(
            target="api",
            max_attempts=3,
            interval_seconds=0,
            required_consecutive=2,
            sleep_func=lambda s: None,
            query_func=query,
            dependency=None,
            dependency_probe_func=probe,
        )
        self.assertEqual(result.status, "recovered")
        self.assertFalse(invoked[0])


if __name__ == "__main__":
    unittest.main()
