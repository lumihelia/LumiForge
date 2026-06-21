"""Conversation adapters for project-scoped Codex and Claude Code history."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .storage import EventStore, ProjectStore, stable_id


MAX_TEXT = 40_000


def _same_path(value: Any, expected: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return Path(value).expanduser().resolve() == expected.resolve()
    except OSError:
        return os.path.normpath(value) == os.path.normpath(str(expected))


def _truncate(value: Any, limit: int = MAX_TEXT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "\n... truncated ..."


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        values = []
        for block in content:
            if isinstance(block, str):
                values.append(block)
            elif isinstance(block, dict) and block.get("type") in {
                "text",
                "input_text",
                "output_text",
            }:
                values.append(str(block.get("text", "")))
        return "\n".join(value for value in values if value).strip()
    if isinstance(content, dict):
        return _extract_text(content.get("content") or content.get("text") or "")
    return ""


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _files_from_tool(name: str, tool_input: Any) -> List[str]:
    value = _json_value(tool_input)
    files: List[str] = []
    if isinstance(value, dict):
        for key in ("file_path", "path", "filepath"):
            path = value.get(key)
            if isinstance(path, str):
                files.append(path)
        patch_text = value.get("patch") or value.get("input") or ""
        if isinstance(patch_text, str):
            files.extend(
                re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", patch_text, re.MULTILINE)
            )
    elif isinstance(value, str) and name == "apply_patch":
        files.extend(
            re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", value, re.MULTILINE)
        )
    return sorted(set(files))


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _run_for_timestamp(runs: Sequence[Dict[str, Any]], timestamp: Optional[str]) -> Optional[str]:
    moment = _parse_timestamp(timestamp)
    if moment is None:
        return None
    for run in runs:
        for period in run.get("recording_periods", []):
            start = _parse_timestamp(period.get("started_at"))
            end = _parse_timestamp(period.get("ended_at"))
            if start and moment >= start and (end is None or moment <= end):
                return run["run_id"]
    return None


def _source_event(
    source: str,
    source_file: Path,
    line_number: int,
    conversation_id: str,
    timestamp: Optional[str],
    event_type: str,
    payload: Dict[str, Any],
    runs: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    source_ref = f"{source_file}:{line_number}"
    return {
        "event_id": stable_id(source, source_ref, event_type),
        "timestamp": timestamp or datetime.fromtimestamp(source_file.stat().st_mtime).astimezone().isoformat(),
        "run_id": _run_for_timestamp(runs, timestamp),
        "conversation_id": conversation_id,
        "source": source,
        "source_ref": source_ref,
        "type": event_type,
        "payload": payload,
    }


class CodexAdapter:
    def __init__(self, home: Optional[Path] = None):
        configured = os.getenv("LUMIFORGE_CODEX_HOME")
        self.root = Path(configured).expanduser() if configured else (home or Path.home()) / ".codex"

    def discover(self) -> Iterable[Path]:
        for base in (self.root / "sessions", self.root / "archived_sessions"):
            if base.exists():
                yield from base.rglob("*.jsonl")

    def matches_project(self, path: Path, project_path: Path) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index > 200:
                        break
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = item.get("payload", {})
                    if item.get("type") == "session_meta" and _same_path(payload.get("cwd"), project_path):
                        return True
                    if item.get("type") == "turn_context":
                        roots = payload.get("workspace_roots") or []
                        if any(_same_path(root, project_path) for root in roots) or _same_path(
                            payload.get("cwd"), project_path
                        ):
                            return True
        except (OSError, PermissionError):
            return False
        return False

    def parse(self, path: Path, runs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        conversation_id = path.stem
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = item.get("payload", {})
                    timestamp = item.get("timestamp")
                    if item.get("type") == "session_meta":
                        conversation_id = payload.get("id") or conversation_id
                        continue

                    if item.get("type") == "event_msg" and payload.get("type") == "user_message":
                        text = _extract_text(payload.get("message"))
                        if text:
                            events.append(
                                _source_event(
                                    "codex",
                                    path,
                                    line_number,
                                    conversation_id,
                                    timestamp,
                                    "message",
                                    {"role": "user", "text": _truncate(text)},
                                    runs,
                                )
                            )
                    elif item.get("type") == "event_msg" and payload.get("type") == "agent_message":
                        text = _extract_text(payload.get("message"))
                        if text:
                            events.append(
                                _source_event(
                                    "codex",
                                    path,
                                    line_number,
                                    conversation_id,
                                    timestamp,
                                    "message",
                                    {"role": "assistant", "text": _truncate(text)},
                                    runs,
                                )
                            )
                    elif item.get("type") == "event_msg" and payload.get("type") == "exec_command_end":
                        events.append(
                            _source_event(
                                "codex",
                                path,
                                line_number,
                                conversation_id,
                                timestamp,
                                "command",
                                {
                                    "command": _truncate(payload.get("command") or payload.get("parsed_cmd") or ""),
                                    "cwd": payload.get("cwd"),
                                    "exit_code": payload.get("exit_code"),
                                    "status": payload.get("status"),
                                    "stdout": _truncate(payload.get("stdout") or payload.get("aggregated_output") or ""),
                                    "stderr": _truncate(payload.get("stderr") or ""),
                                },
                                runs,
                            )
                        )
                    elif item.get("type") == "response_item" and payload.get("type") in {
                        "function_call",
                        "custom_tool_call",
                    }:
                        name = payload.get("name", "unknown")
                        arguments = payload.get("arguments", payload.get("input", ""))
                        events.append(
                            _source_event(
                                "codex",
                                path,
                                line_number,
                                conversation_id,
                                timestamp,
                                "tool_call",
                                {
                                    "name": name,
                                    "input": _json_value(_truncate(arguments)),
                                    "files": _files_from_tool(name, arguments),
                                },
                                runs,
                            )
                        )
        except (OSError, PermissionError):
            return []
        return events


class ClaudeCodeAdapter:
    def __init__(self, home: Optional[Path] = None):
        configured = os.getenv("LUMIFORGE_CLAUDE_HOME")
        self.root = (
            Path(configured).expanduser()
            if configured
            else (home or Path.home()) / ".claude" / "projects"
        )

    def discover(self) -> Iterable[Path]:
        if self.root.exists():
            yield from self.root.rglob("*.jsonl")

    def matches_project(self, path: Path, project_path: Path) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index > 200:
                        break
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if _same_path(item.get("cwd"), project_path):
                        return True
        except (OSError, PermissionError):
            return False
        return False

    def parse(self, path: Path, runs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        conversation_id = path.stem
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    timestamp = item.get("timestamp")
                    conversation_id = item.get("sessionId") or conversation_id
                    message = item.get("message", {})

                    if item.get("type") == "user":
                        text = _extract_text(message.get("content"))
                        if text:
                            events.append(
                                _source_event(
                                    "claude",
                                    path,
                                    line_number,
                                    conversation_id,
                                    timestamp,
                                    "message",
                                    {"role": "user", "text": _truncate(text)},
                                    runs,
                                )
                            )
                        tool_result = item.get("toolUseResult")
                        if tool_result:
                            events.append(
                                _source_event(
                                    "claude",
                                    path,
                                    line_number,
                                    conversation_id,
                                    timestamp,
                                    "tool_result",
                                    {"output": _truncate(tool_result)},
                                    runs,
                                )
                            )

                    elif item.get("type") == "assistant":
                        content = message.get("content", [])
                        text = _extract_text(content)
                        if text:
                            events.append(
                                _source_event(
                                    "claude",
                                    path,
                                    line_number,
                                    conversation_id,
                                    timestamp,
                                    "message",
                                    {"role": "assistant", "text": _truncate(text)},
                                    runs,
                                )
                            )
                        for block in content if isinstance(content, list) else []:
                            if not isinstance(block, dict) or block.get("type") != "tool_use":
                                continue
                            name = block.get("name", "unknown")
                            tool_input = block.get("input", {})
                            events.append(
                                _source_event(
                                    "claude",
                                    path,
                                    line_number,
                                    conversation_id,
                                    timestamp,
                                    "tool_call",
                                    {
                                        "name": name,
                                        "input": tool_input,
                                        "files": _files_from_tool(name, tool_input),
                                    },
                                    runs,
                                )
                            )
        except (OSError, PermissionError):
            return []
        return events


def sync_conversations(project_path: str | Path) -> Dict[str, Any]:
    project_store = ProjectStore(project_path)
    project_store.ensure_project()
    event_store = EventStore(project_path)
    runs = project_store.list_runs()
    root = project_store.paths.root
    stats: Dict[str, Any] = {"codex": 0, "claude": 0, "files": 0, "events": 0}

    for name, adapter in (("codex", CodexAdapter()), ("claude", ClaudeCodeAdapter())):
        for source_file in adapter.discover():
            if not adapter.matches_project(source_file, root):
                continue
            stats["files"] += 1
            imported = event_store.extend(adapter.parse(source_file, runs))
            stats[name] += imported
            stats["events"] += imported
    return stats
