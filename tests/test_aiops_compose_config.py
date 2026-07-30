import json
import os
import subprocess
import sys
import tempfile
import unittest

# Add parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COMPOSE_FILE = os.path.join(PROJECT_ROOT, "docker-compose.yml")
ENTRYPOINT_SCRIPT = os.path.join(PROJECT_ROOT, "alertmanager", "entrypoint.sh")
TEMPLATE_FILE = os.path.join(PROJECT_ROOT, "alertmanager", "alertmanager.yml")


class TestAIOpsComposeConfig(unittest.TestCase):

    def test_env_example_whisper_token_is_valid(self):
        """Verify .env.example WHISPER_TOKEN is a non-blank 32-128 char token using the allowed charset."""
        env_example_path = os.path.join(PROJECT_ROOT, ".env.example")
        self.assertTrue(os.path.exists(env_example_path), ".env.example must exist")

        token = None
        with open(env_example_path, "r") as f:
            for line in f:
                if line.startswith("WHISPER_TOKEN="):
                    token = line.strip().split("=", 1)[1]
                    break

        self.assertIsNotNone(token, "WHISPER_TOKEN must be present in .env.example")
        self.assertTrue(token, "WHISPER_TOKEN must not be blank")
        self.assertGreaterEqual(len(token), 32, "WHISPER_TOKEN must be at least 32 characters")
        self.assertLessEqual(len(token), 128, "WHISPER_TOKEN must be at most 128 characters")
        self.assertRegex(token, r"^[A-Za-z0-9._~-]+$", "WHISPER_TOKEN contains disallowed characters")

    def test_docker_compose_config_quiet(self):
        """Verify docker compose config --quiet passes with safe throwaway values."""
        env = os.environ.copy()
        env["WHISPER_TOKEN"] = "throwaway_safe_token_32_characters_long"
        env["GRAFANA_ADMIN_PASSWORD"] = "throwaway_password_123"

        res = subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "config", "--quiet"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            res.returncode, 0, f"docker compose config failed: {res.stderr}"
        )

    def test_docker_compose_yaml_structure_and_defaults(self):
        """Verify docker-compose.yml contains fail-safe HEALING_ENABLED and bounded work settings."""
        with open(COMPOSE_FILE, "r") as f:
            content = f.read()

        self.assertIn("HEALING_ENABLED: \"${HEALING_ENABLED:-false}\"", content)
        self.assertIn("AIOPS_MAX_WORKERS: \"${AIOPS_MAX_WORKERS:-5}\"", content)
        self.assertIn("AIOPS_QUEUE_CAPACITY: \"${AIOPS_QUEUE_CAPACITY:-10}\"", content)
        self.assertIn("AIOPS_ERROR_RATIO_THRESHOLD: \"${AIOPS_ERROR_RATIO_THRESHOLD:-0.10}\"", content)
        self.assertIn("entrypoint: [\"/bin/sh\", \"/etc/alertmanager/entrypoint.sh\"]", content)
        self.assertIn("- ./alertmanager/entrypoint.sh:/etc/alertmanager/entrypoint.sh:ro", content)
        self.assertIn("- GF_PLUGINS_PREINSTALL_DISABLED=true", content)

    def test_alertmanager_entrypoint_valid_token_rendering(self):
        """Verify entrypoint.sh succeeds with valid token (32-128 chars, safe charset) without leaking token."""
        valid_token = "valid_safe_wh~sper_token_1234567890123456"
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_path = os.path.join(tmp_dir, "alertmanager.yml")
            env = os.environ.copy()
            env["WHISPER_TOKEN"] = valid_token
            env["TEMPLATE_FILE"] = TEMPLATE_FILE
            env["TARGET_FILE"] = target_path

            res = subprocess.run(
                [ENTRYPOINT_SCRIPT, "validate-only"],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, f"Valid token rejected: {res.stderr}")
            self.assertIn("Validation and rendering successful", res.stdout)
            self.assertNotIn(valid_token, res.stdout)
            self.assertNotIn(valid_token, res.stderr)

            # Check rendered file contents
            self.assertTrue(os.path.exists(target_path))
            with open(target_path, "r") as f:
                rendered = f.read()
            self.assertIn(valid_token, rendered)
            self.assertNotIn("__WHISPER_TOKEN__", rendered)

            # Check restrictive 0600 permissions
            mode = oct(os.stat(target_path).st_mode & 0o777)
            self.assertEqual(mode, "0o600")

    def test_alertmanager_entrypoint_multiple_sentinels_no_infinite_loop(self):
        """Verify rendering multiple sentinels, including a token containing __WHISPER_TOKEN__, terminates cleanly."""
        # Token is valid and contains both '~' and the literal sentinel substring.
        valid_token = "valid__WHISPER_TOKEN__wh~sper_token_12345"
        self.assertGreaterEqual(len(valid_token), 32)
        self.assertRegex(valid_token, r"^[A-Za-z0-9._~-]+$")

        template_content = (
            "__WHISPER_TOKEN__\n"
            "prefix: __WHISPER_TOKEN__ middle: __WHISPER_TOKEN__\n"
            "last: __WHISPER_TOKEN__\n"
        )
        expected = template_content.replace("__WHISPER_TOKEN__", valid_token)

        with tempfile.TemporaryDirectory() as tmp_dir:
            template_path = os.path.join(tmp_dir, "template.yml")
            target_path = os.path.join(tmp_dir, "alertmanager.yml")
            with open(template_path, "w") as f:
                f.write(template_content)

            env = os.environ.copy()
            env["WHISPER_TOKEN"] = valid_token
            env["TEMPLATE_FILE"] = template_path
            env["TARGET_FILE"] = target_path

            res = subprocess.run(
                [ENTRYPOINT_SCRIPT, "validate-only"],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(res.returncode, 0, f"Rendering failed: {res.stderr}")
            self.assertNotIn(valid_token, res.stdout)
            self.assertNotIn(valid_token, res.stderr)
            self.assertIn("Validation and rendering successful", res.stdout)

            self.assertTrue(os.path.exists(target_path))
            with open(target_path, "r") as f:
                rendered = f.read()

            self.assertEqual(rendered, expected)
            # All standalone sentinels were replaced; any remaining
            # "__WHISPER_TOKEN__" text is part of the intentionally embedded token.
            self.assertEqual(rendered.count(valid_token), template_content.count("__WHISPER_TOKEN__"))

            mode = oct(os.stat(target_path).st_mode & 0o777)
            self.assertEqual(mode, "0o600")

    def test_alertmanager_entrypoint_invalid_tokens_rejected(self):
        """Verify entrypoint.sh rejects empty, short, long, whitespace, newline, and special character tokens."""
        invalid_tokens = [
            ("", "empty token"),
            ("short_token", "short token < 32 chars"),
            ("a" * 129, "long token > 128 chars"),
            ("token with space inside 32chars long", "space containing token"),
            ("token_with_\n_newline_inside_32chars", "newline containing token"),
            ("token_with_$shell_special_chars_123", "shell special dollar"),
            ("token_with_;command_injection_1234", "shell special semicolon"),
            ("token_with_|pipe_operator_123456", "shell special pipe"),
            ("token_with_/sed_slash_delimiter_12", "sed special slash"),
            ("token_with_\\backslash_char_123456", "sed special backslash"),
            ("token_with_&ampersand_char_123456", "sed special ampersand"),
            ("token_with_'single_quote'_1234567", "single quote"),
            ("token_with_\"double_quote\"_1234567", "double quote"),
        ]

        for token, desc in invalid_tokens:
            with tempfile.TemporaryDirectory() as tmp_dir:
                target_path = os.path.join(tmp_dir, "alertmanager.yml")
                env = os.environ.copy()
                env["WHISPER_TOKEN"] = token
                env["TEMPLATE_FILE"] = TEMPLATE_FILE
                env["TARGET_FILE"] = target_path

                res = subprocess.run(
                    [ENTRYPOINT_SCRIPT, "validate-only"],
                    cwd=PROJECT_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(
                    res.returncode,
                    0,
                    f"Entrypoint script unexpectedly accepted invalid token case: {desc} ({token})",
                )
                self.assertIn("Error:", res.stderr)
                if token:
                    self.assertNotIn(token, res.stdout)
                    self.assertNotIn(token, res.stderr)


    def test_grafana_prometheus_datasource_uid_matches_dashboard_references(self):
        """Provisioned Prometheus datasource UID must match every Prometheus reference in the dashboard."""
        datasource_yaml_path = os.path.join(
            PROJECT_ROOT, "grafana", "provisioning", "datasources", "datasource.yaml"
        )
        dashboard_json_path = os.path.join(
            PROJECT_ROOT, "grafana", "provisioning", "dashboards", "json", "dashboard.json"
        )

        self.assertTrue(os.path.exists(datasource_yaml_path), "datasource.yaml must exist")
        self.assertTrue(os.path.exists(dashboard_json_path), "dashboard.json must exist")

        provisioned_uid = self._prometheus_uid_from_datasource_yaml(datasource_yaml_path)
        self.assertEqual(provisioned_uid, "Prometheus")

        with open(dashboard_json_path, "r") as f:
            dashboard = json.load(f)

        referenced_uids = set(self._collect_prometheus_datasource_uids(dashboard))
        self.assertTrue(referenced_uids, "Dashboard must reference at least one Prometheus datasource")
        self.assertEqual(
            referenced_uids,
            {"Prometheus"},
            f"All dashboard Prometheus datasource references must resolve to the provisioned UID, found {referenced_uids}",
        )

    @staticmethod
    def _prometheus_uid_from_datasource_yaml(path):
        uid = None
        in_prometheus = False
        with open(path, "r") as f:
            for line in f:
                stripped = line.split("#")[0].rstrip()
                if stripped.strip() == "- name: Prometheus":
                    in_prometheus = True
                    uid = None
                elif stripped.strip().startswith("- "):
                    in_prometheus = False
                elif in_prometheus and stripped.strip().startswith("uid:"):
                    uid = stripped.split(":", 1)[1].strip()
        return uid

    @classmethod
    def _collect_prometheus_datasource_uids(cls, obj):
        uids = []
        if isinstance(obj, dict):
            if obj.get("type") == "prometheus" and "uid" in obj:
                uids.append(obj["uid"])
            if "datasource" in obj:
                ds = obj["datasource"]
                if isinstance(ds, dict) and ds.get("type") == "prometheus":
                    uids.append(ds["uid"])
            for value in obj.values():
                uids.extend(cls._collect_prometheus_datasource_uids(value))
        elif isinstance(obj, list):
            for item in obj:
                uids.extend(cls._collect_prometheus_datasource_uids(item))
        return uids


if __name__ == "__main__":
    unittest.main()
