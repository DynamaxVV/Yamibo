from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from yamibo_mcp.errors import LoginRequiredError, ThreadPermissionRequiredError
from yamibo_mcp.daemon.handlers.update_thread import _load_local_thread_snapshot, handle_update_thread
from yamibo_mcp.db.connection import transaction
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.domain.thread_fingerprint import floor_content_hash
from yamibo_mcp.storage.images import ImageDownloadResult
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.storage.thread_archive import materialize_thread
from yamibo_mcp.yamibo.client import FetchResult


def _make_settings(tmp_path: Path) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    export_dir = tmp_path / "exports"
    novel_export_dir = tmp_path / "novel_exports"
    data_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)
    novel_export_dir.mkdir(parents=True, exist_ok=True)
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("", encoding="utf-8")
    return SimpleNamespace(
        data_dir=data_dir,
        export_dir=export_dir,
        novel_txt_export_dir=novel_export_dir,
        project_root=tmp_path,
        cookie_file=cookie_file,
        use_system_proxy=False,
        login_username=None,
        login_password=None,
        image_download_timeout_seconds=5,
        image_download_retries=0,
        worker_lease_seconds=300,
        novel_author_only_max_pages=5,
        novel_author_only_page_delay_seconds=0.0,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
        request_interval_seconds=0.0,
        request_interval_jitter_seconds=0.0,
    )


def _make_snapshot(*, tid: int, content: str, floor_no: int = 1, pid: int = 1001, rich_body_html: str | None = None) -> ThreadSnapshot:
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
    floor = FloorSnapshot(
        pid=pid,
        tid=tid,
        floor_no=floor_no,
        publisher="作者",
        content=content,
        pub_time="2026-06-14 12:00",
        has_images=False,
        rich_body_html=rich_body_html,
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
        floors=[floor],
        image_count=0,
    )


def _page_html(*, tid: int, page: int, content: str) -> str:
    return f"""
    <html>
      <body>
        <span id="thread_subject">[授权转载] 测试小说</span>
        <div class="pg"><span title="共 2 页"> / 2 页</span></div>
        <div id="post_{page}">
          <div class="authi"><a href="space-uid-229047.html">楼主</a></div>
          <em id="authorposton{page}">发表于 2026-06-14 12:00</em>
          <td id="postmessage_{page}">{content}</td>
        </div>
      </body>
    </html>
    """


def test_local_snapshot_repairs_stale_metadata_floor_numbers(db, tmp_path):
    settings = _make_settings(tmp_path)
    tid = 540746
    initial = _make_snapshot(tid=tid, content="楼主", rich_body_html="<div>楼主</div>")
    reply = replace(
        initial.floors[0],
        pid=1002,
        floor_no=2,
        publisher="回复者",
        content="回复",
        rich_body_html="<div>回复</div>",
    )
    snapshot = replace(initial, floors=[initial.floors[0], reply])
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=55)
    paths = StoragePaths(
        settings.data_dir,
        export_dir=settings.export_dir,
        novel_txt_export_dir=settings.novel_txt_export_dir,
    )
    materialize_thread(paths, snapshot)

    metadata = json.loads(paths.thread_metadata(tid).read_text(encoding="utf-8"))
    metadata["floors"][1]["floor_no"] = 1
    paths.thread_metadata(tid).write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

    local = _load_local_thread_snapshot(paths, db, ThreadsRepository(db).get_thread(tid))

    assert [floor.floor_no for floor in local.floors] == [1, 2]
    assert [floor.pid for floor in local.floors] == [1001, 1002]
    assert local.floors[1].rich_body_html == "<div>回复</div>"
    repaired = json.loads(paths.thread_metadata(tid).read_text(encoding="utf-8"))
    assert [floor["floor_no"] for floor in repaired["floors"]] == [1, 2]


@pytest.mark.parametrize("forum_id", [30, 55, 13])
def test_upsert_snapshot_defers_series_commit_to_outer_transaction(db, monkeypatch, forum_id):
    commits: list[str] = []
    connection_type = type(db)
    original_commit = connection_type.commit

    def tracked_commit(connection) -> None:
        commits.append("commit")
        original_commit(connection)

    monkeypatch.setattr(connection_type, "commit", tracked_commit)
    with transaction(db):
        ThreadsRepository(db).upsert_snapshot(
            _make_snapshot(tid=540800 + forum_id, content="内容"),
            forum_id=forum_id,
        )

    assert commits == ["commit"]


def test_handle_update_thread_appends_new_floor(db, tmp_path, monkeypatch):
    settings = _make_settings(tmp_path)
    snapshot = _make_snapshot(tid=540745, content="旧尾章", rich_body_html="<div><strong>旧尾章</strong></div>")
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=55)
    paths = StoragePaths(settings.data_dir, export_dir=settings.export_dir, novel_txt_export_dir=settings.novel_txt_export_dir)
    materialize_thread(paths, snapshot, archived_images={1: ["images/old.jpg"]})

    repo = JobsRepository(db)
    job = repo.create("update_thread", tid=540745, payload={"tid": 540745})
    job = repo.acquire(job.job_id, "worker-1", 300)

    tail_page = _make_snapshot(tid=540745, content="旧尾章")
    new_page = _make_snapshot(tid=540745, content="新增章节", floor_no=2, pid=1002)
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.update_thread.check_thread_updates",
        lambda tid, base_url=None, proxy_url=None: {
            "tid": tid,
            "status": "updated",
            "reason": "remote author-only snapshot differs from local archive",
            "local_snapshot": {
                "author_uid": "229047",
                "archive_signature": {
                    "author_uid": "229047",
                    "last_pid": 1001,
                    "floor_count": 1,
                    "last_floor_hash": floor_content_hash("旧尾章"),
                    "author_only_total_pages": 1,
                },
            },
            "remote_snapshot": {"total_pages": 2},
            "evidence": [],
        },
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.update_thread.YamiboClient",
        lambda **kwargs: SimpleNamespace(
            headers={},
            cookie_jar=None,
            use_system_proxy=False,
            fetch_thread_page=lambda *, tid, page, author_uid, base_url=None: FetchResult(
                url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&page={page}&authorid={author_uid}",
                final_url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&page={page}&authorid={author_uid}",
                status_code=200,
                html=_page_html(tid=tid, page=page, content="旧尾章" if page == 1 else "新增章节"),
            ),
        ),
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.update_thread.parse_thread_snapshot",
        lambda html, url=None, tid=None: tail_page if "page=1" in (url or "") else new_page,
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.update_thread.download_images_to_staging",
        lambda *args, **kwargs: ImageDownloadResult(),
    )

    handle_update_thread(repo, job, "worker-1", 300, settings)

    thread_row = ThreadsRepository(db).get_thread(540745)
    floors = ThreadsRepository(db).list_floors(540745)
    meta = (settings.data_dir / "threads" / "540745" / "metadata.json").read_text(encoding="utf-8")

    assert thread_row is not None
    assert thread_row["sync_time"] is not None
    assert len(floors) == 2
    assert "新增章节" in meta
    assert "旧尾章" in meta


def test_handle_update_thread_refuses_tail_mismatch(db, tmp_path, monkeypatch):
    settings = _make_settings(tmp_path)
    snapshot = _make_snapshot(tid=540745, content="旧尾章")
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=55)
    paths = StoragePaths(settings.data_dir, export_dir=settings.export_dir, novel_txt_export_dir=settings.novel_txt_export_dir)
    materialize_thread(paths, snapshot)

    repo = JobsRepository(db)
    job = repo.create("update_thread", tid=540745, payload={"tid": 540745})
    job = repo.acquire(job.job_id, "worker-1", 300)

    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.update_thread.check_thread_updates",
        lambda tid, base_url=None, proxy_url=None: {
            "tid": tid,
            "status": "updated",
            "reason": "remote author-only snapshot differs from local archive",
            "local_snapshot": {
                "author_uid": "229047",
                "archive_signature": {
                    "author_uid": "229047",
                    "last_pid": 1001,
                    "floor_count": 1,
                    "last_floor_hash": floor_content_hash("旧尾章"),
                    "author_only_total_pages": 1,
                },
            },
            "remote_snapshot": {"total_pages": 2},
            "evidence": [],
        },
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.update_thread.YamiboClient",
        lambda **kwargs: SimpleNamespace(
            headers={},
            cookie_jar=None,
            use_system_proxy=False,
            fetch_thread_page=lambda *, tid, page, author_uid, base_url=None: FetchResult(
                url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&page={page}&authorid={author_uid}",
                final_url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&page={page}&authorid={author_uid}",
                status_code=200,
                html=_page_html(tid=tid, page=page, content="已修改" if page == 1 else "新增章节"),
            ),
        ),
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.update_thread.parse_thread_snapshot",
        lambda html, url=None, tid=None: _make_snapshot(tid=540745, content="已修改" if "page=1" in (url or "") else "新增章节"),
    )

    with pytest.raises(ValueError, match="full resync required"):
        handle_update_thread(repo, job, "worker-1", 300, settings)


def test_handle_update_thread_retries_permission_gate_with_next_threshold(db, tmp_path, monkeypatch):
    settings = _make_settings(tmp_path)
    snapshot = _make_snapshot(tid=540745, content="旧尾章")
    ThreadsRepository(db).upsert_snapshot(snapshot, forum_id=55)
    paths = StoragePaths(settings.data_dir, export_dir=settings.export_dir, novel_txt_export_dir=settings.novel_txt_export_dir)
    materialize_thread(paths, snapshot)

    repo = JobsRepository(db)
    job = repo.create("update_thread", tid=540745, payload={"tid": 540745})
    job = repo.acquire(job.job_id, "worker-1", 300)

    tail_page = _make_snapshot(tid=540745, content="旧尾章")
    new_page = _make_snapshot(tid=540745, content="新增章节", floor_no=2, pid=1002)
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.update_thread.check_thread_updates",
        lambda tid, base_url=None, proxy_url=None: {
            "tid": tid,
            "status": "updated",
            "reason": "remote author-only snapshot differs from local archive",
            "local_snapshot": {
                "author_uid": "229047",
                "archive_signature": {
                    "author_uid": "229047",
                    "last_pid": 1001,
                    "floor_count": 1,
                    "last_floor_hash": floor_content_hash("旧尾章"),
                    "author_only_total_pages": 1,
                },
            },
            "remote_snapshot": {"total_pages": 2},
            "evidence": [],
        },
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.update_thread.parse_thread_snapshot",
        lambda html, url=None, tid=None: tail_page if "page=1" in (url or "") else new_page,
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.update_thread.download_images_to_staging",
        lambda *args, **kwargs: ImageDownloadResult(),
    )

    calls: list[int | None] = []

    class _BorrowContext:
        def __init__(self, mode: str) -> None:
            self.mode = mode

        def __enter__(self):
            if self.mode == "login_required":
                raise LoginRequiredError("please login first")
            if self.mode == "permission_required":
                raise ThreadPermissionRequiredError(
                    "thread requires read permission above 10 for https://bbs.yamibo.com/forum.php?mod=viewthread&tid=540745",
                    required_permission=10,
                )
            return (
                SimpleNamespace(account_id="high"),
                SimpleNamespace(
                    headers={},
                    cookie_jar=None,
                    use_system_proxy=False,
                    cookie_file=None,
                    fetch_thread_page=lambda *, tid, page, author_uid, base_url=None: FetchResult(
                        url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&page={page}&authorid={author_uid}",
                        final_url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}&page={page}&authorid={author_uid}",
                        status_code=200,
                        html=_page_html(tid=tid, page=page, content="旧尾章" if page == 1 else "新增章节"),
                    ),
                ),
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_borrow(settings_arg, *, min_permission=None, prefer_high_permission=False, cookie_file=None, proxy_url=None):
        calls.append(min_permission)
        mode = "login_required" if len(calls) == 1 else "ok"
        return _BorrowContext(mode=mode)

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.update_thread.has_configured_account_pool", lambda settings: True)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.update_thread.borrow_yamibo_client", fake_borrow)

    handle_update_thread(repo, job, "worker-1", 300, settings)

    assert calls == [None, 1]
