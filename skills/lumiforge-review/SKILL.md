---
name: lumiforge-review
description: Turn the current Coding Agent conversation and project evidence into a durable, visual LumiForge Build Record. Use when the user asks to整理、回顾、记录、汇总、可视化, or explain what happened in the current project conversation; when they want a stage/checkpoint report for unfinished work; or when they declare a product version complete and want a final version report that aggregates earlier Build Records.
---

# LumiForge Review

Create evidence-backed project memory without asking the user to operate a terminal. Treat the Skill as the interaction layer and the LumiForge Python package as deterministic infrastructure.

## Choose One Mode

Use `checkpoint` when the user says the work or version is unfinished, ongoing, paused, or at a milestone.

Use `finalize` only when the user explicitly says the current version is complete. Do not infer completion from passing tests or a clean worktree. If completion is ambiguous, ask exactly one short question: “这是阶段记录，还是当前版本已经完成？”

Treat “version complete” as the boundary. Do not ask whether the entire software project is permanently finished.

## Build The Record

1. Identify the project root from the current workspace. Never record an incidental directory as a project.
2. Read the visible current conversation, relevant workspace changes, and existing `build-history/manifest.json` when present.
3. Separate direct evidence from Agent interpretation. Never claim access to hidden reasoning.
4. For a final version, run the smallest relevant verification if none has been recorded and running it is safe. For a checkpoint, preserve existing verification state without inventing proof.
5. Create a context JSON file that follows [references/context-schema.md](references/context-schema.md). Keep it outside source control, preferably under the system temporary directory.
6. Run the deterministic bridge:

```bash
python3 <skill-root>/scripts/run_lumiforge.py checkpoint --path <project-root> --context-file <context.json> --json-output
```

For a completed version:

```bash
python3 <skill-root>/scripts/run_lumiforge.py finalize --version <version> --path <project-root> --context-file <context.json> --json-output
```

7. Delete the temporary context file after the command completes.
8. Confirm that the returned `report` and `history` files exist. Report the mode, output path, evidence count when available, verification state, and any sync warning.

## Evidence Rules

- Base “what changed” on diffs, tool calls, commands, tests, and visible Agent messages.
- Record user requests as intent, not as completed outcomes.
- Record Agent explanations as visible approach, not private reasoning.
- Mark unverified work as unverified even when the implementation looks plausible.
- Preserve failed attempts when they explain a later decision.
- Do not include secrets, credentials, raw private tokens, or sensitive file contents.
- Warn before the user publishes `build-history/`; reports can contain conversations, diffs, paths, and command output.

## Output Contract

A checkpoint creates:

```text
build-history/checkpoints/<date-sequence>/
  record.json
  summary.md
  evidence.json
  report.html
```

A finalized version creates:

```text
build-history/releases/<version>/
  record.json
  summary.md
  evidence.json
  report.html
```

Both modes update `build-history/manifest.json` and `build-history/index.html`. A release includes only the active version’s checkpoints and then seals that version boundary. The next checkpoint starts a new version automatically.

## Failure Handling

If conversation sync is unavailable, continue with current local evidence and surface the warning. If the context schema is invalid, correct the context file and retry. Never overwrite an existing release or silently merge records across finalized versions.
