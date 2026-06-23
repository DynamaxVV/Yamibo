from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
import logging
from unittest.mock import patch

from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.rag.chunker import RagChunk
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.web.api import (
    _archive_threads_batch,
    _batch_delete_threads,
    _forums_list,
    _refresh_forum_size_cache,
    _rag_index_batch,
    _rag_index,
    _rag_overview,
    _rag_search,
    _rag_threads,
    _retry_job,
    _resync_threads_batch,
    _thread_detail,
    _thread_update_check,
    _update_thread,
    _update_title,
)
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


def _make_snapshot(tid: int = 42) -> ThreadSnapshot:
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
        floors=[floor],
        image_count=1,
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
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    handler = _CaptureHandler()
    _thread_detail(handler, 42, db, {"preview_page": ["1"], "preview_page_size": ["10"]}, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["archive_summary"]["archived_images"]["1001"] == ["images/a.jpg"]
    assert payload["archive_summary"]["missing_image_urls"] == ["https://img.example.com/a.jpg"]
    assert payload["archive_summary"]["missing_shared_image_urls"] == ["https://img.example.com/shared-missing.jpg"]


def test_thread_detail_exposes_rag_summary(db, tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = SimpleNamespace(data_dir=data_dir, rag_enabled=True)
    _seed_rag_thread(db)

    handler = _CaptureHandler()
    _thread_detail(handler, 42, db, {"preview_page": ["1"], "preview_page_size": ["10"]}, settings)

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
                        "rich_body_html": "<div><strong>富文本</strong><a href=\"https://example.com\">链接</a></div>",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    handler = _CaptureHandler()
    _thread_detail(handler, 42, db, {"preview_page": ["1"], "preview_page_size": ["10"]}, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["floors"][0]["rich_body_html"] == "<div><strong>富文本</strong>链接</div>"


def test_thread_update_check_endpoint_returns_json(db):
    handler = _CaptureHandler()
    settings = SimpleNamespace()
    with patch("yamibo_mcp.web.api.check_thread_updates", return_value={"tid": 42, "status": "up_to_date"}):
        _thread_update_check(handler, 42, settings)
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["status"] == "up_to_date"


def test_thread_update_endpoint_creates_update_job(db):
    handler = _CaptureHandler()
    body = {"tid": 42, "base_url": "https://bbs.yamibo.com"}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))
    _update_thread(handler, db)
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    job = JobsRepository(db).get(payload["job_id"])
    assert job.job_type == "update_thread"


def test_update_title_defaults_missing_fields_from_existing_title(db):
    settings = SimpleNamespace()
    snapshot = _make_snapshot()
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=55)

    handler = _CaptureHandler()
    body = {"tid": 42, "display_title": "新的标题"}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))
    with patch("yamibo_mcp.web.api.update_title_hints", return_value=None):
        _update_title(handler, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    refreshed = ThreadsRepository(db).get_thread(42)
    assert refreshed["display_title"] == "新的标题"


def test_batch_delete_threads_endpoint_deletes_requested_threads(db):
    repo = ThreadsRepository(db)
    repo.upsert_snapshot(_make_snapshot(tid=42), forum_id=55)
    repo.upsert_snapshot(_make_snapshot(tid=43), forum_id=55)

    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"tids": [42, 43]}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    _batch_delete_threads(handler, db, SimpleNamespace())

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["deleted"] == 2
    assert repo.get_thread(42) is None
    assert repo.get_thread(43) is None


def test_batch_resync_threads_endpoint_creates_jobs(db):
    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"tids": [42, 43]}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    with patch("yamibo_mcp.web.api.create_thread_archive_batch_jobs") as mock_create:
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
        _resync_threads_batch(handler, db)

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
    assert any(marker in entry["msg"] for entry in entries)


def _seed_rag_thread(db):
    snapshot = _make_snapshot()
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=55, archive_status="complete", missing_image_urls=[])
    RagChunksRepository(db).replace_thread_chunks(
        tid=42,
        chunks=[
            RagChunk(
                chunk_id="thread:42:title",
                tid=42,
                pid=None,
                floor_no=None,
                chunk_type="thread_title",
                forum_id=55,
                content_kind="novel",
                series_id=1,
                series_key="测试帖子",
                chapter_index=1.0,
                publisher="u1",
                pub_time="2025-01-01 00:00",
                title="测试帖子",
                metadata_text="作者\n测试帖子",
                text="测试帖子 第一章",
                text_hash="hash-1",
                source_uri="yamibo://threads/42/summary",
            ),
            RagChunk(
                chunk_id="thread:42:floor:1:part:1",
                tid=42,
                pid=1001,
                floor_no=1,
                chunk_type="floor",
                forum_id=55,
                content_kind="novel",
                series_id=1,
                series_key="测试帖子",
                chapter_index=1.0,
                publisher="u1",
                pub_time="2025-01-01 00:00",
                title="测试帖子",
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

    _rag_overview(handler, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["counts"]["thread_total"] >= 1
    assert payload["counts"]["indexed_threads"] >= 1
    assert payload["counts"]["unindexed_threads"] >= 0
    assert payload["counts"]["total_chunks"] >= 2
    assert payload["counts"]["indexed_chunks"] >= 1
    assert payload["counts"]["failed_chunks"] >= 1
    assert payload["index_meta"]["embedding_model"] == "text-embedding-3-small"


def test_rag_threads_returns_per_thread_index_status(db):
    _seed_rag_thread(db)
    handler = _CaptureHandler()

    _rag_threads(handler, {"index_state": ["indexed"], "page": ["1"], "page_size": ["20"]}, db)

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
    _rag_threads(handler, {"index_state": ["unindexed"], "page": ["1"], "page_size": ["20"]}, db)

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

    with patch("yamibo_mcp.web.api.create_rag_index_job") as mock_create:
        from yamibo_mcp.application.contracts import AgentResult

        mock_create.return_value = AgentResult(ok=True, data={"job_id": "rag_index_123", "tid": 42, "created": True})
        _rag_index(handler)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["data"]["job_id"] == "rag_index_123"


def test_rag_index_batch_endpoint_creates_jobs(db):
    _seed_rag_thread(db)
    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"tids": [42], "force": False}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    _rag_index_batch(handler, db)

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
    _refresh_forum_size_cache(refresh_handler, db, settings)

    refresh_payload = json.loads(refresh_handler.wfile.getvalue().decode("utf-8"))
    assert refresh_payload["ok"] is True
    assert refresh_payload["forum_count"] >= 1

    handler = _CaptureHandler()
    _forums_list(handler, db, settings)

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

    _retry_job(handler, db)

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

    _retry_job(handler, db)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["source_job_id"] == job.job_id
    assert payload["status"] == "queued"
    next_job = repo.get(payload["job_id"])
    assert next_job.job_type == "rag_index"
    assert next_job.tid == 43
    source_row = db.execute("SELECT status FROM jobs WHERE job_id = ?", (job.job_id,)).fetchone()
    assert source_row["status"] == "superseded"


def test_archive_threads_batch_endpoint_creates_jobs(db):
    handler = _CaptureHandler()
    handler.command = "POST"
    body = {"tids": [42, 43], "forum_id": 30}
    handler.headers["Content-Length"] = str(len(json.dumps(body)))
    handler.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))

    with patch("yamibo_mcp.web.api.create_thread_archive_batch_jobs") as mock_create:
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
        _archive_threads_batch(handler)

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

    with patch("yamibo_mcp.web.api.search_archived_content") as mock_search:
        from yamibo_mcp.application.contracts import AgentResult

        mock_search.return_value = AgentResult(
            ok=True,
            data={"query": "星空 告白", "mode": "keyword", "top_k": 5, "count": 1, "items": [{"chunk_id": "c1"}]},
        )
        _rag_search(handler)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["data"]["count"] == 1
