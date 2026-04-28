from __future__ import annotations

from typing import Any
from urllib import request
from urllib.error import URLError

from .config import NotificationConfig


class Notifier:
    def __init__(self, config: NotificationConfig) -> None:
        self._config = config

    def enabled(self) -> bool:
        return bool(self._config.ntfy_url and self._config.ntfy_topic)

    def send(self, *, title: str, message: str, priority: str = "default", tags: str = "") -> None:
        if not self.enabled():
            return
        url = f"{self._config.ntfy_url.rstrip('/')}/{self._config.ntfy_topic}"
        headers = {
            "X-Title": title,
            "X-Priority": priority,
        }
        if tags:
            headers["X-Tags"] = tags
        if self._config.ntfy_token:
            headers["Authorization"] = f"Bearer {self._config.ntfy_token}"

        req = request.Request(url, data=message.encode("utf-8"), headers=headers, method="POST")
        try:
            request.urlopen(req, timeout=10).read()
        except URLError:
            return

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
        self.send(
            title=f"dAgent job {status}: {job['intent']}",
            message=f"Job {job['id']} finished with status {status}.\nRepo: {job.get('repo') or '-'}",
            priority=priority,
            tags=tags,
        )

