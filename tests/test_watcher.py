import unittest
import sys
import os
from unittest.mock import patch

# Add parent directory to sys.path so we can import inframirror
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from inframirror import llm_engine
from inframirror import watcher

class TestSREWatcherAndEngine(unittest.TestCase):
    def test_healthy_fallback_generation(self):
        # Generate healthy fallback dialogue
        dialogue = llm_engine._generate_healthy_fallback_dialogue()
        self.assertTrue(len(dialogue) > 0)
        
        # Check that it contains all 5 microservices in the dialog
        self.assertIn("Joy - Frontend", dialogue)
        self.assertIn("Logic - API", dialogue)
        self.assertIn("Memory - Database", dialogue)
        self.assertIn("Swift - Cache", dialogue)
        self.assertIn("Gatekeeper - Auth", dialogue)
        
        # Check that it has exactly 5 lines
        lines = [line for line in dialogue.split('\n') if line.strip()]
        self.assertEqual(len(lines), 5)
        
        # Check correct bracketed tags
        for line in lines:
            self.assertTrue(line.startswith("**["))
            self.assertTrue("]**:" in line)

    def test_incident_fallback_generation(self):
        # Generate incident fallback dialogue for database
        dialogue = llm_engine._generate_fallback_dialogue("database", 92.4, 380)
        self.assertTrue(len(dialogue) > 0)
        
        # Incident should have 6 lines (stressed service + 4 reactions + 1 SRE resolution)
        lines = [line for line in dialogue.split('\n') if line.strip()]
        self.assertEqual(len(lines), 6)
        
        # Check that the stressed service, the other 4 services, and SRE are in the dialog
        self.assertIn("Memory - Database", dialogue)
        self.assertIn("Joy - Frontend", dialogue)
        self.assertIn("Logic - API", dialogue)
        self.assertIn("Swift - Cache", dialogue)
        self.assertIn("Gatekeeper - Auth", dialogue)
        self.assertIn("InfraMirror - SRE", dialogue)
        
        # Check actual values are formatted
        self.assertIn("92.4%", dialogue)
        self.assertIn("380ms", dialogue)

    def test_trigger_healthy_dialogue_builtin(self):
        # Trigger ambient dialogue (should fall back to built-in scripts successfully)
        dialogue = llm_engine.trigger_healthy_dialogue(persist=False)
        self.assertTrue(len(dialogue) > 0)
        lines = [line for line in dialogue.split('\n') if line.strip()]
        self.assertEqual(len(lines), 5)

    def test_public_generators_accept_explicit_key_without_persisting(self):
        dialogue = llm_engine.generate_incident_dialogue(
            "database",
            91.2,
            401,
            gemini_key="",
            persist=False,
        )
        self.assertIn("Memory - Database", dialogue)
        self.assertIn("InfraMirror - SRE", dialogue)

    def test_incident_dialogue_can_suppress_discord(self):
        with patch.object(llm_engine, "_send_discord_embed") as send_embed:
            dialogue = llm_engine.generate_incident_dialogue(
                "api",
                90.0,
                355,
                gemini_key="",
                persist=False,
                send_discord=False,
                webhook_url="https://example.invalid/webhook",
            )

        self.assertIn("Logic - API", dialogue)
        self.assertIn("InfraMirror - SRE", dialogue)
        send_embed.assert_not_called()

    def test_whisper_ignores_resolved_alertmanager_payload(self):
        payload = {
            "status": "resolved",
            "alerts": [{
                "status": "resolved",
                "labels": {
                    "alertname": "CriticalCPULoad",
                    "service": "api",
                },
            }],
        }

        with patch.object(watcher, "_process_whisper_alert") as process_alert:
            response = watcher.app.test_client().post("/whisper", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reason"], "alert resolved")
        process_alert.assert_not_called()

if __name__ == '__main__':
    unittest.main()
