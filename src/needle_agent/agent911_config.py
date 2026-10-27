"""Persistent configuration and upgrade-request queue for the Agent 911 console.

This module stores user configuration outside the agent workspace. Upgrade prompts
are drafts for explicit human review; they never edit source code or enable tools.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def agent911_config_dir(
    system: str | None = None,
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the OS-appropriate per-user Agent 911 configuration directory."""
    system = sys.platform if system is None else system
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    if system == "win32":
        base = env.get("APPDATA") or str(home / "AppData" / "Roaming")
        return Path(base) / "Agent911"
    if system == "darwin":
        return home / "Library" / "Application Support" / "Agent911"
    xdg = env.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".config"
    return base / "agent-911"


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".agent911-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        if os.name != "nt":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ModelConfiguration:
    """Persist runtime paths and default mode; credentials live in CloudSettings."""

    MODES = frozenset({"local", "cloud", "hybrid"})

    def __init__(self, path: Path | None = None):
        self.path = path or agent911_config_dir() / "model-configuration.json"

    @staticmethod
    def defaults() -> dict[str, Any]:
        return {
            "version": 1,
            "default_mode": "local",
            "local": {"weights_path": "", "engine_path": ""},
        }

    def load(self) -> dict[str, Any]:
        data = self.defaults()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            return data
        mode = raw.get("default_mode")
        if mode in self.MODES:
            data["default_mode"] = mode
        local = raw.get("local")
        if isinstance(local, dict):
            for field in ("weights_path", "engine_path"):
                value = local.get(field, "")
                if isinstance(value, str):
                    data["local"][field] = value
        return data

    @staticmethod
    def _normalise_file_path(value: Any, label: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{label} must be a text path.")
        value = value.strip()
        if not value:
            return ""
        if "\x00" in value or len(value) > 4096:
            raise ValueError(f"{label} path is invalid or too long.")
        resolved = Path(value).expanduser().resolve(strict=False)
        if not resolved.is_file():
            raise ValueError(f"{label} file was not found: {resolved}")
        return str(resolved)

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Model configuration must be an object.")
        mode = payload.get("default_mode", "local")
        if mode not in self.MODES:
            raise ValueError("Default mode must be local, cloud, or hybrid.")
        local = payload.get("local", {})
        if not isinstance(local, dict):
            raise ValueError("Local model configuration must be an object.")
        weights = self._normalise_file_path(local.get("weights_path", ""), "Model weights")
        engine = self._normalise_file_path(local.get("engine_path", ""), "Native engine")
        if engine and not weights:
            raise ValueError("Choose a model weights file when configuring a native engine.")
        saved = {
            "version": 1,
            "default_mode": mode,
            "local": {"weights_path": weights, "engine_path": engine},
        }
        _atomic_json_write(self.path, saved)
        return saved


class UpgradePromptStore:
    """Store user-authored upgrade requests and model-drafted plans, never source edits."""

    MAX_ITEMS = 100
    MAX_REQUEST_CHARS = 10000
    MAX_PLAN_CHARS = 20000

    def __init__(self, path: Path | None = None):
        self.path = path or agent911_config_dir() / "upgrade-prompts.json"

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return []
        valid = []
        for item in data["items"]:
            if (isinstance(item, dict)
                    and isinstance(item.get("id"), str)
                    and isinstance(item.get("request"), str)
                    and isinstance(item.get("plan", ""), str)
                    and isinstance(item.get("status", "draft"), str)):
                valid.append({
                    "id": item["id"],
                    "request": item["request"][:self.MAX_REQUEST_CHARS],
                    "plan": item.get("plan", "")[:self.MAX_PLAN_CHARS],
                    "status": item.get("status", "draft"),
                    "created_at": str(item.get("created_at", ""))[:64],
                    "updated_at": str(item.get("updated_at", ""))[:64],
                })
        return valid[-self.MAX_ITEMS:]

    def list(self) -> list[dict[str, Any]]:
        return list(reversed(self._load()))

    def add(self, request: str, plan: str = "") -> dict[str, Any]:
        if not isinstance(request, str) or not request.strip():
            raise ValueError("Describe the upgrade you want to plan.")
        request = request.strip()
        if len(request) > self.MAX_REQUEST_CHARS:
            raise ValueError(f"Upgrade request exceeds {self.MAX_REQUEST_CHARS} characters.")
        if not isinstance(plan, str):
            raise ValueError("Upgrade plan must be text.")
        plan = plan.strip()
        if len(plan) > self.MAX_PLAN_CHARS:
            plan = plan[:self.MAX_PLAN_CHARS]
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "id": uuid.uuid4().hex,
            "request": request,
            "plan": plan,
            "status": "planned" if plan else "draft",
            "created_at": now,
            "updated_at": now,
        }
        items = self._load()
        items.append(item)
        items = items[-self.MAX_ITEMS:]
        _atomic_json_write(self.path, {"version": 1, "items": items})
        return item

    def archive(self, item_id: str) -> dict[str, Any]:
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("Upgrade request ID is required.")
        items = self._load()
        for item in items:
            if item["id"] == item_id:
                item["status"] = "archived"
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                _atomic_json_write(self.path, {"version": 1, "items": items})
                return item
        raise ValueError("Upgrade request was not found.")
