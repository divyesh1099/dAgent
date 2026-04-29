from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from dagent_worker.config import CommandConfig, NotificationConfig, RepoConfig, WorkerConfig
from dagent_worker.jobs import JobStore
from dagent_worker.notifier import Notifier
from dagent_worker.runner import JobRunner


def test_runner_cancel_marks_running_command_cancelled(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text("import time\nwhile True:\n    time.sleep(0.1)\n", encoding="utf-8")
    store = JobStore(tmp_path / "jobs.sqlite3")
    config = WorkerConfig(
        data_dir=tmp_path / "worker",
        notes_dir=tmp_path / "notes",
        auto_run_intents=frozenset({"script_task"}),
        repo_required_intents=frozenset({"script_task"}),
        repos={
            "repo": RepoConfig(
                name="repo",
                path=tmp_path,
                allowed_intents=("script_task",),
            )
        },
        scripts={
            "slow": CommandConfig(
                name="slow",
                command=(sys.executable, str(script)),
                timeout_seconds=30,
                allowed_repos=("repo",),
            )
        },
        notifications=NotificationConfig(),
    )
    record = store.create(
        payload={
            "intent": "script_task",
            "task": "run slow command",
            "repo": "repo",
            "metadata": {"script": "slow"},
        },
        status="queued",
        idempotency_key=None,
        approval_hash=None,
    )
    runner = JobRunner(config, store, Notifier(config.notifications))
    thread = threading.Thread(target=runner.run, args=(record["id"],))

    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = store.get(record["id"])
        if current and current["status"] == "running":
            break
        time.sleep(0.05)
    runner.cancel(record["id"])
    thread.join(timeout=5)

    final = store.get(record["id"])
    assert final is not None
    assert final["status"] == "cancelled"
