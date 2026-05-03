from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from .config import WorkerConfig


class ProjectError(RuntimeError):
    pass


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    source: str
    approved: bool


_SKIP_DIRS = {
    ".cache",
    ".git",
    ".hg",
    ".next",
    ".pytest_cache",
    ".tox",
    ".venv",
    "dist",
    "node_modules",
    "vendor",
}


def registry_path(config: WorkerConfig) -> Path:
    return config.data_dir / "projects.json"


def load_registry(config: WorkerConfig) -> dict[str, Any]:
    path = registry_path(config)
    if not path.exists():
        return {"projects": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError("project registry is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ProjectError("project registry must be a JSON object")
    projects = data.get("projects")
    if not isinstance(projects, dict):
        data["projects"] = {}
    return data


def save_registry(config: WorkerConfig, registry: dict[str, Any]) -> None:
    path = registry_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def list_projects(config: WorkerConfig, *, scan: bool = False) -> list[dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}
    for name, repo in config.repos.items():
        projects[name] = {
            "name": name,
            "path": str(repo.path),
            "approved": True,
            "source": "config",
        }

    registry = load_registry(config)
    for name, entry in registry["projects"].items():
        if not isinstance(entry, dict):
            continue
        projects.setdefault(
            str(name),
            {
                "name": str(name),
                "path": str(entry.get("path") or ""),
                "approved": bool(entry.get("approved", True)),
                "source": "registry",
            },
        )

    if scan:
        for discovered in discover_projects(config):
            if _has_project(projects, discovered):
                continue
            projects.setdefault(
                discovered.name,
                {
                    "name": discovered.name,
                    "path": str(discovered.path),
                    "approved": config.auto_approve_trusted_projects,
                    "source": "discovered",
                },
            )

    return sorted(projects.values(), key=lambda item: (not item["approved"], item["name"].lower()))


def approve_project(config: WorkerConfig, *, name: str | None = None, path: str | None = None) -> Project:
    project = resolve_project(config, name=name, path=path, approve=True)
    registry = load_registry(config)
    registry["projects"][project.name] = {
        "path": str(project.path),
        "approved": True,
    }
    save_registry(config, registry)
    return Project(name=project.name, path=project.path, source="registry", approved=True)


def add_project(
    config: WorkerConfig,
    *,
    name: str | None = None,
    path: str | None = None,
    create_if_missing: bool = False,
) -> Project:
    try:
        return approve_project(config, name=name, path=path)
    except ProjectError:
        if not create_if_missing:
            raise
    return create_project(config, name=name, path=path)


def create_project(config: WorkerConfig, *, name: str | None = None, path: str | None = None) -> Project:
    if not config.trusted_roots:
        raise ProjectError("trusted_roots is required before creating projects")

    registry = load_registry(config)
    clean_name = _clean_project_name(name or (Path(path).name if path else None))
    if path:
        project_path = Path(path).expanduser().resolve()
    else:
        project_path = (config.trusted_roots[0] / clean_name).expanduser().resolve()

    if not _is_in_trusted_roots(config, project_path):
        raise ProjectError(f"project path is outside trusted roots: {project_path}")
    if project_path.exists() and not project_path.is_dir():
        raise ProjectError(f"project path is not a directory: {project_path}")
    if project_path.exists() and (project_path / ".git").exists():
        return approve_project(config, name=clean_name, path=str(project_path))
    if project_path.exists() and any(project_path.iterdir()):
        raise ProjectError(
            f"project path exists but is not a git repository: {project_path}; initialize git first or choose an empty folder"
        )

    project_path.mkdir(parents=True, exist_ok=True)
    _init_git_project(project_path, clean_name)

    registry["projects"][clean_name] = {
        "path": str(project_path),
        "approved": True,
    }
    save_registry(config, registry)
    return Project(name=clean_name, path=project_path, source="registry", approved=True)


def resolve_project(
    config: WorkerConfig,
    *,
    name: str | None = None,
    path: str | None = None,
    approve: bool = False,
    allow_unapproved: bool = False,
) -> Project:
    clean_name = _clean_project_name(name) if name else None
    config_name = _find_key(config.repos, clean_name) if clean_name else None
    if config_name:
        repo = config.repos[config_name]
        _ensure_project_path(repo.path)
        return Project(name=config_name, path=repo.path, source="config", approved=True)

    registry = load_registry(config)
    if clean_name:
        registry_name = _find_key(registry["projects"], clean_name)
        entry = registry["projects"].get(registry_name) if registry_name else None
        if isinstance(entry, dict):
            project_path = Path(str(entry.get("path") or "")).expanduser().resolve()
            _ensure_project_path(project_path)
            if not _is_in_trusted_roots(config, project_path):
                raise ProjectError(f"registered project {registry_name!r} is outside trusted roots")
            return Project(name=str(registry_name), path=project_path, source="registry", approved=bool(entry.get("approved", True)))

    candidate = _resolve_candidate(config, name=clean_name, path=path)
    if candidate and approve:
        registry["projects"][candidate.name] = {
            "path": str(candidate.path),
            "approved": True,
        }
        save_registry(config, registry)
        return Project(name=candidate.name, path=candidate.path, source="registry", approved=True)
    if candidate and config.auto_approve_trusted_projects:
        return Project(name=candidate.name, path=candidate.path, source="trusted_root", approved=True)
    if candidate and allow_unapproved:
        return candidate
    if candidate:
        raise ProjectError(
            f"project {candidate.name!r} was found at {candidate.path}, but is not approved yet; approve it first"
        )

    target = clean_name or path or ""
    raise ProjectError(f"project {target!r} was not found in configured repos, registry, or trusted roots")


def discover_projects(config: WorkerConfig, *, max_depth: int = 5) -> list[Project]:
    found: dict[Path, Project] = {}
    for root in config.trusted_roots:
        if not root.exists() or not root.is_dir():
            continue
        root = root.resolve()
        for current, dirs, _files in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            dirs[:] = [item for item in dirs if item not in _SKIP_DIRS and not item.startswith(".")]
            if (current_path / ".git").exists():
                found[current_path.resolve()] = Project(
                    name=_clean_project_name(current_path.name),
                    path=current_path.resolve(),
                    source="discovered",
                    approved=False,
                )
                dirs[:] = []
                continue
            if depth >= max_depth:
                dirs[:] = []
    return sorted(found.values(), key=lambda project: project.name.lower())


def _resolve_candidate(config: WorkerConfig, *, name: str | None, path: str | None) -> Project | None:
    if path:
        project_path = Path(path).expanduser().resolve()
        if not _is_in_trusted_roots(config, project_path):
            raise ProjectError(f"project path is outside trusted roots: {project_path}")
        _ensure_project_path(project_path)
        return Project(name=name or _clean_project_name(project_path.name), path=project_path, source="discovered", approved=False)

    if not name:
        return None
    name_lower = name.lower()
    matches = [
        project
        for project in discover_projects(config)
        if project.name == name or project.path.name == name or project.name.lower() == name_lower or project.path.name.lower() == name_lower
    ]
    if len(matches) > 1:
        options = ", ".join(str(project.path) for project in matches[:5])
        raise ProjectError(f"project name {name!r} is ambiguous: {options}")
    return matches[0] if matches else None


def _ensure_project_path(path: Path) -> None:
    if not path.exists():
        raise ProjectError(f"project path does not exist: {path}")
    if not path.is_dir():
        raise ProjectError(f"project path is not a directory: {path}")
    if not (path / ".git").exists():
        raise ProjectError(f"project is not a git repository: {path}")


def _init_git_project(path: Path, name: str) -> None:
    readme = path / "README.md"
    if not readme.exists():
        readme.write_text(f"# {name}\n", encoding="utf-8")
    try:
        _run_git(["git", "init"], path)
        _run_git(["git", "add", "README.md"], path)
        _run_git(
            [
                "git",
                "-c",
                "user.email=dagent@local",
                "-c",
                "user.name=dAgent",
                "commit",
                "-m",
                "Initial commit",
            ],
            path,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProjectError(f"could not initialize git repository at {path}") from exc


def _run_git(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _is_in_trusted_roots(config: WorkerConfig, path: Path) -> bool:
    if not config.trusted_roots:
        return False
    resolved = path.resolve()
    return any(_is_relative_to(resolved, root.resolve()) for root in config.trusted_roots)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _clean_project_name(value: str | None) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    if not cleaned:
        raise ProjectError("project name is required")
    return cleaned[:120]


def _find_key(mapping: dict[str, Any], key: str | None) -> str | None:
    if not key:
        return None
    if key in mapping:
        return key
    key_lower = key.lower()
    for candidate in mapping:
        if candidate.lower() == key_lower:
            return candidate
    return None


def _has_project(projects: dict[str, dict[str, Any]], project: Project) -> bool:
    project_path = str(project.path)
    project_name = project.name.lower()
    return any(
        str(item.get("path") or "") == project_path or str(item.get("name") or "").lower() == project_name
        for item in projects.values()
    )
