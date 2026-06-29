from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from yamibo_mcp.errors import ThreadPermissionRequiredError
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.domain.thread_fingerprint import floor_content_hash
from yamibo_mcp.yamibo.client import FetchResult


def _fake_settings(tmp_path):
    settings = MagicMock()
    settings.db_path = str(tmp_path / "test.db")
    settings.data_dir = tmp_path / "data"
    settings.export_dir = tmp_path / "exports"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.worker_lease_seconds = 300
    settings.cookie_file = tmp_path / "cookies.txt"
    settings.use_system_proxy = False
    settings.login_username = None
    settings.login_password = None
    settings.request_interval_seconds = 0
    settings.request_interval_jitter_seconds = 0
    settings.project_root = tmp_path
    return settings


def _novel_snapshot(*, tid: int, last_content: str) -> ThreadSnapshot:
    title = TitleSnapshot(
        raw_title="[授权转载] 测试小说",
        display_title="测试小说",
        group_name=None,
        author_guess="作者",
        core_title_guess="测试小说",
        normalized_core_title="测试小说",
        series_key="测试小说",
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
    return ThreadSnapshot(
        tid=tid,
        url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&authorid=229047",
        page_type="thread_detail",
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher="作者",
        publisher_uid="229047",
        pub_time="2026-06-14 12:00",
        permission=0,
        floors=[
            FloorSnapshot(pid=1, tid=tid, floor_no=1, publisher="作者", content="开头", pub_time="2026-06-14 12:00", has_images=False),
            FloorSnapshot(pid=2, tid=tid, floor_no=2, publisher="作者", content=last_content, pub_time="2026-06-14 13:00", has_images=False),
        ],
        image_count=0,
    )


def _page_html(*, tid: int, title: str, last_pid: int, last_content: str, page_links: str = "") -> str:
    return f"""
    <html>
      <body>
        <span id="thread_subject">{title}</span>
        {page_links}
        <div id="post_{last_pid}">
          <div class="authi"><a href="space-uid-229047.html">楼主</a></div>
          <em id="authorposton{last_pid}">发表于 2026-06-14 13:00</em>
          <td id="postmessage_{last_pid}">{last_content}</td>
        </div>
      </body>
    </html>
    """


def test_check_thread_updates_reports_up_to_date_when_remote_tail_matches(tmp_path, db):
    settings = _fake_settings(tmp_path)
    snapshot = _novel_snapshot(tid=540745, last_content="尾章内容")
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=55)
    job_repo = JobsRepository(db)
    job = job_repo.create("sync_thread", tid=540745)
    job_repo.succeed(
        job.job_id,
        artifacts={
            "archive_signature": {
                "last_pid": 2,
                "floor_count": 2,
                "last_floor_hash": floor_content_hash("尾章内容"),
                "author_only_total_pages": 2,
            }
        },
    )

    page_1 = _page_html(
        tid=540745,
        title=snapshot.raw_title,
        last_pid=1,
        last_content="开头",
        page_links="""
            <div class="pg">
              <a href="forum.php?mod=viewthread&tid=540745&authorid=229047&page=2">2</a>
              <span title="共 2 页"> / 2 页</span>
            </div>
        """,
    )
    page_2 = _page_html(tid=540745, title=snapshot.raw_title, last_pid=2, last_content="尾章内容")

    fake_client = SimpleNamespace(
        fetch_thread_page=lambda *, tid, page, author_uid, base_url=None: FetchResult(
            url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&page={page}&authorid={author_uid}",
            final_url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&page={page}&authorid={author_uid}",
            status_code=200,
            html=page_1 if page == 1 else page_2,
        )
    )

    with patch("yamibo_mcp.application.update_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.update_queries.connect", return_value=db), \
         patch("yamibo_mcp.application.update_queries.YamiboClient", return_value=fake_client):
        from yamibo_mcp.application.update_queries import check_thread_updates

        result = check_thread_updates(tid=540745)

    assert result["status"] == "up_to_date"
    assert result["remote_snapshot"]["total_pages"] == 2
    assert any(item["field"] == "last_pid" for item in result["evidence"])
    assert any(item["field"] == "last_floor_hash" for item in result["evidence"])


def test_check_thread_updates_reports_updated_when_remote_tail_changes(tmp_path, db):
    settings = _fake_settings(tmp_path)
    snapshot = _novel_snapshot(tid=540745, last_content="尾章内容")
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=55)

    page_1 = _page_html(
        tid=540745,
        title=snapshot.raw_title,
        last_pid=1,
        last_content="开头",
        page_links="""
            <div class="pg">
              <a href="forum.php?mod=viewthread&tid=540745&authorid=229047&page=2">2</a>
              <span title="共 2 页"> / 2 页</span>
            </div>
        """,
    )
    page_2 = _page_html(tid=540745, title=snapshot.raw_title, last_pid=2, last_content="尾章内容-已更新")

    fake_client = SimpleNamespace(
        fetch_thread_page=lambda *, tid, page, author_uid, base_url=None: FetchResult(
            url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&page={page}&authorid={author_uid}",
            final_url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&page={page}&authorid={author_uid}",
            status_code=200,
            html=page_1 if page == 1 else page_2,
        )
    )

    with patch("yamibo_mcp.application.update_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.update_queries.connect", return_value=db), \
         patch("yamibo_mcp.application.update_queries.YamiboClient", return_value=fake_client):
        from yamibo_mcp.application.update_queries import check_thread_updates

        result = check_thread_updates(tid=540745)

    assert result["status"] == "updated"
    assert any(item["field"] == "last_floor_hash" for item in result["evidence"])


def test_check_thread_updates_retries_permission_gate_with_next_threshold(tmp_path, db):
    settings = _fake_settings(tmp_path)
    snapshot = _novel_snapshot(tid=540745, last_content="尾章内容")
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=55)

    page_1 = _page_html(
        tid=540745,
        title=snapshot.raw_title,
        last_pid=1,
        last_content="开头",
        page_links="""
            <div class="pg">
              <a href="forum.php?mod=viewthread&tid=540745&authorid=229047&page=2">2</a>
              <span title="共 2 页"> / 2 页</span>
            </div>
        """,
    )
    page_2 = _page_html(tid=540745, title=snapshot.raw_title, last_pid=2, last_content="尾章内容")

    calls: list[int | None] = []

    class _BorrowContext:
        def __init__(self, fail: bool) -> None:
            self.fail = fail

        def __enter__(self):
            if self.fail:
                raise ThreadPermissionRequiredError(
                    "thread requires read permission above 10 for https://bbs.yamibo.com/forum.php?mod=viewthread&tid=540745",
                    required_permission=10,
                )
            return (
                SimpleNamespace(account_id="high"),
                SimpleNamespace(
                    fetch_thread_page=lambda *, tid, page, author_uid, base_url=None: FetchResult(
                        url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&page={page}&authorid={author_uid}",
                        final_url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&page={page}&authorid={author_uid}",
                        status_code=200,
                        html=page_1 if page == 1 else page_2,
                    ),
                ),
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_borrow(settings_arg, *, min_permission=None, prefer_high_permission=False, cookie_file=None):
        calls.append(min_permission)
        return _BorrowContext(fail=len(calls) == 1)

    with patch("yamibo_mcp.application.update_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.update_queries.connect", return_value=db), \
         patch("yamibo_mcp.application.update_queries.has_configured_account_pool", lambda settings: True), \
         patch("yamibo_mcp.application.update_queries.borrow_yamibo_client", fake_borrow):
        from yamibo_mcp.application.update_queries import check_thread_updates

        result = check_thread_updates(tid=540745)

    assert result["status"] == "up_to_date"
    assert calls == [None, 11]


def test_check_thread_updates_returns_not_supported_for_non_novel_forum(tmp_path, db):
    settings = _fake_settings(tmp_path)
    title = TitleSnapshot(
        raw_title="[漫画] 测试",
        display_title="测试",
        group_name=None,
        author_guess="作者",
        core_title_guess="测试",
        normalized_core_title="测试",
        series_key="测试",
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
    snapshot = ThreadSnapshot(
        tid=30,
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=30",
        page_type="thread_detail",
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher="作者",
        publisher_uid="1",
        pub_time="2026-06-14 12:00",
        permission=0,
        floors=[FloorSnapshot(pid=1, tid=30, floor_no=1, publisher="作者", content="正文", pub_time="2026-06-14 12:00", has_images=False)],
        image_count=0,
    )
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=30)

    with patch("yamibo_mcp.application.update_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.update_queries.connect", return_value=db):
        from yamibo_mcp.application.update_queries import check_thread_updates

        result = check_thread_updates(tid=30)

    assert result["status"] == "not_supported"
