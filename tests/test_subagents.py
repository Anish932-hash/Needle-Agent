import unittest
from needle_agent.subagents import merge_proposals


class SubagentConsensusTests(unittest.TestCase):
    def test_only_majority_agreement_is_actionable(self):
        system = {"name": "system_info", "arguments": {}}
        read = {"name": "read_file", "arguments": {"path": "a.txt"}}
        primary = {"confidence": 0.8, "function_calls": [system], "suppressed_calls": []}
        reviewers = [
            {"confidence": 0.6, "function_calls": [system], "suppressed_calls": []},
            {"confidence": 0.9, "function_calls": [read], "suppressed_calls": []},
        ]
        result = merge_proposals(primary, reviewers, requested=2)
        self.assertEqual(result["function_calls"], [system])
        self.assertEqual(result["confidence"], 0.6)
        self.assertEqual(result["subagent_review"]["agreement_threshold"], 2)

    def test_missing_reviewers_fail_closed(self):
        primary = {"confidence": 0.99, "function_calls": [
            {"name": "system_info", "arguments": {}}]}
        result = merge_proposals(primary, [], requested=2, failures=["worker timeout"])
        self.assertEqual(result["function_calls"], [])
        self.assertEqual(result["subagent_review"]["agreement_threshold"], 2)

    def test_disagreement_abstains(self):
        primary = {"confidence": 0.9, "function_calls": [
            {"name": "system_info", "arguments": {}}]}
        reviewers = [{"confidence": 0.9, "function_calls": [
            {"name": "list_files", "arguments": {"path": "."}}]}]
        result = merge_proposals(primary, reviewers, requested=1)
        self.assertEqual(result["function_calls"], [])
        self.assertEqual(result["confidence"], 0.0)

    def test_unsupported_calls_never_win_consensus(self):
        bad = {"name": "run_shell", "arguments": {"command": "rm -rf /"}}
        primary = {"confidence": 0.99, "function_calls": [bad]}
        reviewers = [{"confidence": 0.99, "function_calls": [bad]}]
        result = merge_proposals(primary, reviewers, requested=1)
        self.assertEqual(result["function_calls"], [])


if __name__ == "__main__":
    unittest.main()
