import tempfile
import unittest
from pathlib import Path
from needle_agent.tools import Workspace
from needle_agent.agent import dispatch, validate_call
from needle_agent.evolution import EvolutionMemory
from needle_agent.sandbox import verify_tools, practice_request


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(Path(self.tmp.name) / "space")

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_read_and_list(self):
        dispatch(self.ws, "write_file", {"path": "notes/a.txt", "text": "hello"})
        self.assertEqual(self.ws.read_file("notes/a.txt")["text"], "hello")
        self.assertEqual(self.ws.list_files("notes")["entries"][0]["name"], "a.txt")

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            self.ws.resolve("../outside.txt")

    def test_internal_store_paths_are_reserved_from_workspace_tools(self):
        reserved = (
            ".needle-agent-memory.json",
            ".needle-agent-conversation.json",
            ".needle-agent-capabilities.json",
        )
        for path in reserved:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "reserved"):
                self.ws.write_file(path, "poison")
            with self.subTest(path=path + " read"), self.assertRaisesRegex(ValueError, "reserved"):
                self.ws.read_file(path)
            with self.subTest(path=path + " normalized"), self.assertRaisesRegex(ValueError, "reserved"):
                self.ws.resolve("nested/../" + path)

    def test_workspace_path_length_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "Workspace path"):
            self.ws.resolve("x" * 4097)

    def test_write_to_inside_workspace_symlink_is_confined_to_resolved_target(self):
        target = self.ws.root / "target.txt"
        target.write_text("old", encoding="utf-8")
        link = self.ws.root / "alias.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.ws.write_file("alias.txt", "replacement")
        self.assertEqual(target.read_text(encoding="utf-8"), "replacement")

    def test_unknown_tool_rejected(self):
        with self.assertRaises(ValueError):
            dispatch(self.ws, "run_shell", {})

    def test_search_finds_names_and_text_without_exposing_memory(self):
        self.ws.write_file("docs/plan.txt", "Project Aurora opens in May\nsecond line")
        # Simulate the internal store, which is written by its own persistence layer.
        (self.ws.root / ".needle-agent-memory.json").write_text("private Aurora token", encoding="utf-8")
        found = dispatch(self.ws, "search_workspace", {"query": "aurora"})
        self.assertEqual(len(found["matches"]), 1)
        self.assertEqual(found["matches"][0]["path"], "docs/plan.txt")
        self.assertEqual(found["matches"][0]["line"], 1)

    def test_search_skips_symlinks(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("secret needle", encoding="utf-8")
        link = self.ws.root / "linked.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        result = self.ws.search_workspace("secret")
        self.assertEqual(result["matches"], [])

    def test_search_matches_semantic_queries_with_date_intent(self):
        self.ws.write_file("notes/launch.txt", "Aurora kickoff is scheduled for September 22.")
        result = self.ws.search_workspace("kickoff date")
        self.assertEqual([item["path"] for item in result["matches"]], ["notes/launch.txt"])

    def test_search_matches_natural_language_terms_without_generic_words(self):
        self.ws.write_file("notes/launch.txt", "Aurora kickoff date: September 22.")
        result = self.ws.search_workspace("my Aurora kickoff note")
        paths = {match["path"] for match in result["matches"]}
        self.assertEqual(paths, {"notes/launch.txt"})

    def test_search_rejects_empty_or_long_query(self):
        for query in ("", "x" * 201):
            with self.subTest(query_len=len(query)), self.assertRaises(ValueError):
                self.ws.search_workspace(query)

    def test_current_workspace_alias_is_canonicalized(self):
        name, args = validate_call("list_files", {"path": "current workspace"})
        self.assertEqual(name, "list_files")
        self.assertEqual(args["path"], ".")

    def test_malformed_and_extra_arguments_rejected(self):
        for name, args in [("read_file", {}), ("list_files", {"path": ".", "recursive": True}),
                           ("write_file", {"path": "x", "text": 1})]:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                validate_call(name, args)

    def test_size_limit(self):
        with self.assertRaises(ValueError):
            self.ws.write_file("large.txt", "x" * 65537)


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = EvolutionMemory(Path(self.tmp.name) / "memory.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_learn_persists_and_provides_context(self):
        self.memory.learn("  Keep answers concise.  ")
        self.assertIn("Keep answers concise.", self.memory.prompt_context())
        self.assertEqual(len(self.memory.lessons()), 1)

    def test_bounds_and_forget(self):
        with self.assertRaises(ValueError):
            self.memory.learn("x" * 501)
        self.memory.learn("Do the safe thing")
        self.memory.forget()
        self.assertEqual(self.memory.lessons(), [])


class SandboxTests(unittest.TestCase):
    def test_verification_suite_passes(self):
        report = verify_tools()
        self.assertTrue(report["passed"], report)
        self.assertEqual(len(report["checks"]), 5)

    def test_low_confidence_practice_requires_review(self):
        class FakeAgent:
            def complete(self, prompt):
                return {"confidence": 0.3, "function_calls": [
                    {"name": "system_info", "arguments": {}}]}
        report = practice_request(FakeAgent(), "show info")
        self.assertTrue(report["execution_passed"])
        self.assertTrue(report["review_required"])
        self.assertFalse(report["passed"])

    def test_practice_grounds_read_after_natural_language_search(self):
        class FakeAgent:
            def complete(self, prompt):
                return {"confidence": 0.99, "function_calls": [
                    {"name": "search_workspace", "arguments": {"query": "Aurora kickoff note"}},
                    {"name": "read_file", "arguments": {"path": "Aurora kickoff note"}},
                ]}
        report = practice_request(FakeAgent(), "Could you find my Aurora kickoff note and read it?",
                                 fixtures={"notes/launch.txt": "Aurora kickoff date: September 22."})
        self.assertTrue(report["execution_passed"], report)
        self.assertEqual(report["calls"][-1]["name"], "read_file")
        self.assertEqual(report["calls"][-1]["result"]["path"], "notes/launch.txt")

    def test_practice_executes_only_supported_calls_in_sandbox(self):
        class FakeAgent:
            def complete(self, prompt):
                return {"confidence": 0.99, "function_calls": [
                    {"name": "write_file", "arguments": {"path": "demo.txt", "text": "ok"}},
                    {"name": "run_shell", "arguments": {"command": "whoami"}},
                ]}
        report = practice_request(FakeAgent(), "practice")
        self.assertFalse(report["passed"])
        self.assertTrue(report["calls"][0]["passed"])
        self.assertFalse(report["calls"][1]["passed"])


if __name__ == "__main__":
    unittest.main()
