from __future__ import annotations

from pathlib import Path

from dagent_worker.config import NotificationConfig, WorkerConfig, load_config
from dagent_worker.runner import _builtin_agent_resume_tool, _builtin_agent_tool, _builtin_code_tool


def test_load_config_autodiscovers_codex_from_code_server_extension(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    binary = home / ".local" / "share" / "code-server" / "extensions" / "openai.chatgpt-0.4.78-universal" / "bin" / "linux-x86_64" / "codex"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)

    config_path = tmp_path / "worker" / "config.yml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.delenv("DAGENT_CODEX_BIN", raising=False)

    config = load_config(config_path)

    assert config.code_codex_executable == str(binary.resolve())


def test_load_config_prefers_env_codex_bin_override(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "worker" / "config.yml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}\n", encoding="utf-8")

    override = tmp_path / "tools" / "codex"
    monkeypatch.setenv("DAGENT_CODEX_BIN", str(override))

    config = load_config(config_path)

    assert config.code_codex_executable == str(override.resolve())


def test_builtin_codex_tools_use_configured_executable(tmp_path: Path) -> None:
    config = WorkerConfig(
        data_dir=tmp_path / "worker",
        notes_dir=tmp_path / "notes",
        code_codex_executable="/tmp/custom-codex",
        notifications=NotificationConfig(),
    )

    assert _builtin_code_tool(config, "codex").command[0] == "/tmp/custom-codex"
    assert _builtin_agent_tool(config, "codex").command[0] == "/tmp/custom-codex"
    assert _builtin_agent_resume_tool(config, "codex").command[0] == "/tmp/custom-codex"
