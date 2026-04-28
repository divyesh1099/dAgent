from pathlib import Path

from dagent_worker.config import WorkerConfig
from dagent_worker.jobs import JobStore
from dagent_worker.metrics import metric_line, render_metrics


def test_metric_line_escapes_labels() -> None:
    line = metric_line("dagent_test", 1, {"worker": 'main"one', "line": "a\nb"})
    assert 'worker="main\\"one"' in line
    assert 'line="a\\nb"' in line


def test_render_metrics_includes_jobs(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.create(
        payload={"intent": "capture_idea", "task": "hello"},
        status="queued",
        idempotency_key=None,
        approval_hash=None,
    )
    config = WorkerConfig(data_dir=tmp_path / "data", notes_dir=tmp_path / "notes", max_parallel_jobs=3)

    metrics = render_metrics(config=config, store=store, worker_name="main")

    assert 'dagent_worker_up{worker="main",version=' in metrics
    assert 'dagent_worker_jobs{worker="main",status="queued",intent="capture_idea"} 1.000000' in metrics
    assert 'dagent_worker_max_parallel_jobs{worker="main"} 3.000000' in metrics

