from __future__ import annotations

from types import SimpleNamespace
from contextlib import contextmanager

from yamibo_mcp.daemon.handlers.image_backfill import handle_image_backfill, _diff_snapshot_images
from yamibo_mcp.daemon.image_backfill_scheduler import maybe_enqueue_image_backfill_dry_run
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.errors import ThreadPermissionRequiredError
from yamibo_mcp.storage.images import ImageDownloadResult
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.storage.paths import StoragePaths


def _snapshot(tid: int, image_urls: list[str]) -> ThreadSnapshot:
    title = TitleSnapshot(
        raw_title="title",
        display_title="title",
        group_name=None,
        author_guess=None,
        core_title_guess="title",
        normalized_core_title="title",
        series_key="title",
        title_aliases=[],
        chapter_name=None,
        chapter_index=None,
        chapter_index_end=None,
        chapter_title=None,
        subtitle=None,
        tags=[],
        confidence=1.0,
        needs_review=False,
    )
    return ThreadSnapshot(
        tid=tid,
        url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}",
        page_type="thread",
        raw_title="title",
        display_title="title",
        title=title,
        publisher="a",
        publisher_uid=None,
        pub_time=None,
        permission=0,
        floors=[
            FloorSnapshot(
                pid=tid * 10 + 1,
                tid=tid,
                floor_no=1,
                publisher="a",
                content="first",
                pub_time=None,
                has_images=False,
            ),
            FloorSnapshot(
                pid=tid * 10 + 2,
                tid=tid,
                floor_no=2,
                publisher="b",
                content="reply",
                pub_time=None,
                has_images=True,
                image_urls=image_urls,
            ),
        ],
        image_count=len(image_urls),
    )


def test_image_backfill_diff_skips_existing_static_shared_asset(tmp_path):
    paths = StoragePaths(tmp_path)
    static_url = "https://bbs.yamibo.com/static/image/smiley/gexing/008.gif"
    paths.shared_asset_path(static_url).parent.mkdir(parents=True, exist_ok=True)
    paths.shared_asset_path(static_url).write_bytes(b"gif")

    result = _diff_snapshot_images(_snapshot(123, [static_url]), local_assets={}, paths=paths)

    assert result["existing_non_first_images_ok"] == 1
    assert result["need_fetch_count"] == 0
    assert result["shared_static_missing_count"] == 0


def test_image_backfill_diff_counts_content_missing_separately_from_static(tmp_path):
    paths = StoragePaths(tmp_path)
    static_url = "https://bbs.yamibo.com/static/image/common/back.gif"
    content_url = "https://bbs.yamibo.com/data/attachment/forum/202501/01/image.jpg"

    result = _diff_snapshot_images(_snapshot(124, [static_url, content_url]), local_assets={}, paths=paths)

    assert result["need_fetch_count"] == 1
    assert result["content_need_fetch_count"] == 1
    assert result["shared_static_missing_count"] == 1
    assert result["need_fetch_by_class"]["content"] == 1
    assert result["need_fetch_by_class"]["decorative"] == 1


def test_auto_scheduler_creates_internal_image_backfill_dry_run_job(db, tmp_path):
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
        VALUES (1001, 'raw', 'display', '2026-07-01T00:00:00+00:00', 2, 'complete', 5)
        """
    )
    db.execute(
        """
        INSERT INTO floors (pid, tid, floor_no, content, has_images)
        VALUES (2001, 1001, 2, 'reply', 1)
        """
    )
    db.commit()

    settings = SimpleNamespace(
        image_backfill_enabled=True,
        image_backfill_dry_run=True,
        image_backfill_forum_id=5,
        image_backfill_auto_interval_seconds=0.0,
        image_backfill_daily_limit=10,
        image_backfill_max_pages=1,
        image_backfill_fixed_after="2026-07-02T00:00:00+00:00",
    )

    assert maybe_enqueue_image_backfill_dry_run(JobsRepository(db), settings) is True

    job = db.execute("SELECT * FROM jobs WHERE job_type = 'image_backfill'").fetchone()
    assert job is not None
    assert job["tid"] == 1001
    assert '"dry_run": true' in job["payload_json"]
    assert '"internal_auto": true' in job["payload_json"]


def test_auto_scheduler_apply_can_follow_prior_dry_run(db):
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
        VALUES (1002, 'raw', 'display', '2026-07-01T00:00:00+00:00', 2, 'complete', 5)
        """
    )
    db.execute(
        """
        INSERT INTO floors (pid, tid, floor_no, content, has_images)
        VALUES (2002, 1002, 2, 'reply', 1)
        """
    )
    db.commit()
    repo = JobsRepository(db)
    dry_job = repo.create("image_backfill", tid=1002, payload={"tid": 1002, "dry_run": True})
    repo.succeed(dry_job.job_id, {"dry_run": True})
    settings = SimpleNamespace(
        image_backfill_enabled=True,
        image_backfill_dry_run=False,
        image_backfill_forum_id=5,
        image_backfill_auto_interval_seconds=0.0,
        image_backfill_daily_limit=10,
        image_backfill_max_pages=1,
        image_backfill_fixed_after=None,
    )

    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is True

    jobs = db.execute("SELECT payload_json FROM jobs WHERE job_type = 'image_backfill' AND tid = 1002 ORDER BY created_at").fetchall()
    assert len(jobs) == 2
    assert '"dry_run": false' in jobs[-1]["payload_json"]


def test_image_backfill_apply_downloads_and_persists_missing_images(db, tmp_path, monkeypatch):
    tid = 2002
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id, context_path)
        VALUES (?, 'raw', 'display', '2026-07-01T00:00:00+00:00', 0, 'complete', 5, ?)
        """,
        (tid, f"threads/{tid}/context.md"),
    )
    db.execute(
        """
        INSERT INTO floors (pid, tid, floor_no, content, has_images)
        VALUES (?, ?, 1, 'first', 0)
        """,
        (tid * 10 + 1, tid),
    )
    db.commit()
    repo = JobsRepository(db)
    job = repo.create("image_backfill", tid=tid, payload={"tid": tid, "dry_run": False, "max_pages": 1})

    content_url = "https://bbs.yamibo.com/data/attachment/forum/202501/01/image.jpg"
    static_url = "https://bbs.yamibo.com/static/image/smiley/gexing/008.gif"
    snapshot = _snapshot(tid, [content_url, static_url])

    class _Fetch:
        html = "<html></html>"
        final_url = f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}"

    class _Client:
        headers = {}
        cookie_jar = None
        cookie_file = None
        use_system_proxy = False
        proxy_url = "http://127.0.0.1:9999"

        def fetch_thread_page(self, **kwargs):
            return _Fetch()

    borrow_kwargs = []
    download_kwargs = []

    @contextmanager
    def _borrow(settings, **kwargs):
        borrow_kwargs.append(kwargs)
        yield SimpleNamespace(account_id="pool", permission_level=10), _Client()

    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.image_backfill.select_thread_proxy",
        lambda *args, **kwargs: SimpleNamespace(
            proxy_url="http://127.0.0.1:9999",
            group="archive",
            node="n1",
            best_effort=False,
            diagnostics={},
        ),
    )
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.borrow_yamibo_client", _borrow)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.parse_thread_snapshot", lambda *args, **kwargs: snapshot)

    def _download(*args, **kwargs):
        download_kwargs.append(kwargs)
        return ImageDownloadResult(
            downloaded_relpaths={tid * 10 + 2: ["images/floor_002_01.jpg"]},
            shared_relpaths={tid * 10 + 2: ["shared/bbs.yamibo.com/static/image/smiley/gexing/008.gif"]},
            downloaded_count=1,
            shared_downloaded_count=1,
        )

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.download_images_to_staging", _download)

    settings = SimpleNamespace(
        data_dir=tmp_path,
        export_dir=tmp_path / "exports",
        novel_txt_export_dir=tmp_path / "novel_exports",
        image_backfill_max_pages=1,
        image_backfill_fixed_after=None,
        image_download_timeout_seconds=1.0,
        image_download_retries=0,
    )

    handle_image_backfill(repo, repo.get(job.job_id), "worker", 60, settings)

    completed = repo.get(job.job_id)
    assert completed.status == "succeeded"
    assert completed.artifacts["dry_run"] is False
    assert completed.artifacts["backfilled_image_count"] == 2
    assert borrow_kwargs == [{"min_permission": None, "proxy_url": "http://127.0.0.1:9999"}]
    assert download_kwargs[0]["proxy_url"] == "http://127.0.0.1:9999"
    assert completed.artifacts["proxy_pool.enabled"] is True
    assert completed.artifacts["proxy_pool.node"] == "n1"
    assets = db.execute("SELECT remote_url, local_path, status FROM assets WHERE tid = ? ORDER BY remote_url", (tid,)).fetchall()
    assert {row["remote_url"]: row["local_path"] for row in assets} == {
        content_url: "images/floor_002_01.jpg",
        static_url: "shared/bbs.yamibo.com/static/image/smiley/gexing/008.gif",
    }
    assert {row["status"] for row in assets} == {"downloaded"}


def test_image_backfill_reborrows_with_higher_permission_after_permission_gate(db, tmp_path, monkeypatch):
    tid = 2003
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id, context_path)
        VALUES (?, 'raw', 'display', '2026-07-01T00:00:00+00:00', 0, 'complete', 5, ?)
        """,
        (tid, f"threads/{tid}/context.md"),
    )
    db.execute(
        """
        INSERT INTO floors (pid, tid, floor_no, content, has_images)
        VALUES (?, ?, 1, 'first', 0)
        """,
        (tid * 10 + 1, tid),
    )
    db.commit()
    repo = JobsRepository(db)
    job = repo.create("image_backfill", tid=tid, payload={"tid": tid, "dry_run": True, "max_pages": 1})

    class _Fetch:
        html = "<html></html>"
        final_url = f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}"

    class _Client:
        headers = {}
        cookie_jar = None
        cookie_file = None
        use_system_proxy = False
        proxy_url = None

        def __init__(self, *, fail_permission: bool):
            self.fail_permission = fail_permission

        def fetch_thread_page(self, **kwargs):
            if self.fail_permission:
                raise ThreadPermissionRequiredError("requires permission", required_permission=50)
            return _Fetch()

    borrowed_min_permissions = []

    @contextmanager
    def _borrow(settings, **kwargs):
        borrowed_min_permissions.append(kwargs.get("min_permission"))
        if kwargs.get("min_permission") is None:
            yield SimpleNamespace(account_id="low", permission_level=10), _Client(fail_permission=True)
        else:
            yield SimpleNamespace(account_id="high", permission_level=50), _Client(fail_permission=False)

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.borrow_yamibo_client", _borrow)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.parse_thread_snapshot", lambda *args, **kwargs: _snapshot(tid, []))

    settings = SimpleNamespace(
        data_dir=tmp_path,
        export_dir=tmp_path / "exports",
        novel_txt_export_dir=tmp_path / "novel_exports",
        image_backfill_max_pages=1,
        image_backfill_fixed_after=None,
        proxy_pool=None,
    )

    handle_image_backfill(repo, repo.get(job.job_id), "worker", 60, settings)

    completed = repo.get(job.job_id)
    assert completed.status == "succeeded"
    assert borrowed_min_permissions == [None, 50]
    assert completed.artifacts["account_id"] == "high"
    assert completed.artifacts["account_permission_level"] == 50
    assert completed.artifacts["account_min_permission"] == 50
