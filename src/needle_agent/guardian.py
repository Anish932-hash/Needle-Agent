"""Guardian policy, local audit events, and fail-closed Linux execution sandbox.

Execution is only reachable after the web bridge creates an explicit approval card and
receives approval. Child processes run in bubblewrap with a private PID/network namespace,
a read-only minimal runtime, and only the selected workspace mounted read/write.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_SHELL_CHARS = 8_000
MAX_CODE_CHARS = 20_000
MAX_TIMEOUT_SECONDS = 30
MAX_OUTPUT_BYTES = 32 * 1024
_MAX_AUDIT_BYTES = 512 * 1024
_PRIVATE_STORES = (
    ".needle-agent-memory.json",
    ".needle-agent-conversation.json",
    ".needle-agent-capabilities.json",
)

_EXPLICIT_EXECUTION = re.compile(
    r"\b(run|execute|launch|start|compile|build|test|install)\b",
    re.IGNORECASE,
)
_BLOCKED_SHELL = (
    (re.compile(r"\b(sudo|doas|pkexec|su)\b", re.I), "Privilege escalation is blocked."),
    (re.compile(r"\b(mount|umount|chroot|nsenter|unshare|setcap|capsh|modprobe|insmod|rmmod)\b", re.I),
     "Host/container boundary or kernel-management commands are blocked."),
    (re.compile(r"\b(systemctl|service|reboot|poweroff|shutdown|halt)\b", re.I),
     "Host service and power-control commands are blocked."),
    (re.compile(r"\bmkfs(?:\.[a-z0-9_-]+)?\b|\bdd\b[^;\n]*\bof=/dev/|\brm\s+-[^\n;]*r[^\n;]*f[^\n;]*\s+/(?:\s|$)", re.I),
     "Destructive host-storage patterns are blocked."),
    (re.compile(r"/(?:etc/shadow|root/|home/[^\s;|&]+|proc/sys/|sys/|dev/(?:mem|kmem|sd[a-z]|nvme\w*|mapper))", re.I),
     "Direct references to host-sensitive paths are blocked."),
    (re.compile(r"(?:curl|wget|nc|ncat|netcat|socat|ssh|scp|ftp|telnet)\b", re.I),
     "Network clients are blocked; execution has no network namespace."),
    (re.compile(r"\.needle-agent-(?:memory|conversation|capabilities)\.json", re.I),
     "Shell commands may not access Agent 911's reserved internal stores."),
)
_BLOCKED_CODE = (
    (re.compile(r"/(?:etc/shadow|root/|home/[^\s'\"]+|proc/sys/|sys/|dev/(?:mem|kmem|sd[a-z]|nvme\w*|mapper))", re.I),
     "Code references host-sensitive paths."),
    (re.compile(r"\.needle-agent-(?:memory|conversation|capabilities)\.json", re.I),
     "Code may not access Agent 911's reserved internal stores."),
    (re.compile(r"\b(os\.set(?:euid|uid|egid|gid)|os\.chroot|os\.setns|ctypes\.CDLL)\b", re.I),
     "Code includes a host-boundary or privilege manipulation primitive."),
)


class GuardianDenied(ValueError):
    """The Guardian rejected an action before execution."""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


class Guardian:
    """Local policy engine. Assessments are advisory until rechecked at dispatch."""

    def __init__(self, workspace: str | Path, audit_path: str | Path | None = None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.audit_path = Path(audit_path).expanduser().resolve() if audit_path else None

    @staticmethod
    def sandbox_available() -> bool:
        return os.name == "posix" and __import__("sys").platform.startswith("linux") and bool(shutil.which("bwrap"))

    def assess(self, name: str, arguments: dict[str, Any], *,
               user_request: str = "", mode: str = "local") -> dict[str, Any]:
        findings: list[str] = []
        blocked = False
        risk = "low"
        source = ""
        if name in {"run_shell", "run_code"}:
            risk = "high"
            source = arguments.get("command", "") if name == "run_shell" else arguments.get("code", "")
            if mode != "local":
                blocked = True
                findings.append("Shell/code execution is disabled in cloud and hybrid modes so results cannot be shared with a provider.")
            if not isinstance(user_request, str) or not _EXPLICIT_EXECUTION.search(user_request):
                blocked = True
                findings.append("The current user request does not explicitly ask to run or execute code.")
            if not self.sandbox_available():
                blocked = True
                findings.append("Fail-closed: Linux bubblewrap (bwrap) is required; no unsandboxed fallback is permitted.")
            if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
                blocked = True
                findings.append("Execution is disabled when Agent 911 runs as root.")
            patterns = _BLOCKED_SHELL if name == "run_shell" else _BLOCKED_CODE
            for pattern, reason in patterns:
                if isinstance(source, str) and pattern.search(source):
                    blocked = True
                    findings.append(reason)
            max_chars = MAX_SHELL_CHARS if name == "run_shell" else MAX_CODE_CHARS
            if not isinstance(source, str) or not source.strip():
                blocked = True
                findings.append("The execution input must be non-empty text.")
            elif len(source) > max_chars:
                blocked = True
                findings.append(f"Execution input exceeds the {max_chars}-character limit.")
            timeout = arguments.get("timeout_seconds", 10)
            if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
                blocked = True
                findings.append(f"timeout_seconds must be an integer from 1 to {MAX_TIMEOUT_SECONDS}.")
            findings.extend([
                "Explicit user approval is mandatory for this action.",
                "Execution is confined to a bubblewrap sandbox with networking disabled and host root not mounted.",
                "The selected workspace is writable during execution; review the exact input before approving.",
            ])
        elif name == "write_file":
            risk = "medium"
            findings.append("Writes are limited to the selected workspace and require approval.")
        elif name in {"save_workflow", "run_capability"}:
            risk = "medium"
            findings.append("A user-defined workflow is being changed or invoked; review the action and require approval.")
        else:
            findings.append("Read-only workspace/system operation.")
        return {
            "allowed": not blocked,
            "risk_level": risk,
            "findings": findings,
            "requires_approval": True,
            "sandbox_available": self.sandbox_available(),
            "network_isolated": name in {"run_shell", "run_code"},
            "input_sha256": _digest(source) if source else "",
            "input_characters": len(source) if isinstance(source, str) else 0,
        }

    def record(self, event: str, *, name: str = "", assessment: dict[str, Any] | None = None,
               action_id: str = "", status: str = "", detail: str = "") -> None:
        if self.audit_path is None:
            return
        assessment = assessment or {}
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": str(event)[:80],
            "action": str(name)[:80],
            "action_id": str(action_id)[:120],
            "risk_level": str(assessment.get("risk_level", "unknown"))[:16],
            "allowed": assessment.get("allowed"),
            "status": str(status)[:40],
            "input_sha256": str(assessment.get("input_sha256", ""))[:64],
            "input_characters": assessment.get("input_characters", 0),
            "detail": str(detail)[:500],
        }
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self.audit_path.stat().st_size > _MAX_AUDIT_BYTES:
                    lines = self.audit_path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
                    self.audit_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            except FileNotFoundError:
                pass
            fd = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        except OSError:
            # Audit failure must not crash the UI; the execution policy remains enforced.
            pass

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        if self.audit_path is None:
            return []
        try:
            lines = self.audit_path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(100, limit)):]
        except OSError:
            return []
        result = []
        for line in lines:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    result.append(value)
            except ValueError:
                continue
        return list(reversed(result))


class GuardianExecutor:
    """Execute shell/Python only inside bubblewrap; fail closed if unavailable."""

    def __init__(self, workspace: Any, audit_path: str | Path | None = None):
        self.workspace = workspace
        self.guardian = Guardian(workspace.root, audit_path)

    def execute(self, name: str, arguments: dict[str, Any], *,
                user_request: str, mode: str = "local", action_id: str = "") -> dict[str, Any]:
        assessment = self.guardian.assess(name, arguments, user_request=user_request, mode=mode)
        self.guardian.record("dispatch_check", name=name, action_id=action_id,
                             assessment=assessment,
                             status="allowed" if assessment["allowed"] else "denied")
        if not assessment["allowed"]:
            raise GuardianDenied("Guardian denied execution: " + " ".join(assessment["findings"]))
        if name not in {"run_shell", "run_code"}:
            raise GuardianDenied("GuardianExecutor only handles run_shell and run_code.")

        timeout = arguments.get("timeout_seconds", 10)
        started = time.monotonic()
        execution_directory = None
        private_temp = tempfile.TemporaryDirectory(prefix="agent911-guardian-private-")
        created_private_targets: list[Path] = []
        private_mounts: list[tuple[str, Path]] = []
        try:
            # Hide the real conversation/memory/capability state behind read-only
            # empty files in the sandbox. The agent's internal stores are never
            # mounted into the child namespace, even if a script scans the workspace.
            for private_name in _PRIVATE_STORES:
                target = self.workspace.root / private_name
                if target.is_symlink() or target.is_dir() or (target.exists() and not target.is_file()):
                    raise GuardianDenied(f"Reserved state path is not a regular file: {private_name}")
                if not target.exists():
                    fd = os.open(
                        target,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                    )
                    os.close(fd)
                    created_private_targets.append(target)
                placeholder = Path(private_temp.name) / private_name
                placeholder.write_text("{}", encoding="utf-8")
                placeholder.chmod(0o400)
                private_mounts.append((private_name, placeholder))

            if name == "run_code":
                execution_directory = tempfile.mkdtemp(prefix=".agent911-code-", dir=self.workspace.root)
                script = Path(execution_directory) / "main.py"
                script.write_text(arguments["code"], encoding="utf-8")
                script.chmod(0o600)
                command = ["/usr/bin/python3", "-I", str(Path("/workspace") / Path(execution_directory).name / "main.py")]
                if not Path("/usr/bin/python3").exists():
                    raise GuardianDenied("The sandbox runtime /usr/bin/python3 is unavailable.")
            else:
                command = ["/bin/sh", "-c", arguments["command"]]

            sandbox = self._sandbox_command(command, private_mounts=private_mounts)
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                process = subprocess.Popen(
                    sandbox, cwd=str(self.workspace.root), stdin=subprocess.DEVNULL,
                    stdout=stdout_file, stderr=stderr_file, close_fds=True,
                    start_new_session=True, preexec_fn=self._resource_limits,
                )
                try:
                    return_code = process.wait(timeout=timeout)
                    timed_out = False
                except subprocess.TimeoutExpired:
                    timed_out = True
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    return_code = process.returncode
                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout = stdout_file.read(MAX_OUTPUT_BYTES + 1)
                stderr = stderr_file.read(MAX_OUTPUT_BYTES + 1)
            result = {
                "exit_code": return_code,
                "timed_out": timed_out,
                "stdout": stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
                "stderr": stderr[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
                "stdout_truncated": len(stdout) > MAX_OUTPUT_BYTES,
                "stderr_truncated": len(stderr) > MAX_OUTPUT_BYTES,
                "duration_seconds": round(time.monotonic() - started, 3),
                "sandbox": {
                    "backend": "bubblewrap",
                    "network": "disabled",
                    "host_root": "not mounted",
                    "workspace": "read/write; reserved internal stores masked",
                    "timeout_seconds": timeout,
                },
            }
            self.guardian.record("execution_complete", name=name, action_id=action_id,
                                 assessment=assessment,
                                 status="timed_out" if timed_out else ("succeeded" if return_code == 0 else "failed"),
                                 detail=f"exit_code={return_code}; duration={result['duration_seconds']}s")
            return result
        except Exception as exc:
            self.guardian.record("execution_failed", name=name, action_id=action_id,
                                 assessment=assessment, status="failed",
                                 detail=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            if execution_directory:
                shutil.rmtree(execution_directory, ignore_errors=True)
            private_temp.cleanup()
            for target in created_private_targets:
                try:
                    target.unlink()
                except OSError:
                    pass

    @staticmethod
    def _resource_limits() -> None:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (31, 31))
        resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024 * 1024, 4 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        if hasattr(resource, "RLIMIT_CORE"):
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        if hasattr(resource, "RLIMIT_AS"):
            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))

    def _sandbox_command(self, command: list[str], *,
                        private_mounts: list[tuple[str, Path]] | None = None) -> list[str]:
        bwrap = shutil.which("bwrap")
        if not bwrap or os.name != "posix" or not __import__("sys").platform.startswith("linux"):
            raise GuardianDenied("bubblewrap is required for safe execution; refusing an unsandboxed fallback.")
        if os.geteuid() == 0:
            raise GuardianDenied("Refusing shell/code execution while Agent 911 is running as root.")
        if not Path("/usr/bin/python3").exists() and command[:1] == ["/usr/bin/python3"]:
            raise GuardianDenied("The sandbox runtime /usr/bin/python3 is unavailable.")

        args = [
            bwrap, "--unshare-all", "--die-with-parent", "--new-session",
            "--ro-bind", "/usr", "/usr",
        ]
        # Bind the host's existing system runtime directories read-only. Resolve
        # merged-/usr symlinks on the host so the sandbox gets real mount targets.
        for host_path, target_path in (("/bin", "/bin"), ("/lib", "/lib"), ("/lib64", "/lib64")):
            if Path(host_path).exists():
                args.extend(["--ro-bind", str(Path(host_path).resolve()), target_path])
        args.extend([
            "--dir", "/etc",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--dir", "/workspace",
            "--bind", str(self.workspace.root), "/workspace",
        ])
        for private_name, placeholder in (private_mounts or []):
            args.extend(["--ro-bind", str(placeholder), f"/workspace/{private_name}"])
        args.extend([
            "--chdir", "/workspace",
            "--clearenv", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
            "--setenv", "HOME", "/tmp",
            "--setenv", "LANG", "C.UTF-8",
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--",
            *command,
        ])
        return args
