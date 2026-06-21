"""Recorder process lifecycle management."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .storage import ProjectStore, utc_now


def _read_pid(project_path: str | Path) -> Optional[Dict[str, Any]]:
    path = ProjectStore(project_path).paths.pid_file
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def recorder_status(project_path: str | Path) -> Optional[Dict[str, Any]]:
    info = _read_pid(project_path)
    if not info:
        return None
    if process_is_alive(int(info["pid"])):
        return info
    ProjectStore(project_path).paths.pid_file.unlink(missing_ok=True)
    return None


def start_recorder(project_path: str | Path, run_id: str) -> Dict[str, Any]:
    store = ProjectStore(project_path)
    paths = store.paths
    existing = recorder_status(project_path)
    if existing:
        if existing.get("run_id") == run_id:
            return existing
        raise RuntimeError(f"Recorder already running for {existing.get('run_id')}")

    paths.data_dir.mkdir(parents=True, exist_ok=True)
    log_handle = open(paths.recorder_log, "a", encoding="utf-8")
    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parent.parent)
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        source_root if not existing_pythonpath else source_root + os.pathsep + existing_pythonpath
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "lumiforge.recorder",
            "--path",
            str(paths.root),
            "--run-id",
            run_id,
        ],
        cwd=str(paths.root),
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=log_handle,
        env=environment,
        start_new_session=True,
        close_fds=True,
    )
    log_handle.close()
    info = {"pid": process.pid, "run_id": run_id, "started_at": utc_now()}
    paths.pid_file.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")

    time.sleep(0.15)
    if process.poll() is not None:
        paths.pid_file.unlink(missing_ok=True)
        raise RuntimeError(f"Recorder failed to start; inspect {paths.recorder_log}")
    return info


def stop_recorder(project_path: str | Path, timeout: float = 5.0) -> bool:
    store = ProjectStore(project_path)
    info = _read_pid(project_path)
    if not info:
        return False
    pid = int(info["pid"])
    if process_is_alive(pid):
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + timeout
        while time.time() < deadline and process_is_alive(pid):
            time.sleep(0.05)
        if process_is_alive(pid):
            os.kill(pid, signal.SIGKILL)
    store.paths.pid_file.unlink(missing_ok=True)
    return True
