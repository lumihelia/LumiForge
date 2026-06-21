import json
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from lumiforge.adapters import ClaudeCodeAdapter, CodexAdapter
from lumiforge.builds import BuildHistoryStore, load_context
from lumiforge.cli import cli
from lumiforge.episodes import build_episodes
from lumiforge.report import generate_report
from lumiforge.storage import EventStore, ProjectStore
from lumiforge.watcher import ContentSnapshot


class ProjectLifecycleTests(unittest.TestCase):
    def test_project_run_supports_repeated_pause_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectStore(directory)
            project = store.ensure_project("Example")
            run = store.create_run("Build a useful prototype")

            self.assertTrue(project["project_id"].startswith("proj_"))
            self.assertEqual(run["status"], "running")
            self.assertEqual(store.pause_run()["status"], "paused")
            self.assertEqual(store.pause_run()["status"], "paused")
            self.assertEqual(store.resume_run()["status"], "running")
            self.assertEqual(store.resume_run()["status"], "running")

            closed = store.close_run()
            self.assertEqual(closed["status"], "closed")
            self.assertEqual(len(closed["recording_periods"]), 2)
            self.assertIsNone(store.get_current_run())


class EvidenceStoreTests(unittest.TestCase):
    def test_event_ids_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            events = EventStore(directory)
            value = {
                "event_id": "evt_fixed",
                "type": "message",
                "source": "test",
                "payload": {"role": "user", "text": "Fix login"},
            }
            self.assertTrue(events.append(value))
            self.assertFalse(events.append(value))
            self.assertEqual(len(events.read_all()), 1)


class ContentDiffTests(unittest.TestCase):
    def test_snapshot_records_actual_content_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app.py"
            target.write_text("value = 1\n", encoding="utf-8")
            snapshot = ContentSnapshot(root)

            target.write_text("value = 2\nprint(value)\n", encoding="utf-8")
            event = snapshot.capture("modified", target)

            self.assertIsNotNone(event)
            self.assertIn("-value = 1", event["diff"])
            self.assertIn("+value = 2", event["diff"])
            self.assertEqual(event["lines_added"], 2)
            self.assertEqual(event["lines_removed"], 1)

    def test_sensitive_files_keep_metadata_without_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = ContentSnapshot(root)
            target = root / ".env"
            target.write_text("SECRET=hidden\n", encoding="utf-8")
            event = snapshot.capture("created", target)

            self.assertFalse(event["content_captured"])
            self.assertEqual(event["capture_status"], "sensitive")
            self.assertEqual(event["diff"], "")


class AdapterTests(unittest.TestCase):
    def test_codex_adapter_builds_message_change_and_verification_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            project = Path(directory) / "project"
            project.mkdir()
            source = home / ".codex" / "sessions" / "2026" / "01" / "01" / "rollout.jsonl"
            source.parent.mkdir(parents=True)
            records = [
                {
                    "timestamp": "2026-01-01T10:00:00+00:00",
                    "type": "session_meta",
                    "payload": {"id": "conv-a", "cwd": str(project)},
                },
                {
                    "timestamp": "2026-01-01T10:01:00+00:00",
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": "登录按钮报错，请修复"},
                },
                {
                    "timestamp": "2026-01-01T10:02:00+00:00",
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "问题来自提交事件未绑定，我会修复处理器。"},
                },
                {
                    "timestamp": "2026-01-01T10:03:00+00:00",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "apply_patch",
                        "arguments": json.dumps(
                            {"patch": "*** Begin Patch\n*** Update File: app.py\n@@\n-old\n+new\n*** End Patch"}
                        ),
                    },
                },
                {
                    "timestamp": "2026-01-01T10:04:00+00:00",
                    "type": "event_msg",
                    "payload": {
                        "type": "exec_command_end",
                        "command": "python -m unittest",
                        "cwd": str(project),
                        "exit_code": 0,
                        "status": "completed",
                        "stdout": "OK",
                    },
                },
            ]
            source.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")

            adapter = CodexAdapter(home)
            self.assertTrue(adapter.matches_project(source, project))
            events = adapter.parse(source, [])
            episodes = build_episodes(events)

            self.assertEqual(len(episodes), 1)
            self.assertEqual(episodes[0]["status"], "verified")
            self.assertIn("app.py", episodes[0]["files"])
            self.assertIn("提交事件", episodes[0]["approach"])

    def test_claude_adapter_keeps_all_matching_conversations(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            project = Path(directory) / "project"
            project.mkdir()
            folder = home / ".claude" / "projects" / "project"
            folder.mkdir(parents=True)
            source = folder / "conversation.jsonl"
            records = [
                {
                    "timestamp": "2026-01-01T10:00:00+00:00",
                    "type": "user",
                    "cwd": str(project),
                    "sessionId": "claude-a",
                    "message": {"role": "user", "content": "实现设置页面"},
                },
                {
                    "timestamp": "2026-01-01T10:01:00+00:00",
                    "type": "assistant",
                    "cwd": str(project),
                    "sessionId": "claude-a",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "我会增加设置表单。"},
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {"file_path": "settings.py", "content": "enabled = True\n"},
                            },
                        ],
                    },
                },
            ]
            source.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")

            adapter = ClaudeCodeAdapter(home)
            self.assertTrue(adapter.matches_project(source, project))
            episodes = build_episodes(adapter.parse(source, []))
            self.assertEqual(len(episodes), 1)
            self.assertIn("settings.py", episodes[0]["files"])


class ReportTests(unittest.TestCase):
    def test_report_contains_project_views_and_episode_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectStore(directory)
            store.ensure_project("Evidence Project")
            events = EventStore(directory)
            base = {
                "conversation_id": "conv-1",
                "source": "test",
            }
            events.append(
                {
                    **base,
                    "event_id": "message-1",
                    "timestamp": "2026-01-01T10:00:00+00:00",
                    "type": "message",
                    "payload": {"role": "user", "text": "页面加载失败，请修复"},
                }
            )
            events.append(
                {
                    **base,
                    "event_id": "change-1",
                    "timestamp": "2026-01-01T10:01:00+00:00",
                    "type": "file_change",
                    "payload": {"change_type": "modified", "path": "app.py", "diff": "-old\n+new"},
                }
            )
            events.append(
                {
                    **base,
                    "event_id": "command-1",
                    "timestamp": "2026-01-01T10:02:00+00:00",
                    "type": "command",
                    "payload": {"command": "python -m unittest", "exit_code": 0, "stdout": "OK"},
                }
            )

            output = generate_report(directory)
            html = output.read_text(encoding="utf-8")
            self.assertIn("Project Observatory", html)
            self.assertIn("Cross-conversation journey", html)
            self.assertIn("页面加载失败", html)
            self.assertIn("-old\\n+new", html)


class BuildHistoryTests(unittest.TestCase):
    @staticmethod
    def context(title="Checkpoint"):
        return {
            "title": title,
            "goal": "Make the build understandable",
            "summary": "The Agent connected intent, changes, and verification.",
            "conversation_id": "conversation-visible",
            "decisions": [{"decision": "Use a Skill", "status": "accepted"}],
            "problems": [{"problem": "The CLI was inaccessible", "status": "resolved"}],
            "verification": [{"command": "python -m unittest", "status": "passed"}],
            "next_steps": [{"text": "Test across another conversation"}],
            "confidence_note": "Only visible evidence was used.",
        }

    @staticmethod
    def append_event(directory, event_id, text):
        EventStore(directory).append(
            {
                "event_id": event_id,
                "timestamp": f"2026-01-01T10:0{event_id[-1]}:00+00:00",
                "conversation_id": f"conversation-{event_id}",
                "source": "test",
                "type": "message",
                "payload": {"role": "user", "text": text},
            }
        )

    def test_checkpoints_and_releases_keep_version_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectStore(directory)
            store.ensure_project("Versioned Project")
            history = BuildHistoryStore(directory)

            self.append_event(directory, "event-1", "First version intent")
            first_checkpoint = history.create_checkpoint(self.context("First checkpoint"))
            self.append_event(directory, "event-2", "First version final fix")
            first_release = history.create_release("v0.3", self.context("Version 0.3"))

            self.append_event(directory, "event-3", "Second version intent")
            second_checkpoint = history.create_checkpoint(self.context("Second checkpoint"))
            second_release = history.create_release("v0.4", self.context("Version 0.4"))

            self.assertTrue(first_checkpoint.exists())
            self.assertTrue(second_checkpoint.exists())
            first = json.loads((first_release.parent / "record.json").read_text())
            second = json.loads((second_release.parent / "record.json").read_text())
            self.assertEqual(first["evidence_count"], 2)
            self.assertEqual(second["evidence_count"], 1)
            self.assertEqual(first["included_checkpoints"][0]["title"], "First checkpoint")
            self.assertEqual(second["included_checkpoints"][0]["title"], "Second checkpoint")
            self.assertNotEqual(first["version_id"], second["version_id"])
            self.assertEqual(first["record_status"], "unverified")
            self.assertTrue((Path(directory) / "build-history" / "index.html").exists())

    def test_release_rejects_unsafe_or_duplicate_version(self):
        with tempfile.TemporaryDirectory() as directory:
            history = BuildHistoryStore(directory)
            with self.assertRaises(ValueError):
                history.create_release("../private", self.context())
            history.create_release("v1", self.context())
            with self.assertRaises(ValueError):
                history.create_release("v1", self.context())

    def test_context_validation_and_json_cli_output(self):
        with tempfile.TemporaryDirectory() as directory:
            context_file = Path(directory) / "context.json"
            context_file.write_text(json.dumps(self.context()), encoding="utf-8")
            self.assertEqual(load_context(context_file)["title"], "Checkpoint")

            result = CliRunner().invoke(
                cli,
                [
                    "checkpoint",
                    "--path",
                    directory,
                    "--context-file",
                    str(context_file),
                    "--no-sync",
                    "--json-output",
                ],
            )
            self.assertEqual(result.exit_code, 0, result.output)
            output = json.loads(result.output)
            self.assertEqual(output["kind"], "checkpoint")
            self.assertTrue(Path(output["report"]).exists())

    def test_build_report_shows_agent_context_without_marking_it_as_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            report = BuildHistoryStore(directory).create_checkpoint(self.context("Visible context"))
            html = report.read_text(encoding="utf-8")
            self.assertIn("Visible context", html)
            self.assertIn("Checkpoint build record", html)
            record = json.loads((report.parent / "record.json").read_text())
            self.assertEqual(record["evidence_count"], 0)
            self.assertEqual(record["record_status"], "unverified")


if __name__ == "__main__":
    unittest.main()
