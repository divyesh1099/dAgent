from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import re
import signal
import shlex
import subprocess
import threading
import time
from typing import Any

from .config import CommandConfig, RepoConfig, WorkerConfig
from .jobs import JobStore
from .notifier import Notifier
from .projects import Project, ProjectError, resolve_project
from .schemas import JobRequest, JobStatus


class RunnerError(RuntimeError):
    pass


class RunnerCancelled(RunnerError):
    pass


CODE_TASK_INTENTS = {"code_task", "codex_task", "claude_task"}


class JobRunner:
    def __init__(self, config: WorkerConfig, store: JobStore, notifier: Notifier) -> None:
        self._config = config
        self._store = store
        self._notifier = notifier
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._cancel_requested: set[str] = set()
        self._lock = threading.RLock()

    def cancel(self, job_id: str) -> None:
        with self._lock:
            self._cancel_requested.add(job_id)
            process = self._processes.get(job_id)
        if process and process.poll() is None:
            _terminate_process(process)

    def run(self, job_id: str) -> None:
        record = self._store.get(job_id)
        if record is None:
            raise RunnerError(f"unknown job {job_id}")
        if record["status"] == JobStatus.cancelled.value:
            return

        payload = JobRequest.model_validate(record["payload"])
        log_path = self._log_path(job_id)
        running = self._store.mark_running(job_id, str(log_path))
        if running["status"] == JobStatus.cancelled.value:
            return
        if running["status"] != JobStatus.running.value:
            return

        try:
            result = self._execute(job_id, payload, log_path)
        except RunnerCancelled as exc:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\nCANCELLED: {exc}\n")
            finished = self._store.finish(job_id, status=JobStatus.cancelled.value, error=str(exc))
            self._log_notification_results(log_path, self._notifier.job_finished(finished))
            return
        except Exception as exc:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\nERROR: {exc}\n")
            if self._is_cancelled(job_id):
                finished = self._store.finish(job_id, status=JobStatus.cancelled.value, error="cancelled")
            else:
                finished = self._store.finish(job_id, status=JobStatus.failed.value, error=str(exc))
            self._log_notification_results(log_path, self._notifier.job_finished(finished))
            return

        if self._is_cancelled(job_id):
            finished = self._store.finish(job_id, status=JobStatus.cancelled.value, error="cancelled")
        else:
            finished = self._store.finish(job_id, status=JobStatus.succeeded.value, result=result)
        self._log_notification_results(log_path, self._notifier.job_finished(finished))

    def _execute(self, job_id: str, payload: JobRequest, log_path: Path) -> dict[str, Any]:
        if payload.intent in {"capture_idea", "research_note", "document_task", "job_packet"}:
            return self._create_note(job_id, payload, log_path)
        if payload.intent in CODE_TASK_INTENTS:
            return self._code_task(job_id, payload, log_path)
        if payload.intent == "repo_status":
            repo = self._resolve_repo(payload.repo)
            return self._repo_status(job_id, repo, log_path)
        if payload.intent == "script_task":
            repo = self._resolve_repo(payload.repo)
            return self._script_task(job_id, payload, repo, log_path)
        return self._tool_task(job_id, payload, log_path)

    def _code_task(self, job_id: str, payload: JobRequest, log_path: Path) -> dict[str, Any]:
        project = self._resolve_code_project(payload)
        flavor = _code_flavor(self._config, payload)
        tool = self._config.tools.get(flavor)
        if tool is None:
            tool = _builtin_code_tool(self._config, flavor)
        if not tool.allows_repo(project.name):
            raise RunnerError(f"code flavor {flavor!r} is not allowed for project {project.name!r}")

        worktree_path = self._create_code_worktree(job_id, payload, project, log_path)
        branch = self._current_branch(job_id, worktree_path, log_path)
        summary_path = worktree_path / ".dagent" / f"{job_id}-summary.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        prompt = _code_prompt(payload, project=project, worktree_path=worktree_path, branch=branch)
        command = _render_command(
            tool.command,
            payload=payload,
            repo=_project_repo_config(project),
            job_id=job_id,
            extra={
                "branch": branch,
                "flavor": flavor,
                "project": project.name,
                "project_path": str(project.path),
                "workspace_path": str(worktree_path),
                "summary_path": str(summary_path),
                "prompt": prompt,
            },
        )

        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"Code task project: {project.name} ({project.path})\n")
            log.write(f"Worktree: {worktree_path}\n")
            log.write(f"Branch: {branch}\n")
            log.write(f"Flavor: {flavor}\n")
            if payload.dry_run:
                log.write("Dry run: agent command was not executed.\n")

        if payload.dry_run:
            completed = {"exit_code": 0, "stdout": "", "stderr": ""}
        else:
            completed = self._run_command(
                job_id,
                command,
                worktree_path,
                timeout_seconds=tool.timeout_seconds,
                log_path=log_path,
            )

        status_short = self._git_output(job_id, worktree_path, ["git", "status", "--short"], log_path)
        raw_diff_stat = self._git_output(job_id, worktree_path, ["git", "diff", "--stat"], log_path)
        changed_files = _changed_files_from_status(status_short)
        diff_stat = raw_diff_stat or _untracked_diff_stat(changed_files, status_short)
        last_message = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
        if _agent_reported_failure(last_message, completed["stdout"], completed["stderr"]):
            raise RunnerError(_agent_failure_error(flavor, last_message))
        final_note_path = self._write_code_completion_note(
            job_id=job_id,
            payload=payload,
            project=project,
            flavor=flavor,
            worktree_path=worktree_path,
            branch=branch,
            changed_files=changed_files,
            diff_stat=diff_stat,
            last_message=last_message,
        )
        result = {
            "kind": "code_task",
            "project": project.name,
            "project_path": str(project.path),
            "project_source": project.source,
            "workspace_path": str(worktree_path),
            "branch": branch,
            "flavor": flavor,
            "command": shlex.join(command),
            "exit_code": completed["exit_code"],
            "stdout_tail": _tail(completed["stdout"]),
            "stderr_tail": _tail(completed["stderr"]),
            "status_short": status_short,
            "diff_stat": diff_stat,
            "changed_files": changed_files,
            "last_message": _tail(last_message),
            "completion_note_path": str(final_note_path),
            "code_server_url": _code_server_url(self._config, worktree_path),
        }
        with log_path.open("a", encoding="utf-8") as log:
            log.write("\nDONE code task\n")
            log.write(f"Completion note: {final_note_path}\n")
            if result["code_server_url"]:
                log.write(f"Code-server: {result['code_server_url']}\n")
        return result

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

    def _resolve_code_project(self, payload: JobRequest) -> Project:
        metadata = payload.metadata if isinstance(payload.metadata, dict) else {}
        name = payload.repo or str(metadata.get("project") or metadata.get("repo") or "").strip() or None
        path = str(metadata.get("project_path") or metadata.get("path") or "").strip() or None
        approve = bool(metadata.get("approve_project"))
        try:
            return resolve_project(self._config, name=name, path=path, approve=approve)
        except ProjectError as exc:
            raise RunnerError(str(exc)) from exc

    def _create_code_worktree(self, job_id: str, payload: JobRequest, project: Project, log_path: Path) -> Path:
        use_worktree = payload.metadata.get("worktree", True) is not False
        if not use_worktree:
            return project.path
        worktrees_root = self._config.code_worktrees_dir or (self._config.data_dir / "code-worktrees")
        worktrees_root.mkdir(parents=True, exist_ok=True)
        worktree_path = (worktrees_root / f"{project.name}-{job_id[:8]}").resolve()
        if worktree_path.exists():
            raise RunnerError(f"worktree already exists: {worktree_path}")
        branch = _branch_name(payload.task, job_id)
        self._run_command(
            job_id,
            ["git", "worktree", "add", "-b", branch, str(worktree_path), "HEAD"],
            project.path,
            timeout_seconds=120,
            log_path=log_path,
        )
        return worktree_path

    def _current_branch(self, job_id: str, path: Path, log_path: Path) -> str:
        return self._git_output(job_id, path, ["git", "branch", "--show-current"], log_path).strip()

    def _git_output(self, job_id: str, cwd: Path, command: list[str], log_path: Path) -> str:
        try:
            completed = self._run_command(job_id, command, cwd, timeout_seconds=60, log_path=log_path)
        except RunnerError:
            return ""
        return completed["stdout"].strip()

    def _write_code_completion_note(
        self,
        *,
        job_id: str,
        payload: JobRequest,
        project: Project,
        flavor: str,
        worktree_path: Path,
        branch: str,
        changed_files: list[str],
        diff_stat: str,
        last_message: str,
    ) -> Path:
        note_path = worktree_path / ".dagent" / f"{job_id}-DONE.md"
        lines = [
            f"# DONE: {project.name}",
            "",
            f"- Job: `{job_id}`",
            f"- Flavor: `{flavor}`",
            f"- Branch: `{branch}`",
            f"- Project: `{project.path}`",
            f"- Worktree: `{worktree_path}`",
            "",
            "## Task",
            "",
            payload.task,
            "",
            "## Changed Files",
            "",
            *(f"- `{path}`" for path in changed_files),
            "" if changed_files else "- No changed files detected.",
            "",
            "## Diff Stat",
            "",
            "```",
            diff_stat or "No diff.",
            "```",
        ]
        if last_message.strip():
            lines.extend(["", "## Agent Final Message", "", last_message.strip()])
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return note_path

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
        completed = self._run_command(job_id, command, repo.path, timeout_seconds=script.timeout_seconds, log_path=log_path)
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
        completed = self._run_command(job_id, command, repo.path, timeout_seconds=tool.timeout_seconds, log_path=log_path)
        return _command_result(command, completed)

    def _run_command(
        self,
        job_id: str,
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
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=os.environ.copy(),
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise RunnerError(f"executable not found: {command[0]}") from exc

        with self._lock:
            self._processes[job_id] = process

        try:
            started = time.monotonic()
            while True:
                if self._is_cancelled(job_id):
                    _terminate_process(process)
                    stdout, stderr = process.communicate(timeout=5)
                    with log_path.open("a", encoding="utf-8") as log:
                        log.write(stdout or "")
                        log.write(stderr or "")
                        log.write("\nCommand cancelled\n")
                    raise RunnerCancelled("cancelled")
                try:
                    stdout, stderr = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() - started > timeout_seconds:
                        _terminate_process(process)
                        stdout, stderr = process.communicate(timeout=5)
                        with log_path.open("a", encoding="utf-8") as log:
                            log.write(stdout or "")
                            log.write(stderr or "")
                            log.write(f"\nCommand timed out after {timeout_seconds} seconds\n")
                        raise RunnerError(f"command timed out after {timeout_seconds} seconds")
        finally:
            with self._lock:
                self._processes.pop(job_id, None)

        returncode = process.returncode

        with log_path.open("a", encoding="utf-8") as log:
            if stdout:
                log.write(stdout)
            if stderr:
                log.write("\n[stderr]\n")
                log.write(stderr)
            log.write(f"\nexit_code: {returncode}\n")

        if returncode != 0:
            raise RunnerError(f"command failed with exit code {returncode}")

        return {
            "exit_code": returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    def _repo_status(self, job_id: str, repo: RepoConfig, log_path: Path) -> dict[str, Any]:
        commands = [
            ["git", "branch", "--show-current"],
            ["git", "status", "--short"],
            ["git", "log", "-1", "--oneline"],
            ["git", "remote", "-v"],
        ]
        result: dict[str, Any] = {"repo": repo.name, "path": str(repo.path), "github_account": repo.github_account}
        outputs: list[str] = []
        for command in commands:
            completed = self._run_command(job_id, command, repo.path, timeout_seconds=30, log_path=log_path)
            key = "_".join(command[1:]).replace("-", "_")
            result[key] = completed["stdout"].strip()
            outputs.append(f"$ {shlex.join(command)}\n{completed['stdout']}{completed['stderr']}")
        result["summary"] = "\n\n".join(outputs)
        return result

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._cancel_requested:
                return True
        record = self._store.get(job_id)
        return bool(record and record["status"] == JobStatus.cancelled.value)

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


def _render_command(
    command: tuple[str, ...],
    *,
    payload: JobRequest,
    repo: RepoConfig,
    job_id: str,
    extra: dict[str, str] | None = None,
) -> list[str]:
    context: dict[str, str] = {
        "task": payload.task,
        "repo": repo.name,
        "repo_path": str(repo.path),
        "github_account": repo.github_account,
        "job_id": job_id,
    }
    if extra:
        context.update(extra)
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


def _branch_name(task: str, job_id: str) -> str:
    return f"agent/{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{_slug(task)[:42]}-{job_id[:8]}"


def _project_repo_config(project: Project) -> RepoConfig:
    return RepoConfig(name=project.name, path=project.path, allowed_intents=("*",))


def _code_flavor(config: WorkerConfig, payload: JobRequest) -> str:
    metadata_flavor = str(payload.metadata.get("flavor") or "").strip()
    if metadata_flavor:
        return metadata_flavor
    if payload.tool:
        return payload.tool
    if payload.intent == "claude_task":
        return "claude"
    if payload.intent == "codex_task":
        return "codex"
    return config.intent_tools.get(payload.intent) or "codex"


def _builtin_code_tool(config: WorkerConfig, flavor: str) -> CommandConfig:
    if flavor == "codex":
        return CommandConfig(
            name="codex",
            command=(
                "codex",
                "--ask-for-approval",
                config.code_codex_approval_policy,
                "--sandbox",
                config.code_codex_sandbox,
                "exec",
                "--cd",
                "{workspace_path}",
                "--output-last-message",
                "{summary_path}",
                "{prompt}",
            ),
            timeout_seconds=7200,
            allowed_repos=("*",),
        )
    raise RunnerError(f"code flavor {flavor!r} is not configured")


def _code_prompt(payload: JobRequest, *, project: Project, worktree_path: Path, branch: str) -> str:
    return "\n".join(
        [
            "You are running inside dAgent's Code Task Worker.",
            f"Project: {project.name}",
            f"Source path: {project.path}",
            f"Workspace path: {worktree_path}",
            f"Branch: {branch}",
            "",
            "User task:",
            payload.task,
            "",
            "Rules:",
            "- Work only inside this workspace.",
            "- Keep edits scoped to the requested feature or fix.",
            "- Run the most relevant tests/checks you can find.",
            "- Leave the workspace ready for human review.",
            "- End with a concise summary, changed files, and tests run.",
        ]
    )


def _code_server_url(config: WorkerConfig, workspace_path: Path) -> str:
    if config.code_server_folder_url_template:
        return config.code_server_folder_url_template.format(folder=quote_path(workspace_path), path=str(workspace_path))
    return config.code_server_url


def quote_path(path: Path) -> str:
    from urllib.parse import quote

    return quote(str(path), safe="")


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


def _changed_files_from_status(status_short: str) -> list[str]:
    files: list[str] = []
    for line in status_short.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and not _is_internal_code_task_path(path):
            files.append(path)
    return files


def _untracked_diff_stat(changed_files: list[str], status_short: str) -> str:
    untracked = []
    for line in status_short.splitlines():
        if not line.startswith("?? "):
            continue
        path = line[3:].strip()
        if path in changed_files:
            untracked.append(f"{path} | new file")
    return "\n".join(untracked)


def _is_internal_code_task_path(path: str) -> bool:
    return path == ".dagent" or path.startswith(".dagent/")


def _agent_reported_failure(last_message: str, stdout: str, stderr: str) -> bool:
    text = "\n".join([last_message, stdout, stderr]).lower()
    failure_signals = (
        "blocked by the workspace environment",
        "requested file was not created",
        "could not create",
        "could not complete",
        "shell commands failed before execution",
        "sandbox startup failure",
        "bwrap sandbox error",
    )
    return any(signal in text for signal in failure_signals)


def _agent_failure_error(flavor: str, last_message: str) -> str:
    detail = last_message.strip().splitlines()[0] if last_message.strip() else "agent reported failure"
    return f"{flavor} did not complete the task: {detail}"


def _tail(text: str, limit: int = 6000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            process.kill()
