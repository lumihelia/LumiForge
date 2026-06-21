#!/usr/bin/env python3
"""Run the canonical LumiForge engine from an installed or source Skill."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _runtime() -> tuple[str, Path | None]:
    skill_root = Path(__file__).resolve().parent.parent
    config_file = skill_root / "runtime.json"
    if config_file.exists():
        value = json.loads(config_file.read_text(encoding="utf-8"))
        return value.get("python", sys.executable), Path(value["source_root"])

    source_root = Path(__file__).resolve().parents[3]
    if (source_root / "lumiforge" / "__init__.py").exists():
        for candidate in (
            source_root / ".venv" / "bin" / "python",
            source_root / "venv" / "bin" / "python",
            Path(sys.executable),
        ):
            if candidate.exists():
                return str(candidate.absolute()), source_root
    return sys.executable, None


def main() -> int:
    python, source_root = _runtime()
    environment = os.environ.copy()
    if source_root is not None:
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = str(source_root) + (os.pathsep + existing if existing else "")
    result = subprocess.run(
        [python, "-m", "lumiforge.cli", *sys.argv[1:]], env=environment
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
