from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
import logging
from decimal import Decimal
from unittest.mock import MagicMock, patch

from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.domain.enums import JobStatus
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.rag.chunker import RagChunk
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.web.routes.threads import (
    handle_archive_threads_batch,
    handle_batch_delete_threads,
    handle_delete_thread,
    handle_resync_threads_batch,
    handle_threads_list,
    handle_thread_detail,
    handle_thread_update_check,
    handle_update_chapter,
    handle_update_thread,
)
from yamibo_mcp.web.routes.jobs import (
    handle_jobs_list,
    handle_job_control,
    handle_retry_job,
)
from yamibo_mcp.web.routes.forums import (
    handle_forums_list,
    handle_refresh_forum_size_cache,
)
from yamibo_mcp.web.routes.rag import (
    handle_rag_index_batch,
    handle_rag_index,
    handle_rag_overview,
    handle_rag_search,
    handle_rag_threads,
)
from yamibo_mcp.web.routes.review import handle_merge_series, handle_update_title
from yamibo_mcp.web.routes.settings import handle_settings_get, handle_settings_update
from yamibo_mcp.web.routes.debug import handle_debug_info
from yamibo_mcp.logging import configure_logging
from yamibo_mcp.web.log_buffer import get_log_buffer


class _CaptureHandler:
    def __init__(self):
        self.command = "GET"
        self.headers = {}
        self.status = None
        self.sent_headers: list[tuple[str, str]] = []
        self.wfile = io.BytesIO()
        self.rfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers.append((key, value))

    def end_headers(self):
        pass


def _make_snapshot(tid: int = 42, *, floors: list[FloorSnapshot] | None = None) -> ThreadSnapshot:
    title = TitleSnapshot(
        raw_title="[组] 测试帖子",
        display_title="测试帖子",
        group_name="组",
        author_guess="作者",
        core_title_guess="测试帖子",
        normalized_core_title="测试帖子",
        series_key="测试帖子",
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
    floor = FloorSnapshot(
        pid=tid * 1000 + 1,
        tid=tid,
        floor_no=1,
        publisher="u1",
        content="正文",
        pub_time="2025-01-01 00:00",
        has_images=True,
        image_urls=["https://img.example.com/a.jpg"],
    )
    resolved_floors = floors or [floor]
    return ThreadSnapshot(
        tid=tid,
        url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&authorid=100",
        page_type="thread_detail",
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher="u1",
        publisher_uid="100",
        pub_time="2025-01-01 00:00",
        permission=0,
        floors=resolved_floors,
        image_count=1,
    )


def _make_floor(pid: int, tid: int, floor_no: int, *, pub_time: str, publisher: str) -> FloorSnapshot:
    return FloorSnapshot(
        pid=pid,
        tid=tid,
        floor_no=floor_no,
        publisher=publisher,
        content="正文",
        pub_time=pub_time,
        has_images=False,
        image_urls=[],
    )


def test_thread_detail_exposes_archive_summary_from_metadata(db, tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = SimpleNamespace(data_dir=data_dir)
    snapshot = _make_snapshot()
    ThreadsRepository(db).upsert_snapshot(
        snapshot,
        forum_id=55,
        archive_status="partial",
        missing_image_urls=["https://img.example.com/a.jpg"],
    )

    thread_dir = data_dir / "threads" / "42"
    thread_dir.mkdir(parents=True, exist_ok=True)
    (thread_dir / "metadata.json").write_text(
        json.dumps(
            {
                "context_path": "threads/42/context.md",
                "archived_images": {"1001": ["images/a.jpg"]},
                "non_export_images": {},
                "shared_images": {"1001": ["threads/42/shared.jpg"]},
                "skipped_image_urls": {"1001": ["https://img.example.com/skip.jpg"]},
                "missing_image_urls": ["https://img.example.com/a.jpg"],
                "missing_shared_image_urls": ["https://img.example.com/shared-missing.jpg"],
                "floors": [
                    {
                        "pid": 42001,
                        "remote_image_urls": ["https://img.example.com/a.jpg", "https://img.example.com/b.jpg"],
                        "missing_image_urls": ["https://img.example.com/a.jpg"],
                        "image_slots": [
                            {"remote_url": "https://img.example.com/a.jpg", "local_path": None, "status": "missing"},
                            {"remote_url": "https://img.example.com/b.jpg", "local_path": "images/b.jpg", "status": "content"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    handler = _CaptureHandler()
    handle_thread_detail(handler, 42, db, {"preview_page": ["1"], "preview_page_size": ["10"]}, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["archive_summary"]["archived_images"]["1001"] == ["images/a.jpg"]
    assert payload["archive_summary"]["missing_image_urls"] == ["https://img.example.com/a.jpg"]
    assert payload["archive_summary"]["missing_shared_image_urls"] == ["https://img.example.com/shared-missing.jpg"]
    assert payload["floors"][0]["remote_image_urls"] == ["https://img.example.com/a.jpg", "https://img.example.com/b.jpg"]
    assert payload["floors"][0]["missing_image_urls"] == ["https://img.example.com/a.jpg"]
    assert payload["floors"][0]["image_slots"][0]["status"] == "missing"


def test_thread_detail_exposes_rag_summary(db, tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = SimpleNamespace(data_dir=data_dir, rag_enabled=True)
    _seed_rag_thread(db)

    handler = _CaptureHandler()
    handle_thread_detail(handler, 42, db, {"preview_page": ["1"], "preview_page_size": ["10"]}, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["rag_summary"]["chunk_count"] == 2
    assert payload["rag_summary"]["indexed_chunk_count"] == 1
    assert payload["rag_summary"]["failed_chunk_count"] == 1


def test_thread_detail_merges_rich_body_html_from_metadata(db, tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = SimpleNamespace(data_dir=data_dir)
    snapshot = _make_snapshot()
    ThreadsRepository(db).upsert_snapshot(
        snapshot,
        forum_id=55,
        archive_status="complete",
        missing_image_urls=[],
    )

    thread_dir = data_dir / "threads" / "42"
    thread_dir.mkdir(parents=True, exist_ok=True)
    (thread_dir / "metadata.json").write_text(
        json.dumps(
            {
                "context_path": "threads/42/context.md",
                "archived_images": {},
                "non_export_images": {},
                "shared_images": {},
                "skipped_image_urls": {},
                "missing_image_urls": [],
                "missing_shared_image_urls": [],
                "floors": [
                    {
                        "pid": 42001,
                        "floor_no": 1,
                        "publisher": "u1",
                        "content": "正文",
                        "rich_body_html": "<div><strong>富文本</strong><a href=\"https://example.com\" target=\"_blank\" rel=\"noreferrer\">链接</a></div>",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    handler = _CaptureHandler()
    handle_thread_detail(handler, 42, db, {"preview_page": ["1"], "preview_page_size": ["10"]}, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["floors"][0]["rich_body_html"] == "<div><strong>富文本</strong><a href=\"https://example.com\" target=\"_blank\" rel=\"noreferrer\">链接</a></div>"


def test_thread_detail_accepts_jsonb_array_fields(db, tmp_path: Path, monkeypatch):
    settings = SimpleNamespace(data_dir=tmp_path / "data")
    settings.data_dir.mkdir()

    thread_row = {
        "tid": 66566,
        "raw_title": "测试帖子",
        "display_title": "测试帖子",
        "publisher": "u1",
        "pub_time": "2025-01-01 00:00",
        "sync_time": "2025-01-02 00:00",
        "archive_status": "partial",
        "validation_status": "valid",
        "context_path": None,
        "series_id": None,
        "export_path": None,
        "forum_id": 55,
        "content_kind": "novel",
        "category": None,
        "publisher_uid": "100",
        "missing_images_json": ["https://img.example.com/a.jpg"],
        "image_count": 1,
        "primary_media_type": "text",
    }
    floor_row = {
        "pid": 66566001,
        "tid": 66566,
        "floor_no": 1,
        "publisher": "u1",
        "publisher_uid": "100",
        "content": "正文",
        "pub_time": "2025-01-01 00:00",
        "has_images": False,
        "quote_text": None,
        "reply_text": None,
        "rich_body_html": None,
    }

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def get_thread(self, tid):
            return thread_row if tid == 66566 else None

        def count_floors(self, tid):
            return 1

        def list_floors(self, tid):
            return [floor_row]

        def get_title_parse(self, tid):
            return None

    monkeypatch.setattr("yamibo_mcp.web.routes.threads.ThreadsRepository", FakeRepo)

    handler = _CaptureHandler()
    handle_thread_detail(handler, 66566, db, {}, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["missing_image_urls"] == ["https://img.example.com/a.jpg"]


def test_threads_list_returns_paginated_payload(db):
    repo = ThreadsRepository(db)
    seeded = [
        (101, "2025-01-04 08:00:00", "complete"),
        (102, "2025-01-03 08:00:00", "complete"),
        (103, "2025-01-02 08:00:00", "partial"),
        (104, "2025-01-01 08:00:00", "complete"),
    ]
    for tid, sync_time, archive_status in seeded:
        repo.upsert_snapshot(_make_snapshot(tid=tid), forum_id=55)
        db.execute(
            """
            UPDATE threads
            SET sync_time = ?, pub_time = ?, archive_status = ?, forum_id = ?,
                local_reply_count = ?, remote_last_reply_at = ?, remote_reply_count = ?, remote_last_replier = ?
            WHERE tid = ?
            """,
            (sync_time, sync_time, archive_status, 55, tid + 10, f"2026-07-{tid - 100:02d}T08:00:00+00:00", tid + 20, f"user-{tid}", tid),
        )
    db.commit()

    handler = _CaptureHandler()
    handle_threads_list(
        handler,
        {
            "forum_id": ["55"],
            "page": ["2"],
            "page_size": ["2"],
            "sort_key": ["sync_time"],
            "sort_dir": ["desc"],
        },
        db,
    )

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["page"] == 2
    assert payload["page_size"] == 2
    assert payload["total_count"] == 4
    assert payload["total_pages"] == 2
    assert [item["tid"] for item in payload["items"]] == [103, 104]
    assert payload["items"][0]["remote_last_reply_at"] == "2026-07-03T08:00:00+00:00"
    assert payload["items"][0]["remote_reply_count"] == 123
    assert payload["items"][0]["local_reply_count"] == 113
    assert payload["items"][0]["reply_count"] == 123
    assert payload["items"][0]["author_guess"] == "作者"
    assert payload["items"][0]["group_name"] == "组"


def test_threads_list_falls_back_to_last_floor_time_and_floor_reply_count(db):
    repo = ThreadsRepository(db)
    repo.upsert_snapshot(
        _make_snapshot(
            tid=201,
            floors=[
                _make_floor(pid=2011, tid=201, floor_no=1, pub_time="2025-01-01 08:00:00", publisher="user-a"),
                _make_floor(pid=2012, tid=201, floor_no=2, pub_time="2025-01-02 09:30:00", publisher="user-b"),
            ],
        ),
        forum_id=55,
    )
    db.execute(
        """
        UPDATE threads
        SET sync_time = ?, pub_time = ?, local_reply_count = NULL,
            remote_last_reply_at = NULL, remote_last_reply_at_raw = NULL, remote_reply_count = NULL, remote_last_replier = NULL
        WHERE tid = ?
        """,
        ("2025-01-02 10:00:00", "2025-01-01 08:00:00", 201),
    )
    db.commit()

    handler = _CaptureHandler()
    handle_threads_list(
        handler,
        {"forum_id": ["55"], "page": ["1"], "page_size": ["10"]},
        db,
    )

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    item = next(row for row in payload["items"] if row["tid"] == 201)
    assert item["local_reply_count"] == 1
    assert item["reply_count"] == 1
    assert item["remote_last_reply_at"] == "2025-01-02 09:30:00"
    assert item["remote_last_reply_at_raw"] == "2025-01-02 09:30:00"
    assert item["remote_last_replier"] == "user-b"


def test_thread_update_check_endpoint_returns_json(db):
    handler = _CaptureHandler()
    settings = SimpleNamespace()
    with patch("yamibo_mcp.web.routes.threads.check_thread_updates", return_value={"tid": 42, "status": "up_to_date"}):
        handle_thread_update_check(handler, 42, settings)
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["status"] == "up_to_date"


def test_debug_info_redacts_db_url_and_exposes_pool_status(db):
    handler = _CaptureHandler()
    settings = SimpleNamespace(
        data_dir=Path("/tmp/yamibo-data"),
        db_path=Path("/tmp/yamibo-data/forum.db"),
        db_backend="postgres",
        db_url="postgresql://yamibo:secret@db.example.com:5432/yamibo",
        db_ssl_mode="require",
    )

    handle_debug_info(handler, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["db_backend"] == "postgres"
    assert payload["db_host"] == "db.example.com"
    assert payload["db_name"] == "yamibo"
    assert "postgresql://yamibo:secret@" not in json.dumps(payload)
    assert "status" in payload["db_pool"]
    assert "checked_out_connections" in payload["db_pool"]


def test_settings_get_exposes_field_effects(tmp_path: Path):
    config_path = tmp_path / "yamibo.local.json"
    config_path.write_text("{}", encoding="utf-8")
    settings = SimpleNamespace(
        config_path=config_path,
        request_timeout_seconds=30.0,
        request_interval_seconds=1.0,
        request_interval_jitter_seconds=0.5,
        use_system_proxy=False,
        image_download_timeout_seconds=45.0,
        image_download_retries=2,
        archive_thread_max_pages=50,
        novel_author_only_max_pages=50,
        novel_author_only_page_delay_seconds=0.5,
        db_backend="sqlite",
        db_url=None,
        db_pool_min=1,
        db_pool_max=5,
        db_pool_timeout=30.0,
        db_connect_timeout=10.0,
        db_schema="public",
        db_ssl_mode="prefer",
        db_ssl_root_cert=None,
        llm_base_url="https://api.openai.com/v1",
        llm_api_key=None,
        llm_model="gpt-4.1-mini",
        rag_enabled=True,
        rag_base_url="https://api.openai.com/v1",
        rag_api_key=None,
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_chunker_version="rag-chunker-v1",
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=900,
        rag_hybrid_fts_candidates=50,
        rag_hybrid_vector_candidates=50,
        rag_debug_indexing=False,
        title_parse_use_llm=True,
        common_scanlation_groups=(),
        common_authors=(),
        export_default_strategy="cache_only",
        export_stale_after_hours=24,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
        backup_keep_count=20,
        cleanup_staging_older_than_hours=48,
        worker_poll_seconds=2.0,
        worker_lease_seconds=60,
        worker_heartbeat_seconds=15,
        worker_parallelism=2,
    )

    handler = _CaptureHandler()
    handle_settings_get(handler, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["effects"]["db_backend"] == "restart_daemon_web"
    assert payload["effects"]["db_url"] == "restart_daemon_web"
    assert payload["effects"]["request_timeout_seconds"] == "immediate"
    assert payload["effects"]["worker_parallelism"] == "restart_daemon"
    assert payload["effects"]["llm_base_url"] == "restart_daemon_web"


def test_settings_update_reports_immediate_effect_only(tmp_path: Path):
    config_path = tmp_path / "yamibo.local.json"
    config_path.write_text(json.dumps({"yamibo": {"request_timeout_seconds": 30}}), encoding="utf-8")
    current = SimpleNamespace(
        config_path=config_path,
        request_timeout_seconds=30.0,
        request_interval_seconds=1.0,
        request_interval_jitter_seconds=0.5,
        use_system_proxy=False,
        image_download_timeout_seconds=45.0,
        image_download_retries=2,
        archive_thread_max_pages=50,
        novel_author_only_max_pages=50,
        novel_author_only_page_delay_seconds=0.5,
        db_backend="sqlite",
        db_url=None,
        db_pool_min=1,
        db_pool_max=5,
        db_pool_timeout=30.0,
        db_connect_timeout=10.0,
        db_schema="public",
        db_ssl_mode="prefer",
        db_ssl_root_cert=None,
        llm_base_url="https://api.openai.com/v1",
        llm_api_key=None,
        llm_model="gpt-4.1-mini",
        rag_enabled=True,
        rag_base_url="https://api.openai.com/v1",
        rag_api_key=None,
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_chunker_version="rag-chunker-v1",
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=900,
        rag_hybrid_fts_candidates=50,
        rag_hybrid_vector_candidates=50,
        rag_debug_indexing=False,
        title_parse_use_llm=True,
        common_scanlation_groups=(),
        common_authors=(),
        export_default_strategy="cache_only",
        export_stale_after_hours=24,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
        backup_keep_count=20,
        cleanup_staging_older_than_hours=48,
        worker_poll_seconds=2.0,
        worker_lease_seconds=60,
        worker_heartbeat_seconds=15,
        worker_parallelism=2,
    )
    refreshed = SimpleNamespace(
        config_path=config_path,
        request_timeout_seconds=35.0,
        request_interval_seconds=1.0,
        request_interval_jitter_seconds=0.5,
        use_system_proxy=False,
        image_download_timeout_seconds=45.0,
        image_download_retries=2,
        archive_thread_max_pages=50,
        novel_author_only_max_pages=50,
        novel_author_only_page_delay_seconds=0.5,
        db_backend="sqlite",
        db_url=None,
        db_pool_min=1,
        db_pool_max=5,
        db_pool_timeout=30.0,
        db_connect_timeout=10.0,
        db_schema="public",
        db_ssl_mode="prefer",
        db_ssl_root_cert=None,
        llm_base_url="https://api.openai.com/v1",
        llm_api_key=None,
        llm_model="gpt-4.1-mini",
        rag_enabled=True,
        rag_base_url="https://api.openai.com/v1",
        rag_api_key=None,
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_chunker_version="rag-chunker-v1",
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=900,
        rag_hybrid_fts_candidates=50,
        rag_hybrid_vector_candidates=50,
        rag_debug_indexing=False,
        title_parse_use_llm=True,
        common_scanlation_groups=(),
        common_authors=(),
        export_default_strategy="cache_only",
        export_stale_after_hours=24,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
        backup_keep_count=20,
        cleanup_staging_older_than_hours=48,
        worker_poll_seconds=2.0,
        worker_lease_seconds=60,
        worker_heartbeat_seconds=15,
        worker_parallelism=2,
    )
    body = {"values": {"request_timeout_seconds": 35}}
    handler = _CaptureHandler()
    handler.command = "POST"
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    with patch("yamibo_mcp.web.routes.settings.load_settings", return_value=refreshed):
        handle_settings_update(handler, current)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["restart_required"] is False
    assert payload["restart_targets"] == []
    assert payload["effect_mode_summary"] == "immediate"


def test_settings_update_reports_daemon_restart_target(tmp_path: Path):
    config_path = tmp_path / "yamibo.local.json"
    config_path.write_text(json.dumps({"worker": {"parallelism": 2}}), encoding="utf-8")
    current = SimpleNamespace(
        config_path=config_path,
        request_timeout_seconds=30.0,
        request_interval_seconds=1.0,
        request_interval_jitter_seconds=0.5,
        use_system_proxy=False,
        image_download_timeout_seconds=45.0,
        image_download_retries=2,
        archive_thread_max_pages=50,
        novel_author_only_max_pages=50,
        novel_author_only_page_delay_seconds=0.5,
        db_backend="sqlite",
        db_url=None,
        db_pool_min=1,
        db_pool_max=5,
        db_pool_timeout=30.0,
        db_connect_timeout=10.0,
        db_schema="public",
        db_ssl_mode="prefer",
        db_ssl_root_cert=None,
        llm_base_url="https://api.openai.com/v1",
        llm_api_key=None,
        llm_model="gpt-4.1-mini",
        rag_enabled=True,
        rag_base_url="https://api.openai.com/v1",
        rag_api_key=None,
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_chunker_version="rag-chunker-v1",
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=900,
        rag_hybrid_fts_candidates=50,
        rag_hybrid_vector_candidates=50,
        rag_debug_indexing=False,
        title_parse_use_llm=True,
        common_scanlation_groups=(),
        common_authors=(),
        export_default_strategy="cache_only",
        export_stale_after_hours=24,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
        backup_keep_count=20,
        cleanup_staging_older_than_hours=48,
        worker_poll_seconds=2.0,
        worker_lease_seconds=60,
        worker_heartbeat_seconds=15,
        worker_parallelism=2,
    )
    refreshed = SimpleNamespace(
        config_path=config_path,
        request_timeout_seconds=30.0,
        request_interval_seconds=1.0,
        request_interval_jitter_seconds=0.5,
        use_system_proxy=False,
        image_download_timeout_seconds=45.0,
        image_download_retries=2,
        archive_thread_max_pages=50,
        novel_author_only_max_pages=50,
        novel_author_only_page_delay_seconds=0.5,
        db_backend="sqlite",
        db_url=None,
        db_pool_min=1,
        db_pool_max=5,
        db_pool_timeout=30.0,
        db_connect_timeout=10.0,
        db_schema="public",
        db_ssl_mode="prefer",
        db_ssl_root_cert=None,
        llm_base_url="https://api.openai.com/v1",
        llm_api_key=None,
        llm_model="gpt-4.1-mini",
        rag_enabled=True,
        rag_base_url="https://api.openai.com/v1",
        rag_api_key=None,
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_chunker_version="rag-chunker-v1",
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=900,
        rag_hybrid_fts_candidates=50,
        rag_hybrid_vector_candidates=50,
        rag_debug_indexing=False,
        title_parse_use_llm=True,
        common_scanlation_groups=(),
        common_authors=(),
        export_default_strategy="cache_only",
        export_stale_after_hours=24,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
        backup_keep_count=20,
        cleanup_staging_older_than_hours=48,
        worker_poll_seconds=2.0,
        worker_lease_seconds=60,
        worker_heartbeat_seconds=15,
        worker_parallelism=4,
    )
    body = {"values": {"worker_parallelism": 4}}
    handler = _CaptureHandler()
    handler.command = "POST"
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    with patch("yamibo_mcp.web.routes.settings.load_settings", return_value=refreshed):
        handle_settings_update(handler, current)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["restart_required"] is True
    assert payload["restart_targets"] == ["daemon"]
    assert payload["effect_mode_summary"] == "restart_daemon"


def test_settings_update_reports_daemon_and_web_restart_targets(tmp_path: Path):
    config_path = tmp_path / "yamibo.local.json"
    config_path.write_text(json.dumps({"llm": {"base_url": "https://old.example.com/v1"}}), encoding="utf-8")
    current = SimpleNamespace(
        config_path=config_path,
        request_timeout_seconds=30.0,
        request_interval_seconds=1.0,
        request_interval_jitter_seconds=0.5,
        use_system_proxy=False,
        image_download_timeout_seconds=45.0,
        image_download_retries=2,
        archive_thread_max_pages=50,
        novel_author_only_max_pages=50,
        novel_author_only_page_delay_seconds=0.5,
        db_backend="sqlite",
        db_url=None,
        db_pool_min=1,
        db_pool_max=5,
        db_pool_timeout=30.0,
        db_connect_timeout=10.0,
        db_schema="public",
        db_ssl_mode="prefer",
        db_ssl_root_cert=None,
        llm_base_url="https://old.example.com/v1",
        llm_api_key=None,
        llm_model="gpt-4.1-mini",
        rag_enabled=True,
        rag_base_url="https://old.example.com/v1",
        rag_api_key=None,
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_chunker_version="rag-chunker-v1",
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=900,
        rag_hybrid_fts_candidates=50,
        rag_hybrid_vector_candidates=50,
        rag_debug_indexing=False,
        title_parse_use_llm=True,
        common_scanlation_groups=(),
        common_authors=(),
        export_default_strategy="cache_only",
        export_stale_after_hours=24,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
        backup_keep_count=20,
        cleanup_staging_older_than_hours=48,
        worker_poll_seconds=2.0,
        worker_lease_seconds=60,
        worker_heartbeat_seconds=15,
        worker_parallelism=2,
    )
    refreshed = SimpleNamespace(
        config_path=config_path,
        request_timeout_seconds=30.0,
        request_interval_seconds=1.0,
        request_interval_jitter_seconds=0.5,
        use_system_proxy=False,
        image_download_timeout_seconds=45.0,
        image_download_retries=2,
        archive_thread_max_pages=50,
        novel_author_only_max_pages=50,
        novel_author_only_page_delay_seconds=0.5,
        db_backend="sqlite",
        db_url=None,
        db_pool_min=1,
        db_pool_max=5,
        db_pool_timeout=30.0,
        db_connect_timeout=10.0,
        db_schema="public",
        db_ssl_mode="prefer",
        db_ssl_root_cert=None,
        llm_base_url="https://new.example.com/v1",
        llm_api_key=None,
        llm_model="gpt-4.1-mini",
        rag_enabled=True,
        rag_base_url="https://new.example.com/v1",
        rag_api_key=None,
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_chunker_version="rag-chunker-v1",
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=900,
        rag_hybrid_fts_candidates=50,
        rag_hybrid_vector_candidates=50,
        rag_debug_indexing=False,
        title_parse_use_llm=True,
        common_scanlation_groups=(),
        common_authors=(),
        export_default_strategy="cache_only",
        export_stale_after_hours=24,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
        backup_keep_count=20,
        cleanup_staging_older_than_hours=48,
        worker_poll_seconds=2.0,
        worker_lease_seconds=60,
        worker_heartbeat_seconds=15,
        worker_parallelism=2,
    )
    body = {"values": {"llm_base_url": "https://new.example.com/v1"}}
    handler = _CaptureHandler()
    handler.command = "POST"
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    with patch("yamibo_mcp.web.routes.settings.load_settings", return_value=refreshed):
        handle_settings_update(handler, current)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["restart_required"] is True
    assert payload["restart_targets"] == ["daemon", "web"]
    assert payload["effect_mode_summary"] == "restart_daemon_web"


def test_settings_update_rejects_locked_fields(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "yamibo.local.json"
    config_path.write_text("{}", encoding="utf-8")
    settings = SimpleNamespace(
        config_path=config_path,
        request_timeout_seconds=30.0,
        request_interval_seconds=1.0,
        request_interval_jitter_seconds=0.5,
        use_system_proxy=False,
        image_download_timeout_seconds=45.0,
        image_download_retries=2,
        archive_thread_max_pages=50,
        novel_author_only_max_pages=50,
        novel_author_only_page_delay_seconds=0.5,
        db_backend="sqlite",
        db_url=None,
        db_pool_min=1,
        db_pool_max=5,
        db_pool_timeout=30.0,
        db_connect_timeout=10.0,
        db_schema="public",
        db_ssl_mode="prefer",
        db_ssl_root_cert=None,
        llm_base_url="https://api.openai.com/v1",
        llm_api_key="env-key",
        llm_model="gpt-4.1-mini",
        rag_enabled=True,
        rag_base_url="https://api.openai.com/v1",
        rag_api_key=None,
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_chunker_version="rag-chunker-v1",
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=900,
        rag_hybrid_fts_candidates=50,
        rag_hybrid_vector_candidates=50,
        rag_debug_indexing=False,
        title_parse_use_llm=True,
        common_scanlation_groups=(),
        common_authors=(),
        export_default_strategy="cache_only",
        export_stale_after_hours=24,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
        backup_keep_count=20,
        cleanup_staging_older_than_hours=48,
        worker_poll_seconds=2.0,
        worker_lease_seconds=60,
        worker_heartbeat_seconds=15,
        worker_parallelism=2,
    )
    body = {"values": {"llm_api_key": "new-key"}}
    handler = _CaptureHandler()
    handler.command = "POST"
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))
    monkeypatch.setenv("YAMIBO_LLM_API_KEY", "env-key")

    handle_settings_update(handler, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["error"] == "llm_api_key is overridden by environment variable"


def test_thread_update_endpoint_creates_update_job(db):
    ThreadsRepository(db).upsert_snapshot(_make_snapshot(tid=42), forum_id=55)
    handler = _CaptureHandler()
    body = {"tid": 42, "base_url": "https://bbs.yamibo.com"}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))
    # create_update_thread_job 内部 finally 会 conn.close()；用 wraps 代理真实 db 但让 close 变 no-op
    conn_mock = MagicMock(wraps=db)
    conn_mock.close = lambda: None
    with patch("yamibo_mcp.application.update_commands.connect", return_value=conn_mock), \
         patch("yamibo_mcp.application.update_commands.load_settings", return_value=SimpleNamespace(db_path=Path("test"))):
        handle_update_thread(handler, db)
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    job = JobsRepository(db).get(payload["job_id"])
    assert job.job_type == "update_thread"


def test_thread_update_endpoint_rejects_non_novel_thread(db):
    ThreadsRepository(db).upsert_snapshot(_make_snapshot(tid=42), forum_id=5)
    handler = _CaptureHandler()
    body = {"tid": 42, "base_url": "https://bbs.yamibo.com"}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))
    conn_mock = MagicMock(wraps=db)
    conn_mock.close = lambda: None
    with patch("yamibo_mcp.application.update_commands.connect", return_value=conn_mock), \
         patch("yamibo_mcp.application.update_commands.load_settings", return_value=SimpleNamespace(db_path=Path("test"))):
        handle_update_thread(handler, db)
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert "error" in payload
    assert "not a novel thread" in payload["error"]


def test_update_title_defaults_missing_fields_from_existing_title(db):
    settings = SimpleNamespace()
    snapshot = _make_snapshot()
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=55)

    handler = _CaptureHandler()
    body = {"tid": 42, "display_title": "新的标题"}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))
    with patch("yamibo_mcp.web.routes.review.update_title_hints", return_value=None):
        handle_update_title(handler, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    refreshed = ThreadsRepository(db).get_thread(42)
    assert refreshed["display_title"] == "新的标题"


def test_update_chapter_updates_archive_metadata_without_review_route(db):
    snapshot = _make_snapshot()
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=55)

    handler = _CaptureHandler()
    body = {
        "tid": 42,
        "display_title": "手动改标题",
        "chapter_name": "番外",
        "chapter_index": 1.5,
        "author_guess": "作者乙",
        "group_name": "B组",
    }
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    handle_update_chapter(handler, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    refreshed_thread = ThreadsRepository(db).get_thread(42)
    refreshed_title = ThreadsRepository(db).get_title_parse(42)
    assert refreshed_thread["display_title"] == "手动改标题"
    assert refreshed_title["chapter_name"] == "番外"
    assert refreshed_title["author_guess"] == "作者乙"


def test_merge_series_endpoint_moves_threads_and_deletes_source(db):
    from yamibo_mcp.db.repositories.series import SeriesRepository

    repo = ThreadsRepository(db)
    source_title = TitleSnapshot(
        raw_title="[组] 源系列",
        display_title="源系列",
        group_name="组",
        author_guess="作者",
        core_title_guess="源系列",
        normalized_core_title="源系列",
        series_key="源系列",
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
    target_title = TitleSnapshot(
        raw_title="[组] 目标系列",
        display_title="目标系列",
        group_name="组",
        author_guess="作者",
        core_title_guess="目标系列",
        normalized_core_title="目标系列",
        series_key="目标系列",
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
    source_snapshot = ThreadSnapshot(
        tid=501,
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=501",
        page_type="thread_detail",
        raw_title="源系列",
        display_title="源系列",
        title=source_title,
        publisher="u1",
        publisher_uid="100",
        pub_time="2025-01-01 00:00",
        permission=0,
        floors=[_make_floor(pid=501001, tid=501, floor_no=1, pub_time="2025-01-01 00:00", publisher="u1")],
        image_count=0,
    )
    target_snapshot = ThreadSnapshot(
        tid=502,
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=502",
        page_type="thread_detail",
        raw_title="目标系列",
        display_title="目标系列",
        title=target_title,
        publisher="u1",
        publisher_uid="100",
        pub_time="2025-01-01 00:00",
        permission=0,
        floors=[_make_floor(pid=502001, tid=502, floor_no=1, pub_time="2025-01-01 00:00", publisher="u1")],
        image_count=0,
    )
    repo.upsert_snapshot(source_snapshot, forum_id=55)
    repo.upsert_snapshot(target_snapshot, forum_id=55)

    series_repo = SeriesRepository(db)
    source_id = repo.get_thread(501)["series_id"]
    target_id = repo.get_thread(502)["series_id"]

    handler = _CaptureHandler()
    body = {"source_series_id": source_id, "target_series_id": target_id}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    handle_merge_series(handler, db, SimpleNamespace())

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["target_series_id"] == target_id
    assert series_repo.get_series(source_id) is None
    assert repo.get_thread(501)["series_id"] == target_id


def test_batch_delete_threads_endpoint_deletes_requested_threads(db):
    repo = ThreadsRepository(db)
    repo.upsert_snapshot(_make_snapshot(tid=42), forum_id=55)
    repo.upsert_snapshot(_make_snapshot(tid=43), forum_id=55)

    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"tids": [42, 43]}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    handle_batch_delete_threads(handler, db, SimpleNamespace())

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["deleted"] == 2
    assert repo.get_thread(42) is None
    assert repo.get_thread(43) is None


def test_delete_thread_endpoint_removes_thread_directory(db, tmp_path: Path):
    data_dir = tmp_path / "data"
    settings = SimpleNamespace(data_dir=data_dir)
    repo = ThreadsRepository(db)
    repo.upsert_snapshot(_make_snapshot(tid=42), forum_id=55)

    thread_dir = data_dir / "threads" / "42"
    thread_dir.mkdir(parents=True, exist_ok=True)
    (thread_dir / "metadata.json").write_text("{}", encoding="utf-8")

    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"tid": 42}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    handle_delete_thread(handler, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert repo.get_thread(42) is None
    assert not thread_dir.exists()


def test_batch_resync_threads_endpoint_creates_jobs(db):
    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"tids": [42, 43]}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    with patch("yamibo_mcp.web.routes.threads.create_thread_archive_batch_jobs") as mock_create:
        from yamibo_mcp.application.contracts import AgentResult

        mock_create.return_value = AgentResult(
            ok=True,
            data={
                "job_type": "sync_thread",
                "target_count": 2,
                "created_count": 2,
                "reused_count": 0,
                "created_job_ids": ["sync_thread_1", "sync_thread_2"],
                "reused_job_ids": [],
                "tids": [42, 43],
            },
        )
        handle_resync_threads_batch(handler, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["target_count"] == 2
    assert payload["created_count"] == 2


def test_configure_logging_attaches_web_log_buffer():
    configure_logging()
    logger = logging.getLogger("yamibo_mcp.tests.logging")
    marker = "buffer-capture-marker-001"
    logger.info(marker)

    entries = get_log_buffer().get_recent(limit=20)
    assert any(marker in entry["message"] for entry in entries)


def _seed_rag_thread(db):
    snapshot = _make_snapshot()
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=55, archive_status="complete", missing_image_urls=[])
    RagChunksRepository(db).replace_thread_chunks(
        tid=42,
        chunks=[
            _rag_chunk(
                chunk_id="thread:42:title",
                pid=None,
                floor_no=None,
                chunk_type="thread_title",
                metadata_text="作者\n测试帖子",
                text="测试帖子 第一章",
                text_hash="hash-1",
                source_uri="yamibo://threads/42/summary",
            ),
            _rag_chunk(
                chunk_id="thread:42:floor:1:part:1",
                pid=1001,
                floor_no=1,
                chunk_type="floor",
                metadata_text="作者\n测试帖子",
                text="星空下的告白",
                text_hash="hash-2",
                source_uri="yamibo://threads/42/posts#floor=1",
            ),
        ],
        embedding_model="text-embedding-3-small",
        embedding_dimensions=512,
    )
    db.execute("UPDATE rag_chunks SET embedding_status = 'indexed' WHERE chunk_id = 'thread:42:title'")
    db.execute("UPDATE rag_chunks SET embedding_status = 'failed' WHERE chunk_id = 'thread:42:floor:1:part:1'")
    db.commit()


def _rag_chunk(
    *,
    chunk_id: str,
    pid: int | None,
    floor_no: int | None,
    chunk_type: str,
    metadata_text: str,
    text: str,
    text_hash: str,
    source_uri: str,
) -> RagChunk:
    return RagChunk(
        chunk_id=chunk_id,
        tid=42,
        pid=pid,
        floor_no=floor_no,
        chunk_type=chunk_type,
        forum_id=55,
        content_kind="novel",
        series_id=1,
        series_key="测试帖子",
        chapter_index=1.0,
        publisher="u1",
        pub_time="2025-01-01 00:00",
        title="测试帖子",
        metadata_text=metadata_text,
        text=text,
        text_hash=text_hash,
        source_uri=source_uri,
        source_tid=42,
        source_pid=pid,
        source_floor_no=floor_no,
        cleaner_version="anime-cleaner-1.2",
        chunker_version="anime-chunker-1.2",
        materializer_version="anime-rag-materializer-1.2",
        source_hash="test-source-hash",
        generated_at="2025-01-01T00:00:00+00:00",
        quality_flags=[],
    )


def test_rag_overview_exposes_counts_and_meta(db):
    _seed_rag_thread(db)
    RagChunksRepository(db).write_index_meta({"embedding_model": "text-embedding-3-small", "embedding_dimensions": "512"})
    handler = _CaptureHandler()
    settings = SimpleNamespace(
        rag_enabled=True,
        rag_embedding_provider="openai",
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_chunker_version="rag-chunker-v1",
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=900,
        rag_hybrid_fts_candidates=50,
        rag_hybrid_vector_candidates=50,
    )

    handle_rag_overview(handler, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["counts"]["thread_total"] >= 1
    assert payload["counts"]["indexed_threads"] >= 1
    assert payload["counts"]["unindexed_threads"] >= 0
    assert payload["counts"]["total_chunks"] >= 2
    assert payload["counts"]["indexed_chunks"] >= 1
    assert payload["counts"]["failed_chunks"] >= 1
    assert payload["index_meta"]["embedding_model"] == "text-embedding-3-small"


def test_rag_overview_serializes_decimal_counts(db, monkeypatch):
    _seed_rag_thread(db)
    RagChunksRepository(db).write_index_meta({"embedding_model": "text-embedding-3-small", "embedding_dimensions": "512"})

    def _fake_counts(self):
        return {
            "total_threads": Decimal("1"),
            "indexed_threads": Decimal("1"),
            "unindexed_threads": Decimal("0"),
            "total_chunks": Decimal("2"),
            "indexed_chunks": Decimal("1"),
            "pending_chunks": Decimal("0"),
            "failed_chunks": Decimal("1"),
        }

    monkeypatch.setattr(RagChunksRepository, "count_rag_overview", _fake_counts)

    handler = _CaptureHandler()
    settings = SimpleNamespace(
        rag_enabled=True,
        rag_embedding_provider="openai",
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_chunker_version="rag-chunker-v1",
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=900,
        rag_hybrid_fts_candidates=50,
        rag_hybrid_vector_candidates=50,
    )

    handle_rag_overview(handler, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["counts"]["thread_total"] == 1
    assert payload["counts"]["total_chunks"] == 2
    assert payload["counts"]["failed_chunks"] == 1


def test_rag_threads_returns_per_thread_index_status(db):
    _seed_rag_thread(db)
    handler = _CaptureHandler()

    handle_rag_threads(handler, {"index_state": ["indexed"], "page": ["1"], "page_size": ["20"]}, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["index_state"] == "indexed"
    assert payload["page"] == 1
    assert payload["page_size"] == 20
    assert payload["total_count"] >= 1
    assert payload["items"][0]["tid"] == 42
    assert payload["items"][0]["rag_chunk_count"] == 2
    assert payload["items"][0]["rag_indexed_chunk_count"] == 1
    assert payload["items"][0]["rag_failed_chunk_count"] == 1


def test_rag_threads_orders_indexing_threads_after_unindexed_threads(db):
    _seed_rag_thread(db)
    ThreadsRepository(db).upsert_snapshot(_make_snapshot(tid=43), forum_id=55, archive_status="complete", missing_image_urls=[])
    JobsRepository(db).create("rag_index", tid=42, payload={"tid": 42})

    handler = _CaptureHandler()
    handle_rag_threads(handler, {"index_state": ["unindexed"], "page": ["1"], "page_size": ["20"]}, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["total_count"] == 2
    assert [item["tid"] for item in payload["items"]] == [43, 42]
    assert payload["items"][1]["rag_index_state"] == "indexing"


def test_rag_index_endpoint_returns_job_payload(db):
    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"tid": 42, "force": False, "embedding_dimensions": 512}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    with patch("yamibo_mcp.web.routes.rag.create_rag_index_job") as mock_create:
        from yamibo_mcp.application.contracts import AgentResult

        mock_create.return_value = AgentResult(ok=True, data={"job_id": "rag_index_123", "tid": 42, "created": True})
        handle_rag_index(handler)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["data"]["job_id"] == "rag_index_123"


def test_rag_index_batch_endpoint_creates_jobs(db):
    _seed_rag_thread(db)
    db_path = db.execute("PRAGMA database_list").fetchone()["file"]
    settings = SimpleNamespace(db_path=Path(db_path), rag_embedding_dimensions=512)
    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"tids": [42], "force": False}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    with patch("yamibo_mcp.application.rag_commands.load_settings", return_value=settings):
        handle_rag_index_batch(handler, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["target_count"] == 1
    assert payload["created_count"] == 1


def test_forums_list_includes_cached_archive_size(db, tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = SimpleNamespace(data_dir=data_dir)
    snapshot = _make_snapshot(tid=42)
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=30, archive_status="complete", missing_image_urls=[])
    thread_dir = data_dir / "threads" / "42"
    thread_dir.mkdir(parents=True, exist_ok=True)
    (thread_dir / "context.md").write_bytes(b"1234567890")
    (thread_dir / "metadata.json").write_bytes(b"12345")
    images_dir = thread_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "image.jpg").write_bytes(b"abc")

    refresh_handler = _CaptureHandler()
    refresh_handler.command = "POST"
    handle_refresh_forum_size_cache(refresh_handler, db, settings)

    refresh_payload = json.loads(refresh_handler.wfile.getvalue().decode("utf-8"))
    assert refresh_payload["ok"] is True
    assert refresh_payload["forum_count"] >= 1

    handler = _CaptureHandler()
    handle_forums_list(handler, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    forum_30 = next(row for row in payload if row["forum_id"] == 30)
    assert forum_30["archive_size_bytes"] == 18
    assert forum_30["archive_size_updated_at"] is not None


def test_retry_job_endpoint_requeues_partial_job(db):
    repo = JobsRepository(db)
    job = repo.create("rag_index", tid=42, payload={"tid": 42, "force": False})
    repo.partial(job.job_id, artifacts={"tid": 42, "warning": "embedding failed: 'data'"})

    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"job_id": job.job_id}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    handle_retry_job(handler, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["source_job_id"] == job.job_id
    assert payload["status"] == "queued"
    next_job = repo.get(payload["job_id"])
    assert next_job.job_type == "rag_index"
    assert next_job.tid == 42
    assert next_job.payload["tid"] == 42
    assert next_job.payload["force"] is False
    parent_row = db.execute("SELECT parent_job_id FROM jobs WHERE job_id = ?", (payload["job_id"],)).fetchone()
    assert parent_row["parent_job_id"] == job.job_id
    source_row = db.execute("SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)).fetchone()
    assert source_row["status"] == "superseded"


def test_retry_job_endpoint_requeues_failed_job(db):
    repo = JobsRepository(db)
    job = repo.create("rag_index", tid=43, payload={"tid": 43})
    repo.fail(job.job_id, "HTTP_500", "boom")

    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"job_id": job.job_id}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    handle_retry_job(handler, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["source_job_id"] == job.job_id
    assert payload["status"] == "queued"
    next_job = repo.get(payload["job_id"])
    assert next_job.job_type == "rag_index"
    assert next_job.tid == 43
    source_row = db.execute("SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)).fetchone()
    assert source_row["status"] == "superseded"


def test_retry_job_endpoint_requeues_interrupted_job(db):
    repo = JobsRepository(db)
    job = repo.create("rag_index", tid=44, payload={"tid": 44})
    db.execute(
        "UPDATE jobs SET status = ?, error_code = ?, error_message = ? WHERE job_id = ?",
        ("interrupted", "worker_lost", "lease expired", job.job_id),
    )
    db.commit()

    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"job_id": job.job_id}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    handle_retry_job(handler, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["source_job_id"] == job.job_id
    assert payload["status"] == "queued"
    next_job = repo.get(payload["job_id"])
    assert next_job.job_type == "rag_index"
    assert next_job.tid == 44
    source_row = db.execute("SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)).fetchone()
    assert source_row["status"] == "superseded"


def test_job_control_endpoint_pauses_active_jobs(db):
    repo = JobsRepository(db)
    queued = repo.create("sync_thread", tid=45)
    running = repo.create("sync_thread", tid=46)
    repo.acquire(running.job_id, "worker-1", 300)
    done = repo.create("noop")
    repo.succeed(done.job_id)

    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"action": "pause"}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    handle_job_control(handler, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["action"] == "pause"
    assert set(payload["changed_job_ids"]) == {queued.job_id, running.job_id}
    assert repo.get(queued.job_id).status == JobStatus.PAUSED
    assert repo.get(running.job_id).status == JobStatus.PAUSED
    assert repo.get(done.job_id).status == JobStatus.SUCCEEDED
    assert payload["job_control"]["paused"] == 2


def test_job_control_endpoint_resumes_released_paused_jobs(db):
    repo = JobsRepository(db)
    paused = repo.create("sync_thread", tid=47)
    repo.pause(paused.job_id)
    owned = repo.create("sync_thread", tid=48)
    repo.acquire(owned.job_id, "worker-1", 300)
    repo.pause(owned.job_id)

    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"action": "resume"}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    handle_job_control(handler, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["action"] == "resume"
    assert payload["changed_job_ids"] == [paused.job_id]
    assert repo.get(paused.job_id).status == JobStatus.QUEUED
    assert repo.get(owned.job_id).status == JobStatus.PAUSED
    assert payload["job_control"]["queued"] == 1
    assert payload["job_control"]["paused"] == 1


def test_job_control_endpoint_persists_jobs_enabled(tmp_path: Path, db):
    config_path = tmp_path / "yamibo.local.json"
    config_path.write_text("{}", encoding="utf-8")
    settings = SimpleNamespace(config_path=config_path)

    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"action": "pause"}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    handle_job_control(handler, db, settings)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert raw["worker"]["jobs_enabled"] is False
    assert payload["job_control"]["jobs_enabled"] is False

    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"action": "resume"}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    handle_job_control(handler, db, settings)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert raw["worker"]["jobs_enabled"] is True
    assert payload["job_control"]["jobs_enabled"] is True


def test_jobs_list_omits_payload_and_artifacts(db):
    repo = JobsRepository(db)
    job = repo.create("rag_index", tid=45, payload={"tid": 45, "force": True})
    repo.fail(job.job_id, "HTTP_500", "boom", artifacts={"large": "blob"})

    handler = _CaptureHandler()
    handle_jobs_list(handler, {"status": ["failed"], "page": ["1"], "page_size": ["25"]}, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["total_count"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 25
    assert len(payload["items"]) == 1
    row = payload["items"][0]
    assert row["job_id"] == job.job_id
    assert "payload" not in row
    assert "artifacts" not in row


def test_jobs_list_supports_pagination_and_failure_kind_filter(db):
    repo = JobsRepository(db)
    cancelled = repo.create("sync_thread", tid=50, payload={"tid": 50})
    validation = repo.create("sync_thread", tid=51, payload={"tid": 51})
    queued = repo.create("sync_thread", tid=52, payload={"tid": 52})
    repo.fail(cancelled.job_id, "cancelled", "was cancelled by user")
    repo.fail(validation.job_id, "ValueError", "bad input")

    handler = _CaptureHandler()
    handle_jobs_list(handler, {"status": ["failed"], "failure_kind": ["cancelled"], "page": ["1"], "page_size": ["10"]}, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["total_count"] == 1
    assert payload["total_pages"] == 1
    assert [row["job_id"] for row in payload["items"]] == [cancelled.job_id]
    assert payload["items"][0]["failure_kind"] == "cancelled"


def test_archive_threads_batch_endpoint_creates_jobs(db):
    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"tids": [42, 43], "forum_id": 30}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    with patch("yamibo_mcp.web.routes.threads.create_thread_archive_batch_jobs") as mock_create:
        from yamibo_mcp.application.contracts import AgentResult

        mock_create.return_value = AgentResult(
            ok=True,
            data={
                "job_type": "sync_thread",
                "target_count": 2,
                "created_count": 2,
                "reused_count": 0,
                "created_job_ids": ["sync_thread_1", "sync_thread_2"],
                "reused_job_ids": [],
                "tids": [42, 43],
            },
        )
        handle_archive_threads_batch(handler)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["target_count"] == 2
    assert payload["created_count"] == 2


def test_rag_search_endpoint_returns_search_payload(db):
    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"query": "星空 告白", "mode": "keyword", "top_k": 5}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    with patch("yamibo_mcp.web.routes.rag.search_archived_content") as mock_search:
        from yamibo_mcp.application.contracts import AgentResult

        mock_search.return_value = AgentResult(
            ok=True,
            data={"query": "星空 告白", "mode": "keyword", "top_k": 5, "count": 1, "items": [{"chunk_id": "c1"}]},
        )
        handle_rag_search(handler)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["data"]["count"] == 1
