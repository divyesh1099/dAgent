from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from dagent_worker.config import CommandConfig, NotificationConfig, RepoConfig, WorkerConfig
from dagent_worker.jobs import JobStore
from dagent_worker.notifier import Notifier
from dagent_worker.projects import ProjectError, add_project, approve_project, list_projects, resolve_project
from dagent_worker.runner import JobRunner


def test_project_registry_discovers_and_approves_trusted_git_repo(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    repo = root / "new-app"
    _init_git_repo(repo)
    config = WorkerConfig(data_dir=tmp_path / "worker", notes_dir=tmp_path / "notes", trusted_roots=(root,))

    projects = list_projects(config, scan=True)
    assert {project["name"] for project in projects} == {"new-app"}
    assert projects[0]["approved"] is False

    with pytest.raises(ProjectError):
        resolve_project(config, name="new-app")

    approved = approve_project(config, name="new-app")
    assert approved.name == "new-app"
    assert approved.path == repo.resolve()
    assert resolve_project(config, name="new-app").approved is True


def test_trusted_roots_can_auto_approve_discovered_projects(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    repo = root / "new-app"
    _init_git_repo(repo)
    config = WorkerConfig(
        data_dir=tmp_path / "worker",
        notes_dir=tmp_path / "notes",
        trusted_roots=(root,),
        auto_approve_trusted_projects=True,
    )

    projects = list_projects(config, scan=True)
    assert projects[0]["approved"] is True

    project = resolve_project(config, name="new-app")
    assert project.approved is True
    assert project.source == "trusted_root"


def test_project_registry_can_create_new_git_project_under_trusted_root(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    config = WorkerConfig(data_dir=tmp_path / "worker", notes_dir=tmp_path / "notes", trusted_roots=(root,))

    project = add_project(config, name="brand new app", create_if_missing=True)

    assert project.name == "brand-new-app"
    assert project.path == (root / "brand-new-app").resolve()
    assert (project.path / ".git").exists()
    assert (project.path / "README.md").read_text(encoding="utf-8") == "# brand-new-app\n"
    assert resolve_project(config, name="brand-new-app").approved is True


def test_project_registry_rejects_non_git_existing_project_creation(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    project_path = root / "plain-folder"
    project_path.mkdir(parents=True)
    (project_path / "notes.txt").write_text("not a repo yet\n", encoding="utf-8")
    config = WorkerConfig(data_dir=tmp_path / "worker", notes_dir=tmp_path / "notes", trusted_roots=(root,))

    with pytest.raises(ProjectError, match="not a git repository"):
        add_project(config, name="plain-folder", create_if_missing=True)


def test_code_task_creates_worktree_runs_flavor_and_records_result(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    agent = tmp_path / "agent.py"
    agent.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import sys",
                "workspace = Path(sys.argv[1])",
                "(workspace / 'feature.txt').write_text('done\\n', encoding='utf-8')",
                "Path(sys.argv[2]).write_text('Implemented feature.txt\\n', encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "jobs.sqlite3")
    config = WorkerConfig(
        data_dir=tmp_path / "worker",
        notes_dir=tmp_path / "notes",
        code_worktrees_dir=tmp_path / "worktrees",
        intent_tools={"code_task": "fake"},
        repos={
            "repo": RepoConfig(
                name="repo",
                path=repo,
                allowed_intents=("code_task",),
            )
        },
        tools={
            "fake": CommandConfig(
                name="fake",
                command=(sys.executable, str(agent), "{workspace_path}", "{summary_path}"),
                timeout_seconds=30,
            )
        },
        notifications=NotificationConfig(),
    )
    record = store.create(
        payload={
            "intent": "code_task",
            "task": "Add feature file",
            "repo": "repo",
        },
        status="queued",
        idempotency_key=None,
        approval_hash=None,
    )

    JobRunner(config, store, Notifier(config.notifications)).run(record["id"])

    final = store.get(record["id"])
    assert final is not None
    assert final["status"] == "succeeded"
    result = final["result"]
    assert result["kind"] == "code_task"
    assert result["project"] == "repo"
    assert result["branch"].startswith("agent/")
    assert "feature.txt" in result["changed_files"]
    assert "feature.txt | new file" in result["diff_stat"]
    assert Path(result["workspace_path"], "feature.txt").read_text(encoding="utf-8") == "done\n"
    assert Path(result["completion_note_path"]).exists()

    store.close()


def test_code_task_fails_when_agent_reports_blocked_result(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    agent = tmp_path / "agent.py"
    agent.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import sys",
                "Path(sys.argv[2]).write_text('Blocked by the workspace environment. Requested file was not created.\\n', encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "jobs.sqlite3")
    config = WorkerConfig(
        data_dir=tmp_path / "worker",
        notes_dir=tmp_path / "notes",
        code_worktrees_dir=tmp_path / "worktrees",
        intent_tools={"code_task": "fake"},
        repos={
            "repo": RepoConfig(
                name="repo",
                path=repo,
                allowed_intents=("code_task",),
            )
        },
        tools={
            "fake": CommandConfig(
                name="fake",
                command=(sys.executable, str(agent), "{workspace_path}", "{summary_path}"),
                timeout_seconds=30,
            )
        },
        notifications=NotificationConfig(),
    )
    record = store.create(
        payload={
            "intent": "code_task",
            "task": "Add feature file",
            "repo": "repo",
        },
        status="queued",
        idempotency_key=None,
        approval_hash=None,
    )

    JobRunner(config, store, Notifier(config.notifications)).run(record["id"])

    final = store.get(record["id"])
    assert final is not None
    assert final["status"] == "failed"
    assert "fake did not complete the task" in final["error"]

    store.close()


def test_chatgpt_task_runs_without_repo_and_records_response(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent = tmp_path / "agent.py"
    agent.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import sys",
                "workspace = Path(sys.argv[1])",
                "(workspace / 'hello.txt').write_text('hi\\n', encoding='utf-8')",
                "Path(sys.argv[2]).write_text('Created hello.txt\\n', encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )
    store = JobStore(tmp_path / "jobs.sqlite3")
    config = WorkerConfig(
        data_dir=tmp_path / "worker",
        notes_dir=tmp_path / "notes",
        agent_workspace_dir=workspace,
        agent_summaries_dir=tmp_path / "summaries",
        auto_run_intents=frozenset({"chatgpt_task"}),
        intent_tools={"chatgpt_task": "fake"},
        tools={
            "fake": CommandConfig(
                name="fake",
                command=(sys.executable, str(agent), "{workspace_path}", "{summary_path}"),
                timeout_seconds=30,
            )
        },
        notifications=NotificationConfig(),
    )
    record = store.create(
        payload={
            "intent": "chatgpt_task",
            "task": "Create a hello file",
        },
        status="queued",
        idempotency_key=None,
        approval_hash=None,
    )

    JobRunner(config, store, Notifier(config.notifications)).run(record["id"])

    final = store.get(record["id"])
    assert final is not None
    assert final["status"] == "succeeded"
    result = final["result"]
    assert result["kind"] == "chatgpt_task"
    assert result["workspace_path"] == str(workspace)
    assert result["last_message"] == "Created hello.txt\n"
    assert (workspace / "hello.txt").read_text(encoding="utf-8") == "hi\n"

    store.close()


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
