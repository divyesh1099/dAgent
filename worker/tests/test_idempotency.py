from dagent_worker.main import _collision_idempotency_key, _same_idempotent_payload


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
