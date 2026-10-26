import unittest

from needle_agent.request_guard import ground_explicit_write


class RequestGuardTests(unittest.TestCase):
    def test_write_literal_content_and_case_sensitive_filename_are_grounded(self):
        args, changed = ground_explicit_write(
            "Write Hello in the Text.txt",
            "write_file",
            {"path": "text.txt", "text": "Test"},
        )
        self.assertTrue(changed)
        self.assertEqual(args, {"path": "Text.txt", "text": "Hello"})

    def test_explicit_txt_file_name_gets_extension(self):
        args, changed = ground_explicit_write(
            "Create a txt file named Test",
            "write_file",
            {"path": "Test", "text": "Test"},
        )
        self.assertTrue(changed)
        self.assertEqual(args["path"], "Test.txt")
        self.assertEqual(args["text"], "Test")

    def test_ambiguous_request_is_not_guessed(self):
        original = {"path": "somewhere.txt", "text": "guess"}
        args, changed = ground_explicit_write(
            "Make a note about my plans",
            "write_file",
            original,
        )
        self.assertFalse(changed)
        self.assertEqual(args, original)

    def test_non_write_tool_is_untouched(self):
        original = {"path": "text.txt"}
        args, changed = ground_explicit_write(
            "Write Hello in the Text.txt",
            "read_file",
            original,
        )
        self.assertFalse(changed)
        self.assertEqual(args, original)


if __name__ == "__main__":
    unittest.main()
