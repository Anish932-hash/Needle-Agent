#!/usr/bin/env python3
"""Install Needle Agent and launch either its TUI or the Agent 911 web console."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
MIN_NODE_MAJOR = 20


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


def find_node(executable: str | None = None) -> str | None:
    """Find Node.js, honoring an explicit executable or the NODE environment variable."""
    return shutil.which(executable or os.environ.get("NODE", "node"))


def check_node_version(node: str) -> tuple[int, int, int]:
    """Require a working Node.js >=20 runtime and return its parsed version tuple."""
    result = subprocess.run(
        [node, "--version"], capture_output=True, text=True, check=False,
    )
    version_text = (result.stdout or result.stderr).strip()
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version_text)
    if result.returncode != 0 or not match:
        raise RuntimeError(f"Could not determine Node.js version from {node!r}: {version_text or 'no version output'}")
    version = tuple(int(part) for part in match.groups())
    if version[0] < MIN_NODE_MAJOR:
        raise RuntimeError(
            f"Agent 911's web console requires Node.js {MIN_NODE_MAJOR}+; found {version_text}. "
            "Install a supported Node.js release, or use --interface tui."
        )
    return version


def web_launch_command(node: str, workspace: Path, weights: Path | None = None,
                       engine: Path | None = None, port: int | None = None,
                       config_dir: Path | None = None) -> list[str]:
    """Build the Agent 911 web-console launch command with absolute local paths."""
    command = [
        node, str(ROOT / "web-ui/server.mjs"),
        "--workspace", str(workspace.expanduser().resolve()),
    ]
    if weights:
        command.extend(["--weights", str(weights.expanduser().resolve())])
    if engine:
        command.extend(["--engine", str(engine.expanduser().resolve())])
    if port is not None:
        if isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValueError("Web-console port must be an integer from 0 to 65535.")
        command.extend(["--port", str(port)])
    if config_dir:
        command.extend(["--config-dir", str(config_dir.expanduser().resolve())])
    return command


def setup(*, venv: Path, workspace: Path, no_venv: bool = False,
          weights: str | None = None, engine: str | None = None,
          run: bool = False, interface: str = "tui", port: int = 8765,
          config_dir: Path | None = None, node_executable: str | None = None) -> int:
    """Set up the application and optionally launch the selected interface."""
    if interface not in {"tui", "web"}:
        print("Interface must be 'tui' or 'web'.", file=sys.stderr)
        return 2

    node = None
    if interface == "web":
        if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
            print(
                "Refusing to install/launch the Agent 911 web console as root. "
                "Use a normal user account; Guardian execution is intentionally disabled as root.",
                file=sys.stderr,
            )
            return 2
        node = find_node(node_executable)
        if not node:
            print(
                f"Node.js {MIN_NODE_MAJOR}+ is required for the web console. "
                "Install Node.js or use --interface tui.",
                file=sys.stderr,
            )
            return 2
        try:
            version = check_node_version(node)
        except RuntimeError as exc:
            print(f"Web-console requirement not met: {exc}", file=sys.stderr)
            return 2
        print(f"Node.js detected: {version[0]}.{version[1]}.{version[2]}")

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
    except (OSError, subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1

    if interface == "web":
        assert node is not None
        launch = web_launch_command(
            node, workspace, model, library, port=port, config_dir=config_dir,
        )
        launch_env = os.environ.copy()
        # The Node server starts this exact Python interpreter for its JSON-lines bridge.
        launch_env["NEEDLE_PYTHON"] = str(python.resolve())
    else:
        launch = [str(python), "-m", "needle_agent.cli", "--workspace", str(workspace)]
        if model:
            launch.extend(["--weights", str(model)])
        if library:
            launch.extend(["--engine", str(library)])
        launch_env = os.environ.copy()

    print("\nSetup complete.")
    if model and library:
        print(f"Using local Needle model: {model}")
        print(f"Using local Needle engine: {library}")
    else:
        print("No complete local model/engine pair found; Needle may download/cache runtime assets on first launch.")
    print(f"Workspace ready: {workspace}")
    print("Launch command: " + " ".join(launch))
    if interface == "web":
        print(f"Web console: http://127.0.0.1:{port if port != 0 else '<assigned-port>'}")
        if not shutil.which("bwrap"):
            print("Guardian notice: bubblewrap (bwrap) not detected. Shell/Python execution will fail closed.")
        if os.name == "posix" and not sys.platform.startswith("linux"):
            print("Guardian notice: sandboxed shell/Python execution is Linux-only and will remain disabled.")
        print("Keep the terminal open while using Agent 911. Stop the server with Ctrl+C.")
    else:
        print(f"Terminal UI: {python.parent / ('needle-agent-tui.exe' if os.name == 'nt' else 'needle-agent-tui')}")
    if run:
        return subprocess.run(launch, cwd=ROOT, env=launch_env, check=False).returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Needle Agent or prepare the Agent 911 web console")
    parser.add_argument("--venv", type=Path, default=ROOT / ".venv", help="Virtual environment directory (default: .venv)")
    parser.add_argument("--workspace", type=Path, default=ROOT / "needle-workspace", help="Workspace directory to create")
    parser.add_argument("--no-venv", action="store_true", help="Install into the current Python environment instead")
    parser.add_argument("--weights", help="Use a specific local .cact model archive")
    parser.add_argument("--engine", help="Use a specific native Needle shared library")
    parser.add_argument("--interface", choices=("tui", "web"), default="tui", help="Interface to prepare: terminal UI (default) or Agent 911 web console")
    parser.add_argument("--web", dest="interface", action="store_const", const="web", help="Shortcut for --interface web")
    parser.add_argument("--port", type=int, default=8765, help="Agent 911 web-console port (0 selects an available port)")
    parser.add_argument("--config-dir", type=Path, help="Agent 911 user configuration directory override")
    parser.add_argument("--node", help="Node.js executable to use for the web console (or set NODE)")
    parser.add_argument("--run", action="store_true", help="Launch the selected interface after installation")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be an integer from 0 to 65535")
    return setup(
        venv=args.venv, workspace=args.workspace, no_venv=args.no_venv,
        weights=args.weights, engine=args.engine, run=args.run,
        interface=args.interface, port=args.port, config_dir=args.config_dir,
        node_executable=args.node,
    )


if __name__ == "__main__":
    raise SystemExit(main())
