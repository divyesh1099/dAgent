from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepoConfig:
    name: str
    path: Path
    github_account: str = "default"
    default_tool: str | None = None
    allowed_intents: tuple[str, ...] = ("repo_status",)

    def allows_intent(self, intent: str) -> bool:
        return "*" in self.allowed_intents or intent in self.allowed_intents


@dataclass(frozen=True)
class CommandConfig:
    name: str
    command: tuple[str, ...]
    timeout_seconds: int = 3600
    allowed_repos: tuple[str, ...] = ("*",)

    def allows_repo(self, repo: str | None) -> bool:
        if "*" in self.allowed_repos:
            return True
        if repo is None:
            return "__none__" in self.allowed_repos
        return repo in self.allowed_repos


@dataclass(frozen=True)
class NotificationConfig:
    ntfy_url: str = ""
    ntfy_topics: tuple[str, ...] = ("dagent",)
    ntfy_token: str = ""

    @property
    def ntfy_topic(self) -> str:
        return self.ntfy_topics[0] if self.ntfy_topics else ""


@dataclass(frozen=True)
class WorkerConfig:
    data_dir: Path
    notes_dir: Path
    max_parallel_jobs: int = 2
    default_require_approval: bool = True
    auto_run_intents: frozenset[str] = field(default_factory=frozenset)
    approval_required_intents: frozenset[str] = field(default_factory=frozenset)
    repo_required_intents: frozenset[str] = field(default_factory=frozenset)
    intent_tools: dict[str, str] = field(default_factory=dict)
    repos: dict[str, RepoConfig] = field(default_factory=dict)
    tools: dict[str, CommandConfig] = field(default_factory=dict)
    scripts: dict[str, CommandConfig] = field(default_factory=dict)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)

    def known_intents(self) -> set[str]:
        known = set(self.auto_run_intents)
        known.update(self.approval_required_intents)
        known.update(self.repo_required_intents)
        known.update(self.intent_tools.keys())
        for repo in self.repos.values():
            known.update(repo.allowed_intents)
        return known

    def require_approval_for(self, intent: str, requested: bool | None) -> bool:
        if intent in self.approval_required_intents:
            return True
        if requested is not None:
            return requested
        if intent in self.auto_run_intents:
            return False
        return self.default_require_approval


def load_config(path: str | os.PathLike[str] | None = None) -> WorkerConfig:
    configured_path = Path(path or os.getenv("DAGENT_WORKER_CONFIG", "worker/config.yml"))
    raw: dict[str, Any] = {}
    base_dir = Path.cwd()

    if configured_path.exists():
        resolved_config = configured_path.resolve()
        base_dir = resolved_config.parent.parent if resolved_config.parent.name == "worker" else resolved_config.parent
        loaded = yaml.safe_load(resolved_config.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ConfigError("worker config must be a YAML mapping")
        raw = loaded

    data_dir = _resolve_path(os.getenv("DAGENT_DATA_DIR") or raw.get("data_dir", ".data/worker"), base_dir)
    notes_dir = _resolve_path(raw.get("notes_dir", ".data/notes"), base_dir)

    notifications_raw = raw.get("notifications", {}) or {}
    ntfy_topic_value = os.getenv("NTFY_TOPIC")
    ntfy_topics_value = os.getenv("NTFY_TOPICS")
    if ntfy_topics_value:
        ntfy_topics = tuple(topic.strip() for topic in ntfy_topics_value.split(",") if topic.strip())
    elif ntfy_topic_value:
        ntfy_topics = (ntfy_topic_value,)
    else:
        configured_topics = notifications_raw.get("ntfy_topics")
        if configured_topics is None:
            configured_topics = [notifications_raw.get("ntfy_topic", "dagent")]
        ntfy_topics = tuple(_string_list(configured_topics, "notifications.ntfy_topics"))

    notifications = NotificationConfig(
        ntfy_url=os.getenv("NTFY_URL", str(notifications_raw.get("ntfy_url", ""))),
        ntfy_topics=ntfy_topics,
        ntfy_token=os.getenv("NTFY_TOKEN", str(notifications_raw.get("ntfy_token", ""))),
    )

    repos = _load_repos(raw.get("repos", {}) or {})
    tools = _load_commands(raw.get("tools", {}) or {})
    scripts = _load_commands(raw.get("scripts", {}) or {})

    return WorkerConfig(
        data_dir=data_dir,
        notes_dir=notes_dir,
        max_parallel_jobs=int(raw.get("max_parallel_jobs", 2)),
        default_require_approval=bool(raw.get("default_require_approval", True)),
        auto_run_intents=frozenset(_string_list(raw.get("auto_run_intents", []), "auto_run_intents")),
        approval_required_intents=frozenset(
            _string_list(raw.get("approval_required_intents", []), "approval_required_intents")
        ),
        repo_required_intents=frozenset(_string_list(raw.get("repo_required_intents", []), "repo_required_intents")),
        intent_tools={str(k): str(v) for k, v in (raw.get("intent_tools", {}) or {}).items()},
        repos=repos,
        tools=tools,
        scripts=scripts,
        notifications=notifications,
    )


def _load_repos(raw_repos: dict[str, Any]) -> dict[str, RepoConfig]:
    repos: dict[str, RepoConfig] = {}
    for name, raw in raw_repos.items():
        if not isinstance(raw, dict):
            raise ConfigError(f"repo {name!r} must be a mapping")
        path_value = raw.get("path")
        if not path_value:
            raise ConfigError(f"repo {name!r} is missing path")
        repos[str(name)] = RepoConfig(
            name=str(name),
            path=Path(str(path_value)).expanduser().resolve(),
            github_account=str(raw.get("github_account", "default")),
            default_tool=str(raw["default_tool"]) if raw.get("default_tool") else None,
            allowed_intents=tuple(_string_list(raw.get("allowed_intents", ["repo_status"]), f"repos.{name}.allowed_intents")),
        )
    return repos


def _load_commands(raw_commands: dict[str, Any]) -> dict[str, CommandConfig]:
    commands: dict[str, CommandConfig] = {}
    for name, raw in raw_commands.items():
        if not isinstance(raw, dict):
            raise ConfigError(f"command {name!r} must be a mapping")
        command = _string_list(raw.get("command"), f"{name}.command")
        if not command:
            raise ConfigError(f"command {name!r} must include a command array")
        commands[str(name)] = CommandConfig(
            name=str(name),
            command=tuple(command),
            timeout_seconds=int(raw.get("timeout_seconds", 3600)),
            allowed_repos=tuple(_string_list(raw.get("allowed_repos", ["*"]), f"{name}.allowed_repos")),
        )
    return commands


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be a list")
    return [str(item) for item in value]


def _resolve_path(value: Any, base_dir: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()
