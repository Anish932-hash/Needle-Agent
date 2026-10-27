import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from needle_agent.cloud import (CloudAgent, CloudAPIError, CloudSettings,
                                detect_models, normalize_base_url, user_config_dir)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, value, headers=None):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            if self.headers.get("Authorization") != "Bearer secret-test-key":
                return self._send(401, {"error": "invalid key"})
            return self._send(200, {"data": [{"id": "zeta"}, {"id": "alpha"}, {"id": "alpha"}]})
        if self.path == "/redirect/models":
            return self._send(302, {}, {"Location": "https://example.invalid/steal"})
        return self._send(404, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.server.last_payload = json.loads(self.rfile.read(length))
        return self._send(200, {"choices": [{"message": {
            "content": "I can read that file.", "tool_calls": [{"function": {
                "name": "read_file", "arguments": '{"path":"notes/a.txt"}'}}]}}]})


class CloudAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=2)
        cls.server.server_close()

    def test_url_validation_requires_https_for_remote_services(self):
        self.assertEqual(normalize_base_url("https://api.example.com/v1/"), "https://api.example.com/v1")
        self.assertEqual(normalize_base_url("http://localhost:9000/v1"), "http://localhost:9000/v1")
        self.assertEqual(normalize_base_url("http://127.0.0.1:9000/v1"), "http://127.0.0.1:9000/v1")
        self.assertEqual(normalize_base_url("http://[::1]:9000/v1"), "http://[::1]:9000/v1")
        for url in ("api.example.com", "http://api.example.com/v1", "https://user:pw@example.com/v1",
                    "https://example.com/v1?key=x", "http://0.0.0.0:9000/v1",
                    "http://169.254.169.254/latest/meta-data", "http://127.0.0.2:9000/v1",
                    "http://service.localhost:9000/v1", "http://localhost.evil.example/v1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_base_url(url)

    def test_model_discovery_authenticates_and_deduplicates_models(self):
        self.assertEqual(detect_models(self.base, "secret-test-key"), ["alpha", "zeta"])
        with self.assertRaisesRegex(CloudAPIError, "authentication"):
            detect_models(self.base, "wrong")

    def test_redirects_are_rejected_to_protect_credentials(self):
        with self.assertRaisesRegex(CloudAPIError, "HTTP 302"):
            detect_models(self.base.replace("/v1", "/redirect"), "secret-test-key")

    def test_cloud_plans_and_reviews_without_tool_schemas(self):
        agent = CloudAgent(self.base, "secret-test-key", "alpha")
        plan = agent.plan("Read my note and tell me what it says")
        self.assertEqual(plan, "I can read that file.")
        self.assertEqual(self.server.last_payload["model"], "alpha")
        self.assertNotIn("tools", self.server.last_payload)
        review = agent.review("Read my note", plan, [{"tool": "read_file", "status": "succeeded",
                                                        "result": {"text": "hello"}}])
        self.assertEqual(review, "I can read that file.")
        review_input = json.loads(self.server.last_payload["messages"][1]["content"])
        self.assertEqual(review_input["local_actions"][0]["result"]["text"], "hello")
        self.assertNotIn("tools", self.server.last_payload)

    def test_upgrade_plan_is_text_only_and_never_registers_tools(self):
        agent = CloudAgent(self.base, "secret-test-key", "alpha")
        request = "Add versioned model config and acceptance tests."

        plan = agent.draft_upgrade_plan(request)

        self.assertEqual(plan, "I can read that file.")
        payload = self.server.last_payload
        self.assertEqual(payload["model"], "alpha")
        self.assertNotIn("tools", payload)
        self.assertIn("Do not call tools", payload["messages"][0]["content"])
        self.assertEqual(
            json.loads(payload["messages"][1]["content"])["owner_upgrade_request"], request
        )

    def test_user_config_directory_respects_platform_conventions_and_xdg(self):
        self.assertEqual(user_config_dir("linux", {"XDG_CONFIG_HOME": "/tmp/custom-config"}, Path("/home/test")),
                         Path("/tmp/custom-config/needle-agent"))
        self.assertEqual(user_config_dir("linux", {}, Path("/home/test")),
                         Path("/home/test/.config/needle-agent"))
        self.assertEqual(str(user_config_dir("win32", {"APPDATA": "D:/Profiles/test/AppData/Roaming"})),
                         "D:/Profiles/test/AppData/Roaming/needle-agent")
        self.assertEqual(user_config_dir("darwin", {}, Path("/Users/test")),
                         Path("/Users/test/Library/Application Support/needle-agent"))

    def test_settings_do_not_persist_key_without_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config" / "cloud.json"
            settings = CloudSettings(path)
            settings.save(self.base, "alpha", "secret-test-key")
            self.assertNotIn("api_key", json.loads(path.read_text()))
            settings.save(self.base, "alpha", "secret-test-key", remember_key=True)
            self.assertEqual(json.loads(path.read_text())["api_key"], "secret-test-key")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
