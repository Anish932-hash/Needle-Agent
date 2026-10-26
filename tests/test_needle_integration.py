import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
try:
    import needle
except ImportError:
    needle = None
from needle_agent.agent import build_agent, SAFETY_SYSTEM
from needle_agent.tools import Workspace


class NeedleIntegrationTests(unittest.TestCase):
    @unittest.skipIf(needle is None, "install cactus-needle to test its tool schema API")
    def test_declared_tool_schema_and_stateless_safety_configuration(self):
        captured = {}

        class FakeNeedle:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with tempfile.TemporaryDirectory() as temp, patch.object(needle, "Needle", FakeNeedle):
            weights = Path(temp) / "model.cact"
            build_agent(Workspace(Path(temp)), weights=weights)
        self.assertTrue(captured["stateless"])
        self.assertEqual(captured["system"], SAFETY_SYSTEM)
        self.assertEqual(captured["weights"], str(weights))
        schemas = [tool._needle_tool for tool in captured["tools"]]
        by_name = {schema["name"]: schema for schema in schemas}
        self.assertEqual(set(by_name), {"system_info", "list_files", "search_workspace", "read_file", "write_file", "save_workflow", "run_capability"})
        self.assertEqual(by_name["write_file"]["parameters"]["required"], ["path", "text"])
        self.assertEqual(by_name["save_workflow"]["parameters"]["properties"]["recipe"]["enum"],
                         ["system_check", "list_workspace", "find_files", "find_and_read", "read_text_file", "write_text_file"])
        self.assertIn("workspace", by_name["read_file"]["description"].lower())


if __name__ == "__main__":
    unittest.main()
