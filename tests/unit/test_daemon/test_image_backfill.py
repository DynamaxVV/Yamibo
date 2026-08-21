from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from yamibo_mcp.daemon.handlers.image_backfill import (
    _diff_snapshot_images,
    _selected_download_retries,
    _snapshot_for_missing_images,
    handle_image_backfill,
)
from yamibo_mcp.daemon.image_backfill_scheduler import maybe_enqueue_image_backfill_dry_run
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.system_state import SystemStateRepository
from yamibo_mcp.errors import ThreadPermissionRequiredError
from yamibo_mcp.storage.images import ImageDownloadResult, download_images_to_staging
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


def test_selected_first_floor_download_keeps_original_image_index(tmp_path):
    from dataclasses import replace

    source = tmp_path / "source.png"
    png = bytearray(b"\x89PNG\r\n\x1a\n" + b"\x00" * 108 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    png[16:20] = (640).to_bytes(4, "big")
    png[20:24] = (480).to_bytes(4, "big")
    source.write_bytes(png)
    target_url = source.as_uri()
    urls = [f"https://example.invalid/{index}.jpg" for index in range(1, 23)] + [target_url]
    base = _snapshot(125, [])
    first = replace(base.floors[0], publisher="a", has_images=True, image_urls=urls)
    snapshot = replace(base, floors=[first], image_count=len(urls))
    missing = _snapshot_for_missing_images(snapshot, [{"pid": first.pid, "floor_no": 1, "url": target_url}])

    result = download_images_to_staging(
        StoragePaths(tmp_path),
        "image_backfill_selected",
        missing,
        target_urls={target_url},
        timeout=1,
    )

    assert result.missing_urls == []
    assert result.relative_path_by_url[target_url] == "images/floor_001_23.png"
    assert (tmp_path / "staging/jobs/image_backfill_selected/images/floor_001_23.png").exists()


def test_selected_download_attempts_non_publisher_image_and_external_is_one_shot(tmp_path):
    from dataclasses import replace

    source = tmp_path / "reply.png"
    png = bytearray(b"\x89PNG\r\n\x1a\n" + b"\x00" * 108 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    png[16:20] = (640).to_bytes(4, "big")
    png[20:24] = (480).to_bytes(4, "big")
    source.write_bytes(png)
    target_url = source.as_uri()
    base = _snapshot(126, [])
    reply = replace(
        base.floors[0],
        pid=1262,
        floor_no=2,
        publisher="reply-author",
        has_images=True,
        image_urls=[target_url],
    )
    snapshot = replace(base, floors=[reply], image_count=1)

    result = download_images_to_staging(
        StoragePaths(tmp_path),
        "image_backfill_selected_reply",
        snapshot,
        target_urls={target_url},
        timeout=1,
    )

    assert result.relative_path_by_url[target_url] == "images/floor_002_01.png"
    assert _selected_download_retries(
        target_urls={"https://third-party.invalid/dead.jpg"},
        configured_retries=3,
    ) == 0
    assert _selected_download_retries(
        target_urls={"https://bbs.yamibo.com/forum.php?mod=attachment&aid=1"},
        configured_retries=3,
    ) == 3


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


def _scheduler_settings(*, dry_run: bool = True, interval: float = 0.0, daily_limit: int = 10):
    return SimpleNamespace(
        image_backfill_enabled=True,
        image_backfill_dry_run=dry_run,
        image_backfill_forum_id=5,
        image_backfill_auto_interval_seconds=interval,
        image_backfill_daily_limit=daily_limit,
        image_backfill_max_pages=1,
        image_backfill_fixed_after=None,
    )


def _insert_gap_thread(db, tid: int) -> None:
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
        VALUES (?, 'raw', 'display', '2026-07-01T00:00:00+00:00', 1, 'complete', 5)
        """,
        (tid,),
    )
    db.commit()


def test_auto_scheduler_persists_cursor_and_wraps_without_second_scan(db):
    _insert_gap_thread(db, 3001)
    repo = JobsRepository(db)
    settings = _scheduler_settings()

    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is True
    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert state is not None
    assert state["scan_cursor_tid"] == 3001
    assert state["last_checked_at"]

    # The next bounded cycle reaches the end and only wraps; it does not scan
    # the beginning again in the same invocation.
    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is False
    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert state["scan_cursor_tid"] == 0
    assert state["scan_wrap_count"] == 1


def test_auto_scheduler_batch_is_bounded_and_cursor_uses_batch_end(db, monkeypatch):
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    for tid in (3101, 3102, 3103):
        _insert_gap_thread(db, tid)
    monkeypatch.setattr(scheduler, "_SCAN_BATCH_SIZE", 2)

    repo = JobsRepository(db)
    assert maybe_enqueue_image_backfill_dry_run(repo, _scheduler_settings()) is True
    job = db.execute(
        "SELECT tid FROM jobs WHERE job_type = 'image_backfill' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert job["tid"] == 3101
    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert state["scan_cursor_tid"] == 3102


def test_auto_scheduler_interval_throttles_no_candidate_scan(db, monkeypatch):
    _insert_gap_thread(db, 3201)
    repo = JobsRepository(db)
    settings = _scheduler_settings(interval=60.0)
    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is True

    calls = []
    monkeypatch.setattr(
        "yamibo_mcp.daemon.image_backfill_scheduler._select_candidate_batch",
        lambda *args, **kwargs: calls.append(True),
    )
    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is False
    assert calls == []


def test_auto_scheduler_releases_single_flight_lock_after_failure(db, monkeypatch):
    repo = JobsRepository(db)
    settings = _scheduler_settings()
    calls = []

    def fail_once(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("candidate query failed")

    monkeypatch.setattr(
        "yamibo_mcp.daemon.image_backfill_scheduler._select_candidate_batch",
        fail_once,
    )
    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is False
    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is False
    assert len(calls) == 2


def test_auto_scheduler_local_single_flight_is_nonblocking(db):
    settings = _scheduler_settings()
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    assert scheduler._SCHEDULER_LOCK.acquire(blocking=False)
    try:
        assert maybe_enqueue_image_backfill_dry_run(JobsRepository(db), settings) is False
    finally:
        scheduler._SCHEDULER_LOCK.release()


def test_auto_scheduler_disabled_short_circuits_before_query_and_state_write(db, monkeypatch):
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    state_repo = SystemStateRepository(db)
    state_repo.set_json("image_backfill_auto_scheduler", {"sentinel": "unchanged"})
    monkeypatch.setattr(
        scheduler,
        "_select_candidate_batch",
        lambda *args, **kwargs: pytest.fail("disabled scheduler must not scan candidates"),
    )
    settings = _scheduler_settings()
    settings.image_backfill_enabled = False

    assert maybe_enqueue_image_backfill_dry_run(JobsRepository(db), settings) is False
    assert state_repo.get_json("image_backfill_auto_scheduler") == {"sentinel": "unchanged"}
    assert db.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 0


def test_auto_scheduler_no_candidate_advances_last_checked_and_cursor(db):
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
        VALUES (3400, 'raw', 'display', '2026-07-01T00:00:00+00:00', 0, 'complete', 5)
        """
    )
    db.commit()
    before = time.time()
    assert maybe_enqueue_image_backfill_dry_run(JobsRepository(db), _scheduler_settings()) is False
    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert state["scan_cursor_tid"] == 3400
    assert state["last_checked_at"] >= before
    assert state["last_reason"] == "scanned_no_candidate"
    assert db.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 0


def test_auto_scheduler_preserves_all_candidate_reason_semantics(db, monkeypatch):
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    # Keep each call to one TID so the returned candidate reason maps exactly
    # to the cursor position under test.
    monkeypatch.setattr(scheduler, "_SCAN_BATCH_SIZE", 1)

    def insert_thread(conn, tid: int, image_count: int) -> None:
        conn.execute(
            """
            INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
            VALUES (?, 'raw', 'display', '2026-07-01T00:00:00+00:00', ?, 'complete', 5)
            """,
            (tid, image_count),
        )

    # Non-first-floor image without an asset.
    insert_thread(db, 3410, 0)
    db.execute("INSERT INTO floors (pid, tid, floor_no, content, has_images) VALUES (34101, 3410, 2, 'reply', 1)")
    # Thread image count is greater than the number of image assets.
    insert_thread(db, 3420, 1)
    # Asset count matches image_count, but the existing image is pending.
    insert_thread(db, 3430, 1)
    db.execute(
        """
        INSERT INTO assets (asset_id, tid, pid, asset_type, remote_url, local_path, status)
        VALUES ('asset-3430', 3430, 34301, 'image', 'https://example.invalid/3430.jpg', NULL, 'pending')
        """
    )
    db.commit()

    repo = JobsRepository(db)
    reasons = []
    cursor = 0
    for expected_tid, expected_reason in (
        (3410, "non_first_floor_has_images_without_asset"),
        (3420, "image_count_asset_gap"),
        (3430, "missing_or_pending_asset"),
    ):
        scan = scheduler._select_candidate_batch(repo, _scheduler_settings(), dry_run=True, cursor_tid=cursor)
        assert scan["candidate"]["tid"] == expected_tid
        reasons.append(scan["candidate"]["reason"])
        assert scan["candidate"]["reason"] == expected_reason
        cursor = scan["cursor_after"]
    assert reasons == [
        "non_first_floor_has_images_without_asset",
        "image_count_asset_gap",
        "missing_or_pending_asset",
    ]


def test_auto_scheduler_batch_blocking_rules_match_single_tid_rules(db):
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    for tid in (3440, 3450, 3460):
        _insert_gap_thread(db, tid)
    repo = JobsRepository(db)
    dry_job = repo.create("image_backfill", tid=3440, payload={"tid": 3440, "dry_run": True})
    repo.succeed(dry_job.job_id)
    apply_job = repo.create("image_backfill", tid=3450, payload={"tid": 3450, "dry_run": False})
    repo.succeed(apply_job.job_id)

    tids = [3440, 3450, 3460]
    for dry_run, expected in ((True, {3440, 3450}), (False, {3450})):
        batch_blocked = scheduler._blocking_backfill_tids(repo, tids, dry_run=dry_run)
        single_blocked = {
            tid for tid in tids
            if scheduler._has_blocking_backfill_job(repo, tid=tid, dry_run=dry_run)
        }
        assert batch_blocked == expected
        assert batch_blocked == single_blocked


def test_auto_scheduler_two_threads_enter_candidate_scan_once(monkeypatch):
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    class FakeTransaction:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConnection:
        backend = "sqlite"

        def commit(self):
            return None

        def begin(self):
            return FakeTransaction()

        def execute(self, *args, **kwargs):
            return None

    class FakeStateRepository:
        def __init__(self, conn):
            self.conn = conn

        def get_json(self, key):
            return {}

    entered = threading.Event()
    release = threading.Event()
    scan_calls = []

    def fake_select(*args, **kwargs):
        scan_calls.append(threading.current_thread().name)
        entered.set()
        assert release.wait(timeout=2), "first scheduler scan did not release"
        return {
            "candidate": None,
            "batch_size": 0,
            "candidate_count": 0,
            "blocking_count": 0,
            "cursor_after": 0,
            "wrap_count": 0,
            "elapsed_ms": 0.0,
        }

    monkeypatch.setattr(scheduler, "SystemStateRepository", FakeStateRepository)
    monkeypatch.setattr(scheduler, "_select_candidate_batch", fake_select)
    settings = _scheduler_settings()
    results = []

    def invoke(name):
        repo = SimpleNamespace(conn=FakeConnection())
        results.append((name, maybe_enqueue_image_backfill_dry_run(repo, settings)))

    first = threading.Thread(target=invoke, args=("first",), name="scheduler-first")
    first.start()
    assert entered.wait(timeout=2), "first scheduler did not enter candidate scan"
    second = threading.Thread(target=invoke, args=("second",), name="scheduler-second")
    second.start()
    second.join(timeout=2)
    release.set()
    first.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(scan_calls) == 1
    assert sorted(results) == [("first", False), ("second", False)]


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
