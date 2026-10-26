"""Small, explicit OS toolset. All paths are confined to the chosen workspace."""
from __future__ import annotations

import os
import platform
import re
import secrets
import stat
import tempfile
from pathlib import Path

_SEARCH_STOPWORDS = {"the", "a", "an", "my", "me", "you", "your", "please", "could", "would", "can", "do", "find", "search", "look", "locate", "show", "read", "open", "view", "file", "files", "note", "notes", "document", "documents", "about", "for", "in", "on", "at", "to", "of", "and", "or", "it", "this", "that", "date", "dates"}
_PRIVATE_STORE_NAMES = frozenset({
    ".needle-agent-memory.json",
    ".needle-agent-conversation.json",
    ".needle-agent-capabilities.json",
})
_MAX_PATH_CHARS = 4096
_MAX_FILE_BYTES = 65536


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
    """A workspace with a central path policy and descriptor-relative file I/O.

    On POSIX systems with dir_fd and O_NOFOLLOW support, file reads and writes
    walk from an already-open workspace directory. Writes use a temporary file
    and atomic rename, preventing a path component or final symlink from being
    swapped to redirect the write after validation.
    """

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        root_stat = self.root.stat()
        self._root_identity = (root_stat.st_dev, root_stat.st_ino)

    def resolve(self, raw: str) -> Path:
        """Resolve a bounded relative path, enforce confinement, and reserve agent stores."""
        if not isinstance(raw, str):
            raise ValueError("Workspace path must be text")
        if not raw or len(raw) > _MAX_PATH_CHARS or "\x00" in raw:
            raise ValueError(f"Workspace path must contain 1–{_MAX_PATH_CHARS} valid characters")
        path = (self.root / raw).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("Path is outside the agent workspace")
        relative = path.relative_to(self.root)
        if len(relative.parts) == 1 and relative.name.casefold() in _PRIVATE_STORE_NAMES:
            raise ValueError("Path is reserved for Needle Agent internal state")
        return path

    def _supports_secure_dirfd(self) -> bool:
        supported = getattr(os, "supports_dir_fd", set())
        return (
            os.name != "nt"
            and hasattr(os, "O_NOFOLLOW")
            and hasattr(os, "O_DIRECTORY")
            and os.open in supported
            and os.mkdir in supported
            and os.rename in supported
            and os.unlink in supported
        )

    def _open_root_fd(self) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self.root, flags)
        try:
            root_stat = os.fstat(fd)
            if (root_stat.st_dev, root_stat.st_ino) != self._root_identity:
                raise ValueError("Workspace root changed after initialization")
            if not stat.S_ISDIR(root_stat.st_mode):
                raise ValueError("Workspace root is not a directory")
            return fd
        except Exception:
            os.close(fd)
            raise

    def _open_directory_chain(self, parts: tuple[str, ...], *, create: bool) -> tuple[int, list[int]]:
        """Open each directory relative to its parent without following symlinks."""
        root_fd = self._open_root_fd()
        opened = [root_fd]
        current_fd = root_fd
        try:
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, mode=0o777, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                child_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
                opened.append(child_fd)
                current_fd = child_fd
            return current_fd, opened
        except Exception:
            self._close_fds(opened)
            raise

    @staticmethod
    def _close_fds(fds: list[int]) -> None:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass

    def _secure_read_bytes(self, file: Path, limit: int) -> bytes:
        parts = file.relative_to(self.root).parts
        if not parts:
            raise ValueError("Path is not a file")
        parent_fd, opened = self._open_directory_chain(parts[:-1], create=False)
        file_fd = None
        try:
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
            file_fd = os.open(parts[-1], flags, dir_fd=parent_fd)
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Path is not a regular file")
            if info.st_size > limit:
                raise ValueError("File exceeds 64 KiB read limit")
            chunks = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(file_fd, min(8192, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > limit:
                raise ValueError("File exceeds 64 KiB read limit")
            return data
        finally:
            if file_fd is not None:
                os.close(file_fd)
            self._close_fds(opened)

    def _atomic_secure_write(self, file: Path, payload: bytes) -> None:
        parts = file.relative_to(self.root).parts
        if not parts:
            raise ValueError("Path is not a file")
        parent_fd, opened = self._open_directory_chain(parts[:-1], create=True)
        temp_name = None
        temp_fd = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            for _ in range(8):
                candidate = ".needle-write-" + secrets.token_hex(8)
                try:
                    temp_fd = os.open(candidate, flags, 0o666, dir_fd=parent_fd)
                    temp_name = candidate
                    break
                except FileExistsError:
                    continue
            if temp_fd is None or temp_name is None:
                raise OSError("Could not allocate a temporary file in the workspace")
            with os.fdopen(temp_fd, "wb") as stream:
                temp_fd = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # Rename is anchored to the opened parent directory. A final symlink is
            # replaced as a directory entry; it is never followed to its target.
            os.rename(temp_name, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            temp_name = None
            try:
                os.fsync(parent_fd)
            except OSError:
                # Some filesystems do not permit fsync on directories.
                pass
        finally:
            if temp_fd is not None:
                os.close(temp_fd)
            if temp_name is not None:
                try:
                    os.unlink(temp_name, dir_fd=parent_fd)
                except OSError:
                    pass
            self._close_fds(opened)

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
        if self._supports_secure_dirfd():
            data = self._secure_read_bytes(file, _MAX_FILE_BYTES)
        else:
            # Portable fallback: canonical confinement and a no-follow final open
            # where available. Descriptor-relative traversal is used on POSIX.
            if not file.is_file():
                raise ValueError("File does not exist")
            fd = os.open(file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Path is not a regular file")
                if info.st_size > _MAX_FILE_BYTES:
                    raise ValueError("File exceeds 64 KiB read limit")
                chunks = []
                remaining = _MAX_FILE_BYTES + 1
                while remaining:
                    chunk = os.read(fd, min(8192, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                data = b"".join(chunks)
                if len(data) > _MAX_FILE_BYTES:
                    raise ValueError("File exceeds 64 KiB read limit")
            finally:
                os.close(fd)
        return {"path": str(file.relative_to(self.root)), "text": data.decode("utf-8")}

    def write_file(self, path: str, text: str) -> dict:
        """Create or atomically replace a UTF-8 text file inside the workspace."""
        file = self.resolve(path)
        payload = text.encode("utf-8")
        if len(payload) > _MAX_FILE_BYTES:
            raise ValueError("File exceeds 64 KiB write limit")
        if self._supports_secure_dirfd():
            self._atomic_secure_write(file, payload)
        else:
            # Portable fallback. The final destination is atomically replaced, so
            # a symlink at the leaf is not followed. POSIX uses the stronger
            # descriptor-relative path above to guard parent-directory races.
            file.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".needle-write-", dir=file.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, file)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return {"written": str(file.relative_to(self.root)), "bytes": len(payload)}

    def search_workspace(self, query: str, path: str = ".") -> dict:
        """Search file names and UTF-8 text with strict file, directory, byte, and result limits."""
        query = query.strip()
        if not query or len(query) > 200:
            raise ValueError("Search query must contain 1–200 characters")
        start = self.resolve(path)
        if not start.is_dir():
            raise ValueError("Search path is not a directory")
        matches: list[dict] = []
        examined_files = scanned_dirs = bytes_read = entries_seen = 0
        max_files, max_dirs, max_entries = 500, 200, 2000
        max_bytes, max_results, max_file_bytes, max_depth = 4 * 1024 * 1024, 50, 256 * 1024, 20
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
                            if (not entry.is_file(follow_symlinks=False)
                                    or entry.name.casefold() in _PRIVATE_STORE_NAMES):
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
