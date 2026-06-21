from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
import logging
from unittest.mock import patch

from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.web.api import _thread_detail, _thread_update_check, _update_thread, _update_title
from yamibo_mcp.logging import configure_logging
from yamibo_mcp.web.log_buffer import get_log_buffer


class _CaptureHandler:
    def __init__(self):
        self.command = "GET"
        self.headers = {}
        self.status = None
        self.sent_headers: list[tuple[str, str]] = []
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers.append((key, value))

    def end_headers(self):
        pass


def _make_snapshot() -> ThreadSnapshot:
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
        pid=1001,
        tid=42,
        floor_no=1,
        publisher="u1",
        content="正文",
        pub_time="2025-01-01 00:00",
        has_images=True,
        image_urls=["https://img.example.com/a.jpg"],
    )
    return ThreadSnapshot(
        tid=42,
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42&authorid=100",
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
                        "pid": 1001,
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


def test_configure_logging_attaches_web_log_buffer():
    configure_logging()
    logger = logging.getLogger("yamibo_mcp.tests.logging")
    marker = "buffer-capture-marker-001"
    logger.info(marker)

    entries = get_log_buffer().get_recent(limit=20)
    assert any(marker in entry["msg"] for entry in entries)
