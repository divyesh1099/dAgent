from __future__ import annotations

from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import os
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status

from .config import WorkerConfig, load_config
from .jobs import JobStore
from .metrics import render_metrics
from .notifier import Notifier
from .runner import JobRunner
from .schemas import ApprovalRequest, JobRequest, JobResponse, JobStatus, ReadyResponse
from .security import bearer_token_matches, hash_secret, make_approval_code, verify_body_signature, verify_secret


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.notes_dir.mkdir(parents=True, exist_ok=True)

    store = JobStore(config.data_dir / "jobs.sqlite3")
    notifier = Notifier(config.notifications)
    executor = ThreadPoolExecutor(max_workers=config.max_parallel_jobs, thread_name_prefix="dagent-job")
    runner = JobRunner(config, store, notifier)

    app.state.config = config
    app.state.store = store
    app.state.notifier = notifier
    app.state.executor = executor
    app.state.runner = runner
    app.state.api_token = os.getenv("DAGENT_WORKER_API_TOKEN", "")
    app.state.hmac_secret = os.getenv("DAGENT_WORKER_HMAC_SECRET", "")
    app.state.worker_name = os.getenv("DAGENT_WORKER_NAME", "main")

    yield

    executor.shutdown(wait=False, cancel_futures=False)
    store.close()


app = FastAPI(title="dAgent Worker", version="0.1.0", lifespan=lifespan)


def require_auth_sync(request: Request) -> None:
    token = request.app.state.api_token
    if not token:
        raise HTTPException(status_code=503, detail="DAGENT_WORKER_API_TOKEN is not configured")
    if not bearer_token_matches(request.headers.get("Authorization"), token):
        raise HTTPException(status_code=401, detail="invalid worker token")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "dagent-worker"}


@app.get("/metrics")
def metrics(request: Request) -> Response:
    config: WorkerConfig = request.app.state.config
    store: JobStore = request.app.state.store
    payload = render_metrics(config=config, store=store, worker_name=request.app.state.worker_name)
    return Response(content=payload, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/ready", response_model=ReadyResponse)
def ready(request: Request, _auth: None = Depends(require_auth_sync)) -> ReadyResponse:
    config: WorkerConfig = request.app.state.config
    return ReadyResponse(
        ok=True,
        configured_repos=sorted(config.repos.keys()),
        configured_tools=sorted(config.tools.keys()),
        max_parallel_jobs=config.max_parallel_jobs,
    )


@app.post("/v1/jobs", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(payload: JobRequest, request: Request, _auth: None = Depends(require_auth_sync)) -> JobResponse:
    await require_body_signature(request)
    config: WorkerConfig = request.app.state.config
    store: JobStore = request.app.state.store
    notifier: Notifier = request.app.state.notifier

    _validate_payload(config, payload)

    payload_dict = payload.model_dump(mode="json")
    idempotency_key = payload.idempotency_key or request.headers.get("Idempotency-Key")
    if idempotency_key:
        existing = store.get_by_idempotency(idempotency_key)
        if existing:
            if _same_idempotent_payload(existing["payload"], payload_dict):
                return _response(existing)
            idempotency_key = _collision_idempotency_key(idempotency_key)

    approval_code: str | None = None
    approval_hash: str | None = None
    if config.require_approval_for(payload.intent, payload.require_approval):
        status_value = JobStatus.approval_required.value
        approval_code = make_approval_code()
        approval_hash = hash_secret(approval_code)
    else:
        status_value = JobStatus.queued.value

    record = store.create(
        payload=payload_dict,
        status=status_value,
        idempotency_key=idempotency_key,
        approval_hash=approval_hash,
    )

    if status_value == JobStatus.queued.value:
        _submit_job(request.app, record["id"])
    elif approval_code:
        notifier.approval_required(record, approval_code)

    return _response(record, approval_code=approval_code)


@app.get("/v1/jobs", response_model=list[JobResponse])
def list_jobs(request: Request, limit: int = 50, _auth: None = Depends(require_auth_sync)) -> list[JobResponse]:
    store: JobStore = request.app.state.store
    bounded_limit = max(1, min(limit, 200))
    return [_response(record) for record in store.list_recent(bounded_limit)]


@app.get("/v1/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> JobResponse:
    store: JobStore = request.app.state.store
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _response(record)


@app.post("/v1/jobs/{job_id}/approval", response_model=JobResponse)
async def approve_job(
    job_id: str,
    approval: ApprovalRequest,
    request: Request,
    _auth: None = Depends(require_auth_sync),
) -> JobResponse:
    await require_body_signature(request)
    store: JobStore = request.app.state.store
    notifier: Notifier = request.app.state.notifier
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    if record["status"] != JobStatus.approval_required.value:
        raise HTTPException(status_code=409, detail=f"job is {record['status']}, not approval_required")

    if not verify_secret(approval.approval_code, record["approval_hash"] or ""):
        raise HTTPException(status_code=403, detail="invalid approval code")

    if approval.decision == "reject":
        rejected = store.set_status(job_id, JobStatus.rejected.value)
        notifier.job_finished(rejected)
        return _response(rejected)

    store.clear_approval(job_id)
    queued = store.set_status(job_id, JobStatus.queued.value)
    _submit_job(request.app, job_id)
    return _response(queued)


async def require_body_signature(request: Request) -> None:
    secret = request.app.state.hmac_secret
    if not secret:
        return
    body = await request.body()
    ok = verify_body_signature(
        secret,
        request.headers.get("X-Dagent-Timestamp"),
        body,
        request.headers.get("X-Dagent-Signature"),
    )
    if not ok:
        raise HTTPException(status_code=401, detail="invalid request signature")


def _validate_payload(config: WorkerConfig, payload: JobRequest) -> None:
    if payload.intent not in config.known_intents():
        raise HTTPException(status_code=403, detail=f"intent {payload.intent!r} is not allowlisted")

    if payload.intent in config.repo_required_intents and not payload.repo:
        raise HTTPException(status_code=400, detail=f"intent {payload.intent!r} requires repo")

    if payload.repo:
        repo = config.repos.get(payload.repo)
        if repo is None:
            raise HTTPException(status_code=403, detail=f"repo {payload.repo!r} is not configured")
        if not repo.allows_intent(payload.intent):
            raise HTTPException(status_code=403, detail=f"intent {payload.intent!r} is not allowed for repo {payload.repo!r}")

    if payload.tool:
        tool = config.tools.get(payload.tool)
        if tool is None:
            raise HTTPException(status_code=403, detail=f"tool {payload.tool!r} is not configured")
        if not tool.allows_repo(payload.repo):
            raise HTTPException(status_code=403, detail=f"tool {payload.tool!r} is not allowed for repo {payload.repo!r}")


def _submit_job(app_: FastAPI, job_id: str) -> None:
    runner: JobRunner = app_.state.runner
    executor: ThreadPoolExecutor = app_.state.executor
    executor.submit(runner.run, job_id)


def _same_idempotent_payload(existing_payload: dict[str, Any], incoming_payload: dict[str, Any]) -> bool:
    return _canonical_payload(existing_payload) == _canonical_payload(incoming_payload)


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _collision_idempotency_key(idempotency_key: str) -> str:
    suffix = f"-{uuid4().hex[:12]}"
    prefix_length = 160 - len(suffix)
    return f"{idempotency_key[:prefix_length]}{suffix}"


def _response(record: dict[str, Any], approval_code: str | None = None) -> JobResponse:
    task = record["task"]
    preview = task if len(task) <= 180 else task[:177] + "..."
    return JobResponse(
        id=record["id"],
        status=JobStatus(record["status"]),
        intent=record["intent"],
        repo=record.get("repo"),
        tool=record.get("tool"),
        task_preview=preview,
        created_at=record["created_at"],
        updated_at=record["updated_at"],
        result=record.get("result"),
        error=record.get("error"),
        log_path=record.get("log_path"),
        approval_code=approval_code,
    )
