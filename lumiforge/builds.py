"""Durable checkpoint and release records built from LumiForge evidence."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .episodes import build_episodes
from .report import generate_report
from .storage import EventStore, ProjectStore, utc_now


CONTEXT_LIST_FIELDS = ("decisions", "problems", "verification", "next_steps")
SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_context(path: str | Path) -> Dict[str, Any]:
    """Load and validate the context prepared by the invoking Agent."""
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise ValueError(f"Context file does not exist: {source}")
    try:
        value = _read_json(source)
    except json.JSONDecodeError as error:
        raise ValueError(f"Context file is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("Context must be a JSON object")

    context: Dict[str, Any] = {
        "title": _clean_text(value.get("title"), "Untitled build record"),
        "goal": _clean_text(value.get("goal"), ""),
        "summary": _clean_text(value.get("summary"), "No summary was provided."),
        "conversation_id": _clean_text(value.get("conversation_id"), ""),
        "confidence_note": _clean_text(value.get("confidence_note"), ""),
    }
    for field in CONTEXT_LIST_FIELDS:
        items = value.get(field, [])
        if not isinstance(items, list):
            raise ValueError(f"Context field '{field}' must be a list")
        context[field] = [_normalize_item(item) for item in items[:100]]
    return context


def _clean_text(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text[:40_000] if text else fallback


def _normalize_item(value: Any) -> Dict[str, str]:
    if isinstance(value, str):
        return {"text": _clean_text(value, "")}
    if not isinstance(value, dict):
        raise ValueError("Context list items must be strings or objects")
    return {
        str(key): _clean_text(item, "")
        for key, item in value.items()
        if item is not None and str(key).strip()
    }


@dataclass(frozen=True)
class BuildHistoryPaths:
    root: Path

    @property
    def history_dir(self) -> Path:
        return self.root / "build-history"

    @property
    def manifest_file(self) -> Path:
        return self.history_dir / "manifest.json"

    @property
    def index_file(self) -> Path:
        return self.history_dir / "index.html"

    @property
    def checkpoints_dir(self) -> Path:
        return self.history_dir / "checkpoints"

    @property
    def releases_dir(self) -> Path:
        return self.history_dir / "releases"


class BuildHistoryStore:
    """Creates human-readable build records while keeping raw evidence local."""

    def __init__(self, project_path: str | Path):
        self.project_store = ProjectStore(project_path)
        self.project = self.project_store.ensure_project()
        self.paths = BuildHistoryPaths(self.project_store.paths.root)

    def load_manifest(self) -> Dict[str, Any]:
        if self.paths.manifest_file.exists():
            manifest = _read_json(self.paths.manifest_file)
            if manifest.get("project_id") != self.project["project_id"]:
                raise ValueError("Build history belongs to a different LumiForge project")
            return manifest
        return {
            "schema_version": 1,
            "project_id": self.project["project_id"],
            "project_name": self.project["name"],
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "captured_event_ids": [],
            "active_version_id": None,
            "versions": [],
            "checkpoints": [],
            "releases": [],
        }

    def create_checkpoint(self, context: Dict[str, Any]) -> Path:
        manifest = self.load_manifest()
        all_events = EventStore(self.paths.root).read_all()
        captured = set(manifest.get("captured_event_ids", []))
        events = [event for event in all_events if event.get("event_id") not in captured]
        version_state = self._active_version(manifest, create=True)
        record_id = self._next_checkpoint_id(manifest)
        output_dir = self.paths.checkpoints_dir / record_id
        record = self._record(
            kind="checkpoint",
            record_id=record_id,
            context=context,
            events=events,
            evidence_scope="new_since_previous_checkpoint",
            version_id=version_state["version_id"],
        )
        self._write_record(output_dir, record, events)

        manifest["captured_event_ids"] = sorted(
            {event.get("event_id") for event in all_events if event.get("event_id")}
        )
        manifest["checkpoints"].append(self._manifest_entry(record, output_dir))
        version_state["checkpoint_ids"].append(record_id)
        self._save_manifest(manifest)
        return output_dir / "report.html"

    def create_release(self, version: str, context: Dict[str, Any]) -> Path:
        if not SAFE_VERSION.fullmatch(version):
            raise ValueError(
                "Version must use only letters, numbers, dots, underscores, and hyphens"
            )
        manifest = self.load_manifest()
        if any(item.get("version") == version for item in manifest.get("releases", [])):
            raise ValueError(f"Release '{version}' already exists")

        all_events = EventStore(self.paths.root).read_all()
        version_state = self._active_version(manifest, create=True)
        checkpoint_ids = set(version_state["checkpoint_ids"])
        checkpoint_entries = [
            item
            for item in manifest.get("checkpoints", [])
            if item.get("record_id") in checkpoint_ids
        ]
        checkpoint_records = [self._load_record(item) for item in checkpoint_entries]
        version_event_ids = {
            event_id
            for item in checkpoint_records
            for event_id in item.get("event_ids", [])
        }
        captured = set(manifest.get("captured_event_ids", []))
        version_event_ids.update(
            event.get("event_id")
            for event in all_events
            if event.get("event_id") not in captured
        )
        events = [
            event for event in all_events if event.get("event_id") in version_event_ids
        ]
        output_dir = self.paths.releases_dir / version
        record = self._record(
            kind="release",
            record_id=f"release-{version}",
            context=context,
            events=events,
            evidence_scope="current_version_evidence",
            version_id=version_state["version_id"],
        )
        record["version"] = version
        record["included_checkpoints"] = [
            {
                "record_id": item["record_id"],
                "title": item["title"],
                "summary": item["summary"],
                "created_at": item["created_at"],
                "report_href": f"../../checkpoints/{item['record_id']}/report.html",
            }
            for item in checkpoint_records
        ]
        self._write_record(output_dir, record, events)

        manifest["captured_event_ids"] = sorted(
            {event.get("event_id") for event in all_events if event.get("event_id")}
        )
        entry = self._manifest_entry(record, output_dir)
        entry["version"] = version
        manifest["releases"].append(entry)
        version_state["status"] = "finalized"
        version_state["version"] = version
        version_state["finalized_at"] = record["created_at"]
        version_state["release_record_id"] = record["record_id"]
        manifest["active_version_id"] = None
        self._save_manifest(manifest)
        return output_dir / "report.html"

    def _record(
        self,
        *,
        kind: str,
        record_id: str,
        context: Dict[str, Any],
        events: Sequence[Dict[str, Any]],
        evidence_scope: str,
        version_id: str,
    ) -> Dict[str, Any]:
        episodes = build_episodes(events)
        statuses = Counter(episode.get("status", "unverified") for episode in episodes)
        sources = sorted({event.get("source", "unknown") for event in events})
        conversations = sorted(
            {
                event.get("conversation_id")
                for event in events
                if event.get("conversation_id")
            }
        )
        if context.get("conversation_id"):
            conversations = sorted(set(conversations + [context["conversation_id"]]))
        if statuses.get("failed", 0):
            record_status = "failed"
        elif episodes and statuses.get("verified", 0) == len(episodes):
            record_status = "verified"
        else:
            record_status = "unverified"
        return {
            "schema_version": 1,
            "record_id": record_id,
            "kind": kind,
            "project_id": self.project["project_id"],
            "project_name": self.project["name"],
            "version_id": version_id,
            "created_at": utc_now(),
            "title": context["title"],
            "goal": context["goal"],
            "summary": context["summary"],
            "decisions": context["decisions"],
            "problems": context["problems"],
            "verification": context["verification"],
            "next_steps": context["next_steps"],
            "confidence_note": context["confidence_note"],
            "evidence_scope": evidence_scope,
            "record_status": record_status,
            "evidence_count": len(events),
            "episode_count": len(episodes),
            "verified_count": statuses.get("verified", 0),
            "failed_count": statuses.get("failed", 0),
            "unverified_count": statuses.get("unverified", 0),
            "sources": sources,
            "conversation_ids": conversations,
            "event_ids": [event.get("event_id") for event in events if event.get("event_id")],
        }

    def _write_record(
        self,
        output_dir: Path,
        record: Dict[str, Any],
        events: Sequence[Dict[str, Any]],
    ) -> None:
        if output_dir.exists():
            raise ValueError(f"Build record already exists: {output_dir}")
        staged = output_dir.with_name(f".{output_dir.name}.tmp-{uuid.uuid4().hex[:8]}")
        staged.mkdir(parents=True)
        try:
            _write_json(staged / "record.json", record)
            _write_json(
                staged / "evidence.json",
                {
                    "schema_version": 1,
                    "record_id": record["record_id"],
                    "events": list(events),
                },
            )
            (staged / "summary.md").write_text(
                _summary_markdown(record), encoding="utf-8"
            )
            generate_report(
                self.paths.root,
                output=staged / "report.html",
                events=events,
                build_record=record,
            )
            staged.replace(output_dir)
        except Exception:
            shutil.rmtree(staged, ignore_errors=True)
            raise

    def _next_checkpoint_id(self, manifest: Dict[str, Any]) -> str:
        prefix = datetime.now().strftime("%Y-%m-%d")
        existing = {
            item.get("record_id") for item in manifest.get("checkpoints", [])
        }
        sequence = 1
        while f"{prefix}-{sequence:03d}" in existing:
            sequence += 1
        return f"{prefix}-{sequence:03d}"

    def _active_version(
        self, manifest: Dict[str, Any], *, create: bool
    ) -> Dict[str, Any]:
        active_id = manifest.get("active_version_id")
        for item in manifest.get("versions", []):
            if item.get("version_id") == active_id and item.get("status") == "open":
                return item
        if not create:
            raise ValueError("No open version exists")
        state = {
            "version_id": f"version_{uuid.uuid4().hex[:12]}",
            "status": "open",
            "created_at": utc_now(),
            "checkpoint_ids": [],
        }
        manifest.setdefault("versions", []).append(state)
        manifest["active_version_id"] = state["version_id"]
        return state

    def _manifest_entry(self, record: Dict[str, Any], output_dir: Path) -> Dict[str, Any]:
        relative = output_dir.relative_to(self.paths.history_dir)
        return {
            "record_id": record["record_id"],
            "kind": record["kind"],
            "title": record["title"],
            "created_at": record["created_at"],
            "evidence_count": record["evidence_count"],
            "record_path": str(relative / "record.json"),
            "report_path": str(relative / "report.html"),
        }

    def _load_record(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        return _read_json(self.paths.history_dir / entry["record_path"])

    def _save_manifest(self, manifest: Dict[str, Any]) -> None:
        manifest["project_name"] = self.project["name"]
        manifest["updated_at"] = utc_now()
        _write_json(self.paths.manifest_file, manifest)
        self.paths.index_file.write_text(_history_index(manifest), encoding="utf-8")


def _item_text(item: Dict[str, str]) -> str:
    for key in ("text", "decision", "problem", "command", "result", "resolution"):
        if item.get(key):
            return item[key]
    return " · ".join(value for value in item.values() if value)


def _summary_markdown(record: Dict[str, Any]) -> str:
    kind = "阶段记录" if record["kind"] == "checkpoint" else "版本报告"
    lines = [
        f"# {record['title']}",
        "",
        f"- 类型：{kind}",
        f"- 生成时间：{record['created_at']}",
        f"- 项目：{record['project_name']}",
        f"- 证据：{record['evidence_count']} 条事件，{record['episode_count']} 个 Episode",
        f"- 总体状态：{record['record_status']}",
        "",
        "## 当前结论",
        "",
        record["summary"],
    ]
    if record.get("goal"):
        lines.extend(["", "## 本阶段目标", "", record["goal"]])
    for field, heading in (
        ("decisions", "关键决策"),
        ("problems", "问题与处理"),
        ("verification", "验证"),
        ("next_steps", "下一步"),
    ):
        items = record.get(field, [])
        if items:
            lines.extend(["", f"## {heading}", ""])
            lines.extend(f"- {_item_text(item)}" for item in items)
    if record.get("confidence_note"):
        lines.extend(["", "## 可信度说明", "", record["confidence_note"]])
    if record.get("included_checkpoints"):
        lines.extend(["", "## 包含的阶段记录", ""])
        lines.extend(
            f"- [{item['title']}]({item['report_href']})"
            for item in record["included_checkpoints"]
        )
    lines.extend(
        [
            "",
            "## 证据状态",
            "",
            f"- 已验证：{record['verified_count']}",
            f"- 验证失败：{record['failed_count']}",
            f"- 尚未验证：{record['unverified_count']}",
            "",
        ]
    )
    return "\n".join(lines)


def _history_index(manifest: Dict[str, Any]) -> str:
    def rows(items: Iterable[Dict[str, Any]]) -> str:
        values = []
        for item in reversed(list(items)):
            values.append(
                "<li><a href=\"{}\"><strong>{}</strong><span>{} · {} 条证据</span></a></li>".format(
                    escape(item["report_path"]),
                    escape(item["title"]),
                    escape(item["created_at"][:16].replace("T", " ")),
                    item["evidence_count"],
                )
            )
        return "".join(values) or "<li class=\"empty\">还没有记录。</li>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(manifest['project_name'])} · Build History</title>
  <style>
    :root {{ --text:#2c2c2c; --primary:#4c436f; --paper:#f4f3f7; --line:#d4d0e0; --muted:#756f88; --white:#fff; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--text); background:linear-gradient(145deg,#faf9fc,var(--paper)); font-family:"Source Serif Pro",Georgia,serif; line-height:1.7; }}
    main {{ width:min(880px,calc(100% - 32px)); margin:0 auto; padding:64px 0 96px; }}
    h1,h2 {{ font-family:"Playfair Display",Georgia,serif; font-weight:600; }}
    h1 {{ margin:0; font-size:clamp(40px,8vw,72px); line-height:1; letter-spacing:-.04em; }}
    .dek {{ max-width:68ch; margin:24px 0 64px; color:var(--muted); }}
    section {{ margin-top:64px; }}
    ul {{ list-style:none; margin:24px 0 0; padding:0; border-top:1px solid var(--line); }}
    li {{ border-bottom:1px solid var(--line); }}
    a {{ display:flex; justify-content:space-between; gap:24px; padding:24px 0; color:inherit; text-decoration:none; }}
    a:hover strong,a:focus-visible strong {{ color:var(--primary); }}
    a:focus-visible {{ outline:3px solid var(--primary); outline-offset:8px; }}
    span,.empty {{ color:var(--muted); }}
    @media(max-width:600px) {{ main {{ padding-top:48px; }} a {{ display:block; }} a span {{ display:block; margin-top:8px; }} }}
  </style>
</head>
<body><main>
  <p>LumiForge · Project Build History</p>
  <h1>{escape(manifest['project_name'])}</h1>
  <p class="dek">阶段记录保存每一次构建的上下文与证据；版本报告把这些阶段重新连接成完整的产品演化路径。</p>
  <section><h2>版本报告</h2><ul>{rows(manifest.get('releases', []))}</ul></section>
  <section><h2>阶段记录</h2><ul>{rows(manifest.get('checkpoints', []))}</ul></section>
</main></body></html>"""
