#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "Run setup first: python3 scripts/setup.py"
  exit 1
fi

DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/lumiforge-demo.XXXXXX")"
trap 'rm -rf "$DEMO_DIR"' EXIT

lf() {
  "$PYTHON" -m lumiforge.cli "$@" --path "$DEMO_DIR"
}

echo "Starting LumiForge evidence demo in $DEMO_DIR"
lf init --name "Greeting Demo"
lf start --goal "Build and verify a greeting function"
lf note --kind problem "The product needs a greeting function with a safe empty-name fallback"

cat > "$DEMO_DIR/greeting.py" <<'PY'
def greeting(name: str) -> str:
    clean_name = name.strip() or "there"
    return f"Hello, {clean_name}."
PY

cat > "$DEMO_DIR/test_greeting.py" <<'PY'
import unittest
from greeting import greeting

class GreetingTests(unittest.TestCase):
    def test_blank_name(self):
        self.assertEqual(greeting("   "), "Hello, there.")

if __name__ == "__main__":
    unittest.main()
PY

sleep 1
lf verify "python3 -m unittest -v"
lf close

echo "Demo review: $DEMO_DIR/.lumiforge/artifacts/project_review.html"
echo "The temporary demo is removed when this script exits."
