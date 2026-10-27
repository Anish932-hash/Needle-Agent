from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from needle_agent.web_bridge import WebBridge


class FakeAgent:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def complete(self, prompt):
        self.calls.append(prompt)
        return self.result


class WebBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.config_dir = self.workspace / "agent911-config"
        self.bridge = WebBridge(str(self.workspace), config_dir=self.config_dir)

    def tearDown(self):
        self.temp.cleanup()

    def _propose_write(self, path="note.txt", text="approved content"):
        self.bridge.agent = FakeAgent({
            "assistant_message": "I can propose the requested file write.",
            "function_calls": [{
                "name": "write_file",
                "arguments": {"path": path, "text": text},
            }],
        })
        return self.bridge.handle({
            "op": "chat",
            "payload": {"prompt": "Create a note in the workspace.", "mode": "local"},
        })

    def test_write_is_only_proposed_until_explicit_approval(self):
        response = self._propose_write()
        target = self.workspace / "note.txt"

        self.assertEqual(len(response["proposals"]), 1)
        self.assertFalse(target.exists(), "model output must not execute the proposed write")
        action_id = response["proposals"][0]["id"]

        outcome = self.bridge.handle({
            "op": "resolve_action",
            "payload": {"id": action_id, "approved": True},
        })

        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(encoding="utf-8"), "approved content")
        self.assertEqual(outcome["status"], "succeeded")
        self.assertEqual(outcome["verification"]["status"], "passed")
        self.assertTrue(outcome["turn_result"]["completed"])
        self.assertEqual(self.bridge.pending, {})

    def test_denied_write_never_touches_workspace(self):
        response = self._propose_write()
        target = self.workspace / "note.txt"
        action_id = response["proposals"][0]["id"]

        outcome = self.bridge.handle({
            "op": "resolve_action",
            "payload": {"id": action_id, "approved": False},
        })

        self.assertFalse(target.exists())
        self.assertEqual(outcome["status"], "denied")
        self.assertTrue(outcome["turn_result"]["completed"])

    def test_workspace_escape_is_rejected_before_approval(self):
        response = self._propose_write(path="../outside.txt")

        self.assertEqual(response["proposals"], [])
        self.assertEqual(len(response["rejected"]), 1)
        self.assertIn("outside", response["rejected"][0]["reason"].lower())
        self.assertEqual(self.bridge.pending, {})
        self.assertFalse((self.workspace.parent / "outside.txt").exists())

    def test_settings_response_never_returns_api_key(self):
        marker = "test-secret-that-must-not-leak"
        self.bridge.cloud_values["api_key"] = marker
        response = self.bridge.handle({"op": "settings"})

        self.assertNotIn("api_key", response["settings"])
        self.assertNotIn(marker, json.dumps(response))

    def test_malformed_model_response_does_not_crash_or_create_pending_actions(self):
        self.bridge.agent = FakeAgent(None)
        response = self.bridge.handle({
            "op": "chat",
            "payload": {"prompt": "Inspect the workspace.", "mode": "local"},
        })

        self.assertEqual(response["proposals"], [])
        self.assertEqual(self.bridge.pending, {})
        self.assertTrue(response["turn_result"]["completed"])

    def test_duplicate_or_unknown_action_ids_cannot_be_approved(self):
        with self.assertRaisesRegex(ValueError, "no longer pending"):
            self.bridge.handle({
                "op": "resolve_action",
                "payload": {"id": "not-a-real-action", "approved": True},
            })


    def test_deterministic_verification_uses_a_disposable_workspace(self):
        response = self.bridge.handle({"op": "verify"})
        report = response["report"]

        self.assertTrue(report["passed"], report)
        self.assertEqual(response["kind"], "deterministic_sandbox")
        self.assertGreaterEqual(len(report["checks"]), 5)
        self.assertIn("deleted", report["workspace"])
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_live_run_test_never_executes_model_proposals(self):
        self.bridge.agent = FakeAgent({
            "assistant_message": "Diagnostic complete.",
            "function_calls": [{
                "name": "write_file",
                "arguments": {"path": "must-not-exist.txt", "text": "must not run"},
            }],
            "suppressed_calls": [],
        })

        response = self.bridge.handle({"op": "live_test"})
        report = response["report"]

        self.assertTrue(report["passed"], report)
        self.assertEqual(report["model_proposal_count"], 1)
        self.assertEqual(report["model_proposals_executed"], 0)
        self.assertFalse((self.workspace / "must-not-exist.txt").exists())
        self.assertIn("model_inference", {item["name"] for item in report["checks"]})
        self.assertIn("local_diagnostic_tool", {item["name"] for item in report["checks"]})

    def test_model_configuration_is_saved_and_invalidates_loaded_runtime(self):
        weights = self.workspace / "local-model.cact"
        weights.write_bytes(b"test weights")
        self.bridge.agent = FakeAgent({"assistant_message": "already loaded", "function_calls": []})

        response = self.bridge.handle({
            "op": "save_model_configuration",
            "payload": {
                "default_mode": "hybrid",
                "local": {"weights_path": str(weights), "engine_path": ""},
            },
        })

        self.assertEqual(response["model_configuration"]["default_mode"], "hybrid")
        self.assertEqual(response["model_configuration"]["local"]["weights_path"], str(weights.resolve()))
        self.assertIsNone(self.bridge.agent, "changing model paths must discard the old runtime")
        self.assertEqual(self.bridge.default_mode, "hybrid")

    def test_draft_upgrade_saves_plan_but_ignores_model_tool_calls(self):
        self.bridge.agent = FakeAgent({
            "assistant_message": "1. Define the interface.\\n2. Add focused tests.",
            "function_calls": [{
                "name": "write_file",
                "arguments": {"path": "upgrade.py", "text": "this must not be written"},
            }],
        })

        response = self.bridge.handle({
            "op": "draft_upgrade",
            "payload": {"request": "Add a capability configuration screen with validation."},
        })

        self.assertEqual(response["item"]["status"], "planned")
        self.assertIn("Define the interface", response["item"]["plan"])
        self.assertEqual(len(response["items"]), 1)
        self.assertFalse((self.workspace / "upgrade.py").exists())
        self.assertIn("Do not call tools", self.bridge.agent.calls[0])

        archived = self.bridge.handle({
            "op": "archive_upgrade",
            "payload": {"id": response["item"]["id"]},
        })
        self.assertEqual(archived["item"]["status"], "archived")
        self.assertEqual(archived["items"][0]["status"], "archived")



if __name__ == "__main__":
    unittest.main()
