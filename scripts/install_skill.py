"""Install the LumiForge Skill into the current user's Codex home."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def _runtime_python(root: Path) -> Path:
    candidates = [
        root / ".venv" / "bin" / "python",
        root / "venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists():
            # Resolving a venv symlink bypasses its site-packages on macOS.
            return candidate.absolute()
    raise RuntimeError("No Python runtime is available for LumiForge")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    source = root / "skills" / "lumiforge-review"
    codex_home = Path(os.getenv("CODEX_HOME", Path.home() / ".codex")).expanduser()
    target = codex_home / "skills" / "lumiforge-review"
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=target.parent) as directory:
        staged = Path(directory) / "lumiforge-review"
        shutil.copytree(source, staged)
        (staged / "runtime.json").write_text(
            json.dumps(
                {
                    "source_root": str(root),
                    "python": str(_runtime_python(root)),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        backup = target.with_name(target.name + ".previous")
        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            target.replace(backup)
        shutil.copytree(staged, target)

    print(f"Installed Skill: {target}")
    print("Restart Codex if the Skill does not appear immediately.")


if __name__ == "__main__":
    main()
