from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from needle_agent.agent911_config import ModelConfiguration, UpgradePromptStore, agent911_config_dir


class ModelConfigurationTests(unittest.TestCase):
    def test_default_configuration_is_safe_and_local_first(self):
        with tempfile.TemporaryDirectory() as temp:
            config = ModelConfiguration(Path(temp) / "models.json").load()
            self.assertEqual(config["version"], 1)
            self.assertEqual(config["default_mode"], "local")
            self.assertEqual(config["local"], {"weights_path": "", "engine_path": ""})

    def test_saves_existing_model_assets_and_default_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            weights = root / "model.cact"
            engine = root / "libneedle3.so"
            weights.write_bytes(b"test model asset")
            engine.write_bytes(b"test engine asset")
            store = ModelConfiguration(root / "settings" / "models.json")

            saved = store.save({
                "default_mode": "hybrid",
                "local": {"weights_path": str(weights), "engine_path": str(engine)},
            })

            self.assertEqual(saved["default_mode"], "hybrid")
            self.assertEqual(saved["local"]["weights_path"], str(weights.resolve()))
            self.assertEqual(store.load(), saved)
            self.assertTrue(store.path.is_file())
            if os_name_is_posix():
                self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)

    def test_rejects_missing_files_and_engine_without_weights(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ModelConfiguration(Path(temp) / "models.json")
            with self.assertRaisesRegex(ValueError, "not found"):
                store.save({"default_mode": "local", "local": {"weights_path": str(Path(temp) / "missing.cact")}})
            engine = Path(temp) / "libneedle3.so"
            engine.write_bytes(b"not loaded by this test")
            with self.assertRaisesRegex(ValueError, "weights file"):
                store.save({"default_mode": "local", "local": {"engine_path": str(engine)}})

    def test_rejects_unknown_default_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ModelConfiguration(Path(temp) / "models.json")
            with self.assertRaisesRegex(ValueError, "Default mode"):
                store.save({"default_mode": "dangerous", "local": {}})


def os_name_is_posix():
    import os
    return os.name != "nt"


class UpgradePromptStoreTests(unittest.TestCase):
    def test_adds_persists_and_archives_upgrade_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UpgradePromptStore(Path(temp) / "upgrades.json")
            item = store.add("Add a deterministic health report", "Plan: add checks and acceptance tests.")
            self.assertEqual(item["status"], "planned")
            self.assertEqual(store.list()[0]["id"], item["id"])

            archived = store.archive(item["id"])

            self.assertEqual(archived["status"], "archived")
            self.assertEqual(store.list()[0]["status"], "archived")
            raw = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(raw["items"][0]["status"], "archived")

    def test_rejects_empty_or_excessive_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UpgradePromptStore(Path(temp) / "upgrades.json")
            with self.assertRaisesRegex(ValueError, "Describe the upgrade"):
                store.add("  ")
            with self.assertRaisesRegex(ValueError, "exceeds"):
                store.add("x" * (store.MAX_REQUEST_CHARS + 1))

    def test_unknown_upgrade_id_cannot_be_archived(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UpgradePromptStore(Path(temp) / "upgrades.json")
            with self.assertRaisesRegex(ValueError, "not found"):
                store.archive("unknown")


class ConfigDirectoryTests(unittest.TestCase):
    def test_linux_respects_absolute_xdg_config_home(self):
        from pathlib import Path
        path = agent911_config_dir("linux", {"XDG_CONFIG_HOME": "/tmp/xdg"}, Path("/home/test"))
        self.assertEqual(path, Path("/tmp/xdg/agent-911"))

    def test_invalid_xdg_config_home_falls_back_to_home(self):
        from pathlib import Path
        path = agent911_config_dir("linux", {"XDG_CONFIG_HOME": "relative"}, Path("/home/test"))
        self.assertEqual(path, Path("/home/test/.config/agent-911"))

    def test_windows_and_macos_paths(self):
        from pathlib import Path
        self.assertEqual(
            agent911_config_dir("win32", {"APPDATA": "C:/Users/test/AppData/Roaming"}, Path("C:/Users/test")),
            Path("C:/Users/test/AppData/Roaming/Agent911"),
        )
        self.assertEqual(
            agent911_config_dir("darwin", {}, Path("/Users/test")),
            Path("/Users/test/Library/Application Support/Agent911"),
        )


if __name__ == "__main__":
    unittest.main()
