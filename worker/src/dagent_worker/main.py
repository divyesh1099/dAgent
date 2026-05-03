from __future__ import annotations

from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import html
import json
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from uuid import uuid4

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import ValidationError

from .config import WorkerConfig, load_config
from .jobs import JobStore, utc_now
from .metrics import render_metrics
from .notifier import Notifier
from .projects import Project, ProjectError, add_project, approve_project, list_projects, resolve_project
from .runner import JobRunner
from .schemas import (
    ApprovalRequest,
    JobRequest,
    JobRequeueRequest,
    JobResponse,
    JobStatus,
    ProjectAddRequest,
    ProjectListResponse,
    ProjectResponse,
    ReadyResponse,
)
from .security import bearer_token_matches, hash_secret, make_approval_code, verify_body_signature, verify_secret


STATIC_DIR = Path(__file__).resolve().parent / "static"
NEW_PROJECT_OPTION = "New Project"
IDEA_DOCUMENT_INTENTS = {"capture_idea"}
IDEA_DOCUMENT_MAX_HTML_BYTES = 2_000_000
IDEA_DOCUMENT_MAX_ASSET_BYTES = 2_500_000
SHARE_VISIBILITIES = {"private", "public"}
INLINE_DATA_ATTACHMENT_LINK_RE = re.compile(
    r"<a\b[^>]*\bhref\s*=\s*([\"'])data:(?:application/(?:pdf|vnd\.|msword|octet-stream)|text/(?:csv|plain|markdown))[^\"']*\1[^>]*>(.*?)</a>",
    flags=re.IGNORECASE | re.DOTALL,
)


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


@app.get("/", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
@app.get("/ui/", response_class=HTMLResponse)
def dashboard_ui() -> HTMLResponse:
    dashboard_path = STATIC_DIR / "dashboard.html"
    return HTMLResponse(dashboard_path.read_text(encoding="utf-8"))


@app.get("/ui/detail", response_class=HTMLResponse)
@app.get("/ui/detail/", response_class=HTMLResponse)
def job_detail_ui() -> HTMLResponse:
    detail_path = STATIC_DIR / "detail.html"
    return HTMLResponse(detail_path.read_text(encoding="utf-8"))


@app.get("/public/share/{worker_name}/{share_id}", response_class=HTMLResponse)
def dashboard_public_share(worker_name: str, share_id: str, request: Request) -> HTMLResponse:
    worker = _find_worker(request.app, worker_name)
    share = _worker_request_json(worker, "GET", f"/v1/shares/{quote(share_id)}?visibility=public")
    return HTMLResponse(_render_share_html(share, worker_name=worker_name))


@app.get("/private/share/{worker_name}/{share_id}", response_class=HTMLResponse)
def dashboard_private_share(worker_name: str, share_id: str, request: Request) -> HTMLResponse:
    worker = _find_worker(request.app, worker_name)
    share = _worker_request_json(worker, "GET", f"/v1/shares/{quote(share_id)}?visibility=private")
    return HTMLResponse(_render_share_html(share, worker_name=worker_name))


@app.get("/public/share/{share_id}", response_class=HTMLResponse)
def public_share(share_id: str, request: Request) -> HTMLResponse:
    share = _load_share(request.app.state.config, share_id, visibility="public")
    return HTMLResponse(_render_share_html(share, worker_name=request.app.state.worker_name))


@app.get("/private/share/{share_id}", response_class=HTMLResponse)
def private_share(share_id: str, request: Request) -> HTMLResponse:
    share = _load_share(request.app.state.config, share_id, visibility="private")
    return HTMLResponse(_render_share_html(share, worker_name=request.app.state.worker_name))


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


@app.get("/v1/projects", response_model=ProjectListResponse)
def projects(
    request: Request,
    scan: bool = False,
    include_new: bool = False,
    _auth: None = Depends(require_auth_sync),
) -> ProjectListResponse:
    config: WorkerConfig = request.app.state.config
    try:
        return _project_list_response(config, scan=scan, include_new_option=include_new)
    except ProjectError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/v1/projects/options", response_model=ProjectListResponse)
def project_options(
    request: Request,
    scan: bool = True,
    include_new: bool = True,
    _auth: None = Depends(require_auth_sync),
) -> ProjectListResponse:
    config: WorkerConfig = request.app.state.config
    try:
        return _project_list_response(config, scan=scan, include_new_option=include_new)
    except ProjectError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/projects", response_model=ProjectResponse)
def add_code_project(
    body: ProjectAddRequest,
    request: Request,
    _auth: None = Depends(require_auth_sync),
) -> ProjectResponse:
    config: WorkerConfig = request.app.state.config
    try:
        project = _add_project(config, body)
    except ProjectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _project_response(project)


@app.post("/v1/projects/approve", response_model=ProjectResponse)
def approve_code_project(
    request: Request,
    body: ProjectAddRequest,
    _auth: None = Depends(require_auth_sync),
) -> ProjectResponse:
    config: WorkerConfig = request.app.state.config
    try:
        project = approve_project(config, name=_project_request_name(body), path=body.path)
    except ProjectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _project_response(project)


@app.post("/v1/shortcut")
async def shortcut_dispatch(
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
    _auth: None = Depends(require_auth_sync),
) -> dict[str, Any]:
    await require_body_signature(request)
    config: WorkerConfig = request.app.state.config
    body = _normalize_shortcut_body(body)
    action = _shortcut_action(body)

    if action in {"list_projects", "project_list", "projects"}:
        try:
            response = _project_list_response(
                config,
                scan=_boolish(body.get("scan"), default=True),
                include_new_option=_boolish(body.get("include_new"), default=True),
            )
        except ProjectError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return response.model_dump(mode="json")

    if action in {"add_project", "project_add", "new_project"}:
        try:
            add_request = ProjectAddRequest.model_validate(
                {
                    "name": body.get("name"),
                    "repo": body.get("repo"),
                    "project": body.get("project"),
                    "path": body.get("path") or _metadata_value(body, "project_path") or _metadata_value(body, "path"),
                    "create_if_missing": _boolish(body.get("create_if_missing"), default=(action == "new_project")),
                }
            )
            project = _add_project(config, add_request)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        project_response = _project_response(project).model_dump(mode="json")
        return {
            "status": "project_added",
            "repo": project_response["name"],
            "project": project_response,
        }

    if action in {"pending_approvals", "list_approvals", "approval_required"}:
        store: JobStore = request.app.state.store
        approvals = [
            _approval_summary(record)
            for record in store.list_recent(max(1, min(int(body.get("limit") or 50), 200)))
            if record["status"] == JobStatus.approval_required.value
        ]
        return {"status": "ok", "approvals": approvals}

    if action in {"approve_job", "approval", "approve", "reject_job", "reject"}:
        try:
            approval = ApprovalRequest.model_validate(
                {
                    "decision": _approval_decision(action, body),
                    "approval_code": body.get("approval_code") or body.get("code"),
                }
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        job_id = str(body.get("job_id") or body.get("id") or "").strip()
        if not job_id:
            raise HTTPException(status_code=400, detail="job_id is required")
        return _approve_job_response(request.app, job_id, approval).model_dump(mode="json")

    try:
        payload = JobRequest.model_validate(_shortcut_job_body(body))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    idempotency_key = payload.idempotency_key or request.headers.get("Idempotency-Key")
    return _create_job_response(request.app, payload, idempotency_key=idempotency_key).model_dump(mode="json")


@app.post("/v1/jobs", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(payload: JobRequest, request: Request, _auth: None = Depends(require_auth_sync)) -> JobResponse:
    await require_body_signature(request)
    return _create_job_response(
        request.app,
        payload,
        idempotency_key=payload.idempotency_key or request.headers.get("Idempotency-Key"),
    )


@app.get("/v1/jobs", response_model=list[JobResponse])
def list_jobs(request: Request, limit: int = 50, _auth: None = Depends(require_auth_sync)) -> list[JobResponse]:
    store: JobStore = request.app.state.store
    bounded_limit = max(1, min(limit, 200))
    return [_response(record, worker_name=request.app.state.worker_name) for record in store.list_recent(bounded_limit)]


@app.get("/v1/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> JobResponse:
    store: JobStore = request.app.state.store
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _response(record, worker_name=request.app.state.worker_name)


@app.get("/v1/jobs/{job_id}/log", response_class=PlainTextResponse)
def get_job_log(job_id: str, request: Request, tail: int = 60000, _auth: None = Depends(require_auth_sync)) -> PlainTextResponse:
    store: JobStore = request.app.state.store
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    log_path = record.get("log_path")
    if not log_path:
        return PlainTextResponse("")
    config: WorkerConfig = request.app.state.config
    root = (config.data_dir / "logs").resolve()
    path = Path(str(log_path)).expanduser().resolve()
    if not _is_relative_to(path, root):
        raise HTTPException(status_code=403, detail="log path is outside worker log directory")
    return PlainTextResponse(_read_tail(path, tail))


@app.get("/v1/jobs/{job_id}/note", response_class=PlainTextResponse)
def get_job_note(job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> PlainTextResponse:
    store: JobStore = request.app.state.store
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    result = record.get("result") or {}
    note_path = result.get("note_path") if isinstance(result, dict) else None
    if not note_path:
        raise HTTPException(status_code=404, detail="job has no note output")
    config: WorkerConfig = request.app.state.config
    root = config.notes_dir.resolve()
    path = Path(str(note_path)).expanduser().resolve()
    if not _is_relative_to(path, root):
        raise HTTPException(status_code=403, detail="note path is outside worker notes directory")
    return PlainTextResponse(_read_tail(path, 120000))


@app.get("/v1/jobs/{job_id}/idea-document")
def get_idea_document(job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> dict[str, Any]:
    store: JobStore = request.app.state.store
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    _ensure_idea_document_job(record)
    config: WorkerConfig = request.app.state.config
    return _load_idea_document(record, config)


@app.put("/v1/jobs/{job_id}/idea-document")
def put_idea_document(
    job_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
    _auth: None = Depends(require_auth_sync),
) -> dict[str, Any]:
    store: JobStore = request.app.state.store
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    _ensure_idea_document_job(record)
    config: WorkerConfig = request.app.state.config
    document = _normalize_idea_document(body, record, touch=True)
    _write_idea_document(document, record, config)
    return document


@app.post("/v1/jobs/{job_id}/shares")
def create_job_share(
    job_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
    _auth: None = Depends(require_auth_sync),
) -> dict[str, Any]:
    store: JobStore = request.app.state.store
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    _ensure_idea_document_job(record)
    config: WorkerConfig = request.app.state.config
    visibility = _share_visibility(body.get("visibility"))
    document = _load_idea_document(record, config)
    share = _create_share(record, document, config, visibility=visibility)
    share["url_path"] = f"/{visibility}/share/{share['id']}"
    return share


@app.get("/v1/shares/{share_id}")
def get_share(
    share_id: str,
    request: Request,
    visibility: str | None = None,
    _auth: None = Depends(require_auth_sync),
) -> dict[str, Any]:
    return _load_share(request.app.state.config, share_id, visibility=visibility)


@app.post("/v1/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> JobResponse:
    store: JobStore = request.app.state.store
    runner: JobRunner = request.app.state.runner
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    if record["status"] in _TERMINAL_STATUSES:
        return _response(record, worker_name=request.app.state.worker_name)
    runner.cancel(job_id)
    cancelled = store.set_status(job_id, JobStatus.cancelled.value, error="cancel requested")
    return _response(cancelled, worker_name=request.app.state.worker_name)


@app.post("/v1/jobs/{job_id}/retry", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_job(job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> JobResponse:
    store: JobStore = request.app.state.store
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    payload = JobRequest.model_validate({**record["payload"], "idempotency_key": None})
    return _enqueue_job(request.app, payload, idempotency_key=_derived_idempotency_key("retry", job_id))


@app.post("/v1/jobs/{job_id}/requeue", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
def requeue_job(
    job_id: str,
    edits: JobRequeueRequest,
    request: Request,
    _auth: None = Depends(require_auth_sync),
) -> JobResponse:
    store: JobStore = request.app.state.store
    runner: JobRunner = request.app.state.runner
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    if edits.cancel_existing and record["status"] not in _TERMINAL_STATUSES:
        runner.cancel(job_id)
        store.set_status(job_id, JobStatus.cancelled.value, error="cancelled before edited requeue")

    base = dict(record["payload"])
    for key, value in edits.model_dump(mode="json", exclude_none=True).items():
        if key != "cancel_existing":
            base[key] = value
    base["idempotency_key"] = None
    payload = JobRequest.model_validate(base)
    return _enqueue_job(request.app, payload, idempotency_key=_derived_idempotency_key("edit", job_id))


@app.post("/v1/jobs/{job_id}/notify")
def notify_job(job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> dict[str, Any]:
    store: JobStore = request.app.state.store
    notifier: Notifier = request.app.state.notifier
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "ok": True,
        "job": _response(record, worker_name=request.app.state.worker_name).model_dump(mode="json"),
        "notifications": notifier.job_finished(record),
    }


@app.delete("/v1/jobs/{job_id}")
def delete_job(job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> dict[str, Any]:
    store: JobStore = request.app.state.store
    runner: JobRunner = request.app.state.runner
    record = store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    if record["status"] not in _TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"job is {record['status']}; stop it before deleting")

    log_deleted = _delete_job_log(record, request.app.state.config)
    if record["status"] == JobStatus.running.value:
        runner.cancel(job_id)
    deleted = store.delete(job_id)
    return {
        "ok": deleted,
        "id": job_id,
        "worker": request.app.state.worker_name,
        "log_deleted": log_deleted,
    }


@app.get("/v1/dashboard")
def dashboard(request: Request, limit_per_worker: int = 50, _auth: None = Depends(require_auth_sync)) -> dict[str, Any]:
    bounded_limit = max(1, min(limit_per_worker, 200))
    workers = _discover_workers(request.app)
    worker_results: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []

    for worker in workers:
        item = {"name": worker["name"], "url": worker["url"], "ok": False, "job_count": 0}
        try:
            ready = _worker_request_json(worker, "GET", "/ready")
            worker_jobs = _worker_request_json(worker, "GET", f"/v1/jobs?limit={bounded_limit}")
        except Exception as exc:
            item["error"] = str(exc)
        else:
            item["ok"] = True
            item["ready"] = ready
            item["job_count"] = len(worker_jobs)
            for job in worker_jobs:
                job["worker"] = worker["name"]
                jobs.append(job)
        worker_results.append(item)

    jobs.sort(key=lambda job: str(job.get("created_at") or ""), reverse=True)
    return {
        "generated_at": time.time(),
        "workers": worker_results,
        "jobs": jobs,
    }


@app.get("/v1/dashboard/workers/{worker_name}/jobs/{job_id}/log", response_class=PlainTextResponse)
def dashboard_worker_log(
    worker_name: str,
    job_id: str,
    request: Request,
    tail: int = 60000,
    _auth: None = Depends(require_auth_sync),
) -> PlainTextResponse:
    worker = _find_worker(request.app, worker_name)
    text = _worker_request_text(worker, "GET", f"/v1/jobs/{quote(job_id)}/log?tail={max(1, min(tail, 200000))}")
    return PlainTextResponse(text)


@app.get("/v1/dashboard/workers/{worker_name}/jobs/{job_id}")
def dashboard_worker_job(worker_name: str, job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> dict[str, Any]:
    worker = _find_worker(request.app, worker_name)
    job = _worker_request_json(worker, "GET", f"/v1/jobs/{quote(job_id)}")
    if isinstance(job, dict):
        job["worker"] = worker_name
    return job


@app.get("/v1/dashboard/workers/{worker_name}/jobs/{job_id}/note", response_class=PlainTextResponse)
def dashboard_worker_note(worker_name: str, job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> PlainTextResponse:
    worker = _find_worker(request.app, worker_name)
    text = _worker_request_text(worker, "GET", f"/v1/jobs/{quote(job_id)}/note")
    return PlainTextResponse(text)


@app.get("/v1/dashboard/workers/{worker_name}/jobs/{job_id}/idea-document")
def dashboard_worker_idea_document(
    worker_name: str,
    job_id: str,
    request: Request,
    _auth: None = Depends(require_auth_sync),
) -> dict[str, Any]:
    worker = _find_worker(request.app, worker_name)
    return _worker_request_json(worker, "GET", f"/v1/jobs/{quote(job_id)}/idea-document")


@app.put("/v1/dashboard/workers/{worker_name}/jobs/{job_id}/idea-document")
def dashboard_worker_put_idea_document(
    worker_name: str,
    job_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
    _auth: None = Depends(require_auth_sync),
) -> dict[str, Any]:
    worker = _find_worker(request.app, worker_name)
    return _worker_request_json(worker, "PUT", f"/v1/jobs/{quote(job_id)}/idea-document", body)


@app.post("/v1/dashboard/workers/{worker_name}/jobs/{job_id}/shares")
def dashboard_worker_create_share(
    worker_name: str,
    job_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
    _auth: None = Depends(require_auth_sync),
) -> dict[str, Any]:
    visibility = _share_visibility(body.get("visibility"))
    worker = _find_worker(request.app, worker_name)
    share = _worker_request_json(worker, "POST", f"/v1/jobs/{quote(job_id)}/shares", {"visibility": visibility})
    if isinstance(share, dict):
        share["worker"] = worker_name
        share["url_path"] = f"/{visibility}/share/{quote(worker_name)}/{quote(str(share.get('id', '')))}"
    return share


@app.post("/v1/dashboard/workers/{worker_name}/jobs/{job_id}/cancel")
def dashboard_worker_cancel(worker_name: str, job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> dict[str, Any]:
    worker = _find_worker(request.app, worker_name)
    return _worker_request_json(worker, "POST", f"/v1/jobs/{quote(job_id)}/cancel", {})


@app.post("/v1/dashboard/workers/{worker_name}/jobs/{job_id}/retry")
def dashboard_worker_retry(worker_name: str, job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> dict[str, Any]:
    worker = _find_worker(request.app, worker_name)
    return _worker_request_json(worker, "POST", f"/v1/jobs/{quote(job_id)}/retry", {})


@app.post("/v1/dashboard/workers/{worker_name}/jobs/{job_id}/requeue")
def dashboard_worker_requeue(
    worker_name: str,
    job_id: str,
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
    _auth: None = Depends(require_auth_sync),
) -> dict[str, Any]:
    worker = _find_worker(request.app, worker_name)
    return _worker_request_json(worker, "POST", f"/v1/jobs/{quote(job_id)}/requeue", body)


@app.post("/v1/dashboard/workers/{worker_name}/jobs/{job_id}/notify")
def dashboard_worker_notify(worker_name: str, job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> dict[str, Any]:
    worker = _find_worker(request.app, worker_name)
    return _worker_request_json(worker, "POST", f"/v1/jobs/{quote(job_id)}/notify", {})


@app.delete("/v1/dashboard/workers/{worker_name}/jobs/{job_id}")
def dashboard_worker_delete(worker_name: str, job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> dict[str, Any]:
    worker = _find_worker(request.app, worker_name)
    return _worker_request_json(worker, "DELETE", f"/v1/jobs/{quote(job_id)}")


@app.post("/v1/jobs/{job_id}/approval", response_model=JobResponse)
async def approve_job(
    job_id: str,
    approval: ApprovalRequest,
    request: Request,
    _auth: None = Depends(require_auth_sync),
) -> JobResponse:
    await require_body_signature(request)
    return _approve_job_response(request.app, job_id, approval)


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


def _project_list_response(config: WorkerConfig, *, scan: bool, include_new_option: bool) -> ProjectListResponse:
    projects = [
        ProjectResponse(
            name=str(project.get("name") or ""),
            path=str(project.get("path") or ""),
            approved=bool(project.get("approved", False)),
            source=str(project.get("source") or "unknown"),
        )
        for project in list_projects(config, scan=scan)
    ]
    options = _project_options(projects)
    new_project_option = NEW_PROJECT_OPTION if include_new_option else None
    if include_new_option and NEW_PROJECT_OPTION not in options:
        options.append(NEW_PROJECT_OPTION)
    return ProjectListResponse(
        trusted_roots=[str(root) for root in config.trusted_roots],
        projects=projects,
        options=options,
        new_project_option=new_project_option,
    )


def _project_options(projects: list[ProjectResponse]) -> list[str]:
    seen: set[str] = set()
    options: list[str] = []
    for project in projects:
        name = project.name.strip()
        key = name.lower()
        if name and key not in seen:
            options.append(name)
            seen.add(key)
    return options


def _add_project(config: WorkerConfig, body: ProjectAddRequest) -> Project:
    name = _project_request_name(body)
    if not name and not body.path:
        raise ProjectError("project name, repo, project, or path is required")
    return add_project(config, name=name, path=body.path, create_if_missing=body.create_if_missing)


def _project_request_name(body: ProjectAddRequest) -> str | None:
    for value in (body.name, body.repo, body.project):
        if value and str(value).strip():
            return str(value).strip()
    return None


def _project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        name=project.name,
        path=str(project.path),
        approved=project.approved,
        source=project.source,
    )


def _approval_summary(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    return {
        "id": record["id"],
        "status": record["status"],
        "intent": record["intent"],
        "repo": record.get("repo"),
        "task_preview": record["task"] if len(record["task"]) <= 180 else record["task"][:177] + "...",
        "source": payload.get("source"),
        "input_type": payload.get("input_type"),
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }


def _approval_decision(action: str, body: dict[str, Any]) -> str:
    decision = str(body.get("decision") or "").strip().lower()
    if decision in {"approve", "reject"}:
        return decision
    return "reject" if action in {"reject_job", "reject"} else "approve"


def _approve_job_response(app_: FastAPI, job_id: str, approval: ApprovalRequest) -> JobResponse:
    store: JobStore = app_.state.store
    notifier: Notifier = app_.state.notifier
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
        return _response(rejected, worker_name=app_.state.worker_name)

    store.clear_approval(job_id)
    queued = store.set_status(job_id, JobStatus.queued.value)
    _submit_job(app_, job_id)
    return _response(queued, worker_name=app_.state.worker_name)


def _shortcut_action(body: dict[str, Any]) -> str:
    action = body.get("action") or body.get("intent") or ""
    action = str(action).strip().lower()
    if action:
        return action
    return "capture_idea" if body.get("task") or body.get("text") or body.get("idea") else ""


def _normalize_shortcut_body(body: dict[str, Any]) -> dict[str, Any]:
    for key in ("Body", "body"):
        raw = body.get(key)
        if not isinstance(raw, str):
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            merged = {**body, **parsed}
            merged.pop(key, None)
            return merged
    return body


def _shortcut_job_body(body: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in body.items()
        if key
        not in {
            "action",
            "create_if_missing",
            "idea",
            "include_new",
            "name",
            "path",
            "project",
            "scan",
            "text",
            "Body",
        }
    }
    if not payload.get("intent"):
        payload["intent"] = "capture_idea"
    if not payload.get("task"):
        payload["task"] = body.get("text") or body.get("idea")
    if body.get("project") and not payload.get("repo"):
        payload["repo"] = body["project"]
    if not payload.get("source"):
        payload["source"] = "apple_watch"
    elif isinstance(payload.get("source"), str):
        payload["source"] = str(payload["source"]).strip().lower()
    if payload.get("task") and not payload.get("input_type"):
        payload["input_type"] = "voice"
    elif isinstance(payload.get("input_type"), str):
        payload["input_type"] = str(payload["input_type"]).strip().lower()

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    metadata = dict(metadata)
    project_path = body.get("path") or _metadata_value(body, "project_path") or _metadata_value(body, "path")
    if project_path and "project_path" not in metadata:
        metadata["project_path"] = project_path
    if _boolish(body.get("approve_project"), default=False) or _boolish(body.get("create_if_missing"), default=False):
        metadata["approve_project"] = True
    if metadata:
        payload["metadata"] = metadata
    return payload


def _metadata_value(body: dict[str, Any], key: str) -> Any:
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        return metadata.get(key)
    return None


def _boolish(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _create_job_response(app_: FastAPI, payload: JobRequest, *, idempotency_key: str | None) -> JobResponse:
    config: WorkerConfig = app_.state.config
    store: JobStore = app_.state.store
    notifier: Notifier = app_.state.notifier

    _validate_payload(config, payload)

    payload_dict = payload.model_dump(mode="json")
    if idempotency_key:
        existing = store.get_by_idempotency(idempotency_key)
        if existing:
            if _same_idempotent_payload(existing["payload"], payload_dict):
                return _response(existing, worker_name=app_.state.worker_name)
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
        _submit_job(app_, record["id"])
    elif approval_code:
        notifier.approval_required(record, approval_code)

    return _response(record, approval_code=approval_code, worker_name=app_.state.worker_name)


def _validate_payload(config: WorkerConfig, payload: JobRequest) -> None:
    if payload.intent not in config.known_intents():
        raise HTTPException(status_code=403, detail=f"intent {payload.intent!r} is not allowlisted")

    if payload.intent in config.repo_required_intents and not payload.repo:
        raise HTTPException(status_code=400, detail=f"intent {payload.intent!r} requires repo")

    if payload.intent in {"code_task", "codex_task", "claude_task"}:
        _validate_code_project(config, payload)
        return

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


def _validate_code_project(config: WorkerConfig, payload: JobRequest) -> None:
    metadata = payload.metadata if isinstance(payload.metadata, dict) else {}
    name = payload.repo or str(metadata.get("project") or metadata.get("repo") or "").strip() or None
    path = str(metadata.get("project_path") or metadata.get("path") or "").strip() or None
    if not name and not path:
        raise HTTPException(status_code=400, detail=f"intent {payload.intent!r} requires repo, metadata.project, or metadata.project_path")
    try:
        project = resolve_project(config, name=name, path=path, approve=False)
    except ProjectError as exc:
        if metadata.get("approve_project"):
            try:
                resolve_project(config, name=name, path=path, allow_unapproved=True)
            except ProjectError as approve_exc:
                raise HTTPException(status_code=403, detail=str(approve_exc)) from approve_exc
        else:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    else:
        if payload.tool:
            tool = config.tools.get(payload.tool)
            if tool is not None and not tool.allows_repo(project.name):
                raise HTTPException(status_code=403, detail=f"tool {payload.tool!r} is not allowed for project {project.name!r}")


def _submit_job(app_: FastAPI, job_id: str) -> None:
    runner: JobRunner = app_.state.runner
    executor: ThreadPoolExecutor = app_.state.executor
    executor.submit(runner.run, job_id)


_TERMINAL_STATUSES = {
    JobStatus.succeeded.value,
    JobStatus.failed.value,
    JobStatus.rejected.value,
    JobStatus.cancelled.value,
}


def _enqueue_job(app_: FastAPI, payload: JobRequest, *, idempotency_key: str | None) -> JobResponse:
    config: WorkerConfig = app_.state.config
    store: JobStore = app_.state.store
    notifier: Notifier = app_.state.notifier
    _validate_payload(config, payload)

    payload_with_key = JobRequest.model_validate({**payload.model_dump(mode="json"), "idempotency_key": idempotency_key})
    if config.require_approval_for(payload_with_key.intent, payload_with_key.require_approval):
        status_value = JobStatus.approval_required.value
        approval_code = make_approval_code()
        approval_hash = hash_secret(approval_code)
    else:
        status_value = JobStatus.queued.value
        approval_code = None
        approval_hash = None

    record = store.create(
        payload=payload_with_key.model_dump(mode="json"),
        status=status_value,
        idempotency_key=idempotency_key,
        approval_hash=approval_hash,
    )

    if status_value == JobStatus.queued.value:
        _submit_job(app_, record["id"])
    elif approval_code:
        notifier.approval_required(record, approval_code)
    return _response(record, approval_code=approval_code, worker_name=app_.state.worker_name)


def _derived_idempotency_key(prefix: str, job_id: str) -> str:
    return f"{prefix}-{job_id[:12]}-{uuid4().hex[:12]}"


def _same_idempotent_payload(existing_payload: dict[str, Any], incoming_payload: dict[str, Any]) -> bool:
    return _canonical_payload(existing_payload) == _canonical_payload(incoming_payload)


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _collision_idempotency_key(idempotency_key: str) -> str:
    suffix = f"-{uuid4().hex[:12]}"
    prefix_length = 160 - len(suffix)
    return f"{idempotency_key[:prefix_length]}{suffix}"


def _response(record: dict[str, Any], approval_code: str | None = None, worker_name: str | None = None) -> JobResponse:
    task = record["task"]
    preview = task if len(task) <= 180 else task[:177] + "..."
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    return JobResponse(
        id=record["id"],
        worker=worker_name,
        status=JobStatus(record["status"]),
        intent=record["intent"],
        repo=record.get("repo"),
        tool=record.get("tool"),
        source=payload.get("source"),
        input_type=payload.get("input_type"),
        task_preview=preview,
        created_at=record["created_at"],
        updated_at=record["updated_at"],
        started_at=record.get("started_at"),
        finished_at=record.get("finished_at"),
        idempotency_key=record.get("idempotency_key"),
        payload=payload,
        result=record.get("result"),
        error=record.get("error"),
        log_path=record.get("log_path"),
        approval_code=approval_code,
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_tail(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    bounded = max(1, min(limit, 500000))
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - bounded))
        return handle.read().decode("utf-8", errors="replace")


def _delete_job_log(record: dict[str, Any], config: WorkerConfig) -> bool:
    log_path = record.get("log_path")
    if not log_path:
        return False
    root = (config.data_dir / "logs").resolve()
    path = Path(str(log_path)).expanduser().resolve()
    if not _is_relative_to(path, root):
        raise HTTPException(status_code=403, detail="log path is outside worker log directory")
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _ensure_idea_document_job(record: dict[str, Any]) -> None:
    if record.get("intent") not in IDEA_DOCUMENT_INTENTS:
        raise HTTPException(status_code=409, detail="this job does not have an idea document")


def _idea_document_path(config: WorkerConfig, job_id: str) -> Path:
    root = (config.notes_dir / ".dagent-idea-docs").resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = (root / f"{job_id}.json").resolve()
    if not _is_relative_to(path, root):
        raise HTTPException(status_code=403, detail="idea document path is outside notes directory")
    return path


def _load_idea_document(record: dict[str, Any], config: WorkerConfig) -> dict[str, Any]:
    path = _idea_document_path(config, str(record["id"]))
    if not path.exists():
        return _default_idea_document(record, config)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="idea document is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=500, detail="idea document is not a JSON object")
    base = _default_idea_document(record, config)
    merged = {**base, **raw}
    if "title" in raw and "title_auto" not in raw:
        merged["title_auto"] = False
    return _normalize_idea_document(merged, record, touch=False)


def _write_idea_document(document: dict[str, Any], record: dict[str, Any], config: WorkerConfig) -> None:
    path = _idea_document_path(config, str(record["id"]))
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _default_idea_document(record: dict[str, Any], config: WorkerConfig) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    task = str(payload.get("task") or record.get("task") or "").strip()
    note_text = _editable_idea_text(record, config) or task
    metadata_title = _clean_short_text(metadata.get("title"), max_length=240)
    title = metadata_title or _title_from_text(note_text)
    return {
        "job_id": record["id"],
        "intent": record["intent"],
        "title": title,
        "title_auto": not bool(metadata_title),
        "visibility": "private",
        "content_html": _markdownish_to_html(note_text),
        "assets": _file_refs_to_assets(payload.get("files") or []),
        "capture": _idea_document_context(record),
        "created_at": record.get("created_at") or utc_now(),
        "updated_at": record.get("updated_at") or utc_now(),
        "source_note": _note_path_for_record(record, config),
    }


def _normalize_idea_document(data: dict[str, Any], record: dict[str, Any], *, touch: bool) -> dict[str, Any]:
    visibility = str(data.get("visibility") or "private").strip().lower()
    if visibility not in {"private", "public"}:
        raise HTTPException(status_code=400, detail="visibility must be private or public")

    content_html = _strip_inline_data_attachment_links(str(data.get("content_html") or "").strip())
    if len(content_html.encode("utf-8")) > IDEA_DOCUMENT_MAX_HTML_BYTES:
        raise HTTPException(status_code=413, detail="idea document is too large")
    content_html = _strip_generated_capture_scaffold_html(content_html)
    if not content_html:
        content_html = "<p></p>"
    title_auto = data.get("title_auto") is True
    supplied_title = _clean_short_text(data.get("title"), max_length=240)
    if title_auto or not supplied_title:
        title = _title_from_content_html(content_html)
        title_auto = True
    else:
        title = supplied_title

    raw_assets = data.get("assets") or []
    if not isinstance(raw_assets, list):
        raise HTTPException(status_code=400, detail="assets must be a list")

    assets: list[dict[str, Any]] = []
    for raw_asset in raw_assets[:80]:
        if not isinstance(raw_asset, dict):
            continue
        asset = _normalize_idea_asset(raw_asset)
        if asset:
            assets.append(asset)

    return {
        "job_id": record["id"],
        "intent": record["intent"],
        "title": title,
        "title_auto": title_auto,
        "visibility": visibility,
        "content_html": content_html,
        "assets": assets,
        "capture": _idea_document_context(record),
        "created_at": _clean_short_text(data.get("created_at") or record.get("created_at") or utc_now(), max_length=80),
        "updated_at": utc_now() if touch else _clean_short_text(data.get("updated_at") or record.get("updated_at") or utc_now(), max_length=80),
        "source_note": _clean_short_text(data.get("source_note") or _note_path_for_record(record, None), max_length=2000),
    }


def _normalize_idea_asset(raw_asset: dict[str, Any]) -> dict[str, Any] | None:
    asset: dict[str, Any] = {}
    for key, limit in {
        "id": 120,
        "kind": 40,
        "name": 240,
        "url": 4000,
        "mime_type": 160,
        "note": 1000,
    }.items():
        value = raw_asset.get(key)
        if value is not None:
            cleaned = _clean_short_text(value, max_length=limit)
            if cleaned:
                asset[key] = cleaned
    size = raw_asset.get("size")
    if isinstance(size, int | float) and size >= 0:
        asset["size"] = int(size)
    data_url = raw_asset.get("data_url")
    if isinstance(data_url, str) and data_url.startswith("data:"):
        if len(data_url.encode("utf-8")) > IDEA_DOCUMENT_MAX_ASSET_BYTES:
            raise HTTPException(status_code=413, detail=f"asset {asset.get('name') or ''} is too large")
        asset["data_url"] = data_url
    if not (asset.get("name") or asset.get("url") or asset.get("data_url")):
        return None
    asset["kind"] = _clean_short_text(asset.get("kind") or _kind_from_asset(asset), max_length=40) or "file"
    return asset


def _strip_inline_data_attachment_links(content_html: str) -> str:
    return INLINE_DATA_ATTACHMENT_LINK_RE.sub(lambda match: f"<span>{match.group(2)}</span>", content_html)


def _read_note_text(record: dict[str, Any], config: WorkerConfig) -> str:
    note_path = _note_path_for_record(record, config)
    if not note_path:
        return ""
    path = Path(note_path).expanduser().resolve()
    return _read_tail(path, 200000)


def _editable_idea_text(record: dict[str, Any], config: WorkerConfig) -> str:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    task = str(payload.get("task") or record.get("task") or "").strip()
    note_text = _read_note_text(record, config)
    if not note_text:
        return task
    return _extract_markdown_section(note_text, "Task") or task or note_text


def _extract_markdown_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    start: int | None = None
    target = heading.strip().lower()
    for index, line in enumerate(lines):
        if line.strip().lower() == f"## {target}":
            start = index + 1
            break
    if start is None:
        return ""

    end = len(lines)
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("## ") and stripped.lower() != f"## {target}":
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def _strip_generated_capture_scaffold_html(content_html: str) -> str:
    stripped = content_html.strip()
    if not re.search(r"<h1>\s*Capture Idea\s*</h1>", stripped, flags=re.IGNORECASE):
        return content_html

    match = re.search(
        r"<h2>\s*Task\s*</h2>(.*?)(?=<h2\b[^>]*>.*?</h2>|$)",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return content_html

    generated_header = stripped[: match.start()]
    if not all(marker in generated_header for marker in ("Job:", "Source:", "Input:")):
        return content_html

    return match.group(1).strip() or "<p></p>"


def _idea_document_context(record: dict[str, Any]) -> dict[str, str]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    return {
        "label": record.get("intent", "").replace("_", " ").title(),
        "job_id": str(record.get("id") or ""),
        "created_at": str(record.get("created_at") or ""),
        "source": str(payload.get("source") or ""),
        "input_type": str(payload.get("input_type") or ""),
    }


def _note_path_for_record(record: dict[str, Any], config: WorkerConfig | None) -> str:
    result = record.get("result") or {}
    note_path = result.get("note_path") if isinstance(result, dict) else None
    if not note_path:
        return ""
    if config is None:
        return str(note_path)
    root = config.notes_dir.resolve()
    path = Path(str(note_path)).expanduser().resolve()
    if not _is_relative_to(path, root):
        return ""
    return str(path)


def _file_refs_to_assets(files: Any) -> list[dict[str, Any]]:
    if not isinstance(files, list):
        return []
    assets: list[dict[str, Any]] = []
    for index, file_ref in enumerate(files[:80]):
        if not isinstance(file_ref, dict):
            continue
        url = file_ref.get("url") or file_ref.get("path") or ""
        name = file_ref.get("name") or url or f"file-{index + 1}"
        mime_type = _clean_short_text(file_ref.get("mime_type") or "", max_length=160)
        asset = {
            "id": f"file-{index + 1}",
            "kind": _kind_from_mime_or_name(mime_type, str(name)),
            "name": _clean_short_text(name, max_length=240),
            "url": _clean_short_text(url, max_length=4000),
            "mime_type": mime_type,
        }
        assets.append({key: value for key, value in asset.items() if value})
    return assets


def _title_from_text(text: str) -> str:
    source_text = next((line.strip() for line in str(text or "").splitlines() if line.strip()), str(text or ""))
    plain_text = re.sub(r"\s+", " ", source_text).strip()
    if not plain_text:
        return "Untitled idea"
    words = plain_text.split()
    title = " ".join(words[:6])
    if len(words) > 6:
        title = title.rstrip(".,;:") + "..."
    return _clean_short_text(title, max_length=72) or "Untitled idea"


def _title_from_content_html(content_html: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", content_html)
    return _title_from_text(html.unescape(without_tags))


def _clean_short_text(value: Any, *, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_length:
        return text[:max_length]
    return text


def _markdownish_to_html(text: str) -> str:
    if not text.strip():
        return "<p></p>"

    output: list[str] = []
    paragraph: list[str] = []
    in_code = False
    code_lines: list[str] = []
    in_list = False

    def flush_paragraph() -> None:
        if paragraph:
            output.append(f"<p>{html.escape(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            output.append("</ul>")
            in_list = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            close_list()
            if in_code:
                output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            flush_paragraph()
            close_list()
            continue
        if stripped.startswith("# "):
            flush_paragraph()
            close_list()
            output.append(f"<h1>{html.escape(stripped[2:].strip())}</h1>")
            continue
        if stripped.startswith("## "):
            flush_paragraph()
            close_list()
            output.append(f"<h2>{html.escape(stripped[3:].strip())}</h2>")
            continue
        if stripped.startswith("### "):
            flush_paragraph()
            close_list()
            output.append(f"<h3>{html.escape(stripped[4:].strip())}</h3>")
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{html.escape(stripped[2:].strip())}</li>")
            continue
        paragraph.append(stripped)

    flush_paragraph()
    close_list()
    if in_code:
        output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    return "\n".join(output) or "<p></p>"


def _kind_from_asset(asset: dict[str, Any]) -> str:
    return _kind_from_mime_or_name(str(asset.get("mime_type") or ""), str(asset.get("name") or asset.get("url") or ""))


def _kind_from_mime_or_name(mime_type: str, name: str) -> str:
    lower_mime = mime_type.lower()
    lower_name = name.lower()
    if lower_mime.startswith("image/") or lower_name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg")):
        return "image"
    if lower_mime.startswith("video/") or lower_name.endswith((".mp4", ".mov", ".webm", ".m4v")):
        return "video"
    if lower_mime == "application/pdf" or lower_name.endswith(".pdf"):
        return "pdf"
    if "spreadsheet" in lower_mime or lower_name.endswith((".xls", ".xlsx", ".csv", ".numbers")):
        return "sheet"
    if lower_name.endswith((".md", ".markdown")):
        return "markdown"
    return "file"


def _share_visibility(value: Any) -> str:
    visibility = str(value or "private").strip().lower()
    if visibility not in SHARE_VISIBILITIES:
        raise HTTPException(status_code=400, detail="visibility must be private or public")
    return visibility


def _share_root(config: WorkerConfig) -> Path:
    root = (config.notes_dir / ".dagent-shares").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _share_path(config: WorkerConfig, share_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,120}", share_id):
        raise HTTPException(status_code=404, detail="share not found")
    root = _share_root(config)
    path = (root / f"{share_id}.json").resolve()
    if not _is_relative_to(path, root):
        raise HTTPException(status_code=403, detail="share path is outside notes directory")
    return path


def _create_share(
    record: dict[str, Any],
    document: dict[str, Any],
    config: WorkerConfig,
    *,
    visibility: str,
) -> dict[str, Any]:
    now = utc_now()
    share_id = secrets.token_urlsafe(24)
    share = {
        "id": share_id,
        "visibility": visibility,
        "job_id": record["id"],
        "intent": record["intent"],
        "title": _clean_short_text(document.get("title") or record.get("task") or "Untitled idea", max_length=240),
        "content_html": _strip_generated_capture_scaffold_html(
            _strip_inline_data_attachment_links(str(document.get("content_html") or "<p></p>"))
        ),
        "assets": document.get("assets") if isinstance(document.get("assets"), list) else [],
        "capture": _idea_document_context(record),
        "created_at": now,
        "updated_at": now,
    }
    path = _share_path(config, share_id)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(share, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)
    return share


def _load_share(config: WorkerConfig, share_id: str, visibility: str | None = None) -> dict[str, Any]:
    expected_visibility = _share_visibility(visibility) if visibility else None
    path = _share_path(config, share_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="share not found")
    try:
        share = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="share is not valid JSON") from exc
    if not isinstance(share, dict):
        raise HTTPException(status_code=500, detail="share is not a JSON object")
    actual_visibility = _share_visibility(share.get("visibility"))
    if expected_visibility and actual_visibility != expected_visibility:
        raise HTTPException(status_code=404, detail="share not found")
    share["visibility"] = actual_visibility
    share["content_html"] = _strip_generated_capture_scaffold_html(
        _strip_inline_data_attachment_links(str(share.get("content_html") or "<p></p>"))
    )
    return share


def _render_share_html(share: dict[str, Any], *, worker_name: str) -> str:
    title = html.escape(str(share.get("title") or "Untitled idea"))
    visibility = html.escape(str(share.get("visibility") or "private"))
    content = str(share.get("content_html") or "<p></p>")
    assets = _render_share_assets_html(share.get("assets"))
    capture = share.get("capture") if isinstance(share.get("capture"), dict) else {}
    created = html.escape(str(share.get("created_at") or ""))
    source = html.escape(str(capture.get("source") or "-"))
    input_type = html.escape(str(capture.get("input_type") or "-"))
    worker = html.escape(worker_name)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: data:; media-src https: data:; frame-src https://www.youtube.com https://www.youtube-nocookie.com https://player.vimeo.com; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f6f7f9; color: #202124; }}
    main {{ max-width: 820px; margin: 0 auto; padding: 44px 20px 64px; }}
    article {{ background: white; border: 1px solid #e5e7eb; border-radius: 18px; padding: clamp(20px, 4vw, 42px); box-shadow: 0 1px 2px rgba(60,64,67,.14), 0 12px 32px rgba(60,64,67,.08); }}
    h1 {{ margin: 0 0 18px; color: #111827; font-size: clamp(28px, 5vw, 44px); line-height: 1.12; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 28px; color: #667085; font-size: 12px; }}
    .pill {{ display: inline-flex; min-height: 24px; align-items: center; border-radius: 999px; background: #f1f3f4; padding: 0 9px; }}
    .content {{ font-size: 16px; line-height: 1.72; overflow-wrap: anywhere; }}
    .content h1, .content h2, .content h3 {{ color: #111827; line-height: 1.22; margin: 24px 0 10px; }}
    .content p, .content ul, .content ol, .content blockquote, .content pre {{ margin: 0 0 14px; }}
    .content blockquote {{ border-left: 3px solid #1a73e8; color: #667085; padding-left: 14px; }}
    .content pre {{ background: #111827; color: #e5e7eb; padding: 14px; border-radius: 12px; overflow: auto; }}
    .content img, .content video, .content iframe {{ display: block; max-width: 100%; border: 1px solid #e5e7eb; border-radius: 12px; background: #f8fafc; margin: 12px 0 16px; }}
    .content iframe {{ width: 100%; aspect-ratio: 16 / 9; }}
    .content table {{ width: 100%; border-collapse: collapse; margin: 12px 0 16px; font-size: 14px; }}
    .content th, .content td {{ border: 1px solid #e5e7eb; padding: 8px; text-align: left; }}
    .assets {{ margin: 0 0 28px; padding: 0 0 24px; border-bottom: 1px solid #e5e7eb; }}
    .assets h2 {{ margin: 0 0 12px; color: #111827; font-size: 18px; }}
    .asset-list {{ display: grid; gap: 10px; }}
    .asset {{ display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; gap: 10px; align-items: center; border: 1px solid #e5e7eb; border-radius: 12px; padding: 10px; background: #fbfcff; }}
    .asset-kind {{ display: grid; place-items: center; width: 36px; height: 36px; border-radius: 10px; background: #eef4ff; color: #1a73e8; font-size: 10px; font-weight: 750; text-transform: uppercase; }}
    .asset-name {{ color: #111827; font-weight: 650; overflow-wrap: anywhere; }}
    .asset-meta {{ display: block; color: #667085; font-size: 12px; margin-top: 2px; overflow-wrap: anywhere; }}
    .asset-link {{ display: inline-flex; align-items: center; min-height: 32px; border: 1px solid #d0d7e2; border-radius: 999px; padding: 0 12px; background: white; text-decoration: none; white-space: nowrap; }}
    .asset-unavailable {{ color: #667085; font-size: 12px; white-space: nowrap; }}
    a {{ color: #1a73e8; }}
    @media (max-width: 560px) {{ .asset {{ grid-template-columns: 36px minmax(0, 1fr); }} .asset-link, .asset-unavailable {{ grid-column: 2; width: max-content; }} }}
  </style>
</head>
<body>
  <main>
    <article>
      <h1>{title}</h1>
      <div class="meta">
        <span class="pill">{visibility}</span>
        <span class="pill">{worker}</span>
        <span class="pill">{source}</span>
        <span class="pill">{input_type}</span>
        <span class="pill">{created}</span>
      </div>
      {assets}
      <div class="content">{content}</div>
    </article>
  </main>
</body>
</html>"""


def _render_share_assets_html(raw_assets: Any) -> str:
    if not isinstance(raw_assets, list) or not raw_assets:
        return ""

    items: list[str] = []
    for raw_asset in raw_assets[:80]:
        if not isinstance(raw_asset, dict):
            continue
        kind = html.escape(str(raw_asset.get("kind") or _kind_from_asset(raw_asset) or "file"))
        name = html.escape(str(raw_asset.get("name") or raw_asset.get("url") or "Untitled attachment"))
        mime_type = str(raw_asset.get("mime_type") or "").strip()
        size = raw_asset.get("size")
        meta_parts = [kind]
        if mime_type:
            meta_parts.append(mime_type)
        if isinstance(size, int | float) and size >= 0:
            meta_parts.append(_format_bytes(int(size)))
        if raw_asset.get("data_url"):
            meta_parts.append("embedded attachment")
        elif _is_external_share_url(str(raw_asset.get("url") or "")):
            meta_parts.append(_url_host(str(raw_asset.get("url") or "")))
        elif raw_asset.get("url"):
            meta_parts.append("local reference")

        href = _share_asset_href(raw_asset)
        action = (
            f'<a class="asset-link" href="{html.escape(href)}" target="_blank" rel="noopener noreferrer" download="{name}">Open</a>'
            if href
            else '<span class="asset-unavailable">No public link</span>'
        )
        items.append(
            f"""
        <div class="asset">
          <span class="asset-kind">{kind[:4]}</span>
          <div>
            <div class="asset-name">{name}</div>
            <span class="asset-meta">{html.escape(' / '.join(part for part in meta_parts if part))}</span>
          </div>
          {action}
        </div>"""
        )

    if not items:
        return ""
    return f"""
      <section class="assets">
        <h2>Assets</h2>
        <div class="asset-list">{''.join(items)}
        </div>
      </section>"""


def _share_asset_href(asset: dict[str, Any]) -> str:
    data_url = str(asset.get("data_url") or "")
    if data_url.startswith(("data:application/pdf", "data:image/", "data:video/", "data:text/", "data:application/vnd.")):
        return data_url
    url = str(asset.get("url") or "")
    if _is_external_share_url(url):
        return url
    return ""


def _is_external_share_url(url: str) -> bool:
    return url.startswith(("https://", "http://"))


def _url_host(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc or "external link"
    except Exception:
        return "external link"


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _worker_env_dir() -> Path:
    config_home = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))).expanduser()
    return config_home / "dagent" / "workers"


def _discover_workers(app_: FastAPI) -> list[dict[str, str]]:
    workers: dict[str, dict[str, str]] = {}
    env_dir = _worker_env_dir()
    for env_file in sorted(env_dir.glob("*.env")) if env_dir.exists() else []:
        values = _parse_env_file(env_file)
        name = values.get("DAGENT_WORKER_NAME") or env_file.stem
        host = values.get("DAGENT_WORKER_HOST") or "127.0.0.1"
        if host == "0.0.0.0":
            host = "127.0.0.1"
        port = values.get("DAGENT_WORKER_PORT")
        token = values.get("DAGENT_WORKER_API_TOKEN")
        if not port or not token:
            continue
        workers[name] = {
            "name": name,
            "url": f"http://{host}:{port}",
            "token": token,
        }

    current_name = str(getattr(app_.state, "worker_name", "main"))
    current_token = str(getattr(app_.state, "api_token", ""))
    current_port = os.getenv("DAGENT_WORKER_PORT")
    current_host = os.getenv("DAGENT_WORKER_HOST", "127.0.0.1")
    if current_host == "0.0.0.0":
        current_host = "127.0.0.1"
    if current_name not in workers and current_token and current_port:
        workers[current_name] = {
            "name": current_name,
            "url": f"http://{current_host}:{current_port}",
            "token": current_token,
        }

    return list(workers.values())


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


def _find_worker(app_: FastAPI, worker_name: str) -> dict[str, str]:
    for worker in _discover_workers(app_):
        if worker["name"] == worker_name:
            return worker
    raise HTTPException(status_code=404, detail=f"worker {worker_name!r} is not configured")


def _worker_request_json(
    worker: dict[str, str],
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> Any:
    raw = _worker_request(worker, method, path, body=body)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _worker_request_text(
    worker: dict[str, str],
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> str:
    return _worker_request(worker, method, path, body=body).decode("utf-8", errors="replace")


def _worker_request(
    worker: dict[str, str],
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> bytes:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {worker['token']}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urlrequest.Request(f"{worker['url']}{path}", data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=5) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") or str(exc)
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
