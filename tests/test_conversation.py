import tempfile
import unittest
from pathlib import Path
from needle_agent.conversation import ConversationMemory


class ConversationMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "history.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_persists_and_reloads_turns(self):
        memory = ConversationMemory(self.path)
        memory.add("find my note", "read_file succeeded")
        reloaded = ConversationMemory(self.path)
        self.assertIn("find my note", reloaded.context())
        self.assertEqual(reloaded.status()["recent_turns"], 1)

    def test_compacts_old_turns_under_budget(self):
        memory = ConversationMemory(self.path, max_context_chars=250, keep_recent=2,
                                    max_summary_chars=100)
        for i in range(10):
            memory.add(f"task {i} " + "x" * 30, "done " + "y" * 30)
        status = memory.status()
        self.assertLessEqual(status["context_characters"], 250)
        self.assertLessEqual(status["recent_turns"], 2)
        self.assertGreater(status["summary_characters"], 0)

    def test_context_prefers_relevant_turns_and_keeps_latest(self):
        memory = ConversationMemory(self.path, keep_recent=10)
        memory.add("Find Aurora launch note", "search_workspace succeeded")
        memory.add("Check grocery list", "read_file succeeded")
        memory.add("Check the calendar", "read_file succeeded")
        memory.add("Play quiet music", "no supported call")
        memory.add("Look at today's weather", "no supported call")
        memory.add("Set the meeting room", "no supported call")
        context = memory.relevant_context("Open the Aurora launch note")
        self.assertIn("Aurora launch note", context)
        self.assertIn("Set the meeting room", context)
        self.assertNotIn("Check grocery list", context)

    def test_clear(self):
        memory = ConversationMemory(self.path)
        memory.add("task", "done")
        memory.clear()
        self.assertEqual(memory.context(), "")
        self.assertEqual(memory.status()["recent_turns"], 0)


if __name__ == "__main__":
    unittest.main()
