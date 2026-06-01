import unittest
import sys
import os

# Add parent directory to sys.path so we can import inframirror
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from inframirror import llm_engine

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

    def test_trigger_healthy_dialogue_local(self):
        # Trigger ambient dialogue (should fall back to local scripts successfully)
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

if __name__ == '__main__':
    unittest.main()
