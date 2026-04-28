from __future__ import annotations

import hashlib
import hmac
import secrets
import time


def make_approval_code() -> str:
    return secrets.token_urlsafe(24)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_secret(value: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(value), expected_hash)


def bearer_token_matches(header_value: str | None, expected_token: str) -> bool:
    if not header_value or not header_value.startswith("Bearer "):
        return False
    supplied = header_value.removeprefix("Bearer ").strip()
    return hmac.compare_digest(supplied, expected_token)


def sign_body(secret: str, timestamp: str, body: bytes) -> str:
    payload = timestamp.encode("utf-8") + b"." + body
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={signature}"


def verify_body_signature(
    secret: str,
    timestamp: str | None,
    body: bytes,
    signature: str | None,
    *,
    now: float | None = None,
    max_skew_seconds: int = 300,
) -> bool:
    if not secret:
        return True
    if not timestamp or not signature:
        return False
    try:
        request_time = int(timestamp)
    except ValueError:
        return False
    current_time = int(now if now is not None else time.time())
    if abs(current_time - request_time) > max_skew_seconds:
        return False
    expected = sign_body(secret, timestamp, body)
    return hmac.compare_digest(signature, expected)

