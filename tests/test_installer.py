import tempfile
import unittest
from pathlib import Path
from install import install_command, local_engine_name, resolve_assets, venv_python


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
