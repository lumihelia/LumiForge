#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "LumiForge is not set up. Run: python3 scripts/setup.py"
  exit 1
fi

exec "$PYTHON" -m lumiforge.cli "$@"
