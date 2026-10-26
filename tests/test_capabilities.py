import json
import tempfile
import unittest
from pathlib import Path
from needle_agent.capabilities import CapabilityStore
from needle_agent.tools import Workspace
from needle_agent.sandbox import practice_request


class CapabilityStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.temp.name) / "ws")
        self.store = CapabilityStore(self.workspace)
        self.workspace.write_file("notes/aurora.txt", "Project Aurora launches in June.")

    def tearDown(self):
        self.temp.cleanup()

    def test_build_and_run_composed_workflow(self):
        definition = {
            "inputs": {"query": "Text phrase to find"},
            "steps": [
                {"tool": "search_workspace", "arguments": {"query": "${input.query}"}},
                {"tool": "read_file", "arguments": {"path": "${steps.0.matches.0.path}"}},
            ],
        }
        created = self.store.create("find_and_read", "Find and read the first matching note", json.dumps(definition))
        self.assertEqual(created["steps"], ["search_workspace", "read_file"])
        result = self.store.execute("find_and_read", json.dumps({"query": "Aurora"}))
        self.assertIn("Project Aurora", result["results"][1]["text"])
        self.assertEqual(self.store.list()[0]["name"], "find_and_read")

    def test_natural_capability_recipe_dispatch(self):
        self.workspace.write_file("notes/aurora.txt", "Aurora announcement")
        result = self.store.create_from_recipe("find_aurora", "Search and read a matching note", "find_and_read")
        self.assertEqual(result["steps"], ["search_workspace", "read_file"])
        output = self.store.execute("find_aurora", json.dumps({"query": "Aurora"}))
        self.assertIn("Aurora announcement", output["results"][1]["text"])

    def test_capability_delete_is_explicit_and_persistent(self):
        self.store.create_from_recipe("list_here", "List files", "list_workspace")
        self.assertEqual(self.store.delete("list_here"), {"deleted": "list_here"})
        self.assertEqual(self.store.list(), [])

    def test_saved_capability_can_be_practiced_in_disposable_workspace(self):
        self.store.create_from_recipe("list_here", "List files", "list_workspace")
        class FakeAgent:
            def complete(self, prompt):
                return {"confidence": 0.99, "function_calls": [
                    {"name": "run_capability", "arguments": {"name": "list_here", "inputs_json": "{}"}}]}
        report = practice_request(FakeAgent(), "run list_here", self.store.snapshot())
        self.assertTrue(report["passed"], report)
        self.assertTrue(report["calls"][0]["result"]["results"])

    def test_rejects_arbitrary_code_and_recursive_capabilities(self):
        for tool in ("run_shell", "create_capability", "run_capability"):
            definition = {"inputs": {}, "steps": [{"tool": tool, "arguments": {}}]}
            with self.subTest(tool=tool), self.assertRaises(ValueError):
                self.store.create("unsafe_flow", "Must not be saved", json.dumps(definition))

    def test_rejects_embedded_or_malformed_placeholders(self):
        definition = {"inputs": {"query": "Search phrase"}, "steps": [
            {"tool": "search_workspace", "arguments": {"query": "prefix ${input.query}"}},
        ]}
        with self.assertRaisesRegex(ValueError, "Invalid placeholder"):
            self.store.create("bad_placeholder", "Malformed template", json.dumps(definition))

    def test_rejects_write_before_later_steps(self):
        definition = {"inputs": {"path": "Relative file path", "text": "File text"}, "steps": [
            {"tool": "write_file", "arguments": {"path": "${input.path}", "text": "${input.text}"}},
            {"tool": "system_info", "arguments": {}},
        ]}
        with self.assertRaisesRegex(ValueError, "write step"):
            self.store.create("partial_write", "Unsafe partial workflow", json.dumps(definition))

    def test_rejects_symlinked_registry(self):
        external = Path(self.temp.name) / "external.json"
        external.write_text('{"version":1,"capabilities":{}}', encoding="utf-8")
        self.store.path.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.store.list()

    def test_rejects_forward_references_and_mismatched_inputs(self):
        definition = {"inputs": {}, "steps": [
            {"tool": "read_file", "arguments": {"path": "${steps.1.path}"}},
        ]}
        with self.assertRaises(ValueError):
            self.store.create("bad_flow", "Invalid reference", json.dumps(definition))
        valid = {"inputs": {"query": "Search query"}, "steps": [
            {"tool": "search_workspace", "arguments": {"query": "${input.query}"}},
        ]}
        self.store.create("search_notes", "Search notes", json.dumps(valid))
        with self.assertRaises(ValueError):
            self.store.execute("search_notes", "{}")


if __name__ == "__main__":
    unittest.main()
