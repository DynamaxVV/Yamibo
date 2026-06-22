from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobStatus
from yamibo_mcp.domain.models import ContentBlock, FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.server.agent_tools import (
    create_thread_archive_job,
    create_thread_export_job,
    read_archived_thread,
    read_job,
    read_job_events,
)


def _fake_settings(tmp_path: Path):
    settings = MagicMock()
    settings.db_path = tmp_path / "test.db"
    settings.data_dir = tmp_path / "data"
    settings.export_dir = tmp_path / "exports"
    settings.novel_txt_export_dir = tmp_path / "novel_exports"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.novel_txt_export_dir.mkdir(parents=True, exist_ok=True)
    settings.cookie_file = tmp_path / "cookies.txt"
    settings.use_system_proxy = False
    settings.login_username = None
    settings.login_password = None
    settings.request_interval_seconds = 0.0
    settings.request_interval_jitter_seconds = 0.0
    return settings


def _seed_thread(conn, *, tid: int = 9001, floor_count: int = 3) -> None:
    title = TitleSnapshot(
        raw_title="[TestGroup] Agent Workflow",
        display_title="Agent Workflow",
        group_name="TestGroup",
        author_guess="Author",
        core_title_guess="Agent Workflow",
        normalized_core_title="agentworkflow",
        series_key="agentworkflow",
        title_aliases=[],
        chapter_name=None,
        chapter_index=None,
        chapter_index_end=None,
        chapter_title=None,
        subtitle=None,
        tags=[],
        confidence=0.9,
        needs_review=False,
        parser_version="title-v1",
    )
    floors = [
        FloorSnapshot(
            pid=20_000 + index,
            tid=tid,
            floor_no=index,
            publisher="author",
            content=f"floor {index}",
            pub_time="2026-01-01",
            has_images=False,
        )
        for index in range(1, floor_count + 1)
    ]
    snapshot = ThreadSnapshot(
        tid=tid,
        url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}",
        page_type="thread_detail",
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher="author",
        publisher_uid="1",
        pub_time="2026-01-01",
        permission=0,
        floors=floors,
        image_count=0,
    )
    ThreadsRepository(conn).upsert_snapshot(snapshot, context_path=f"threads/{tid}/context.md")
    ContentBlocksRepository(conn).upsert_blocks(
        tid,
        [
            ContentBlock(
                block_id=f"b-{floor.pid}",
                pid=floor.pid,
                order_index=floor.floor_no,
                block_type="text",
                text=f"block {floor.floor_no}",
            )
            for floor in floors
        ],
    )


def _open_db(settings):
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def test_archive_job_workflow_exposes_reusable_polling_surface(tmp_path):
    settings = _fake_settings(tmp_path)
    conn = _open_db(settings)
    conn.close()

    with patch("yamibo_mcp.application.archive_commands.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.job_queries.load_settings", return_value=settings):
        created = create_thread_archive_job(tid=572313)
        status = read_job(job_id=created["data"]["job_id"])
        events = read_job_events(job_id=created["data"]["job_id"])

    assert created["ok"] is True
    assert created["data"]["created"] is True
    assert created["data"]["status"] == "queued"
    assert created["next_actions"][0]["tool"] == "read_job"
    assert "sqlite_job_created" in created["side_effects"]

    assert status["ok"] is True
    assert status["data"]["status"] == "queued"
    assert status["data"]["is_terminal"] is False
    assert status["data"]["result_ready"] is False
    assert status["data"]["recommended_poll_after_seconds"] == 2

    assert events["ok"] is True
    assert events["data"]["events"][0]["event_type"] == "job.created"
    assert events["data"]["events"][0]["payload"] == {}


def test_archive_job_workflow_reuses_existing_live_job(tmp_path):
    settings = _fake_settings(tmp_path)
    conn = _open_db(settings)
    existing = JobsRepository(conn).create("sync_thread", tid=572313, payload={"tid": 572313})
    conn.close()

    with patch("yamibo_mcp.application.archive_commands.load_settings", return_value=settings):
        created = create_thread_archive_job(tid=572313)

    assert created["ok"] is True
    assert created["data"]["job_id"] == existing.job_id
    assert created["data"]["created"] is False
    assert "sqlite_job_reused" in created["side_effects"]


def test_export_job_workflow_does_not_reuse_when_strategy_changes(tmp_path):
    settings = _fake_settings(tmp_path)
    conn = _open_db(settings)
    existing = JobsRepository(conn).create("export_thread", tid=572313, payload={"tid": 572313})
    conn.close()

    with patch("yamibo_mcp.application.archive_commands.load_settings", return_value=settings):
        created = create_thread_export_job(tid=572313, strategy="force_resync")

    assert created["ok"] is True
    assert created["data"]["job_id"] != existing.job_id
    assert created["data"]["created"] is True
    assert "sqlite_job_created" in created["side_effects"]


def test_local_archive_workflow_moves_from_missing_to_summary_read(tmp_path):
    settings = _fake_settings(tmp_path)
    conn = _open_db(settings)
    conn.close()

    with patch("yamibo_mcp.application.archive_queries.load_settings", return_value=settings):
        missing = read_archived_thread(tid=9001, view="summary")

    assert missing["ok"] is False
    assert missing["error"]["code"] == "LOCAL_ARCHIVE_NOT_FOUND"
    assert missing["error"]["suggested_actions"][0]["tool"] == "create_thread_archive_job"

    conn = _open_db(settings)
    _seed_thread(conn, tid=9001)
    conn.commit()
    conn.close()

    with patch("yamibo_mcp.application.archive_queries.load_settings", return_value=settings):
        summary = read_archived_thread(tid=9001, view="summary")
        content = read_archived_thread(tid=9001, view="content", chunk_size=2)

    assert summary["ok"] is True
    assert summary["data"]["tid"] == 9001
    assert summary["resources"]["summary"] == "yamibo://threads/9001/summary"

    assert content["ok"] is True
    assert len(content["data"]["floors"]) == 2
    assert content["data"]["has_more"] is True
    assert content["data"]["next_cursor"] == "offset:2"


def test_job_recovery_workflow_exposes_partial_and_interrupted_states(tmp_path):
    settings = _fake_settings(tmp_path)
    conn = _open_db(settings)
    repo = JobsRepository(conn)
    partial_job = repo.create("sync_thread", tid=7001, payload={"tid": 7001})
    repo.partial(partial_job.job_id, artifacts={"tid": 7001, "archive_status": "partial"})
    interrupted_job = repo.create("sync_thread", tid=7002, payload={"tid": 7002})
    conn.execute(
        "UPDATE jobs SET status = ?, updated_at = created_at WHERE job_id = ?",
        (JobStatus.INTERRUPTED.value, interrupted_job.job_id),
    )
    conn.commit()
    conn.close()

    with patch("yamibo_mcp.application.job_queries.load_settings", return_value=settings):
        partial_status = read_job(job_id=partial_job.job_id)
        interrupted_status = read_job(job_id=interrupted_job.job_id)
        interrupted_events = read_job_events(job_id=interrupted_job.job_id)

    assert partial_status["ok"] is True
    assert partial_status["data"]["status"] == "partial"
    assert partial_status["data"]["is_terminal"] is True
    assert partial_status["data"]["result_ready"] is True
    assert partial_status["data"]["recommended_poll_after_seconds"] is None

    assert interrupted_status["ok"] is True
    assert interrupted_status["data"]["status"] == "interrupted"
    assert interrupted_status["data"]["is_terminal"] is False
    assert interrupted_status["data"]["result_ready"] is False
    assert interrupted_status["data"]["execution_state"] == "attention"
    assert interrupted_status["data"]["needs_attention"] is True
    assert interrupted_status["data"]["recommended_poll_after_seconds"] == 5

    assert interrupted_events["ok"] is True
    assert interrupted_events["data"]["events"][0]["event_type"] == "job.created"
