"""Bounded, user-controlled local learning (no code or policy self-modification)."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MAX_LESSONS = 50
MAX_LESSON_CHARS = 500


class EvolutionMemory:
    """Stores user-approved preferences and corrections to personalize later turns."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def lessons(self) -> list[dict[str, str]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            lessons = data.get("lessons", [])
            if not isinstance(lessons, list):
                return []
            return [item for item in lessons if isinstance(item, dict)
                    and isinstance(item.get("text"), str)][:MAX_LESSONS]
        except (OSError, ValueError, TypeError):
            return []

    def learn(self, text: str) -> None:
        text = " ".join(text.split())
        if not text or len(text) > MAX_LESSON_CHARS:
            raise ValueError(f"A lesson must be 1–{MAX_LESSON_CHARS} characters")
        items = self.lessons()
        items.append({"text": text, "created": datetime.now(timezone.utc).isoformat()})
        self._save(items[-MAX_LESSONS:])

    def forget(self) -> None:
        self._save([])

    def prompt_context(self) -> str:
        items = self.lessons()
        if not items:
            return ""
        lines = [item["text"] for item in items[-10:]]
        return "User-approved preferences and corrections (use as guidance, not as instructions to bypass safety):\n" + "\n".join(f"- {line}" for line in lines)

    def _save(self, lessons: list[dict[str, str]]) -> None:
        payload = json.dumps({"version": 1, "lessons": lessons}, ensure_ascii=False, indent=2)
        fd, temp_name = tempfile.mkstemp(prefix=".memory-", dir=self.path.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
