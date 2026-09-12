from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from yamibo_mcp.config import AccountConfig
from yamibo_mcp.daemon.daily_sign_in_scheduler import _has_daily_job, maybe_enqueue_daily_sign_ins, scheduled_sign_in_time
from yamibo_mcp.daemon.handlers.daily_sign_in import handle_daily_sign_in
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.enums import JobStatus, JobType
from yamibo_mcp.maintenance.sign_in_cache import read_sign_in_cache, update_sign_in_cache_account


def _settings(tmp_path: Path, *, account_pool=()):
    return SimpleNamespace(
        account_pool=account_pool,
        cookie_file=tmp_path / "default.cookie",
        data_dir=tmp_path,
        use_system_proxy=False,
        login_username=None,
        login_password=None,
        request_interval_seconds=0.0,
        request_interval_jitter_seconds=0.0,
    )


def _account(account_id: str, cookie_file: Path) -> AccountConfig:
    return AccountConfig(
        account_id=account_id,
        username=None,
        password=None,
        cookie_file=cookie_file,
        enabled=True,
        weight=1,
        permission_level=0,
        request_interval_seconds=0.0,
        request_interval_jitter_seconds=0.0,
        max_concurrent_leases=1,
        login_mode="refresh_on_login_required",
    )


def test_daily_scheduler_waits_until_one_and_enqueues_each_enabled_account(db, tmp_path):
    settings = _settings(
        tmp_path,
        account_pool=(
            _account("one", tmp_path / "one.cookie"),
            _account("two", tmp_path / "two.cookie"),
        ),
    )
    before_one = datetime.fromisoformat("2026-08-16T00:59:00+08:00")
    after_random_window = datetime.fromisoformat("2026-08-16T02:00:00+08:00")
    repo = JobsRepository(db)

    assert maybe_enqueue_daily_sign_ins(repo, settings, now=before_one) == 0
    assert maybe_enqueue_daily_sign_ins(repo, settings, now=after_random_window) == 2
    assert maybe_enqueue_daily_sign_ins(repo, settings, now=after_random_window) == 0

    rows = db.execute(
        "SELECT job_type, tid, payload_json, status FROM jobs WHERE job_type = ? ORDER BY job_id",
        (JobType.DAILY_SIGN_IN.value,),
    ).fetchall()
    assert len(rows) == 2
    assert {row["tid"] for row in rows} == {20260816}
    assert {row["status"] for row in rows} == {JobStatus.QUEUED.value}
    assert {row["payload_json"] for row in rows} == {
        '{"account_id": "one", "local_day": "2026-08-16", "scheduled_at": "' + scheduled_sign_in_time(local_day="2026-08-16", account_id="one").isoformat() + '"}',
        '{"account_id": "two", "local_day": "2026-08-16", "scheduled_at": "' + scheduled_sign_in_time(local_day="2026-08-16", account_id="two").isoformat() + '"}',
    }


def test_daily_scheduler_waits_for_each_account_randomized_minute(db, tmp_path):
    settings = _settings(tmp_path, account_pool=(_account("one", tmp_path / "one.cookie"),))
    scheduled_at = scheduled_sign_in_time(local_day="2026-08-16", account_id="one")
    repo = JobsRepository(db)

    before = scheduled_at - timedelta(seconds=1)
    assert maybe_enqueue_daily_sign_ins(repo, settings, now=before) == 0
    assert maybe_enqueue_daily_sign_ins(repo, settings, now=scheduled_at) == 1


def test_daily_scheduler_does_not_enqueue_a_startup_probe(db, tmp_path):
    settings = _settings(
        tmp_path,
        account_pool=(_account("one", tmp_path / "one.cookie"),),
    )
    repo = JobsRepository(db)

    assert maybe_enqueue_daily_sign_ins(
        repo,
        settings,
        now=datetime.fromisoformat("2026-08-16T00:30:00+08:00"),
    ) == 0
    assert repo.list(limit=None, status=JobStatus.QUEUED.value) == []


def test_daily_scheduler_skips_account_when_cached_last_sign_in_is_today(db, tmp_path):
    settings = _settings(tmp_path, account_pool=(_account("one", tmp_path / "one.cookie"),))
    update_sign_in_cache_account(
        settings,
        "one",
        {"today_status": "checked", "recent_checkin": "2026-08-16 04:49:20"},
        fetched_at="2026-08-15T00:00:00+08:00",
    )
    repo = JobsRepository(db)

    assert maybe_enqueue_daily_sign_ins(
        repo,
        settings,
        now=datetime.fromisoformat("2026-08-16T02:00:00+08:00"),
    ) == 0


def test_daily_scheduler_enqueues_when_cached_last_sign_in_is_not_today(db, tmp_path):
    settings = _settings(tmp_path, account_pool=(_account("one", tmp_path / "one.cookie"),))
    update_sign_in_cache_account(
        settings,
        "one",
        {"today_status": "checked", "recent_checkin": "2026-08-15 04:49:20"},
        fetched_at="2026-08-16T00:00:00+08:00",
    )
    repo = JobsRepository(db)

    assert maybe_enqueue_daily_sign_ins(
        repo,
        settings,
        now=datetime.fromisoformat("2026-08-16T02:00:00+08:00"),
    ) == 1


def test_daily_sign_in_handler_checks_status_without_clicking(monkeypatch, db, tmp_path):
    repo = JobsRepository(db)
    job = repo.create(
        JobType.DAILY_SIGN_IN.value,
        tid=20260816,
        payload={"account_id": "two", "local_day": "2026-08-16", "check_only": True},
    )
    calls = []
    borrow_kwargs = []

    class _Client:
        def fetch_daily_checkin_page(self):
            calls.append("check")
            return SimpleNamespace(status_code=200, html='<a href="plugin.php?id=zqlj_sign">今日已打卡</a>')

        def sign_daily_checkin(self, **kwargs):
            calls.append("sign")
            raise AssertionError("startup status check must not click sign-in")

    class _Context:
        def __enter__(self):
            return SimpleNamespace(account_id="two"), _Client()

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "yamibo_mcp.yamibo.sign_in.borrow_yamibo_client",
        lambda settings, **kwargs: borrow_kwargs.append(kwargs) or _Context(),
    )
    monkeypatch.setattr(
        "yamibo_mcp.yamibo.sign_in.select_random_proxy",
        lambda settings: None,
    )

    handle_daily_sign_in(repo, job, "worker", 60, _settings(tmp_path))

    assert calls == ["check"]
    assert borrow_kwargs == [{"account_id": "two", "proxy_url": None, "force_direct": True}]
    assert repo.get(job.job_id).artifacts["check_only"] is True


def test_daily_scheduler_accepts_database_json_objects():
    class _Conn:
        def execute(self, *_args):
            return SimpleNamespace(fetchall=lambda: [{"payload_json": {"account_id": "one", "local_day": "2026-08-16"}}])

    assert _has_daily_job(
        SimpleNamespace(conn=_Conn()),
        day_key=20260816,
        account_id="one",
        local_day="2026-08-16",
    ) is True


def test_daily_sign_in_handler_borrows_the_job_account(monkeypatch, db, tmp_path):
    repo = JobsRepository(db)
    job = repo.create(
        JobType.DAILY_SIGN_IN.value,
        tid=20260816,
        payload={"account_id": "two", "local_day": "2026-08-16"},
    )
    calls = []

    class _Client:
        SIGN_IN_PAGE_URL = "https://bbs.yamibo.com/plugin.php?id=zqlj_sign"
        SIGN_IN_ACTION_URL = "https://bbs.yamibo.com/plugin.php?id=zqlj_sign&sign=35981a55"

        def sign_daily_checkin(self, **kwargs):
            calls.append("sign")
            return SimpleNamespace(final_url="https://bbs.yamibo.com/plugin.php?id=zqlj_sign&sign=35981a55", status_code=200)

    class _Context:
        def __enter__(self):
            calls.append("borrow")
            return SimpleNamespace(account_id="two"), _Client()

        def __exit__(self, *args):
            calls.append("release")

    monkeypatch.setattr(
        "yamibo_mcp.yamibo.sign_in.borrow_yamibo_client",
        lambda settings, **kwargs: calls.append(kwargs) or _Context(),
    )
    monkeypatch.setattr(
        "yamibo_mcp.yamibo.sign_in.select_random_proxy",
        lambda settings: None,
    )
    handle_daily_sign_in(repo, job, "worker", 60, _settings(tmp_path))

    assert calls == [{"account_id": "two", "proxy_url": None, "force_direct": True}, "borrow", "sign", "release"]
    assert repo.get(job.job_id).status == JobStatus.SUCCEEDED.value
    assert repo.get(job.job_id).artifacts["local_day"] == "2026-08-16"
    assert read_sign_in_cache(_settings(tmp_path))["two"]["data"]["recent_checkin"] == "2026-08-16"
