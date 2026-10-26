#!/usr/bin/env python3
"""Create an isolated environment, install Needle Agent, and prepare a workspace."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def venv_python(venv: Path, *, windows: bool | None = None) -> Path:
    """Return the interpreter path inside a virtual environment."""
    is_windows = os.name == "nt" if windows is None else windows
    return venv / ("Scripts/python.exe" if is_windows else "bin/python")


def local_engine_name(system: str | None = None) -> str:
    """Pick the native library filename expected on the current operating system."""
    system = sys.platform if system is None else system
    if system == "win32":
        return "libneedle3.dll"
    if system == "darwin":
        return "libneedle3.dylib"
    return "libneedle3.so"


def resolve_assets(weights: str | None, engine: str | None) -> tuple[Path | None, Path | None]:
    """Use explicitly supplied assets or compatible local model and engine files, if present."""
    model = Path(weights).expanduser().resolve() if weights else ROOT / "google_drive/needle3.cact"
    library = Path(engine).expanduser().resolve() if engine else ROOT / "google_drive" / local_engine_name()
    if weights and not model.is_file():
        raise FileNotFoundError(f"Model archive not found: {model}")
    if engine and not library.is_file():
        raise FileNotFoundError(f"Needle engine library not found: {library}")
    return (model if model.is_file() else None, library if library.is_file() else None)


def install_command(python: Path) -> list[str]:
    """Install the editable project, preferring compatible wheels already staged locally."""
    return [str(python), "-m", "pip", "install", "--find-links", str(ROOT / "google_drive"), "-e", str(ROOT)]


def setup(*, venv: Path, workspace: Path, no_venv: bool = False,
          weights: str | None = None, engine: str | None = None,
          run: bool = False) -> int:
    """Set up the application and optionally launch it."""
    try:
        model, library = resolve_assets(weights, engine)
        workspace = workspace.expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        if no_venv:
            python = Path(sys.executable)
        else:
            venv = venv.expanduser().resolve()
            python = venv_python(venv)
            if not python.is_file():
                print(f"Creating virtual environment: {venv}")
                subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
            python = venv_python(venv)
        command = install_command(python)
        print("Installing Needle Agent and its Python dependencies...")
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1

    launch = [str(python), "-m", "needle_agent.cli", "--workspace", str(workspace)]
    if model:
        launch.extend(["--weights", str(model)])
    if library:
        launch.extend(["--engine", str(library)])
    print("\nSetup complete.")
    if model and library:
        print(f"Using local Needle model: {model}")
        print(f"Using local Needle engine: {library}")
    elif not model or not library:
        print("No complete local model/engine pair found; Needle may download its runtime assets on first launch.")
    print(f"Workspace ready: {workspace}")
    print("Launch command: " + " ".join(launch))
    print(f"Terminal UI: {python.parent / ('needle-agent-tui.exe' if os.name == 'nt' else 'needle-agent-tui')}")
    if run:
        return subprocess.run(launch, check=False).returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Install and prepare Needle Agent")
    parser.add_argument("--venv", type=Path, default=ROOT / ".venv", help="Virtual environment directory (default: .venv)")
    parser.add_argument("--workspace", type=Path, default=ROOT / "needle-workspace", help="Workspace directory to create")
    parser.add_argument("--no-venv", action="store_true", help="Install into the current Python environment instead")
    parser.add_argument("--weights", help="Use a specific local .cact model archive")
    parser.add_argument("--engine", help="Use a specific native Needle shared library")
    parser.add_argument("--run", action="store_true", help="Launch Needle Agent after installation")
    args = parser.parse_args()
    return setup(venv=args.venv, workspace=args.workspace, no_venv=args.no_venv,
                 weights=args.weights, engine=args.engine, run=args.run)


if __name__ == "__main__":
    raise SystemExit(main())
