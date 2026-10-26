"""Small, explicit OS toolset. All paths are confined to the chosen workspace."""
from __future__ import annotations

import os
import platform
import re
from pathlib import Path

_SEARCH_STOPWORDS = {"the", "a", "an", "my", "me", "you", "your", "please", "could", "would", "can", "do", "find", "search", "look", "locate", "show", "read", "open", "view", "file", "files", "note", "notes", "document", "documents", "about", "for", "in", "on", "at", "to", "of", "and", "or", "it", "this", "that", "date", "dates"}


def _query_terms(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.casefold())
            if len(word) > 1 and word not in _SEARCH_STOPWORDS}


def _matches_query(query: str, haystack: str) -> bool:
    if query in haystack.casefold():
        return True
    terms = _query_terms(query)
    if not terms:
        return False
    found = set(re.findall(r"[a-z0-9]+", haystack.casefold()))
    return len(terms & found) >= max(1, (3 * len(terms) + 4) // 5)


class Workspace:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, raw: str) -> Path:
        path = (self.root / raw).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("Path is outside the agent workspace")
        return path

    def list_files(self, path: str = ".") -> dict:
        """List names and types in a workspace directory (no recursive traversal)."""
        folder = self.resolve(path)
        if not folder.is_dir():
            raise ValueError("Not a directory")
        entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]
        return {"path": str(folder.relative_to(self.root) or "."), "entries": [
            {"name": p.name, "type": "directory" if p.is_dir() else "file"} for p in entries
        ]}

    def read_file(self, path: str) -> dict:
        """Read a UTF-8 text file in the workspace, up to 64 KiB."""
        file = self.resolve(path)
        if not file.is_file():
            raise ValueError("File does not exist")
        if file.stat().st_size > 65536:
            raise ValueError("File exceeds 64 KiB read limit")
        return {"path": str(file.relative_to(self.root)), "text": file.read_text(encoding="utf-8")}

    def write_file(self, path: str, text: str) -> dict:
        """Create or replace a UTF-8 text file in the workspace (requires user confirmation)."""
        file = self.resolve(path)
        if len(text.encode("utf-8")) > 65536:
            raise ValueError("File exceeds 64 KiB write limit")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
        return {"written": str(file.relative_to(self.root)), "bytes": file.stat().st_size}

    def search_workspace(self, query: str, path: str = ".") -> dict:
        """Search file names and UTF-8 text with strict file, directory, byte, and result limits."""
        query = query.strip()
        if not query or len(query) > 200:
            raise ValueError("Search query must contain 1–200 characters")
        start = self.resolve(path)
        if not start.is_dir():
            raise ValueError("Search path is not a directory")
        needle = query.casefold()
        matches: list[dict] = []
        examined_files = scanned_dirs = bytes_read = entries_seen = 0
        max_files, max_dirs, max_entries = 500, 200, 2000
        max_bytes, max_results, max_file_bytes, max_depth = 4 * 1024 * 1024, 50, 256 * 1024, 20
        private = {".needle-agent-memory.json", ".needle-agent-conversation.json",
                   ".needle-agent-capabilities.json"}
        pending = [(start, 0)]
        truncated = False
        while pending and len(matches) < max_results and entries_seen < max_entries:
            folder, depth = pending.pop()
            if scanned_dirs >= max_dirs:
                truncated = True
                break
            scanned_dirs += 1
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        entries_seen += 1
                        if entries_seen > max_entries:
                            truncated = True
                            break
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if depth < max_depth and len(pending) + scanned_dirs < max_dirs:
                                    pending.append((Path(entry.path), depth + 1))
                                else:
                                    truncated = True
                                continue
                            if not entry.is_file(follow_symlinks=False) or entry.name in private:
                                continue
                        except OSError:
                            continue
                        candidate = Path(entry.path)
                        rel = str(candidate.relative_to(self.root))
                        if _matches_query(query, entry.name):
                            matches.append({"path": rel, "match": "filename"})
                            if len(matches) >= max_results:
                                break
                        if examined_files >= max_files or bytes_read >= max_bytes:
                            truncated = True
                            break
                        examined_files += 1
                        try:
                            size = entry.stat(follow_symlinks=False).st_size
                            if size > max_file_bytes or bytes_read + size > max_bytes:
                                truncated = True
                                continue
                            with open(entry.path, "rb") as stream:
                                content = stream.read(max_file_bytes + 1)
                            if len(content) > max_file_bytes:
                                truncated = True
                                continue
                            bytes_read += len(content)
                            text = content.decode("utf-8")
                        except (OSError, UnicodeDecodeError):
                            continue
                        for line_no, line in enumerate(text.splitlines(), start=1):
                            if _matches_query(query, line):
                                matches.append({"path": rel, "match": "content", "line": line_no,
                                                "snippet": line[:200]})
                                if len(matches) >= max_results:
                                    break
                        if len(matches) >= max_results:
                            break
            except OSError:
                continue
        if pending or entries_seen >= max_entries or examined_files >= max_files or bytes_read >= max_bytes:
            truncated = True
        return {"query": query, "matches": matches, "files_scanned": examined_files,
                "directories_scanned": scanned_dirs, "bytes_scanned": bytes_read,
                "truncated": truncated}

    def system_info(self) -> dict:
        """Show basic operating-system and Python runtime information."""
        return {"system": platform.system(), "release": platform.release(),
                "architecture": platform.machine(), "python": platform.python_version(),
                "working_directory": str(self.root), "user": os.environ.get("USER", "unknown")}
