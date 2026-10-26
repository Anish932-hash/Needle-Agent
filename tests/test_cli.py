import unittest
from needle_agent.cli import explicit_dates, explicit_system_capability, grounded_read_path, requested_reminder_path, select_proposals


class ProposalSelectionTests(unittest.TestCase):
    def test_creation_request_keeps_creation_only_from_active_and_held(self):
        create = {"name": "save_workflow", "arguments": {}}
        read = {"name": "read_file", "arguments": {}}
        selected, intent = select_proposals("Create a reusable capability to find notes", [], [create, read])
        self.assertTrue(intent)
        self.assertEqual(selected, [create])

    def test_natural_capability_creation_request_suppresses_premature_run(self):
        create = {"name": "save_workflow", "arguments": {}}
        run = {"name": "run_capability", "arguments": {"name": "environment_audit"}}
        prompt = ("I'd like a reusable capability called environment_audit. Please create and test it, "
                  "but don't run it yet.")
        selected, intent = select_proposals(prompt, [run, create], [])
        self.assertTrue(intent)
        self.assertEqual(selected, [create])

    def test_explicit_date_extraction_is_literal_and_ambiguous_dates_are_preserved(self):
        self.assertEqual(explicit_dates("Launch: September 22, 2026."), ["September 22, 2026"])
        self.assertEqual(explicit_dates("Meeting 2026-09-22"), ["2026-09-22"])
        self.assertEqual(explicit_dates("Launch date not specified."), [])

    def test_explicit_os_capability_is_recognized_from_natural_request(self):
        prompt = ("I'd like a reusable capability called environment_audit to report my OS, Python, "
                  "architecture, and workspace. Please create it.")
        self.assertEqual(explicit_system_capability(prompt), (
            "environment_audit",
            "Report operating system, OS release, CPU architecture, Python version, and workspace root."))
        self.assertIsNone(explicit_system_capability("Tell me my Python version"))

    def test_reminder_path_requires_explicit_write_and_destination(self):
        self.assertEqual(requested_reminder_path("Save a short reminder in reminder.txt"), "reminder.txt")
        self.assertIsNone(requested_reminder_path("Find the reminder note"))
        self.assertIsNone(requested_reminder_path("Save a reminder for me"))

    def test_read_path_is_derived_from_unique_search_result(self):
        result = {"matches": [{"path": "notes/aurora.txt"}, {"path": "notes/aurora.txt"}]}
        self.assertEqual(grounded_read_path("Read the note", result), ("notes/aurora.txt", None))

    def test_ambiguous_search_results_do_not_guess(self):
        result = {"matches": [{"path": "a.txt"}, {"path": "b.txt"}]}
        path, notice = grounded_read_path("Open the file", result)
        self.assertIsNone(path)
        self.assertIn("multiple", notice)

    def test_question_intent_reads_unique_grounded_search_result(self):
        result = {"matches": [{"path": "notes/aurora.txt"}]}
        self.assertEqual(grounded_read_path("What date was it?", result),
                         ("notes/aurora.txt", None))

    def test_no_read_intent_does_not_chain(self):
        self.assertEqual(grounded_read_path("Find a matching note", {"matches": []}), (None, None))

    def test_old_creation_memory_cannot_trigger_create_on_normal_request(self):
        create = {"name": "save_workflow", "arguments": {}}
        search = {"name": "search_workspace", "arguments": {}}
        selected, intent = select_proposals("Find Aurora using find_and_read", [create], [search])
        self.assertFalse(intent)
        self.assertEqual(selected, [search])


if __name__ == "__main__":
    unittest.main()
