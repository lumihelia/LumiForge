"""Background recorder process entry point."""

from __future__ import annotations

import argparse
import signal
import threading

from .storage import EventStore
from .watcher import WatcherManager


def run(project_path: str, run_id: str) -> None:
    events = EventStore(project_path)
    stopped = threading.Event()

    def on_file_event(payload):
        events.append(
            {
                "run_id": run_id,
                "source": "watcher",
                "type": "file_change",
                "payload": payload,
            }
        )

    manager = WatcherManager(project_path, on_file_event)

    def stop(_signum=None, _frame=None):
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    manager.start()
    events.append(
        {
            "run_id": run_id,
            "source": "recorder",
            "type": "recorder",
            "payload": {"action": "started"},
        }
    )
    stopped.wait()
    manager.stop()
    events.append(
        {
            "run_id": run_id,
            "source": "recorder",
            "type": "recorder",
            "payload": {"action": "stopped"},
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="LumiForge background recorder")
    parser.add_argument("--path", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    run(args.path, args.run_id)


if __name__ == "__main__":
    main()
