"""LumiForge command-line interface."""

from __future__ import annotations

import json
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Optional

import click

from . import __version__
from .adapters import sync_conversations
from .builds import BuildHistoryStore, load_context
from .report import generate_report
from .runtime import recorder_status, start_recorder, stop_recorder
from .storage import EventStore, ProjectStore, utc_now


def _root(path: str) -> Path:
    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise click.ClickException(f"Project directory does not exist: {root}")
    return root


def _echo_run(run, prefix: str) -> None:
    click.echo(f"{prefix} {run['run_id']}")
    click.echo(f"   Status: {run['status']}")
    if run.get("goal"):
        click.echo(f"   Goal: {run['goal']}")


def _sync_for_record(root: Path, context: dict) -> tuple[dict, Optional[str]]:
    try:
        return sync_conversations(root), None
    except Exception as error:  # A local adapter failure must not destroy the record.
        warning = f"Conversation sync was unavailable: {error}"
        existing = context.get("confidence_note", "")
        context["confidence_note"] = f"{existing}\n{warning}".strip()
        return {"codex": 0, "claude": 0, "files": 0, "events": 0}, warning


@click.group()
@click.version_option(version=__version__)
def cli() -> None:
    """Turn Coding Agent activity into evidence-backed Build Records."""


@cli.command("init")
@click.option("--name", help="Human-readable project name")
@click.option("--path", default=".", help="Project directory")
def init_project(name: Optional[str], path: str) -> None:
    """Create a stable local project identity."""
    root = _root(path)
    project = ProjectStore(root).ensure_project(name=name)
    click.echo(f"Initialized LumiForge project: {project['name']}")
    click.echo(f"   Project ID: {project['project_id']}")
    click.echo(f"   Data: {root / '.lumiforge'}")


@cli.command()
@click.option("--goal", help="What this Project Run should make true")
@click.option("--phase", help="Deprecated alias for --goal")
@click.option("--path", default=".", help="Project directory")
def start(goal: Optional[str], phase: Optional[str], path: str) -> None:
    """Start a Project Run and return immediately."""
    root = _root(path)
    store = ProjectStore(root)
    store.ensure_project()
    current = store.get_current_run()
    if current:
        if current["status"] == "running":
            info = recorder_status(root)
            if not info:
                start_recorder(root, current["run_id"])
            _echo_run(current, "Already recording:")
            return
        raise click.ClickException(
            f"Project Run '{current['run_id']}' is paused. Use 'lumiforge resume'."
        )

    run = store.create_run(goal=goal or phase)
    events = EventStore(root)
    events.append_lifecycle("started", run)
    try:
        process = start_recorder(root, run["run_id"])
    except Exception:
        store.pause_run()
        raise
    _echo_run(run, "Recording started:")
    click.echo(f"   Recorder PID: {process['pid']}")
    click.echo("   You can close this terminal. LumiForge keeps recording locally.")


@cli.command()
@click.option("--path", default=".", help="Project directory")
def pause(path: str) -> None:
    """Pause recording without ending the Project Run."""
    root = _root(path)
    store = ProjectStore(root)
    run = store.get_current_run()
    if not run:
        raise click.ClickException("No open Project Run found")
    if run["status"] == "paused":
        _echo_run(run, "Already paused:")
        return
    stop_recorder(root)
    run = store.pause_run()
    EventStore(root).append_lifecycle("paused", run)
    _echo_run(run, "Recording paused:")


@cli.command()
@click.option("--path", default=".", help="Project directory")
def resume(path: str) -> None:
    """Resume a paused Project Run."""
    root = _root(path)
    store = ProjectStore(root)
    run = store.get_current_run()
    if not run:
        raise click.ClickException("No paused Project Run found")
    if run["status"] == "running":
        if not recorder_status(root):
            start_recorder(root, run["run_id"])
        _echo_run(run, "Already recording:")
        return
    run = store.resume_run()
    try:
        process = start_recorder(root, run["run_id"])
    except Exception:
        store.pause_run()
        raise
    EventStore(root).append_lifecycle("resumed", run)
    _echo_run(run, "Recording resumed:")
    click.echo(f"   Recorder PID: {process['pid']}")


def _close_project(path: str, open_report: bool = False) -> Path:
    root = _root(path)
    store = ProjectStore(root)
    run = store.get_current_run()
    if not run:
        latest = store.latest_run()
        if latest and latest.get("status") == "closed":
            output = generate_report(root)
            click.echo(f"No open Project Run. Refreshed existing report: {output}")
            return output
        raise click.ClickException("No open Project Run found")

    stop_recorder(root)
    run = store.close_run()
    EventStore(root).append_lifecycle("closed", run)
    click.echo("Synchronizing project conversations...")
    stats = sync_conversations(root)
    output = generate_report(root)
    _echo_run(run, "Project Run closed:")
    click.echo(
        f"   Imported: {stats['events']} new events from {stats['files']} conversation files"
    )
    click.echo(f"   Review: {output}")
    if open_report:
        webbrowser.open(output.as_uri())
    return output


@cli.command(name="close")
@click.option("--path", default=".", help="Project directory")
@click.option("--open", "open_report", is_flag=True, help="Open the generated report")
def close_command(path: str, open_report: bool) -> None:
    """Close the Project Run, sync conversations, and build the review."""
    _close_project(path, open_report)


@cli.command(name="stop")
@click.option("--path", default=".", help="Project directory")
def stop_command(path: str) -> None:
    """Compatibility alias for 'lumiforge close'."""
    _close_project(path)


@cli.command()
@click.option("--path", default=".", help="Project directory")
def status(path: str) -> None:
    """Show project identity, Project Run state, and evidence totals."""
    root = _root(path)
    store = ProjectStore(root)
    project = store.ensure_project()
    run = store.get_current_run() or store.latest_run()
    process = recorder_status(root)
    events = EventStore(root).read_all()
    click.echo(f"Project: {project['name']} ({project['project_id']})")
    click.echo(f"Root: {root}")
    if run:
        _echo_run(run, "Run:")
    else:
        click.echo("Run: none")
    click.echo(f"Recorder: {'running (PID ' + str(process['pid']) + ')' if process else 'stopped'}")
    click.echo(f"Evidence events: {len(events)}")
    click.echo(f"Report: {store.paths.report_file if store.paths.report_file.exists() else 'not generated'}")


@cli.command()
@click.option("--path", default=".", help="Project directory")
def sync(path: str) -> None:
    """Import all Codex and Claude Code conversations for this project."""
    root = _root(path)
    stats = sync_conversations(root)
    click.echo(
        f"Imported {stats['events']} new events from {stats['files']} project conversations "
        f"(Codex {stats['codex']}, Claude {stats['claude']})."
    )


@cli.command()
@click.option("--path", default=".", help="Project directory")
@click.option("--no-sync", is_flag=True, help="Generate from existing evidence only")
@click.option("--open", "open_report", is_flag=True, help="Open the generated report")
def review(path: str, no_sync: bool, open_report: bool) -> None:
    """Generate the local project observatory report."""
    root = _root(path)
    if not no_sync:
        stats = sync_conversations(root)
        click.echo(f"Conversation sync: {stats['events']} new events")
    output = generate_report(root)
    click.echo(f"Project review generated: {output}")
    if open_report:
        webbrowser.open(output.as_uri())


@cli.command()
@click.option(
    "--context-file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Structured context prepared by the invoking Agent",
)
@click.option("--path", default=".", help="Project directory")
@click.option("--no-sync", is_flag=True, help="Use existing evidence only")
@click.option("--json-output", is_flag=True, help="Print a machine-readable result")
def checkpoint(context_file: Path, path: str, no_sync: bool, json_output: bool) -> None:
    """Create a stage Build Record from new project evidence."""
    root = _root(path)
    try:
        context = load_context(context_file)
        stats, warning = (
            ({"codex": 0, "claude": 0, "files": 0, "events": 0}, None)
            if no_sync
            else _sync_for_record(root, context)
        )
        output = BuildHistoryStore(root).create_checkpoint(context)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    result = {
        "status": "created",
        "kind": "checkpoint",
        "report": str(output),
        "history": str(output.parents[2] / "index.html"),
        "sync": stats,
        "warning": warning,
    }
    if json_output:
        click.echo(json.dumps(result, ensure_ascii=False))
    else:
        click.echo(f"Checkpoint created: {output}")
        if warning:
            click.echo(f"Warning: {warning}", err=True)


@cli.command()
@click.option("--version", required=True, help="Human-readable version label, such as v0.3")
@click.option(
    "--context-file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Structured context prepared by the invoking Agent",
)
@click.option("--path", default=".", help="Project directory")
@click.option("--no-sync", is_flag=True, help="Use existing evidence only")
@click.option("--json-output", is_flag=True, help="Print a machine-readable result")
def finalize(
    version: str, context_file: Path, path: str, no_sync: bool, json_output: bool
) -> None:
    """Finalize the active version and aggregate its Build Records."""
    root = _root(path)
    try:
        context = load_context(context_file)
        stats, warning = (
            ({"codex": 0, "claude": 0, "files": 0, "events": 0}, None)
            if no_sync
            else _sync_for_record(root, context)
        )
        output = BuildHistoryStore(root).create_release(version, context)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    result = {
        "status": "created",
        "kind": "release",
        "version": version,
        "report": str(output),
        "history": str(output.parents[2] / "index.html"),
        "sync": stats,
        "warning": warning,
    }
    if json_output:
        click.echo(json.dumps(result, ensure_ascii=False))
    else:
        click.echo(f"Version report created: {output}")
        if warning:
            click.echo(f"Warning: {warning}", err=True)


@cli.command()
@click.argument("text")
@click.option(
    "--kind",
    type=click.Choice(["goal", "problem", "decision", "outcome"]),
    default="problem",
    show_default=True,
)
@click.option("--path", default=".", help="Project directory")
def note(text: str, kind: str, path: str) -> None:
    """Add a visible user intent when an Agent adapter is unavailable."""
    root = _root(path)
    store = ProjectStore(root)
    run = store.get_current_run() or store.latest_run()
    conversation_id = f"manual:{run['run_id'] if run else store.ensure_project()['project_id']}"
    EventStore(root).append(
        {
            "run_id": run.get("run_id") if run else None,
            "conversation_id": conversation_id,
            "source": "manual",
            "type": "message",
            "payload": {"role": "user", "kind": kind, "text": text},
        }
    )
    click.echo(f"Recorded {kind}: {text}")


@cli.command()
@click.argument("command")
@click.option("--path", default=".", help="Project directory")
def verify(command: str, path: str) -> None:
    """Run a verification command and preserve its output as evidence."""
    root = _root(path)
    store = ProjectStore(root)
    run = store.get_current_run() or store.latest_run()
    conversation_id = f"manual:{run['run_id'] if run else store.ensure_project()['project_id']}"
    result = subprocess.run(command, cwd=root, shell=True, capture_output=True, text=True)
    EventStore(root).append(
        {
            "timestamp": utc_now(),
            "run_id": run.get("run_id") if run else None,
            "conversation_id": conversation_id,
            "source": "manual",
            "type": "command",
            "payload": {
                "command": command,
                "cwd": str(root),
                "exit_code": result.returncode,
                "status": "completed",
                "stdout": result.stdout[-40_000:],
                "stderr": result.stderr[-40_000:],
            },
        }
    )
    click.echo(result.stdout, nl=False)
    if result.stderr:
        click.echo(result.stderr, err=True, nl=False)
    if result.returncode != 0:
        raise click.exceptions.Exit(result.returncode)
    click.echo("Verification passed and was recorded.")


@cli.command()
@click.option("--path", default=".", help="Project directory")
def show(path: str) -> None:
    """Show all Project Runs."""
    root = _root(path)
    runs = ProjectStore(root).list_runs()
    if not runs:
        click.echo("No Project Runs recorded.")
        return
    for run in runs:
        click.echo(
            f"{run['run_id']}  {run['status']:<7}  {run.get('goal') or '(no goal)'}"
        )


if __name__ == "__main__":
    cli()
