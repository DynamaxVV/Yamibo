from contextlib import contextmanager

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.web.routes.remote_forum import (
    handle_remote_forums_list,
    handle_remote_forum_browse,
    handle_remote_thread_detail,
)


class _CaptureHandler:
    def __init__(self, command="GET", body=None):
        self.command = command
        self.headers = {"Content-Length": str(len(body or b""))}
        self.status = None
        self.sent_headers: list[tuple[str, str]] = []
        self.wfile = io.BytesIO()
        self.rfile = io.BytesIO(body or b"")

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers.append((key, value))

    def end_headers(self):
        pass


def _make_settings(tmp_path: Path) -> SimpleNamespace:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return SimpleNamespace(
        cookie_file=cookie_file,
        data_dir=data_dir,
        use_system_proxy=False,
        login_username=None,
        login_password=None,
        request_timeout_seconds=15.0,
        request_interval_seconds=1.0,
        request_interval_jitter_seconds=0.5,
    )


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
        rich_body_html="<p>正文</p>",
    )
    return ThreadSnapshot(
        tid=tid,
        url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}",
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


def _mock_open_web_client(mock_client):
    @contextmanager
    def _cm(settings, **kwargs):
        yield None, mock_client
    return _cm


def test_remote_forums_list_returns_enabled_forums(db):
    handler = _CaptureHandler()
    settings = SimpleNamespace()
    handle_remote_forums_list(handler, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert handler.status == 200
    assert len(payload) >= 2
    comic = next(f for f in payload if f["forum_id"] == 30)
    assert comic["name"] == "漫画区"
    assert comic["enabled"] is True
    assert comic["content_kind"] == "comic"


def test_remote_forum_browse_returns_parsed_items(db, tmp_path: Path):
    settings = _make_settings(tmp_path)

    mock_result = MagicMock()
    mock_result.final_url = "https://bbs.yamibo.com/forum-30-1.html"
    mock_result.html = "<html></html>"

    mock_client = MagicMock()
    mock_client.fetch_forum_threads.return_value = (mock_result, [])

    with patch(
        "yamibo_mcp.web.routes.remote_forum._open_web_client", _mock_open_web_client(mock_client)
    ), patch(
        "yamibo_mcp.web.routes.remote_forum.extract_total_pages", return_value=5
    ):
        handler = _CaptureHandler()
        handle_remote_forum_browse(handler, {"forum_id": ["30"], "page": ["1"]}, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert handler.status == 200
    assert payload["source"] == "remote"
    assert payload["forum_id"] == 30
    assert payload["page"] == 1
    assert payload["order"] == "default"
    assert payload["total_pages"] == 5
    assert payload["final_url"] == "https://bbs.yamibo.com/forum-30-1.html"


def test_remote_forum_browse_dateline_order(db, tmp_path: Path):
    settings = _make_settings(tmp_path)

    mock_result = MagicMock()
    mock_result.final_url = "https://bbs.yamibo.com/forum.php?mod=forumdisplay&fid=30&orderby=dateline&page=1"

    mock_client = MagicMock()
    mock_client.fetch_forum_threads_dateline.return_value = (mock_result, [], 10)

    with patch("yamibo_mcp.web.routes.remote_forum._open_web_client", _mock_open_web_client(mock_client)):
        handler = _CaptureHandler()
        handle_remote_forum_browse(
            handler, {"forum_id": ["30"], "page": ["1"], "order": ["dateline"]}, db, settings
        )

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert handler.status == 200
    assert payload["order"] == "dateline"
    assert payload["total_pages"] == 10


def test_remote_forum_browse_invalid_page(db, tmp_path: Path):
    settings = _make_settings(tmp_path)
    handler = _CaptureHandler()
    handle_remote_forum_browse(handler, {"forum_id": ["30"], "page": ["0"]}, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert handler.status == 400
    assert "page must be positive" in payload["error"]


def test_remote_forum_browse_disabled_forum(db, tmp_path: Path):
    settings = _make_settings(tmp_path)
    handler = _CaptureHandler()
    handle_remote_forum_browse(handler, {"forum_id": ["999"], "page": ["1"]}, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert handler.status == 404


def test_remote_thread_detail_returns_reader_friendly_floors(db, tmp_path: Path):
    settings = _make_settings(tmp_path)
    snapshot = _make_snapshot(42)

    mock_result = MagicMock()
    mock_result.final_url = "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42&page=1"
    mock_result.html = "<html></html>"

    mock_client = MagicMock()
    mock_client.fetch_thread_page.return_value = mock_result

    with patch(
        "yamibo_mcp.web.routes.remote_forum._open_web_client", _mock_open_web_client(mock_client)
    ), patch(
        "yamibo_mcp.web.routes.remote_forum.parse_thread_snapshot", return_value=snapshot
    ):
        handler = _CaptureHandler()
        handle_remote_thread_detail(handler, 42, {"page": ["1"]}, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert handler.status == 200
    assert payload["source"] == "remote"
    assert payload["tid"] == 42
    assert payload["page"] == 1
    assert payload["total_pages"] is None
    assert payload["raw_title"] == "[组] 测试帖子"
    assert payload["display_title"] == "测试帖子"
    assert payload["publisher"] == "u1"
    assert payload["archive_status"] is None
    assert payload["local_thread"] is None
    assert len(payload["floors"]) == 1
    floor = payload["floors"][0]
    assert floor["pid"] == 42001
    assert floor["content"] == "正文"
    assert floor["has_images"] is True
    assert floor["rich_body_html"] == "<p>正文</p>"
    assert len(floor["image_slots"]) == 1
    assert floor["image_slots"][0]["remote_url"] == "https://img.example.com/a.jpg"
    assert floor["image_slots"][0]["local_path"] is None
    assert floor["image_slots"][0]["status"] == "remote"


def test_remote_thread_detail_enriches_local_status(db, tmp_path: Path):
    settings = _make_settings(tmp_path)
    snapshot = _make_snapshot(42)
    ThreadsRepository(db).upsert_snapshot(
        snapshot, forum_id=30, archive_status="complete", missing_image_urls=[]
    )

    mock_result = MagicMock()
    mock_result.final_url = "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42&page=1"
    mock_result.html = "<html></html>"

    mock_client = MagicMock()
    mock_client.fetch_thread_page.return_value = mock_result

    with patch(
        "yamibo_mcp.web.routes.remote_forum._open_web_client", _mock_open_web_client(mock_client)
    ), patch(
        "yamibo_mcp.web.routes.remote_forum.parse_thread_snapshot", return_value=snapshot
    ):
        handler = _CaptureHandler()
        handle_remote_thread_detail(handler, 42, {}, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["archive_status"] == "complete"
    assert payload["local_thread"]["archived"] is True
    assert payload["local_thread"]["content_kind"] == "comic"
    assert payload["forum_id"] == 30
    assert payload["content_kind"] == "comic"


def test_remote_thread_detail_invalid_page(db, tmp_path: Path):
    settings = _make_settings(tmp_path)
    handler = _CaptureHandler()
    handle_remote_thread_detail(handler, 42, {"page": ["0"]}, db, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert handler.status == 400
    assert "page must be positive" in payload["error"]


def test_rewrite_thread_links():
    from yamibo_mcp.web.routes.remote_forum import _rewrite_thread_links

    result = _rewrite_thread_links(
        '<a href="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=572313">原贴</a>'
    )
    assert 'href="/forum/572313"' in result
    assert 'tid=572313' not in result

    result = _rewrite_thread_links(
        '<a href="https://bbs.yamibo.com/thread-12345-1-1.html">链接</a>'
    )
    assert 'href="/forum/12345"' in result

    # Non-yamibo links should be unchanged
    result = _rewrite_thread_links(
        '<a href="https://example.com/page">外部</a>'
    )
    assert 'href="https://example.com/page"' in result
