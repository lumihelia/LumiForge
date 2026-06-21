# Usage

## Natural-language workflow

Use `lumiforge-review` through a Coding Agent. The Skill has two modes:

- `checkpoint`: the current version is unfinished.
- `finalize`: the user explicitly confirms the current version is complete.

If completion is unclear, the Skill asks one question instead of guessing.

## Build Record context

The Skill prepares a temporary JSON document containing the visible goal, summary, decisions, problems, verification, and next steps. The schema is documented in `skills/lumiforge-review/references/context-schema.md`.

Agent interpretation and raw evidence remain separate. A statement in the context file does not become verified evidence unless tests, commands, diffs, or other observable events support it.

## Developer commands

Create a stage record:

```bash
lumiforge checkpoint \
  --path /path/to/project \
  --context-file /tmp/lumiforge-context.json \
  --json-output
```

Finalize the active version:

```bash
lumiforge finalize \
  --version v0.3 \
  --path /path/to/project \
  --context-file /tmp/lumiforge-context.json \
  --json-output
```

Use `--no-sync` to generate a record from evidence already present in `.lumiforge/events.jsonl`.

## Optional continuous evidence capture

```bash
lumiforge init --name "My Product"
lumiforge start --goal "Complete a demonstrable login flow"
lumiforge pause
lumiforge resume
lumiforge close
```

The recorder runs in the background. These commands are optional when using the Skill; conversation adapters and Agent-prepared context can still produce a Build Record without an active recorder.

## Manual evidence

```bash
lumiforge note --kind problem "The submit button does not respond"
lumiforge verify "python -m unittest -v"
```

Only pass trusted verification commands. `verify` executes the supplied command in the project directory.

## Local files

```text
.lumiforge/                  raw local evidence and recorder state
build-history/manifest.json  active and finalized version boundaries
build-history/index.html     local report index
build-history/checkpoints/   stage records
build-history/releases/      finalized version reports
```

Both `.lumiforge/` and `build-history/` are private by default and ignored by Git.
