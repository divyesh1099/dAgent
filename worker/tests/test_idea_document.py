from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from dagent_worker.config import NotificationConfig, WorkerConfig
from dagent_worker.jobs import JobStore
from dagent_worker.main import (
    IDEA_DOCUMENT_MAX_HTML_BYTES,
    _create_share,
    _load_idea_document,
    _load_share,
    _normalize_idea_document,
    _render_share_html,
    _write_idea_document,
)


def test_idea_document_default_and_round_trip(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    config = WorkerConfig(
        data_dir=tmp_path / "worker",
        notes_dir=tmp_path / "notes",
        notifications=NotificationConfig(),
    )
    record = store.create(
        payload={
            "intent": "capture_idea",
            "task": "Build a calm editor\n\nWith links and files.",
            "files": [{"name": "brief.pdf", "url": "https://example.test/brief.pdf", "mime_type": "application/pdf"}],
        },
        status="succeeded",
        idempotency_key=None,
        approval_hash=None,
    )

    default_doc = _load_idea_document(record, config)
    assert default_doc["title"] == "Build a calm editor"
    assert default_doc["assets"][0]["kind"] == "pdf"

    saved_doc = _normalize_idea_document(
        {
            "title": "Detailed idea",
            "visibility": "public",
            "content_html": "<h2>Plan</h2><p>Ship it.</p>",
            "assets": [{"kind": "sheet", "name": "numbers.xlsx", "url": "https://example.test/numbers.xlsx"}],
        },
        record,
        touch=True,
    )
    _write_idea_document(saved_doc, record, config)

    reloaded = _load_idea_document(record, config)
    assert reloaded["title"] == "Detailed idea"
    assert reloaded["visibility"] == "public"
    assert reloaded["content_html"] == "<h2>Plan</h2><p>Ship it.</p>"
    assert reloaded["assets"][0]["kind"] == "sheet"

    store.close()


def test_idea_document_editor_uses_task_section_not_capture_metadata(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    config = WorkerConfig(
        data_dir=tmp_path / "worker",
        notes_dir=notes_dir,
        notifications=NotificationConfig(),
    )
    record = store.create(
        payload={
            "intent": "capture_idea",
            "task": "Only this should be editable.",
            "source": "apple_watch",
            "input_type": "voice",
        },
        status="queued",
        idempotency_key=None,
        approval_hash=None,
    )
    note_path = notes_dir / "capture.md"
    note_path.write_text(
        "\n".join(
            [
                "# Capture Idea",
                "",
                f"- Job: `{record['id']}`",
                "- Created: `2026-05-01T07:17:42.310180+00:00`",
                "- Source: `apple_watch`",
                "- Input: `voice`",
                "",
                "## Task",
                "",
                "Only this should be editable.",
                "",
                "## Files",
                "",
                "## Metadata",
                "",
                "```json",
                "{}",
                "```",
            ]
        ),
        encoding="utf-8",
    )
    finished = store.finish(record["id"], status="succeeded", result={"note_path": str(note_path)})

    default_doc = _load_idea_document(finished, config)
    assert "Only this should be editable." in default_doc["content_html"]
    assert "Job:" not in default_doc["content_html"]
    assert "apple_watch" not in default_doc["content_html"]
    assert default_doc["capture"]["source"] == "apple_watch"
    assert default_doc["capture"]["input_type"] == "voice"

    store.close()


def test_idea_document_load_strips_previously_saved_capture_scaffold(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    config = WorkerConfig(
        data_dir=tmp_path / "worker",
        notes_dir=tmp_path / "notes",
        notifications=NotificationConfig(),
    )
    record = store.create(
        payload={
            "intent": "capture_idea",
            "task": "Please capture this new idea.",
            "source": "apple_watch",
            "input_type": "voice",
        },
        status="succeeded",
        idempotency_key=None,
        approval_hash=None,
    )
    saved_doc = _normalize_idea_document(
        {
            "title": "Old saved scaffold",
            "visibility": "private",
            "content_html": (
                "<h1>Capture Idea</h1>"
                f"<ul><li>Job: `{record['id']}`</li><li>Source: `apple_watch`</li><li>Input: `voice`</li></ul>"
                "<h2>Task</h2><p>Please capture this new idea.</p><h2>Files</h2><h2>Metadata</h2>"
            ),
        },
        record,
        touch=True,
    )
    _write_idea_document(saved_doc, record, config)

    reloaded = _load_idea_document(record, config)
    assert reloaded["content_html"] == "<p>Please capture this new idea.</p>"

    store.close()


def test_idea_document_compacts_inline_pdf_data_links_before_size_check(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    record = store.create(
        payload={"intent": "capture_idea", "task": "Capture this"},
        status="succeeded",
        idempotency_key=None,
        approval_hash=None,
    )
    content_html = (
        '<p>See <a href="data:application/pdf;base64,'
        + ("A" * (IDEA_DOCUMENT_MAX_HTML_BYTES + 10))
        + '">brief.pdf</a></p>'
    )

    document = _normalize_idea_document(
        {
            "title": "Compact asset link",
            "visibility": "private",
            "content_html": content_html,
            "assets": [],
        },
        record,
        touch=True,
    )

    assert document["content_html"] == "<p>See <span>brief.pdf</span></p>"
    assert len(document["content_html"]) < 100

    store.close()


def test_idea_share_is_tokenless_snapshot_with_visibility(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    config = WorkerConfig(
        data_dir=tmp_path / "worker",
        notes_dir=tmp_path / "notes",
        notifications=NotificationConfig(),
    )
    record = store.create(
        payload={"intent": "capture_idea", "task": "Share this safely."},
        status="succeeded",
        idempotency_key=None,
        approval_hash=None,
    )
    document = _normalize_idea_document(
        {
            "title": "Shared note",
            "visibility": "private",
            "content_html": "<p>Read only.</p>",
            "assets": [
                {
                    "kind": "pdf",
                    "name": "brief.pdf",
                    "mime_type": "application/pdf",
                    "size": 2048,
                    "data_url": "data:application/pdf;base64,JVBERi0xLjQK",
                }
            ],
        },
        record,
        touch=True,
    )

    share = _create_share(record, document, config, visibility="private")
    assert share["id"]
    assert "token" not in share
    assert share["visibility"] == "private"

    reloaded = _load_share(config, share["id"], visibility="private")
    assert reloaded["content_html"] == "<p>Read only.</p>"
    rendered = _render_share_html(reloaded, worker_name="main")
    assert "Assets" in rendered
    assert "brief.pdf" in rendered
    assert "application/pdf" in rendered
    assert "2 KB" in rendered
    assert "data:application/pdf" in rendered
    assert rendered.index('<section class="assets">') < rendered.index('<div class="content">')

    with pytest.raises(HTTPException):
        _load_share(config, share["id"], visibility="public")

    store.close()


def test_idea_document_rejects_unknown_visibility(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    record = store.create(
        payload={"intent": "capture_idea", "task": "Capture this"},
        status="succeeded",
        idempotency_key=None,
        approval_hash=None,
    )

    with pytest.raises(HTTPException):
        _normalize_idea_document({"visibility": "team", "content_html": "<p>x</p>"}, record, touch=True)

    store.close()
