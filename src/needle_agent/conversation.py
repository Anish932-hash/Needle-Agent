"""Persistent bounded conversation memory with deterministic context compaction."""
from __future__ import annotations
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ConversationMemory:
    """Remember recent tasks locally and compact older turns to a strict character budget."""
    def __init__(self, path: Path, *, max_context_chars: int = 6000,
                 keep_recent: int = 8, max_summary_chars: int = 1800):
        for label, value in (("max_context_chars", max_context_chars),
                             ("keep_recent", keep_recent),
                             ("max_summary_chars", max_summary_chars)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{label} must be a positive integer")
        self.path = path
        self.max_context_chars = max_context_chars
        self.keep_recent = keep_recent
        # A summary larger than the whole context budget would make _compact()
        # unable to satisfy its invariant even after all recent turns were removed.
        self.max_summary_chars = min(max_summary_chars, max_context_chars)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = self._load()
        raw_summary = data.get("summary", "")
        self.summary: str = raw_summary[:self.max_summary_chars] if isinstance(raw_summary, str) else ""
        raw_turns = data.get("turns", [])
        self.turns = [item for item in raw_turns if isinstance(item, dict)
                      and isinstance(item.get("user"), str) and isinstance(item.get("outcome"), str)][-self.keep_recent:] \
            if isinstance(raw_turns, list) else []

    def _load(self) -> dict[str, Any]:
        try:
            obj = json.loads(self.path.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {}
        except (OSError, ValueError):
            return {}

    def add(self, user: str, outcome: str) -> None:
        """Persist a concise record; never record raw filesystem contents or secrets."""
        user = _clean(user, 500)
        outcome = _clean(outcome, 500)
        self.turns.append({"user": user, "outcome": outcome,
                           "time": datetime.now(timezone.utc).isoformat()})
        self._compact()
        self._save()

    def _compact(self) -> None:
        while len(self.turns) > self.keep_recent or self._estimated_chars() > self.max_context_chars:
            if not self.turns:
                break
            old = self.turns.pop(0)
            fragment = f"Earlier task: {old['user']} — {old['outcome']}"
            self.summary = (self.summary + "\n" + fragment).strip()
            if len(self.summary) > self.max_summary_chars:
                if self.max_summary_chars == 1:
                    self.summary = "…"
                else:
                    self.summary = "…" + self.summary[-(self.max_summary_chars - 1):]

    def _estimated_chars(self) -> int:
        return len(self.summary) + sum(len(t["user"]) + len(t["outcome"]) + 24 for t in self.turns)

    def relevant_context(self, request: str, *, max_turns: int = 4) -> str:
        """Rank recent turns by lexical relevance; retain the newest for follow-up references."""
        stop = {"the", "and", "for", "with", "that", "this", "from", "into", "about", "please", "could", "would", "have", "what", "when", "where", "which", "your", "you", "are", "was", "were", "an", "to", "of", "in", "on", "is", "it"}
        words = {w for w in re.findall(r"[a-z0-9]+", request.casefold()) if len(w) > 2 and w not in stop}
        scored = []
        for i, turn in enumerate(self.turns):
            text = f"{turn.get('user', '')} {turn.get('outcome', '')}"
            terms = set(re.findall(r"[a-z0-9]+", text.casefold()))
            overlap = len(words & terms)
            recency = i / max(1, len(self.turns))
            scored.append((overlap, recency, i))
        selected_ids = {i for _, _, i in sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[:max_turns]}
        if self.turns:
            selected_ids.add(len(self.turns) - 1)
        selected = [self.turns[i] for i in sorted(selected_ids)]
        blocks = []
        summary_terms = set(re.findall(r"[a-z0-9]+", self.summary.casefold()))
        if self.summary and (words & summary_terms or re_has_reference(request)):
            blocks.append("Older compacted context (may be incomplete):\n" + self.summary[-1200:])
        if selected:
            blocks.append("Relevant prior turns (background only):\n" + "\n".join(
                f"User: {t['user']}\nOutcome: {t['outcome']}" for t in selected))
        return "\n\n".join(blocks)[-self.max_context_chars:]

    def context(self) -> str:
        return self.relevant_context("", max_turns=4)

    def status(self) -> dict[str, int]:
        return {"recent_turns": len(self.turns), "summary_characters": len(self.summary),
                "context_characters": len(self.context()), "context_limit": self.max_context_chars}

    def clear(self) -> None:
        self.summary = ""
        self.turns = []
        self._save()

    def _save(self) -> None:
        payload = json.dumps({"version": 1, "summary": self.summary, "turns": self.turns},
                             ensure_ascii=False, indent=2)
        fd, name = tempfile.mkstemp(prefix=".conversation-", dir=self.path.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def _clean(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def re_has_reference(request: str) -> bool:
    return bool(re.search(r"\b(it|that|those|them|there|same|above|previous|continue)\b", request.casefold()))
