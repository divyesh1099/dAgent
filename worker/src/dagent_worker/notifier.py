from __future__ import annotations

from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

from .config import NotificationConfig


class Notifier:
    def __init__(self, config: NotificationConfig) -> None:
        self._config = config

    def enabled(self) -> bool:
        return bool(self._config.ntfy_url and self._config.ntfy_topics)

    def send(self, *, title: str, message: str, priority: str = "default", tags: str = "") -> list[dict[str, Any]]:
        if not self.enabled():
            return []

        results: list[dict[str, Any]] = []
        headers = {
            "X-Title": title,
            "X-Priority": priority,
        }
        if tags:
            headers["X-Tags"] = tags
        if self._config.ntfy_token:
            headers["Authorization"] = f"Bearer {self._config.ntfy_token}"

        for topic in self._config.ntfy_topics:
            url = f"{self._config.ntfy_url.rstrip('/')}/{topic}"
            req = request.Request(url, data=message.encode("utf-8"), headers=headers, method="POST")
            try:
                response = request.urlopen(req, timeout=10)
                response.read()
            except HTTPError as exc:
                results.append({"topic": topic, "ok": False, "status": exc.code, "error": str(exc)})
            except URLError as exc:
                results.append({"topic": topic, "ok": False, "error": str(exc.reason)})
            else:
                results.append({"topic": topic, "ok": True, "status": response.status})
        return results

    def approval_required(self, job: dict[str, Any], approval_code: str) -> None:
        self.send(
            title=f"dAgent approval: {job['intent']}",
            message=(
                f"Job {job['id']} needs approval.\n"
                f"Repo: {job.get('repo') or '-'}\n"
                f"Task: {job['task'][:500]}\n"
                f"Approval code: {approval_code}"
            ),
            priority="high",
            tags="warning",
        )

    def job_finished(self, job: dict[str, Any]) -> None:
        status = job["status"]
        priority = "high" if status == "failed" else "default"
        tags = "x" if status == "failed" else "white_check_mark"
        note_path = ""
        result = job.get("result")
        if isinstance(result, dict):
            note_path = str(result.get("note_path") or "")
        message = f"Job {job['id']} finished with status {status}.\nRepo: {job.get('repo') or '-'}"
        if note_path:
            message += f"\nNote: {note_path}"
        return self.send(
            title=f"dAgent job {status}: {job['intent']}",
            message=message,
            priority=priority,
            tags=tags,
        )
