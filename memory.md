# Project Memory

## Current State

LumiForge v0.3.0 is a local, single-user Alpha MVP. The product is now Skill-first: a nontechnical user asks an Agent to create either a checkpoint or a finalized version report, while the CLI remains deterministic infrastructure.

## Completed

- Evidence ledger, background content recorder, Codex and Claude Code adapters, Change Episodes, and offline HTML Project Review.
- `lumiforge-review` Skill with checkpoint and finalize modes.
- Version-scoped `build-history/` records with manifest, local index, Markdown, JSON evidence, and HTML reports.
- Skill installer and runtime bridge that prefer the project's Python environment.

## Decisions

- The user never has to operate the CLI; developers may still use it directly.
- Only the user may declare a version complete.
- Agent-prepared conversation context remains distinct from direct engineering evidence.
- Finalized versions are sealed; later checkpoints automatically start a new version boundary.
- Raw evidence and generated Build Records are private and Git-ignored by default.
- MIT is the selected license for low-friction use and modification.

## Verification

- 11 unit tests pass under `venv/bin/python`.
- Skill structure validation passes.
- One-command setup, editable installation, wheel build, isolated Skill installation, installed Skill validation, and runtime bridge smoke tests pass.
- Independent Agent forward testing created an unfinished checkpoint and then finalized it as v0.1 through the installed Skill. Both modes preserved the unverified state; a runtime-selection issue found in the first pass was fixed and the clean reruns passed.

## Known Issues

- The Alpha MVP still needs real use across at least three separate conversations and one finalized version.
- Adapter compatibility depends on local Codex and Claude Code history formats.
- Build Record HTML can contain private conversation text, paths, diffs, and command output.
- The v0.3 report additions have not been visually checked because the browser security policy blocked local `file://` navigation. Content and responsive structure are covered only by automated checks and code review.
- Architecture audit is overdue at Alpha MVP stage. It was surfaced on 2026-06-21 and has not been run.

## Architecture Audits

No `architecture-due-diligence` audit has been run.

## Next Steps

- Complete browser checks for checkpoint, release, empty, and mobile report states.
- Publish the curated source repository to GitHub.
- Validate the full three-conversation checkpoint-to-release workflow in real project use.
