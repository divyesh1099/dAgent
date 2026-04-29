from __future__ import annotations

from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from uuid import uuid4

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse

from .config import WorkerConfig, load_config
from .jobs import JobStore
from .metrics import render_metrics
from .notifier import Notifier
from .runner import JobRunner
from .schemas import ApprovalRequest, JobRequest, JobRequeueRequest, JobResponse, JobStatus, ReadyResponse
from .security import bearer_token_matches, hash_secret, make_approval_code, verify_body_signature, verify_secret


STATIC_DIR = Path(__file__).resolve().parent / "static"


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
                return _response(existing, worker_name=request.app.state.worker_name)
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

    return _response(record, approval_code=approval_code, worker_name=request.app.state.worker_name)


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


@app.get("/v1/dashboard/workers/{worker_name}/jobs/{job_id}/note", response_class=PlainTextResponse)
def dashboard_worker_note(worker_name: str, job_id: str, request: Request, _auth: None = Depends(require_auth_sync)) -> PlainTextResponse:
    worker = _find_worker(request.app, worker_name)
    text = _worker_request_text(worker, "GET", f"/v1/jobs/{quote(job_id)}/note")
    return PlainTextResponse(text)


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
        return _response(rejected, worker_name=request.app.state.worker_name)

    store.clear_approval(job_id)
    queued = store.set_status(job_id, JobStatus.queued.value)
    _submit_job(request.app, job_id)
    return _response(queued, worker_name=request.app.state.worker_name)


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
