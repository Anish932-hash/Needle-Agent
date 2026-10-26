import unittest
from unittest.mock import patch

from needle_agent.cloud import CloudAgent


class HybridCloudProposalTests(unittest.TestCase):
    def setUp(self):
        self.agent = CloudAgent("https://api.example.test/v1", "test-key", "test-model")

    def test_parses_advisory_tool_proposal_without_execution(self):
        response = (
            '{"assistant_message":"I propose reading a note.",'
            '"function_calls":[{"name":"read_file","arguments":{"path":"notes.txt"}}]}'
        )
        with patch.object(self.agent, "_chat", return_value=response):
            result = self.agent.propose_tools("Read notes.txt")

        self.assertEqual(result["assistant_message"], "I propose reading a note.")
        self.assertEqual(len(result["function_calls"]), 1)
        self.assertEqual(result["function_calls"][0]["name"], "read_file")
        self.assertEqual(result["function_calls"][0]["arguments"], {"path": "notes.txt"})
        self.assertEqual(result["function_calls"][0]["_source"], "cloud")

    def test_invalid_json_yields_no_actions(self):
        with patch.object(self.agent, "_chat", return_value="I will read the file now."):
            result = self.agent.propose_tools("Read notes.txt")

        self.assertEqual(result["function_calls"], [])
        self.assertIn("not valid JSON", result["assistant_message"])

    def test_limits_proposals_to_three(self):
        calls = [
            {"name": "system_info", "arguments": {}},
            {"name": "list_files", "arguments": {"path": "."}},
            {"name": "read_file", "arguments": {"path": "a.txt"}},
            {"name": "read_file", "arguments": {"path": "b.txt"}},
        ]
        with patch.object(self.agent, "_chat", return_value=__import__("json").dumps({
            "assistant_message": "Inspecting workspace.", "function_calls": calls
        })):
            result = self.agent.propose_tools("Inspect workspace")

        self.assertEqual(len(result["function_calls"]), 3)


    def test_proposals_receive_bounded_outcomes_and_round_number(self):
        response = json.dumps({"assistant_message": "Need more evidence.", "function_calls": []})
        previous = [{"tool": "read_file", "status": "succeeded",
                     "result": {"text": "x" * 10000}}]
        with patch.object(self.agent, "_chat", return_value=response) as chat:
            result = self.agent.propose_tools("Read note.txt", "advisory plan",
                                              previous_outcomes=previous, round_number=2)

        self.assertEqual(result["function_calls"], [])
        system, payload = chat.call_args.args
        parsed = json.loads(payload)
        self.assertEqual(parsed["user_request"], "Read note.txt")
        self.assertEqual(parsed["round_number"], 2)
        self.assertEqual(parsed["previous_local_outcomes"][0]["status"], "succeeded")
        self.assertLess(len(parsed["previous_local_outcomes"][0]["result"]), 2600)
        self.assertIn("Do not repeat an exact action", system)

if __name__ == "__main__":
    unittest.main()
