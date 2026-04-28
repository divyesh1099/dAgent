from __future__ import annotations

from datetime import datetime
from typing import Any

from . import __version__
from .config import WorkerConfig
from .jobs import JobStore


def render_metrics(*, config: WorkerConfig, store: JobStore, worker_name: str) -> str:
    snapshot = store.metric_snapshot()
    lines: list[str] = []

    _help(lines, "dagent_worker_up", "Whether the dAgent worker process is up.")
    _type(lines, "dagent_worker_up", "gauge")
    lines.append(metric_line("dagent_worker_up", 1, {"worker": worker_name, "version": __version__}))

    _help(lines, "dagent_worker_configured_repos", "Number of repos configured in this worker.")
    _type(lines, "dagent_worker_configured_repos", "gauge")
    lines.append(metric_line("dagent_worker_configured_repos", len(config.repos), {"worker": worker_name}))

    _help(lines, "dagent_worker_configured_tools", "Number of tools configured in this worker.")
    _type(lines, "dagent_worker_configured_tools", "gauge")
    lines.append(metric_line("dagent_worker_configured_tools", len(config.tools), {"worker": worker_name}))

    _help(lines, "dagent_worker_max_parallel_jobs", "Configured worker job concurrency.")
    _type(lines, "dagent_worker_max_parallel_jobs", "gauge")
    lines.append(metric_line("dagent_worker_max_parallel_jobs", config.max_parallel_jobs, {"worker": worker_name}))

    _help(lines, "dagent_worker_jobs", "Current count of jobs by status and intent.")
    _type(lines, "dagent_worker_jobs", "gauge")
    for item in snapshot["jobs_by_status_intent"]:
        lines.append(
            metric_line(
                "dagent_worker_jobs",
                item["count"],
                {"worker": worker_name, "status": item["status"], "intent": item["intent"]},
            )
        )

    _help(lines, "dagent_worker_jobs_total", "Total jobs ever recorded in this worker database.")
    _type(lines, "dagent_worker_jobs_total", "gauge")
    lines.append(metric_line("dagent_worker_jobs_total", snapshot["total_jobs"], {"worker": worker_name}))

    _help(lines, "dagent_worker_job_duration_seconds", "Finished job duration by status and intent.")
    _type(lines, "dagent_worker_job_duration_seconds", "summary")
    for item in snapshot["durations_by_status_intent"]:
        labels = {"worker": worker_name, "status": item["status"], "intent": item["intent"]}
        lines.append(metric_line("dagent_worker_job_duration_seconds_count", item["count"], labels))
        lines.append(metric_line("dagent_worker_job_duration_seconds_sum", item["sum"], labels))
        lines.append(metric_line("dagent_worker_job_duration_seconds_max", item["max"], labels))

    _help(lines, "dagent_worker_last_created_timestamp_seconds", "Unix timestamp of the newest job creation.")
    _type(lines, "dagent_worker_last_created_timestamp_seconds", "gauge")
    lines.append(
        metric_line(
            "dagent_worker_last_created_timestamp_seconds",
            _timestamp_or_zero(snapshot["last_created_at"]),
            {"worker": worker_name},
        )
    )

    _help(lines, "dagent_worker_last_finished_timestamp_seconds", "Unix timestamp of the newest finished job.")
    _type(lines, "dagent_worker_last_finished_timestamp_seconds", "gauge")
    lines.append(
        metric_line(
            "dagent_worker_last_finished_timestamp_seconds",
            _timestamp_or_zero(snapshot["last_finished_at"]),
            {"worker": worker_name},
        )
    )

    return "".join(lines)


def metric_line(name: str, value: int | float, labels: dict[str, str] | None = None) -> str:
    label_text = ""
    if labels:
        label_text = "{" + ",".join(f'{key}="{_escape_label(str(raw_value))}"' for key, raw_value in labels.items()) + "}"
    return f"{name}{label_text} {float(value):.6f}\n"


def _help(lines: list[str], name: str, text: str) -> None:
    lines.append(f"# HELP {name} {text}\n")


def _type(lines: list[str], name: str, metric_type: str) -> None:
    lines.append(f"# TYPE {name} {metric_type}\n")


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _timestamp_or_zero(value: str | None) -> float:
    if not value:
        return 0.0
    return datetime.fromisoformat(value).timestamp()

