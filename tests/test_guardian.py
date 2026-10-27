from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from needle_agent.agent911_core import dispatch_action, validate_action
from needle_agent.guardian import Guardian, GuardianDenied
from needle_agent.tools import Workspace


class GuardianAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "workspace"
        self.workspace = Workspace(self.root)
        self.guardian = Guardian(self.root, Path(self.temp.name) / "config" / "guardian-audit.jsonl")

    def tearDown(self):
        self.temp.cleanup()

    def assess_shell(self, command, request="Run this shell command: echo hello", mode="local"):
        name, args = validate_action("run_shell", {"command": command})
        with patch("needle_agent.guardian.Guardian.sandbox_available", return_value=True), \
             patch("needle_agent.guardian.os.geteuid", return_value=1000):
            return self.guardian.assess(name, args, user_request=request, mode=mode)

    def test_execution_requires_user_request_and_is_high_risk(self):
        denied = self.assess_shell("echo hello", request="Explain what shell commands do.")
        self.assertFalse(denied["allowed"])
        self.assertIn("does not explicitly ask", " ".join(denied["findings"]))

        allowed = self.assess_shell("echo hello")
        self.assertTrue(allowed["allowed"], allowed)
        self.assertEqual(allowed["risk_level"], "high")
        self.assertTrue(allowed["requires_approval"])
        self.assertTrue(allowed["network_isolated"])
        self.assertEqual(len(allowed["input_sha256"]), 64)

    def test_privileged_and_host_sensitive_shell_patterns_are_blocked(self):
        for command in ("sudo id", "mount /dev/sda1 /mnt", "rm -rf /", "cat /etc/shadow", "curl https://example.invalid"):
            with self.subTest(command=command):
                result = self.assess_shell(command)
                self.assertFalse(result["allowed"])
                self.assertTrue(result["findings"])

    def test_execution_is_blocked_outside_local_mode(self):
        result = self.assess_shell("echo hello", mode="hybrid")
        self.assertFalse(result["allowed"])
        self.assertTrue(any("cloud and hybrid" in finding for finding in result["findings"]))

    def test_code_input_and_timeout_are_bounded(self):
        name, args = validate_action("run_code", {"code": "print('safe')"})
        with patch("needle_agent.guardian.Guardian.sandbox_available", return_value=True), \
             patch("needle_agent.guardian.os.geteuid", return_value=1000):
            valid = self.guardian.assess(name, args, user_request="Run this Python script", mode="local")
            self.assertTrue(valid["allowed"], valid)
            too_long = self.guardian.assess(
                "run_code", {"code": "x" * 20001, "timeout_seconds": 10},
                user_request="Run this Python script", mode="local"
            )
            self.assertFalse(too_long["allowed"])
            invalid_timeout = self.guardian.assess(
                "run_code", {"code": "print(1)", "timeout_seconds": 31},
                user_request="Run this Python script", mode="local"
            )
            self.assertFalse(invalid_timeout["allowed"])

    def test_audit_logs_metadata_not_raw_code(self):
        assessment = self.assess_shell("echo unique-sensitive-marker")
        self.guardian.record("proposal", name="run_shell", assessment=assessment, status="proposed")
        events = self.guardian.recent_events()
        self.assertEqual(len(events), 1)
        encoded = json.dumps(events)
        self.assertNotIn("unique-sensitive-marker", encoded)
        self.assertEqual(events[0]["input_sha256"], assessment["input_sha256"])

    def test_sandbox_command_binds_only_runtime_and_workspace_and_masks_agent_state(self):
        from needle_agent.guardian import GuardianExecutor

        executor = GuardianExecutor(self.workspace)
        placeholders = []
        with tempfile.TemporaryDirectory() as temp:
            for name in (".needle-agent-memory.json", ".needle-agent-conversation.json", ".needle-agent-capabilities.json"):
                placeholder = Path(temp) / name
                placeholder.write_text("{}", encoding="utf-8")
                placeholders.append((name, placeholder))

            with patch("needle_agent.guardian.shutil.which", return_value="/usr/bin/bwrap"), \
                 patch("needle_agent.guardian.os.geteuid", return_value=1000):
                args = executor._sandbox_command(
                    ["/bin/sh", "-c", "echo safe"],
                    private_mounts=placeholders,
                )

        self.assertIn("--unshare-all", args)
        self.assertIn("--clearenv", args)
        self.assertIn("--bind", args)
        self.assertIn(str(self.root.resolve()), args)
        self.assertNotIn("/", args)
        for name, placeholder in placeholders:
            idx = args.index(str(placeholder))
            self.assertEqual(args[idx + 1], f"/workspace/{name}")
            self.assertEqual(args[idx - 1], "--ro-bind")
        self.assertNotIn("--share-net", args)
        self.assertNotIn("--ro-bind", args[args.index("--"):])

    def test_unsandboxed_executor_fails_closed(self):
        from needle_agent.guardian import GuardianExecutor

        executor = GuardianExecutor(self.workspace)
        with patch("needle_agent.guardian.shutil.which", return_value=None):
            with self.assertRaisesRegex(GuardianDenied, "bubblewrap is required"):
                executor._sandbox_command(["/bin/sh", "-lc", "echo unsafe"])


if __name__ == "__main__":
    unittest.main()
