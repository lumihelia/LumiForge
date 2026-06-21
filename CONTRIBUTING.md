# Contributing to LumiForge

LumiForge is an opinionated, local-first Build Record system. Contributions should preserve one invariant: every displayed engineering claim must be traceable to observable evidence or clearly labeled as Agent interpretation.

## Setup

```bash
python3 scripts/setup.py
./run.sh --version
python3 scripts/install_skill.py
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

New behavior requires tests. Adapter fixtures must use synthetic messages and must not include real conversation data or credentials.

## Change checklist

- Preserve append-only source evidence.
- Keep the Skill thin; deterministic record logic belongs in the Python package.
- Keep finalized version evidence isolated from future versions.
- Label inferred relationships; do not convert confidence into fact.
- Never capture `.env`, private keys, certificates, or credential contents.
- Keep pause/resume repeatable and free of structural meaning.
- Make adapter failures non-blocking.
- Verify keyboard access, focus indicators, mobile layout, and reduced motion for report changes.
- Update README, QUICKSTART, USAGE, and ARCHITECTURE when commands or models change.

## Code style

- Python 3.10+ with type annotations.
- Small modules with explicit responsibilities.
- Standard-library solutions before new infrastructure.
- No external AI dependency in the deterministic evidence pipeline.

## Commit examples

```text
feat(adapter): import all project-scoped Codex conversations
fix(runtime): keep paused Project Runs discoverable
test(report): cover evidence-backed verification status
```
