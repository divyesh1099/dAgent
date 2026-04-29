from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_db()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create(
        self,
        *,
        payload: dict[str, Any],
        status: str,
        idempotency_key: str | None,
        approval_hash: str | None,
    ) -> dict[str, Any]:
        now = utc_now()
        job_id = uuid4().hex
        with self._lock:
            if idempotency_key:
                existing = self.get_by_idempotency(idempotency_key)
                if existing:
                    return existing
            self._conn.execute(
                """
                INSERT INTO jobs (
                    id, idempotency_key, intent, repo, tool, task, payload_json, status,
                    approval_hash, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    idempotency_key,
                    payload["intent"],
                    payload.get("repo"),
                    payload.get("tool"),
                    payload["task"],
                    json.dumps(payload, sort_keys=True),
                    status,
                    approval_hash,
                    now,
                    now,
                ),
            )
            self._conn.commit()
        created = self.get(job_id)
        if created is None:
            raise RuntimeError("created job could not be loaded")
        return created

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def get_by_idempotency(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return _row_to_dict(row) if row else None

    def delete(self, job_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._conn.commit()
        return cursor.rowcount > 0

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def metric_snapshot(self) -> dict[str, Any]:
        with self._lock:
            total_row = self._conn.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()
            status_rows = self._conn.execute(
                """
                SELECT status, intent, COUNT(*) AS count
                FROM jobs
                GROUP BY status, intent
                ORDER BY status, intent
                """
            ).fetchall()
            duration_rows = self._conn.execute(
                """
                SELECT status, intent, started_at, finished_at
                FROM jobs
                WHERE started_at IS NOT NULL AND finished_at IS NOT NULL
                """
            ).fetchall()
            latest_row = self._conn.execute(
                """
                SELECT
                    MAX(created_at) AS last_created_at,
                    MAX(finished_at) AS last_finished_at
                FROM jobs
                """
            ).fetchone()

        durations: dict[tuple[str, str], dict[str, float]] = {}
        for row in duration_rows:
            seconds = _duration_seconds(row["started_at"], row["finished_at"])
            if seconds is None:
                continue
            key = (row["status"], row["intent"])
            bucket = durations.setdefault(key, {"count": 0.0, "sum": 0.0, "max": 0.0})
            bucket["count"] += 1
            bucket["sum"] += seconds
            bucket["max"] = max(bucket["max"], seconds)

        return {
            "total_jobs": int(total_row["count"] if total_row else 0),
            "jobs_by_status_intent": [
                {"status": row["status"], "intent": row["intent"], "count": int(row["count"])}
                for row in status_rows
            ],
            "durations_by_status_intent": [
                {
                    "status": status,
                    "intent": intent,
                    "count": values["count"],
                    "sum": values["sum"],
                    "max": values["max"],
                }
                for (status, intent), values in sorted(durations.items())
            ],
            "last_created_at": latest_row["last_created_at"] if latest_row else None,
            "last_finished_at": latest_row["last_finished_at"] if latest_row else None,
        }

    def set_status(self, job_id: str, status: str, *, error: str | None = None) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, error, now, job_id),
            )
            self._conn.commit()
        updated = self.get(job_id)
        if updated is None:
            raise KeyError(job_id)
        return updated

    def mark_running(self, job_id: str, log_path: str) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE jobs
                SET status = ?, started_at = ?, updated_at = ?, log_path = ?
                WHERE id = ? AND status = ?
                """,
                ("running", now, now, log_path, job_id, "queued"),
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                unchanged = self.get(job_id)
                if unchanged is None:
                    raise KeyError(job_id)
                return unchanged
        updated = self.get(job_id)
        if updated is None:
            raise KeyError(job_id)
        return updated

    def finish(
        self,
        job_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            self._conn.execute(
                """
                UPDATE jobs
                SET status = ?, result_json = ?, error = ?, finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, json.dumps(result or {}, sort_keys=True), error, now, now, job_id),
            )
            self._conn.commit()
        updated = self.get(job_id)
        if updated is None:
            raise KeyError(job_id)
        return updated

    def clear_approval(self, job_id: str) -> None:
        now = utc_now()
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET approval_hash = NULL, updated_at = ? WHERE id = ?",
                (now, job_id),
            )
            self._conn.commit()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    intent TEXT NOT NULL,
                    repo TEXT,
                    tool TEXT,
                    task TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    approval_hash TEXT,
                    log_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            self._conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["payload"] = json.loads(data.pop("payload_json"))
    result_json = data.pop("result_json")
    data["result"] = json.loads(result_json) if result_json else None
    return data


def _duration_seconds(started_at: str, finished_at: str) -> float | None:
    try:
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return None
    return max(0.0, (finished - started).total_seconds())
