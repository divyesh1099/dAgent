from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import re
import shlex
import subprocess
from typing import Any

from .config import CommandConfig, RepoConfig, WorkerConfig
from .jobs import JobStore
from .notifier import Notifier
from .schemas import JobRequest


class RunnerError(RuntimeError):
    pass


class JobRunner:
    def __init__(self, config: WorkerConfig, store: JobStore, notifier: Notifier) -> None:
        self._config = config
        self._store = store
        self._notifier = notifier

    def run(self, job_id: str) -> None:
        record = self._store.get(job_id)
        if record is None:
            raise RunnerError(f"unknown job {job_id}")

        payload = JobRequest.model_validate(record["payload"])
        log_path = self._log_path(job_id)
        self._store.mark_running(job_id, str(log_path))

        try:
            result = self._execute(job_id, payload, log_path)
        except Exception as exc:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\nERROR: {exc}\n")
            finished = self._store.finish(job_id, status="failed", error=str(exc))
            self._log_notification_results(log_path, self._notifier.job_finished(finished))
            return

        finished = self._store.finish(job_id, status="succeeded", result=result)
        self._log_notification_results(log_path, self._notifier.job_finished(finished))

    def _execute(self, job_id: str, payload: JobRequest, log_path: Path) -> dict[str, Any]:
        if payload.intent in {"capture_idea", "research_note", "document_task", "job_packet"}:
            return self._create_note(job_id, payload, log_path)
        if payload.intent == "repo_status":
            repo = self._resolve_repo(payload.repo)
            return self._repo_status(repo, log_path)
        if payload.intent == "script_task":
            repo = self._resolve_repo(payload.repo)
            return self._script_task(job_id, payload, repo, log_path)
        return self._tool_task(job_id, payload, log_path)

    def _resolve_repo(self, repo_name: str | None) -> RepoConfig:
        if not repo_name:
            raise RunnerError("repo is required for this intent")
        repo = self._config.repos.get(repo_name)
        if repo is None:
            raise RunnerError(f"repo {repo_name!r} is not configured")
        if not repo.path.exists():
            raise RunnerError(f"repo path does not exist: {repo.path}")
        if not repo.path.is_dir():
            raise RunnerError(f"repo path is not a directory: {repo.path}")
        return repo

    def _create_note(self, job_id: str, payload: JobRequest, log_path: Path) -> dict[str, Any]:
        self._config.notes_dir.mkdir(parents=True, exist_ok=True)
        created = datetime.now(timezone.utc)
        slug = _slug(payload.task)
        note_path = self._config.notes_dir / f"{created.strftime('%Y%m%d-%H%M%S')}-{slug}.md"
        body = "\n".join(
            [
                f"# {payload.intent.replace('_', ' ').title()}",
                "",
                f"- Job: `{job_id}`",
                f"- Created: `{created.isoformat()}`",
                f"- Source: `{payload.source.value}`",
                f"- Input: `{payload.input_type.value}`",
                "",
                "## Task",
                "",
                payload.task,
                "",
                "## Files",
                "",
                *[f"- {file.name}: {file.url or file.path or '-'}" for file in payload.files],
                "",
                "## Metadata",
                "",
                "```json",
                _jsonish(payload.metadata),
                "```",
                "",
            ]
        )
        note_path.write_text(body, encoding="utf-8")
        log_path.write_text(f"Created note {note_path}\n", encoding="utf-8")
        return {"note_path": str(note_path)}

    def _repo_status(self, repo: RepoConfig, log_path: Path) -> dict[str, Any]:
        commands = [
            ["git", "branch", "--show-current"],
            ["git", "status", "--short"],
            ["git", "log", "-1", "--oneline"],
            ["git", "remote", "-v"],
        ]
        result: dict[str, Any] = {"repo": repo.name, "path": str(repo.path), "github_account": repo.github_account}
        outputs: list[str] = []
        for command in commands:
            completed = self._run_command(command, repo.path, timeout_seconds=30, log_path=log_path)
            key = "_".join(command[1:]).replace("-", "_")
            result[key] = completed["stdout"].strip()
            outputs.append(f"$ {shlex.join(command)}\n{completed['stdout']}{completed['stderr']}")
        result["summary"] = "\n\n".join(outputs)
        return result

    def _script_task(self, job_id: str, payload: JobRequest, repo: RepoConfig, log_path: Path) -> dict[str, Any]:
        script_name = str(payload.metadata.get("script", ""))
        if not script_name:
            raise RunnerError("script_task requires metadata.script")
        script = self._config.scripts.get(script_name)
        if script is None:
            raise RunnerError(f"script {script_name!r} is not configured")
        if not script.allows_repo(repo.name):
            raise RunnerError(f"script {script_name!r} is not allowed for repo {repo.name!r}")
        command = _render_command(script.command, payload=payload, repo=repo, job_id=job_id)
        completed = self._run_command(command, repo.path, timeout_seconds=script.timeout_seconds, log_path=log_path)
        return _command_result(command, completed)

    def _tool_task(self, job_id: str, payload: JobRequest, log_path: Path) -> dict[str, Any]:
        repo = self._resolve_repo(payload.repo)
        tool_name = payload.tool or self._config.intent_tools.get(payload.intent) or repo.default_tool
        if not tool_name:
            raise RunnerError(f"no tool configured for intent {payload.intent!r}")
        tool = self._config.tools.get(tool_name)
        if tool is None:
            raise RunnerError(f"tool {tool_name!r} is not configured")
        if not tool.allows_repo(repo.name):
            raise RunnerError(f"tool {tool_name!r} is not allowed for repo {repo.name!r}")
        command = _render_command(tool.command, payload=payload, repo=repo, job_id=job_id)
        completed = self._run_command(command, repo.path, timeout_seconds=tool.timeout_seconds, log_path=log_path)
        return _command_result(command, completed)

    def _run_command(
        self,
        command: list[str],
        cwd: Path,
        *,
        timeout_seconds: int,
        log_path: Path,
    ) -> dict[str, Any]:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n$ {shlex.join(command)}\n")
            log.write(f"cwd: {cwd}\n\n")

        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise RunnerError(f"executable not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(exc.stdout or "")
                log.write(exc.stderr or "")
                log.write(f"\nCommand timed out after {timeout_seconds} seconds\n")
            raise RunnerError(f"command timed out after {timeout_seconds} seconds") from exc

        with log_path.open("a", encoding="utf-8") as log:
            if completed.stdout:
                log.write(completed.stdout)
            if completed.stderr:
                log.write("\n[stderr]\n")
                log.write(completed.stderr)
            log.write(f"\nexit_code: {completed.returncode}\n")

        if completed.returncode != 0:
            raise RunnerError(f"command failed with exit code {completed.returncode}")

        return {
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

    def _log_path(self, job_id: str) -> Path:
        log_dir = self._config.data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"{job_id}.log"

    def _log_notification_results(self, log_path: Path, results: list[dict[str, Any]]) -> None:
        if not results:
            with log_path.open("a", encoding="utf-8") as log:
                log.write("Notification: disabled\n")
            return
        with log_path.open("a", encoding="utf-8") as log:
            for result in results:
                status = "ok" if result.get("ok") else "failed"
                detail = result.get("status") or result.get("error") or "-"
                log.write(f"Notification {status}: {result.get('topic')} ({detail})\n")


def _render_command(command: tuple[str, ...], *, payload: JobRequest, repo: RepoConfig, job_id: str) -> list[str]:
    context: dict[str, str] = {
        "task": payload.task,
        "repo": repo.name,
        "repo_path": str(repo.path),
        "github_account": repo.github_account,
        "job_id": job_id,
    }
    for key, value in payload.metadata.items():
        if isinstance(value, (str, int, float, bool)):
            context[f"meta_{key}"] = str(value)
    return [part.format_map(_SafeDict(context)) for part in command]


class _SafeDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return (slug or "note")[:60]


def _jsonish(value: Any) -> str:
    import json

    return json.dumps(value, indent=2, sort_keys=True)


def _command_result(command: list[str], completed: dict[str, Any]) -> dict[str, Any]:
    return {
        "command": shlex.join(command),
        "exit_code": completed["exit_code"],
        "stdout_tail": _tail(completed["stdout"]),
        "stderr_tail": _tail(completed["stderr"]),
    }


def _tail(text: str, limit: int = 6000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]
