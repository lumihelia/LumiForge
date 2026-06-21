"""One-command local development setup for LumiForge."""

from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    environment = root / ".venv"
    python = environment / "bin" / "python"

    if not python.exists():
        print(f"Creating virtual environment: {environment}")
        venv.EnvBuilder(with_pip=True).create(environment)

    print("Installing Python build tools...")
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "setuptools>=68",
            "wheel",
        ],
        check=True,
    )

    print("Installing LumiForge in editable mode...")
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-e",
            str(root),
            "--no-build-isolation",
        ],
        check=True,
    )
    print("\nSetup complete.")
    print(f"Verify: {root / 'run.sh'} --version")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(f"Setup failed with exit code {error.returncode}", file=sys.stderr)
        raise SystemExit(error.returncode)
