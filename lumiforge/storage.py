"""Persistent project, run, and evidence storage for LumiForge."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def _read_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def data_dir(self) -> Path:
        return self.root / ".lumiforge"

    @property
    def project_file(self) -> Path:
        return self.data_dir / "project.json"

    @property
    def events_file(self) -> Path:
        return self.data_dir / "events.jsonl"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def current_run_file(self) -> Path:
        return self.data_dir / "current_run.txt"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def report_file(self) -> Path:
        return self.artifacts_dir / "project_review.html"

    @property
    def pid_file(self) -> Path:
        return self.data_dir / "recorder.pid.json"

    @property
    def recorder_log(self) -> Path:
        return self.data_dir / "recorder.log"


class ProjectStore:
    """Owns the durable project identity and Project Run lifecycle."""

    def __init__(self, project_path: str | Path):
        self.paths = ProjectPaths(Path(project_path).expanduser().resolve())

    def ensure_project(self, name: Optional[str] = None) -> Dict[str, Any]:
        paths = self.paths
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        paths.runs_dir.mkdir(parents=True, exist_ok=True)
        paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
        paths.events_file.touch(exist_ok=True)

        if paths.project_file.exists():
            project = _read_json(paths.project_file)
            if name and project.get("name") != name:
                project["name"] = name
                project["updated_at"] = utc_now()
                _write_json(paths.project_file, project)
            return project

        project = {
            "schema_version": 2,
            "project_id": f"proj_{uuid.uuid4().hex[:16]}",
            "name": name or paths.root.name,
            "root": str(paths.root),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        _write_json(paths.project_file, project)
        return project

    def load_project(self) -> Dict[str, Any]:
        return self.ensure_project()

    def _run_file(self, run_id: str) -> Path:
        return self.paths.runs_dir / f"{run_id}.json"

    def save_run(self, run: Dict[str, Any]) -> None:
        run["updated_at"] = utc_now()
        _write_json(self._run_file(run["run_id"]), run)

    def load_run(self, run_id: str) -> Dict[str, Any]:
        path = self._run_file(run_id)
        if not path.exists():
            raise FileNotFoundError(f"Project Run '{run_id}' not found")
        return _read_json(path)

    def list_runs(self) -> List[Dict[str, Any]]:
        runs = []
        for path in sorted(self.paths.runs_dir.glob("run_*.json")):
            try:
                runs.append(_read_json(path))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(runs, key=lambda item: item.get("created_at", ""))

    def get_current_run(self) -> Optional[Dict[str, Any]]:
        marker = self.paths.current_run_file
        if marker.exists():
            run_id = marker.read_text(encoding="utf-8").strip()
            if run_id:
                try:
                    return self.load_run(run_id)
                except FileNotFoundError:
                    marker.unlink(missing_ok=True)

        unfinished = [run for run in self.list_runs() if run.get("status") != "closed"]
        return unfinished[-1] if unfinished else None

    def latest_run(self) -> Optional[Dict[str, Any]]:
        runs = self.list_runs()
        return runs[-1] if runs else None

    def create_run(self, goal: Optional[str] = None) -> Dict[str, Any]:
        current = self.get_current_run()
        if current and current.get("status") != "closed":
            raise RuntimeError(
                f"Project Run '{current['run_id']}' is {current['status']}; "
                "close it before starting another"
            )

        now = utc_now()
        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        project = self.ensure_project()
        run = {
            "schema_version": 2,
            "run_id": run_id,
            "project_id": project["project_id"],
            "goal": goal or "",
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
            "recording_periods": [{"started_at": now, "ended_at": None}],
        }
        self.save_run(run)
        self.paths.current_run_file.write_text(run_id, encoding="utf-8")
        return run

    def pause_run(self) -> Dict[str, Any]:
        run = self._require_current()
        if run["status"] == "paused":
            return run
        if run["status"] == "closed":
            raise RuntimeError("Closed Project Runs cannot be paused")
        now = utc_now()
        self._close_open_period(run, now)
        run["status"] = "paused"
        self.save_run(run)
        return run

    def resume_run(self) -> Dict[str, Any]:
        run = self._require_current()
        if run["status"] == "running":
            return run
        if run["status"] == "closed":
            raise RuntimeError("Closed Project Runs cannot be resumed")
        now = utc_now()
        run["recording_periods"].append({"started_at": now, "ended_at": None})
        run["status"] = "running"
        self.save_run(run)
        return run

    def close_run(self) -> Dict[str, Any]:
        run = self._require_current()
        if run["status"] == "closed":
            return run
        now = utc_now()
        self._close_open_period(run, now)
        run["status"] = "closed"
        run["closed_at"] = now
        self.save_run(run)
        self.paths.current_run_file.unlink(missing_ok=True)
        return run

    def _require_current(self) -> Dict[str, Any]:
        run = self.get_current_run()
        if not run:
            raise RuntimeError("No open Project Run found")
        return run

    @staticmethod
    def _close_open_period(run: Dict[str, Any], ended_at: str) -> None:
        periods = run.setdefault("recording_periods", [])
        if periods and periods[-1].get("ended_at") is None:
            periods[-1]["ended_at"] = ended_at


class EventStore:
    """Append-only evidence ledger with deterministic deduplication."""

    def __init__(self, project_path: str | Path):
        self.project_store = ProjectStore(project_path)
        self.project = self.project_store.ensure_project()
        self.path = self.project_store.paths.events_file
        self._known_ids: Optional[set[str]] = None

    def append(self, event: Dict[str, Any]) -> bool:
        value = dict(event)
        value.setdefault("timestamp", utc_now())
        value.setdefault("ingested_at", utc_now())
        value.setdefault("project_id", self.project["project_id"])
        value.setdefault("source", "lumiforge")
        value.setdefault("evidence_level", "direct")
        value.setdefault(
            "event_id",
            stable_id(
                value.get("source"),
                value.get("conversation_id"),
                value.get("timestamp"),
                value.get("type"),
                json.dumps(value.get("payload", {}), ensure_ascii=False, sort_keys=True),
            ),
        )
        if value["event_id"] in self.known_ids:
            return False
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(encoded)
                handle.flush()
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self.known_ids.add(value["event_id"])
        return True

    @property
    def known_ids(self) -> set[str]:
        if self._known_ids is None:
            self._known_ids = {
                event.get("event_id", "") for event in self.read_all() if event.get("event_id")
            }
        return self._known_ids

    def read_all(self) -> List[Dict[str, Any]]:
        events = []
        if not self.path.exists():
            return events
        with open(self.path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    if line.strip():
                        events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return sorted(events, key=lambda event: event.get("timestamp", ""))

    def append_lifecycle(self, action: str, run: Dict[str, Any]) -> bool:
        return self.append(
            {
                "run_id": run["run_id"],
                "type": "lifecycle",
                "payload": {"action": action, "status": run["status"], "goal": run.get("goal", "")},
            }
        )

    def extend(self, events: Iterable[Dict[str, Any]]) -> int:
        return sum(1 for event in events if self.append(event))
