"""Content-aware workspace monitoring.

The watcher records what changed, not only that a path changed. Sensitive,
binary, and oversized files are represented by metadata without contents.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set, Tuple

from watchdog.events import FileSystemEvent, FileSystemEventHandler, FileMovedEvent
from watchdog.observers.polling import PollingObserver


DEFAULT_IGNORES = {
    ".git",
    ".lumiforge",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".cache",
    ".DS_Store",
    ".vscode",
    ".idea",
}

SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "secrets.json",
    "auth.json",
    "id_rsa",
    "id_ed25519",
}

SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ContentSnapshot:
    """Maintains the last observed text contents for a workspace."""

    def __init__(
        self,
        project_path: str | Path,
        ignore_patterns: Optional[Set[str]] = None,
        max_file_bytes: int = 512_000,
        max_diff_chars: int = 80_000,
    ):
        self.project_path = Path(project_path).resolve()
        self.ignore_patterns = ignore_patterns or DEFAULT_IGNORES
        self.max_file_bytes = max_file_bytes
        self.max_diff_chars = max_diff_chars
        self.contents: Dict[str, str] = {}
        self.hashes: Dict[str, str] = {}
        self.lock = threading.Lock()
        self._scan_baseline()

    def should_ignore(self, path: str | Path) -> bool:
        path_obj = Path(path)
        try:
            relative = path_obj.resolve().relative_to(self.project_path)
        except (ValueError, OSError):
            relative = path_obj
        if any(part in self.ignore_patterns for part in relative.parts):
            return True
        if path_obj.name in SENSITIVE_NAMES or path_obj.suffix.lower() in SENSITIVE_SUFFIXES:
            return False
        return path_obj.suffix.lower() in {".pyc", ".pyo", ".swp", ".swo"}

    def relative_path(self, path: str | Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.project_path))
        except (ValueError, OSError):
            return str(path)

    def _read_text(self, path: Path) -> Tuple[Optional[str], str]:
        if path.name in SENSITIVE_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES:
            return None, "sensitive"
        try:
            size = path.stat().st_size
            if size > self.max_file_bytes:
                return None, "oversized"
            raw = path.read_bytes()
            if b"\x00" in raw[:4096]:
                return None, "binary"
            return raw.decode("utf-8"), "captured"
        except UnicodeDecodeError:
            return None, "binary"
        except (FileNotFoundError, PermissionError, OSError):
            return None, "unavailable"

    def _scan_baseline(self) -> None:
        for path in self.project_path.rglob("*"):
            if not path.is_file() or self.should_ignore(path):
                continue
            text, status = self._read_text(path)
            if status == "captured" and text is not None:
                relative = self.relative_path(path)
                self.contents[relative] = text
                self.hashes[relative] = _digest(text)

    def capture(self, change_type: str, path: str | Path) -> Optional[Dict[str, Any]]:
        path_obj = Path(path)
        if self.should_ignore(path_obj):
            return None
        relative = self.relative_path(path_obj)

        with self.lock:
            before = self.contents.get(relative)
            before_hash = self.hashes.get(relative)

            if change_type == "deleted":
                current = None
                capture_status = "captured" if before is not None else "metadata_only"
            else:
                current, capture_status = self._read_text(path_obj)

            current_hash = _digest(current) if current is not None else None
            if change_type == "modified" and current_hash and current_hash == before_hash:
                return None

            if current is not None:
                self.contents[relative] = current
                self.hashes[relative] = current_hash or ""
            elif change_type == "deleted":
                self.contents.pop(relative, None)
                self.hashes.pop(relative, None)

            diff_text = ""
            if capture_status == "captured" and (before is not None or current is not None):
                before_lines = (before or "").splitlines(keepends=True)
                current_lines = (current or "").splitlines(keepends=True)
                diff_text = "".join(
                    difflib.unified_diff(
                        before_lines,
                        current_lines,
                        fromfile=f"a/{relative}",
                        tofile=f"b/{relative}",
                    )
                )
                if len(diff_text) > self.max_diff_chars:
                    diff_text = diff_text[: self.max_diff_chars] + "\n... diff truncated ...\n"

            try:
                size = path_obj.stat().st_size if path_obj.exists() else None
            except OSError:
                size = None

            return {
                "change_type": change_type,
                "path": relative,
                "size": size,
                "content_captured": capture_status == "captured",
                "capture_status": capture_status,
                "before_hash": before_hash,
                "after_hash": current_hash,
                "diff": diff_text,
                "lines_added": sum(
                    1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++")
                ),
                "lines_removed": sum(
                    1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---")
                ),
            }


class FileWatcher(FileSystemEventHandler):
    """Converts watchdog events into content-aware evidence payloads."""

    def __init__(self, project_path: str, on_event: Callable[[Dict[str, Any]], None]):
        super().__init__()
        self.snapshot = ContentSnapshot(project_path)
        self.on_event = on_event

    def _capture(self, change_type: str, path: str) -> None:
        payload = self.snapshot.capture(change_type, path)
        if payload:
            self.on_event(payload)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._capture("created", event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._capture("modified", event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._capture("deleted", event.src_path)

    def on_moved(self, event: FileMovedEvent) -> None:
        if not event.is_directory:
            self._capture("deleted", event.src_path)
            self._capture("created", event.dest_path)


class WatcherManager:
    def __init__(self, project_path: str, on_event: Callable[[Dict[str, Any]], None]):
        self.project_path = str(Path(project_path).resolve())
        # Polling is slightly less efficient but avoids silent FSEvents failures
        # on moved virtual environments and restricted macOS processes.
        self.observer = PollingObserver(timeout=0.35)
        self.event_handler = FileWatcher(self.project_path, on_event)

    def start(self) -> None:
        self.observer.schedule(self.event_handler, self.project_path, recursive=True)
        self.observer.start()

    def stop(self) -> None:
        self.observer.stop()
        self.observer.join()

    def is_alive(self) -> bool:
        return self.observer.is_alive()
