"""Build evidence chains from raw project events.

Episodes never claim access to hidden model reasoning. They connect observable
user intent, visible agent explanations, code changes, and verification output.
"""

from __future__ import annotations

import difflib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from .storage import stable_id


BUG_WORDS = {
    "bug",
    "error",
    "failed",
    "failure",
    "broken",
    "fix",
    "issue",
    "问题",
    "错误",
    "报错",
    "失败",
    "修复",
    "不工作",
    "没反应",
    "无法",
}
FEATURE_WORDS = {"add", "build", "create", "implement", "feature", "新增", "添加", "创建", "实现", "功能"}
VERIFY_PATTERN = re.compile(
    r"(?:^|\s)(?:pytest|unittest|test|tests|lint|typecheck|check|build|playwright|curl)(?:\s|$)|"
    r"npm\s+(?:test|run\s+(?:test|build|lint|check))|"
    r"pnpm\s+(?:test|run\s+(?:test|build|lint|check))|"
    r"cargo\s+(?:test|check)|go\s+test",
    re.IGNORECASE,
)


def _timestamp(value: Optional[str]) -> datetime:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _compact(text: str, limit: int = 420) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _title(text: str) -> str:
    value = re.sub(r"^[#>*\-\s]+", "", text or "").strip()
    first = re.split(r"[\n。！？!?]", value, maxsplit=1)[0]
    first = re.sub(r"<environment_context>.*", "", first).strip()
    return _compact(first or "未命名构建事件", 88)


def _classification(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in BUG_WORDS):
        return "bug"
    if any(word in lowered for word in FEATURE_WORDS):
        return "feature"
    if "重构" in lowered or "refactor" in lowered:
        return "refactor"
    return "change"


def _keywords(text: str) -> Set[str]:
    lowered = text.lower()
    latin = set(re.findall(r"[a-z][a-z0-9_\-]{2,}", lowered))
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    cjk = {run[index : index + 2] for run in cjk_runs for index in range(len(run) - 1)}
    stop = {
        "这个",
        "那个",
        "然后",
        "可以",
        "需要",
        "我们",
        "帮我",
        "一下",
        "please",
        "with",
        "from",
        "that",
        "this",
    }
    return {word for word in latin | cjk if word not in stop}


def _tool_diff(payload: Dict[str, Any]) -> str:
    name = str(payload.get("name", "")).lower()
    tool_input = payload.get("input", {})
    if isinstance(tool_input, str):
        if name == "apply_patch" or "*** Begin Patch" in tool_input:
            return tool_input
        return ""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("patch", "input"):
        value = tool_input.get(key)
        if isinstance(value, str) and (name == "apply_patch" or "*** Begin Patch" in value):
            return value
    old = tool_input.get("old_string")
    new = tool_input.get("new_string")
    file_path = tool_input.get("file_path") or tool_input.get("path") or "file"
    if isinstance(old, str) and isinstance(new, str):
        return "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
            )
        )
    if name.lower() == "write" and isinstance(tool_input.get("content"), str):
        content = tool_input["content"]
        preview = content[:12_000]
        return f"--- /dev/null\n+++ b/{file_path}\n" + "\n".join(
            f"+{line}" for line in preview.splitlines()
        )
    return ""


def _files(event: Dict[str, Any]) -> List[str]:
    payload = event.get("payload", {})
    files: List[str] = []
    if event.get("type") == "file_change" and payload.get("path"):
        files.append(str(payload["path"]))
    files.extend(str(path) for path in payload.get("files", []) if path)
    tool_input = payload.get("input")
    if isinstance(tool_input, dict):
        for key in ("file_path", "path"):
            if tool_input.get(key):
                files.append(str(tool_input[key]))
    return sorted(set(files))


def _command_text(payload: Dict[str, Any]) -> str:
    value = payload.get("command", "")
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _candidate_from_segment(
    user_event: Dict[str, Any],
    segment: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    trigger = str(user_event.get("payload", {}).get("text", ""))
    assistant_messages = [
        str(event.get("payload", {}).get("text", ""))
        for event in segment
        if event.get("type") == "message" and event.get("payload", {}).get("role") == "assistant"
    ]
    change_events = [
        event for event in segment if event.get("type") in {"file_change", "tool_call"} and _files(event)
    ]
    command_events = [event for event in segment if event.get("type") == "command"]
    verifications = []
    for event in command_events:
        payload = event.get("payload", {})
        command = _command_text(payload)
        if VERIFY_PATTERN.search(command):
            exit_code = payload.get("exit_code")
            verifications.append(
                {
                    "event_id": event.get("event_id"),
                    "command": _compact(command, 240),
                    "exit_code": exit_code,
                    "passed": exit_code == 0,
                    "output": _compact(payload.get("stdout") or payload.get("stderr") or "", 800),
                }
            )

    changes = []
    touched_files: Set[str] = set()
    for event in change_events:
        payload = event.get("payload", {})
        paths = _files(event)
        touched_files.update(paths)
        diff = payload.get("diff", "") if event.get("type") == "file_change" else _tool_diff(payload)
        changes.append(
            {
                "event_id": event.get("event_id"),
                "source": event.get("source"),
                "action": payload.get("change_type") or payload.get("name") or "change",
                "files": paths,
                "diff": diff,
                "lines_added": payload.get("lines_added"),
                "lines_removed": payload.get("lines_removed"),
            }
        )

    if verifications:
        status = "verified" if verifications[-1]["passed"] else "failed"
    elif changes:
        status = "unverified"
    else:
        status = "discussed"

    status_text = {
        "verified": "验证通过",
        "failed": "验证失败，仍需处理",
        "unverified": "代码已修改，但没有发现验证证据",
        "discussed": "只发现讨论，未发现代码修改",
    }[status]
    approach = _compact(assistant_messages[-1], 700) if assistant_messages else "未捕获到 Agent 的可见解释"
    evidence = [user_event.get("event_id")] + [event.get("event_id") for event in segment]

    return {
        "episode_id": stable_id("episode", user_event.get("event_id")),
        "title": _title(trigger),
        "kind": _classification(trigger),
        "status": status,
        "trigger": _compact(trigger, 1_200),
        "approach": approach,
        "outcome": status_text,
        "started_at": user_event.get("timestamp"),
        "ended_at": segment[-1].get("timestamp") if segment else user_event.get("timestamp"),
        "conversation_ids": sorted(
            {event.get("conversation_id") for event in [user_event, *segment] if event.get("conversation_id")}
        ),
        "sources": sorted({event.get("source") for event in [user_event, *segment] if event.get("source")}),
        "files": sorted(touched_files),
        "changes": changes,
        "verifications": verifications,
        "evidence_ids": [value for value in evidence if value],
        "related_episode_ids": [],
        "relation_confidence": None,
    }


def _attach_unscoped_file_events(
    candidates: List[Dict[str, Any]], events: Sequence[Dict[str, Any]]
) -> None:
    unscoped = [event for event in events if event.get("type") == "file_change" and not event.get("conversation_id")]
    for event in unscoped:
        moment = _timestamp(event.get("timestamp"))
        eligible = [
            candidate
            for candidate in candidates
            if _timestamp(candidate.get("started_at")) <= moment
            and (moment - _timestamp(candidate.get("started_at"))).total_seconds() <= 7_200
        ]
        if not eligible:
            continue
        candidate = max(eligible, key=lambda value: _timestamp(value.get("started_at")))
        payload = event.get("payload", {})
        path = payload.get("path")
        if path and path not in candidate["files"]:
            candidate["files"].append(path)
            candidate["files"].sort()
        candidate["changes"].append(
            {
                "event_id": event.get("event_id"),
                "source": "watcher",
                "action": payload.get("change_type", "change"),
                "files": [path] if path else [],
                "diff": payload.get("diff", ""),
                "lines_added": payload.get("lines_added"),
                "lines_removed": payload.get("lines_removed"),
            }
        )
        candidate["evidence_ids"].append(event.get("event_id"))
        if candidate["status"] == "discussed":
            candidate["status"] = "unverified"
            candidate["outcome"] = "代码已修改，但没有发现验证证据"


def _link_related(candidates: List[Dict[str, Any]]) -> None:
    for index, left in enumerate(candidates):
        left_words = _keywords(left["trigger"])
        left_files = {Path(path).name for path in left["files"]}
        for right in candidates[index + 1 :]:
            if set(left["conversation_ids"]) == set(right["conversation_ids"]):
                continue
            right_words = _keywords(right["trigger"])
            right_files = {Path(path).name for path in right["files"]}
            words_union = left_words | right_words
            word_score = len(left_words & right_words) / len(words_union) if words_union else 0.0
            file_score = len(left_files & right_files) / max(1, len(left_files | right_files))
            confidence = round((word_score * 0.65) + (file_score * 0.35), 2)
            if confidence >= 0.28 or (file_score >= 0.5 and word_score >= 0.12):
                left["related_episode_ids"].append(right["episode_id"])
                right["related_episode_ids"].append(left["episode_id"])
                left["relation_confidence"] = max(left.get("relation_confidence") or 0, confidence)
                right["relation_confidence"] = max(right.get("relation_confidence") or 0, confidence)


def build_episodes(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = sorted(events, key=lambda event: event.get("timestamp", ""))
    by_conversation: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in ordered:
        if event.get("conversation_id"):
            by_conversation[event["conversation_id"]].append(event)

    candidates: List[Dict[str, Any]] = []
    for conversation_events in by_conversation.values():
        user_indexes = [
            index
            for index, event in enumerate(conversation_events)
            if event.get("type") == "message" and event.get("payload", {}).get("role") == "user"
        ]
        for position, user_index in enumerate(user_indexes):
            end = user_indexes[position + 1] if position + 1 < len(user_indexes) else len(conversation_events)
            segment = conversation_events[user_index + 1 : end]
            candidates.append(_candidate_from_segment(conversation_events[user_index], segment))

    _attach_unscoped_file_events(candidates, ordered)
    candidates.sort(key=lambda episode: episode.get("started_at", ""))
    _link_related(candidates)
    return candidates
