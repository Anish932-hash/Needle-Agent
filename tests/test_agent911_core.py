from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from needle_agent.agent911_core import (
    Agent911Core,
    dispatch_action,
    validate_action,
    verify_agent911,
)
from needle_agent.tools import Workspace


class FakeModel:
    def __init__(self, result):
        self.result = result
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.result


class Agent911ToolContractTests(unittest.TestCase):
    def test_default_arguments_and_workspace_aliases_are_normalized(self):
        name, args = validate_action("list_files", {})
        self.assertEqual((name, args), ("list_files", {"path": "."}))

        name, args = validate_action("list_files", {"path": "the workspace folder"})
        self.assertEqual((name, args), ("list_files", {"path": "."}))

        name, args = validate_action("search_workspace", {"query": "needle"})
        self.assertEqual((name, args), ("search_workspace", {"query": "needle", "path": "."}))

    def test_schema_rejects_unknown_tool_unexpected_fields_and_missing_arguments(self):
        with self.assertRaisesRegex(ValueError, "Unsupported Agent 911 tool"):
            validate_action("run_shell", {"command": "echo unsafe"})
        with self.assertRaisesRegex(ValueError, "Unexpected argument"):
            validate_action("system_info", {"extra": "field"})
        with self.assertRaisesRegex(ValueError, "Missing required"):
            validate_action("write_file", {"path": "note.txt"})

    def test_dispatch_is_workspace_confined(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Workspace(Path(temp) / "workspace")
            with self.assertRaisesRegex(ValueError, "outside"):
                dispatch_action(workspace, "read_file", {"path": "../outside.txt"})
            with self.assertRaisesRegex(ValueError, "reserved"):
                dispatch_action(workspace, "read_file", {"path": ".needle-agent-memory.json"})

    def test_write_round_trip_runs_only_when_explicitly_dispatched(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Workspace(Path(temp) / "workspace")
            destination = workspace.root / "reviewed.txt"
            proposal = {"name": "write_file", "arguments": {
                "path": "reviewed.txt", "text": "exact literal",
            }}
            self.assertFalse(destination.exists(), "a proposal object by itself must not execute")
            result = dispatch_action(workspace, proposal["name"], proposal["arguments"])
            self.assertEqual(destination.read_text(encoding="utf-8"), "exact literal")
            self.assertEqual(result["written"], "reviewed.txt")

    def test_core_builds_its_own_toolset_and_restores_engine_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = Workspace(root / "workspace")
            weights = root / "needle3.cact"
            engine = root / "libneedle3.so"
            weights.write_bytes(b"fake weights")
            engine.write_bytes(b"fake engine")
            created = []

            fake_needle = types.ModuleType("needle")
            fake_needle.tool = lambda function: function

            def fake_constructor(**options):
                created.append(options)
                return FakeModel({"type": "respond", "function_calls": [], "success": True})

            fake_needle.Needle = fake_constructor
            with patch.dict(sys.modules, {"needle": fake_needle}):
                with patch.dict(os.environ, {}, clear=False):
                    with patch("needle_agent.agent911_core._BASELINE_NEEDLE_ENGINE", None):
                        with patch("needle_agent.agent911_core._ACTIVE_AGENT911_ENGINE_OVERRIDE", None):
                            os.environ.pop("NEEDLE3_LIB_PATH", None)
                            Agent911Core(workspace, weights=weights, engine_path=engine)
                            self.assertEqual(os.environ.get("NEEDLE3_LIB_PATH"), str(engine.resolve()))
                            self.assertIn("system_info", [fn.__name__ for fn in created[0]["tools"]])
                            self.assertTrue(created[0]["stateless"])
                            self.assertEqual(created[0]["weights"], str(weights))

                            Agent911Core(workspace, weights=weights, engine_path=None)
                            self.assertNotIn("NEEDLE3_LIB_PATH", os.environ)
                            self.assertEqual(len(created), 2)

    def test_live_model_output_is_returned_without_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Workspace(Path(temp) / "workspace")
            destination = workspace.root / "must-not-exist.txt"
            proposal = {
                "assistant_message": "This is a proposal.",
                "function_calls": [{
                    "name": "write_file",
                    "arguments": {"path": "must-not-exist.txt", "text": "not executed"},
                }],
            }
            core = object.__new__(Agent911Core)
            core.model = FakeModel(proposal)

            result = core.complete("Please make a proposal only.")

            self.assertEqual(result, proposal)
            self.assertEqual(len(core.model.prompts), 1)
            self.assertFalse(destination.exists())

    def test_deterministic_verification_suite_passes_without_loading_a_model(self):
        report = verify_agent911()

        self.assertTrue(report["passed"], report)
        self.assertFalse(report["model_inference_performed"])
        self.assertIn("deleted", report["workspace"])
        self.assertGreaterEqual(len(report["checks"]), 6)
        self.assertTrue(all(check["passed"] for check in report["checks"]))


if __name__ == "__main__":
    unittest.main()
