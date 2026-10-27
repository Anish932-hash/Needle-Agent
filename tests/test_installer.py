import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch\n\nfrom install import (\n    check_node_version, install_command, local_engine_name, resolve_assets,\n    venv_python, web_launch_command,\n)


class InstallerTests(unittest.TestCase):
    def test_virtualenv_interpreter_paths_cover_platforms(self):
        self.assertEqual(venv_python(Path("env"), windows=False), Path("env/bin/python"))
        self.assertEqual(venv_python(Path("env"), windows=True), Path("env/Scripts/python.exe"))

    def test_native_engine_names_cover_major_platforms(self):
        self.assertEqual(local_engine_name("win32"), "libneedle3.dll")
        self.assertEqual(local_engine_name("darwin"), "libneedle3.dylib")
        self.assertEqual(local_engine_name("linux"), "libneedle3.so")

    def test_installer_prefers_staged_wheel_directory(self):
        command = install_command(Path("env/bin/python"))
        self.assertEqual(command[:4], ["env/bin/python", "-m", "pip", "install"])
        self.assertIn("--find-links", command)
        self.assertEqual(command[-2:], ["-e", str(Path(__file__).resolve().parents[1])])

    def test_web_launch_command_selects_agent911_console_and_forwards_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "work"
            weights = root / "model.cact"
            engine = root / "libneedle3.so"
            config = root / "config"
            command = web_launch_command(
                "/usr/bin/node", workspace, weights, engine, port=8766, config_dir=config,
            )
            self.assertEqual(command[0], "/usr/bin/node")
            self.assertIn(str(Path("web-ui/server.mjs").resolve()), command[1])
            self.assertIn("--workspace", command)
            self.assertIn(str(workspace.resolve()), command)
            self.assertIn("--weights", command)
            self.assertIn(str(weights.resolve()), command)
            self.assertIn("--engine", command)
            self.assertIn(str(engine.resolve()), command)
            self.assertIn("--port", command)
            self.assertIn("8766", command)
            self.assertIn("--config-dir", command)
            self.assertIn(str(config.resolve()), command)

    def test_web_launch_command_checks_port_range(self):
        with self.assertRaisesRegex(ValueError, "port"):
            web_launch_command("node", Path("workspace"), port=70000)

    def test_node_version_requires_supported_release(self):
        with patch("install.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "v22.10.1\\n"
            run.return_value.stderr = ""
            self.assertEqual(check_node_version("node"), (22, 10, 1))

            run.return_value.stdout = "v18.19.0"
            with self.assertRaisesRegex(RuntimeError, "requires Node.js 20"):
                check_node_version("node")

    def test_node_version_rejects_unparseable_output(self):
        with patch("install.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "not-node"
            run.return_value.stderr = ""
            with self.assertRaisesRegex(RuntimeError, "Could not determine Node.js version"):
                check_node_version("node")

    def test_explicit_missing_model_assets_fail_with_clear_path(self):
        with tempfile.TemporaryDirectory() as temp:
            absent = Path(temp) / "missing.cact"
            with self.assertRaisesRegex(FileNotFoundError, "missing.cact"):
                resolve_assets(str(absent), None)

    def test_automatic_asset_detection_returns_existing_pair_when_available(self):
        model, library = resolve_assets(None, None)
        # The assets are optional for source installs and some non-Linux platforms.
        if model is not None:
            self.assertTrue(model.is_file())
        if library is not None:
            self.assertTrue(library.is_file())


if __name__ == "__main__":
    unittest.main()
