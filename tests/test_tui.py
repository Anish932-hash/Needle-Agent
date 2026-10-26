import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from needle_agent.tui import NeedleTUI
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False


@unittest.skipUnless(TEXTUAL_AVAILABLE, "Textual is not installed")
class TUITests(unittest.TestCase):
    def test_cloud_setup_model_discovery_populates_selector(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                app = NeedleTUI(Path(temp))
                with patch("needle_agent.tui.detect_models", return_value=["model-a", "model-b"]):
                    async with app.run_test() as pilot:
                        app.action_show_setup()
                        await pilot.pause()
                        app.query_one("#base-url").value = "https://api.example.com/v1"
                        app.query_one("#api-key").value = "test-key"
                        await pilot.click("#detect-models")
                        await pilot.pause()
                        selector = app.query_one("#model-select")
                        self.assertEqual(selector.value, "model-a")
                        self.assertIn("2 model(s)", str(app.query_one("#cloud-status").render()))
        asyncio.run(scenario())

    def test_cloud_plans_then_local_needle_executes_and_cloud_reviews(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                app = NeedleTUI(Path(temp))

                class FakeCloud:
                    def __init__(self):
                        self.reviewed = None
                    def plan(self, prompt):
                        return "Search the workspace, then read the matching note."
                    def review(self, prompt, plan, actions):
                        self.reviewed = (prompt, plan, actions)
                        return "COMPLETED: local read action succeeded."

                class FakeLocal:
                    def __init__(self):
                        self.prompt = ""
                    def complete(self, prompt):
                        self.prompt = prompt
                        return {"function_calls": [
                            {"name": "read_file", "arguments": {"path": "notes/a.txt"}}]}

                cloud, local = FakeCloud(), FakeLocal()
                app.local_agent = local
                with patch("needle_agent.tui.CloudAgent", return_value=cloud), \
                     patch("needle_agent.tui.dispatch", return_value={"path": "notes/a.txt", "text": "hello"}) as dispatch:
                    async with app.run_test() as pilot:
                        app.query_one("#provider").value = "cloud"
                        app.query_one("#base-url").value = "https://api.example.com/v1"
                        app.query_one("#api-key").value = "test-key"
                        app.query_one("#model-select").set_options([("model-a", "model-a")])
                        app.query_one("#model-select").value = "model-a"
                        app.query_one("#prompt").value = "Read notes/a.txt"
                        task = asyncio.create_task(app.submit_prompt())
                        await pilot.pause(0.3)
                        await pilot.click("#approve")
                        await task
                        self.assertEqual(dispatch.call_count, 1)
                        self.assertIn("Cloud planning notes", local.prompt)
                        self.assertEqual(cloud.reviewed[2][0]["result"]["text"], "hello")
                        self.assertIn("Read notes/a.txt", cloud.reviewed[0])
        asyncio.run(scenario())

    def test_tui_mounts_chat_and_cloud_setup_screens(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                app = NeedleTUI(Path(temp))
                async with app.run_test() as pilot:
                    self.assertIsNotNone(app.query_one("#transcript"))
                    self.assertIsNotNone(app.query_one("#model-select"))
                    app.action_show_setup()
                    await pilot.pause()
                    self.assertEqual(app.query_one("#base-url").value, "https://api.openai.com/v1")
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
