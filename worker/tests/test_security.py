from dagent_worker.security import (
    bearer_token_matches,
    hash_secret,
    sign_body,
    verify_body_signature,
    verify_secret,
)


def test_bearer_token_matches_exact_value() -> None:
    assert bearer_token_matches("Bearer abc123", "abc123")
    assert not bearer_token_matches("Bearer abc124", "abc123")
    assert not bearer_token_matches("Basic abc123", "abc123")


def test_approval_secret_hash_roundtrip() -> None:
    hashed = hash_secret("approval-code")
    assert verify_secret("approval-code", hashed)
    assert not verify_secret("wrong-code", hashed)


def test_body_signature_uses_timestamp_and_body() -> None:
    secret = "secret"
    timestamp = "1700000000"
    body = b'{"intent":"repo_status"}'
    signature = sign_body(secret, timestamp, body)

    assert verify_body_signature(secret, timestamp, body, signature, now=1700000000)
    assert not verify_body_signature(secret, timestamp, b"{}", signature, now=1700000000)
    assert not verify_body_signature(secret, timestamp, body, signature, now=1700001000)

