import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from rich.text import Text
    from needle_agent.tui import NeedleTUI, _write_log
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

    def test_hybrid_runs_bounded_followup_and_verifies_literal_file_write(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                app = NeedleTUI(Path(temp) / "workspace")

                class FakeCloud:
                    def __init__(self):
                        self.proposals = []
                        self.reviewed = None

                    def plan(self, prompt):
                        return "Write the requested literal text, then verify it from the workspace."

                    def propose_tools(self, prompt, plan="", previous_outcomes=None, round_number=1):
                        self.proposals.append((prompt, plan, previous_outcomes or [], round_number))
                        return {"assistant_message": "", "function_calls": []}

                    def review(self, prompt, plan, outcomes):
                        self.reviewed = (prompt, plan, outcomes)
                        return "INCOMPLETE or COMPLETED should be judged from recorded evidence."

                class FakeLocal:
                    def __init__(self):
                        self.prompts = []

                    def complete(self, prompt):
                        self.prompts.append(prompt)
                        if len(self.prompts) == 1:
                            return {"assistant_message": "", "function_calls": [
                                {"name": "write_file", "arguments": {"path": "text.txt", "text": "Test"}}
                            ]}
                        if len(self.prompts) == 2:
                            return {"assistant_message": "", "function_calls": [
                                {"name": "read_file", "arguments": {"path": "Text.txt"}}
                            ]}
                        return {"assistant_message": "The file contains Hello.", "function_calls": []}

                cloud, local = FakeCloud(), FakeLocal()
                app.local_agent = local
                with patch("needle_agent.tui.CloudAgent", return_value=cloud):
                    async with app.run_test() as pilot:
                        app.query_one("#provider").value = "hybrid"
                        app.query_one("#base-url").value = "https://api.example.com/v1"
                        app.query_one("#api-key").value = "test-key"
                        app.query_one("#model-select").set_options([("model-a", "model-a")])
                        app.query_one("#model-select").value = "model-a"
                        app.query_one("#prompt").value = "Write Hello in the Text.txt"
                        task = asyncio.create_task(app.submit_prompt())
                        await pilot.pause(0.3)
                        await pilot.click("#approve")
                        await pilot.pause(0.4)
                        await pilot.click("#approve")
                        await task

                file_path = app.workspace.root / "Text.txt"
                self.assertTrue(file_path.is_file())
                self.assertEqual(file_path.read_text(encoding="utf-8"), "Hello")
                self.assertEqual(len(local.prompts), 3)
                self.assertIn("Previously observed local outcomes", local.prompts[1])
                self.assertEqual(len(cloud.proposals), 3)
                self.assertEqual(cloud.proposals[1][2][0]["verification"]["status"], "passed")
                self.assertEqual(len(cloud.reviewed[2]), 2)
                self.assertEqual(cloud.reviewed[2][0]["arguments"], {"path": "Text.txt", "text": "Hello"})
                self.assertEqual(cloud.reviewed[2][0]["verification"]["status"], "passed")
                self.assertEqual(cloud.reviewed[2][1]["verification"]["status"], "passed")
        asyncio.run(scenario())

    def test_log_keeps_user_supplied_rich_markup_literal(self):
        class Collector:
            renderable = None
            def write(self, renderable):
                self.renderable = renderable

        collector = Collector()
        user_text = "[bold red]this is user input[/bold red]"
        _write_log(collector, "You", user_text, "bold green")

        self.assertIsInstance(collector.renderable, Text)
        self.assertEqual(collector.renderable.plain, "You  " + user_text)
        self.assertEqual(collector.renderable.spans[0].style, "bold green")

    def test_submit_lock_prevents_overlapping_model_requests(self):
        async def scenario():
            import time
            with tempfile.TemporaryDirectory() as temp:
                app = NeedleTUI(Path(temp))
                class SlowLocal:
                    calls = 0
                    def complete(self, prompt):
                        self.calls += 1
                        time.sleep(0.15)
                        return {"assistant_message": "No action needed.", "function_calls": []}
                local = SlowLocal()
                app.local_agent = local
                async with app.run_test() as pilot:
                    app.query_one("#prompt").value = "first request"
                    task = asyncio.create_task(app.submit_prompt())
                    await pilot.pause(0.03)
                    self.assertTrue(app._busy)
                    self.assertTrue(app.query_one("#send").disabled)
                    await app.submit_prompt()
                    await task
                    self.assertEqual(local.calls, 1)
                    self.assertFalse(app._busy)
                    self.assertFalse(app.query_one("#send").disabled)
        asyncio.run(scenario())

    def test_tui_mounts_chat_and_cloud_setup_screens(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                app = NeedleTUI(Path(temp))
                async with app.run_test() as pilot:
                    self.assertIsNotNone(app.query_one("#transcript"))
                    self.assertIsNotNone(app.query_one("#model-select"))
                    self.assertIsNotNone(app.query_one("#runtime-status"))
                    self.assertIsNotNone(app.query_one("#clear-transcript"))
                    app.action_show_setup()
                    await pilot.pause()
                    self.assertEqual(app.query_one("#base-url").value, "https://api.openai.com/v1")
        asyncio.run(scenario())


    def test_missing_cloud_model_is_reported_without_crashing(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                with patch("needle_agent.tui.CloudSettings") as settings_class:
                    settings_class.return_value.load.return_value = {
                        "base_url": "https://api.example.com/v1", "model": "", "api_key": "",
                    }
                    app = NeedleTUI(Path(temp))
                    with patch("needle_agent.tui.CloudAgent") as cloud_factory:
                        async with app.run_test() as pilot:
                            app.query_one("#provider").value = "cloud"
                            app.query_one("#prompt").value = "Plan a task"
                            await app.submit_prompt()
                            await pilot.pause()
                            cloud_factory.assert_not_called()
                            self.assertFalse(app._busy)
                            self.assertFalse(app.query_one("#send").disabled)
                            status = app.query_one("#runtime-status").render()
                            rendered = status.plain if hasattr(status, "plain") else str(status)
                            self.assertIn("select a model", rendered.lower())
        asyncio.run(scenario())

    def test_unexpected_model_discovery_error_is_contained_and_unlocks_button(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                app = NeedleTUI(Path(temp))
                with patch("needle_agent.tui.detect_models", side_effect=RuntimeError("provider exploded")):
                    async with app.run_test() as pilot:
                        app.action_show_setup()
                        app.query_one("#base-url").value = "https://api.example.com/v1"
                        app.query_one("#api-key").value = "test-key"
                        await pilot.click("#detect-models")
                        await pilot.pause()
                        self.assertFalse(app._detecting_models)
                        self.assertFalse(app.query_one("#detect-models").disabled)
                        status = app.query_one("#cloud-status").render()
                        rendered = status.plain if hasattr(status, "plain") else str(status)
                        self.assertIn("RuntimeError", rendered)
                        self.assertIn("provider exploded", rendered)
        asyncio.run(scenario())

    def test_cloud_save_filesystem_error_is_contained(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                app = NeedleTUI(Path(temp))
                with patch.object(app.settings, "save", side_effect=OSError("read-only config directory")):
                    async with app.run_test() as pilot:
                        app.action_show_setup()
                        app.query_one("#model-select").set_options([("model-a", "model-a")])
                        app.query_one("#model-select").value = "model-a"
                        await pilot.click("#save-cloud")
                        await pilot.pause()
                        status = app.query_one("#cloud-status").render()
                        rendered = status.plain if hasattr(status, "plain") else str(status)
                        self.assertIn("OSError", rendered)
                        self.assertIn("read-only config directory", rendered)
        asyncio.run(scenario())

    def test_invalid_model_response_is_rejected_and_controls_unlock(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                app = NeedleTUI(Path(temp))
                class InvalidLocal:
                    def complete(self, prompt):
                        return None
                app.local_agent = InvalidLocal()
                with patch("needle_agent.tui._write_log", wraps=_write_log) as write_log:
                    async with app.run_test() as pilot:
                        app.query_one("#prompt").value = "do something"
                        await app.submit_prompt()
                        await pilot.pause()
                        self.assertFalse(app._busy)
                        self.assertFalse(app.query_one("#send").disabled)
                        self.assertFalse(app.query_one("#prompt").disabled)
                        # The invalid response is handled as a rejected proposal, not an app exception.
                        self.assertTrue(any(
                            call.args[1] == "Rejected"
                            and "invalid response structure" in str(call.args[2]).lower()
                            for call in write_log.call_args_list
                        ))
        asyncio.run(scenario())

    def test_local_runtime_exception_does_not_leave_controls_disabled(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp:
                app = NeedleTUI(Path(temp))
                class BrokenLocal:
                    def complete(self, prompt):
                        raise RuntimeError("simulated runtime failure")
                app.local_agent = BrokenLocal()
                async with app.run_test() as pilot:
                    app.query_one("#prompt").value = "request"
                    await app.submit_prompt()
                    await pilot.pause()
                    self.assertFalse(app._busy)
                    self.assertFalse(app.query_one("#send").disabled)
                    self.assertFalse(app.query_one("#prompt").disabled)
                    status = app.query_one("#runtime-status").render()
                    rendered = status.plain if hasattr(status, "plain") else str(status)
                    self.assertIn("Error", rendered)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
