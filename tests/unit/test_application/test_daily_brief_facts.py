from datetime import date, timedelta

import pytest

from yamibo_mcp.application.daily_brief_facts import (
    make_manual_issue_key,
    target_day_window,
)


def test_daily_brief_window_uses_shanghai_calendar_day_as_utc_half_open_range():
    start, end = target_day_window(date(2026, 9, 24))

    assert start.isoformat() == "2026-09-23T16:00:00+00:00"
    assert end.isoformat() == "2026-09-24T16:00:00+00:00"
    assert end - start == timedelta(days=1)


def test_daily_brief_window_respects_dst_calendar_day_length():
    start, end = target_day_window(date(2026, 3, 8), "America/Los_Angeles")

    assert start.isoformat() == "2026-03-08T08:00:00+00:00"
    assert end.isoformat() == "2026-03-09T07:00:00+00:00"
    assert end - start == timedelta(hours=23)


def test_manual_issue_key_canonicalizes_configuration_order():
    first = make_manual_issue_key(
        owner_id="owner", target_day=date(2026, 9, 24), timezone_name="Asia/Shanghai",
        config_snapshot={"forums": [3, 2], "top_n": 10},
    )
    second = make_manual_issue_key(
        owner_id="owner", target_day=date(2026, 9, 24), timezone_name="Asia/Shanghai",
        config_snapshot={"top_n": 10, "forums": [3, 2]},
    )
    different_owner = make_manual_issue_key(
        owner_id="other", target_day=date(2026, 9, 24), timezone_name="Asia/Shanghai",
        config_snapshot={"forums": [3, 2], "top_n": 10},
    )

    assert first == second
    assert first != different_owner


def test_daily_brief_window_rejects_unknown_timezone():
    with pytest.raises(ValueError, match="unknown timezone"):
        target_day_window(date(2026, 9, 24), "Mars/Olympus")


@pytest.mark.parametrize(("created", "expected"), [
    ("2026-09-23T15:59:59+00:00", "old_thread"),
    ("2026-09-23T16:00:00+00:00", "new_thread"),
    ("2026-09-24T15:59:59+00:00", "new_thread"),
    ("2026-09-24T16:00:00+00:00", "unknown"),
    (None, "unknown"), ("invalid", "unknown"),
    ("2026-09-24T01:00:00", "unknown"),
])
def test_activity_kind_uses_creation_in_target_window(created, expected):
    from yamibo_mcp.application.daily_brief_facts import _activity_kind
    start, end = target_day_window(date(2026, 9, 24))
    assert _activity_kind(created, start, end) == expected
