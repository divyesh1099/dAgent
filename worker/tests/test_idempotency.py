from dagent_worker.main import _collision_idempotency_key, _normalize_shortcut_body, _same_idempotent_payload, _shortcut_job_body


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
