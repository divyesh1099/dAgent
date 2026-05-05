from types import SimpleNamespace

from dagent_worker.config import NotificationConfig, WorkerConfig
from dagent_worker.jobs import JobStore
from dagent_worker.main import (
    _cancel_chatgpt_thread_response,
    _collision_idempotency_key,
    _continue_job_response,
    _delete_chatgpt_thread_response,
    _job_thread_session_id,
    _normalize_shortcut_body,
    _same_idempotent_payload,
    _session_id_from_text,
    _shortcut_approval_code,
    _shortcut_job_body,
)
from dagent_worker.notifier import Notifier
from dagent_worker.schemas import JobContinueRequest


def test_idempotency_payload_match_uses_full_payload() -> None:
    payload = {
        "intent": "capture_idea",
        "task": "same text",
        "source": "apple_watch",
        "input_type": "voice",
        "idempotency_key": "watch-1",
    }

    assert _same_idempotent_payload(payload, dict(payload))
    assert not _same_idempotent_payload(payload, {**payload, "task": "different text"})


def test_collision_idempotency_key_is_fresh_and_bounded() -> None:
    key = "n8n-" + ("x" * 200)

    collision_key = _collision_idempotency_key(key)

    assert collision_key != key
    assert len(collision_key) <= 160
    assert collision_key.startswith("n8n-")


def test_shortcut_body_accepts_json_string_body_field() -> None:
    body = _normalize_shortcut_body(
        {
            "Body": '{"scan":true,"include_new":true,"source":"iOS","intent":"list_projects"}',
        }
    )

    assert body == {
        "scan": True,
        "include_new": True,
        "source": "iOS",
        "intent": "list_projects",
    }


def test_shortcut_job_body_normalizes_ios_source_and_input_type() -> None:
    payload = _shortcut_job_body(
        {
            "source": "iOS",
            "intent": "code_task",
            "repo": "dagent",
            "task": "check route",
            "input_type": "Shortcut",
        }
    )

    assert payload["source"] == "ios"
    assert payload["input_type"] == "shortcut"


def test_shortcut_job_body_accepts_iphone_task_variants() -> None:
    payload = _shortcut_job_body(
        {
            "source": "apple_watch",
            "intent": "chatgpt_task",
            "task": None,
            "Task": "reply from shortcut variable",
            "wait_seconds": 90,
        }
    )

    assert payload["task"] == "reply from shortcut variable"
    assert "Task" not in payload
    assert "wait_seconds" not in payload


def test_shortcut_job_body_accepts_message_as_task() -> None:
    payload = _shortcut_job_body(
        {
            "intent": "chat",
            "message": "hello assistant",
            "unused_shortcut_field": "ignored",
        }
    )

    assert payload["intent"] == "chatgpt_task"
    assert payload["task"] == "hello assistant"
    assert "message" not in payload
    assert "unused_shortcut_field" not in payload


def test_shortcut_approval_code_ignores_missing_and_blank_values() -> None:
    assert _shortcut_approval_code({"approval_code": None}) is None
    assert _shortcut_approval_code({"approval_code": "   "}) is None
    assert _shortcut_approval_code({"code": " approve-me "}) == "approve-me"


def test_codex_session_id_can_be_read_from_worker_output() -> None:
    text = "OpenAI Codex\nsession id: 019df010-746e-7bc0-abf1-6f4fe81fe143\n"

    assert _session_id_from_text(text) == "019df010-746e-7bc0-abf1-6f4fe81fe143"


def test_continue_job_reuses_parent_thread_id(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    config = WorkerConfig(
        data_dir=tmp_path / "worker",
        notes_dir=tmp_path / "notes",
        approval_required_intents=frozenset({"chatgpt_task"}),
        notifications=NotificationConfig(),
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            config=config,
            store=store,
            notifier=Notifier(config.notifications),
            worker_name="chatgpt",
        )
    )
    parent = store.create(
        payload={
            "intent": "chatgpt_task",
            "task": "first prompt",
        },
        status="succeeded",
        idempotency_key=None,
        approval_hash=None,
    )
    session_id = "019df010-746e-7bc0-abf1-6f4fe81fe143"
    store.finish(parent["id"], status="succeeded", result={"kind": "chatgpt_task", "session_id": session_id})

    response = _continue_job_response(app, parent["id"], JobContinueRequest(task="follow up"))

    child = store.get(response.id)
    assert child is not None
    assert child["task"] == "follow up"
    assert child["payload"]["metadata"]["resume_session_id"] == session_id
    assert child["payload"]["metadata"]["continuation_of"] == parent["id"]
    assert _job_thread_session_id(child, config) == session_id

    store.close()


def test_chatgpt_thread_cancel_and_delete_apply_to_same_session(tmp_path) -> None:
    class RunnerStub:
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        def cancel(self, job_id: str) -> None:
            self.cancelled.append(job_id)

    store = JobStore(tmp_path / "jobs.sqlite3")
    config = WorkerConfig(
        data_dir=tmp_path / "worker",
        notes_dir=tmp_path / "notes",
        notifications=NotificationConfig(),
    )
    runner = RunnerStub()
    app = SimpleNamespace(
        state=SimpleNamespace(
            config=config,
            store=store,
            runner=runner,
            worker_name="chatgpt",
        )
    )
    session_id = "019df010-746e-7bc0-abf1-6f4fe81fe143"
    parent = store.create(
        payload={
            "intent": "chatgpt_task",
            "task": "first prompt",
        },
        status="succeeded",
        idempotency_key=None,
        approval_hash=None,
    )
    child = store.create(
        payload={
            "intent": "chatgpt_task",
            "task": "follow up",
            "metadata": {"resume_session_id": session_id, "thread_id": session_id, "continuation_of": parent["id"]},
        },
        status="queued",
        idempotency_key=None,
        approval_hash=None,
    )
    store.finish(parent["id"], status="succeeded", result={"kind": "chatgpt_task", "session_id": session_id})

    cancelled = _cancel_chatgpt_thread_response(app, parent["id"])

    assert cancelled["thread_id"] == session_id
    assert cancelled["cancelled"] == [child["id"]]
    assert runner.cancelled == [child["id"]]
    assert store.get(child["id"])["status"] == "cancelled"

    deleted = _delete_chatgpt_thread_response(app, parent["id"])

    assert set(deleted["deleted"]) == {parent["id"], child["id"]}
    assert store.get(parent["id"]) is None
    assert store.get(child["id"]) is None

    store.close()
