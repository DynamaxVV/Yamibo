from datetime import datetime, time, timezone

import pytest

from yamibo_mcp.db.repositories.daily_rules import (
    _validate_daily_times,
    latest_due_run,
    next_run_after,
)


def test_next_run_is_strictly_future_and_independent_of_server_timezone():
    now = datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc)  # 09:00 in Shanghai
    assert next_run_after(now, "Asia/Shanghai", "09:00") == datetime(
        2026, 9, 26, 1, 0, tzinfo=timezone.utc
    )
    assert next_run_after(now, "Asia/Shanghai", "09:01") == datetime(
        2026, 9, 25, 1, 1, tzinfo=timezone.utc
    )


def test_dst_gap_moves_to_first_valid_minute_and_fold_uses_first_occurrence():
    gap = next_run_after(datetime(2026, 3, 7, 12, tzinfo=timezone.utc), "America/New_York", "02:30")
    assert gap == datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc)  # 03:00 EDT

    fold = next_run_after(datetime(2026, 10, 31, 12, tzinfo=timezone.utc), "America/New_York", "01:30")
    assert fold == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)  # fold=0 / EDT


def test_latest_due_run_selects_latest_daily_wall_clock_occurrence():
    cursor = datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc)
    now = datetime(2026, 9, 25, 5, 0, tzinfo=timezone.utc)
    latest = latest_due_run(cursor, now, "Asia/Shanghai", time(9))
    assert latest == datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc)


def test_preparation_deadline_must_be_later_on_the_same_day():
    _validate_daily_times(time(8, 30), time(9))
    for deadline in (time(8), time(8, 30)):
        with pytest.raises(ValueError, match="after execution_time"):
            _validate_daily_times(time(8, 30), deadline)
