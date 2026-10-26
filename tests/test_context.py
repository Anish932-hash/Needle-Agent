import tempfile
import unittest
from pathlib import Path
from needle_agent.conversation import ConversationMemory
from needle_agent.evolution import EvolutionMemory
from needle_agent.context import AutomaticContextManager


class ContextManagerTests(unittest.TestCase):
    def test_current_request_survives_context_pressure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prefs = EvolutionMemory(root / "prefs.json")
            history = ConversationMemory(root / "history.json")
            prefs.learn("Keep answers brief")
            for i in range(12):
                history.add(f"older task {i} " + "z" * 400, "done")
            manager = AutomaticContextManager(history, prefs, token_budget=256)
            prompt = manager.prepare("CURRENT unique request")
            self.assertIn("CURRENT unique request", prompt)
            self.assertLessEqual(manager.last_stats["estimated_tokens"], 256)
            self.assertIn("Current request", prompt)

    def test_oversized_request_is_rejected_not_silently_truncated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = AutomaticContextManager(ConversationMemory(root / "h"),
                                             EvolutionMemory(root / "p"), 256)
            request = "x" * 1000
            with self.assertRaisesRegex(ValueError, "shorten it"):
                manager.prepare(request)

    def test_budget_minimum(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                AutomaticContextManager(ConversationMemory(root / "h"),
                                        EvolutionMemory(root / "p"), 100)


if __name__ == "__main__":
    unittest.main()
