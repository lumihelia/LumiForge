# Quickstart

## 1. Install once

From the LumiForge repository:

```bash
python3 scripts/setup.py
python3 scripts/install_skill.py
```

Restart Codex if `lumiforge-review` does not appear immediately.

## 2. Create a checkpoint

Work with a Coding Agent normally. When you want to preserve the current stage, say:

> 用 LumiForge 整理当前对话。这个版本还没有完成。

The Skill creates a report under:

```text
build-history/checkpoints/<date-sequence>/report.html
```

## 3. Finalize a version

When the current version is complete, say:

> 用 LumiForge 汇总当前项目。这个版本已经完成，版本号是 v0.3。

The final report appears under:

```text
build-history/releases/v0.3/report.html
```

`build-history/index.html` links all local checkpoints and releases.

## Privacy

Generated reports can include conversations, diffs, local paths, and command output. `build-history/` is ignored by Git by default. Review a report before sharing it.
