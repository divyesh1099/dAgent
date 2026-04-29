from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InputType(str, Enum):
    text = "text"
    voice = "voice"
    image = "image"
    file = "file"
    mixed = "mixed"


class Source(str, Enum):
    apple_watch = "apple_watch"
    ios = "ios"
    ios_share_sheet = "ios_share_sheet"
    android = "android"
    laptop = "laptop"
    desktop = "desktop"
    n8n = "n8n"
    api = "api"
    unknown = "unknown"


class JobStatus(str, Enum):
    received = "received"
    approval_required = "approval_required"
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    rejected = "rejected"
    cancelled = "cancelled"


class FileRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    url: str | None = Field(default=None, max_length=2000)
    path: str | None = Field(default=None, max_length=2000)
    mime_type: str | None = Field(default=None, max_length=160)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(min_length=2, max_length=96, pattern=r"^[a-z][a-z0-9_:-]*$")
    task: str = Field(min_length=1, max_length=16000)
    repo: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    tool: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    source: Source = Source.api
    input_type: InputType = InputType.text
    files: list[FileRef] = Field(default_factory=list, max_length=20)
    priority: Literal["low", "normal", "high"] = "normal"
    require_approval: bool | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)
    dry_run: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task")
    @classmethod
    def task_must_have_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("task must not be blank")
        return stripped


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    approval_code: str = Field(min_length=8, max_length=200)


class JobResponse(BaseModel):
    id: str
    worker: str | None = None
    status: JobStatus
    intent: str
    repo: str | None = None
    tool: str | None = None
    source: Source | None = None
    input_type: InputType | None = None
    task_preview: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    log_path: str | None = None
    approval_code: str | None = None


class JobRequeueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str | None = Field(default=None, min_length=2, max_length=96, pattern=r"^[a-z][a-z0-9_:-]*$")
    task: str | None = Field(default=None, min_length=1, max_length=16000)
    repo: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    tool: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    source: Source | None = None
    input_type: InputType | None = None
    files: list[FileRef] | None = Field(default=None, max_length=20)
    priority: Literal["low", "normal", "high"] | None = None
    require_approval: bool | None = None
    dry_run: bool | None = None
    metadata: dict[str, Any] | None = None
    cancel_existing: bool = False


class ReadyResponse(BaseModel):
    ok: bool
    configured_repos: list[str]
    configured_tools: list[str]
    max_parallel_jobs: int
