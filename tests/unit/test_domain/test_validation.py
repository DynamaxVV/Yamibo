import pytest

from yamibo_mcp.domain.validation import (
    empty_primary_floor_exclusion_reason,
    has_later_external_reply,
    validate_thread_snapshot,
)
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot

_SENTINEL = object()


def _make_title(**overrides) -> TitleSnapshot:
    defaults = dict(
        raw_title="[A组] 测试漫画 第1话",
        display_title="测试漫画 第1话",
        group_name="A组",
        author_guess="作者A",
        core_title_guess="测试漫画",
        normalized_core_title="测试漫画",
        series_key="测试漫画",
        title_aliases=[],
        chapter_name="第1话",
        chapter_index=1.0,
        chapter_index_end=None,
        chapter_title=None,
        subtitle=None,
        tags=[],
        confidence=0.9,
        needs_review=False,
        parser_version="title-v1",
    )
    defaults.update(overrides)
    return TitleSnapshot(**defaults)


def _make_floor(pid=1000, tid=999, floor_no=1, **overrides) -> FloorSnapshot:
    defaults = dict(
        pid=pid, tid=tid, floor_no=floor_no,
        publisher="user1", content="测试内容",
        pub_time="2025-01-01", has_images=False, image_urls=[],
    )
    defaults.update(overrides)
    return FloorSnapshot(**defaults)


def _make_snapshot(tid=999, title=None, floors=_SENTINEL, **overrides) -> ThreadSnapshot:
    title = title or _make_title()
    if floors is _SENTINEL:
        floors = [_make_floor(tid=tid)]
    defaults = dict(
        tid=tid, url=None, page_type="thread_detail",
        raw_title=title.raw_title, display_title=title.display_title,
        title=title, publisher="user1", publisher_uid="1",
        pub_time="2025-01-01", permission=0, floors=floors, image_count=0,
    )
    defaults.update(overrides)
    return ThreadSnapshot(**defaults)


class TestValidSnapshot:
    def test_valid_snapshot_returns_no_errors(self):
        snapshot = _make_snapshot()
        result = validate_thread_snapshot(snapshot)
        assert result.valid is True
        assert result.errors == []

    def test_valid_snapshot_with_images(self):
        floor = _make_floor(has_images=True, image_urls=["http://img/a.jpg"])
        snapshot = _make_snapshot(floors=[floor], image_count=1)
        result = validate_thread_snapshot(snapshot)
        assert result.valid is True


class TestInvalidTid:
    @pytest.mark.parametrize("tid", [0, -1, -100], ids=str)
    def test_non_positive_tid_is_error(self, tid):
        snapshot = _make_snapshot(tid=tid)
        result = validate_thread_snapshot(snapshot)
        assert any("tid must be positive" in e for e in result.errors)


class TestInvalidPageType:
    def test_wrong_page_type_is_error(self):
        snapshot = _make_snapshot(page_type="forum_list")
        result = validate_thread_snapshot(snapshot)
        assert any("page_type must be thread_detail" in e for e in result.errors)


class TestMissingTitles:
    def test_empty_raw_title_is_error(self):
        snapshot = _make_snapshot(raw_title="")
        result = validate_thread_snapshot(snapshot)
        assert any("raw_title is required" in e for e in result.errors)

    def test_empty_display_title_is_error(self):
        snapshot = _make_snapshot(display_title="")
        result = validate_thread_snapshot(snapshot)
        assert any("display_title is required" in e for e in result.errors)

    def test_empty_core_title_is_error(self):
        title = _make_title(core_title_guess="")
        snapshot = _make_snapshot(title=title)
        result = validate_thread_snapshot(snapshot)
        assert any("core_title_guess is required" in e for e in result.errors)

    def test_empty_series_key_is_warning(self):
        title = _make_title(series_key="")
        snapshot = _make_snapshot(title=title)
        result = validate_thread_snapshot(snapshot)
        assert result.valid
        assert any("series_key is missing" in w for w in result.warnings)


class TestFloorValidation:
    def test_no_floors_is_error(self):
        snapshot = _make_snapshot(floors=[])
        result = validate_thread_snapshot(snapshot)
        assert any("at least one floor is required" in e for e in result.errors)

    def test_floor_tid_mismatch_is_error(self):
        floor = _make_floor(pid=1000, tid=9999, floor_no=1)
        snapshot = _make_snapshot(tid=999, floors=[floor])
        result = validate_thread_snapshot(snapshot)
        assert any("tid mismatch" in e for e in result.errors)

    def test_negative_pid_is_error(self):
        floor = _make_floor(pid=-1, tid=999, floor_no=1)
        snapshot = _make_snapshot(floors=[floor])
        result = validate_thread_snapshot(snapshot)
        assert any("pid must be positive" in e for e in result.errors)

    def test_duplicate_pid_is_error(self):
        f1 = _make_floor(pid=1000, tid=999, floor_no=1)
        f2 = _make_floor(pid=1000, tid=999, floor_no=2, content="第二层")
        snapshot = _make_snapshot(floors=[f1, f2])
        result = validate_thread_snapshot(snapshot)
        assert any("duplicate pid" in e for e in result.errors)

    def test_zero_floor_no_is_error(self):
        floor = _make_floor(floor_no=0)
        snapshot = _make_snapshot(floors=[floor])
        result = validate_thread_snapshot(snapshot)
        assert any("floor_no must be positive" in e for e in result.errors)

    def test_duplicate_floor_no_is_error(self):
        floors = [
            _make_floor(pid=1000, floor_no=1),
            _make_floor(pid=1001, floor_no=1),
        ]
        result = validate_thread_snapshot(_make_snapshot(floors=floors))
        assert any("floor_no values must be unique" in error for error in result.errors)

    def test_floor_no_gap_is_error(self):
        floors = [
            _make_floor(pid=1000, floor_no=1),
            _make_floor(pid=1001, floor_no=3),
        ]
        result = validate_thread_snapshot(_make_snapshot(floors=floors))
        assert any("floor_no sequence must be contiguous" in error for error in result.errors)

    def test_floor_no_out_of_order_is_error(self):
        floors = [
            _make_floor(pid=1000, floor_no=2),
            _make_floor(pid=1001, floor_no=1),
        ]
        result = validate_thread_snapshot(_make_snapshot(floors=floors))
        assert any("floor_no sequence must be contiguous" in error for error in result.errors)

    def test_primary_floor_empty_no_images_is_error(self):
        floor = _make_floor(content="", has_images=False)
        snapshot = _make_snapshot(floors=[floor])
        result = validate_thread_snapshot(snapshot)
        assert any("content is required" in e for e in result.errors)

    def test_empty_primary_with_other_user_reply_is_retained(self):
        primary = _make_floor(content="", publisher="user1", publisher_uid="1")
        reply = _make_floor(
            pid=1001,
            floor_no=2,
            publisher="other",
            publisher_uid="2",
            content="后来回复",
        )
        snapshot = _make_snapshot(floors=[primary, reply])

        result = validate_thread_snapshot(snapshot)

        assert has_later_external_reply(snapshot) is True
        assert empty_primary_floor_exclusion_reason(snapshot) is None
        assert result.valid is True
        assert any("later external replies exist" in warning for warning in result.warnings)

    def test_empty_primary_with_only_same_author_replies_is_excluded(self):
        primary = _make_floor(content="", publisher="user1", publisher_uid="1")
        reply = _make_floor(
            pid=1001,
            floor_no=2,
            publisher="user1",
            publisher_uid=None,
            content="楼主补充",
        )
        snapshot = _make_snapshot(floors=[primary, reply])

        assert has_later_external_reply(snapshot) is False
        assert empty_primary_floor_exclusion_reason(snapshot) == "empty_primary_without_external_reply"

    def test_single_empty_primary_floor_is_excluded(self):
        snapshot = _make_snapshot(floors=[_make_floor(content="", has_images=False)])

        assert empty_primary_floor_exclusion_reason(snapshot) == "empty_primary_single_floor"

    def test_reply_floor_empty_is_warning_not_error(self):
        f1 = _make_floor(pid=1000, floor_no=1, publisher="user1")
        f2 = _make_floor(pid=1001, floor_no=2, publisher="other", content="", has_images=False)
        snapshot = _make_snapshot(floors=[f1, f2])
        result = validate_thread_snapshot(snapshot)
        assert result.valid is True
        assert any("empty and skipped" in w for w in result.warnings)

    def test_has_images_without_urls_is_warning(self):
        floor = _make_floor(has_images=True, image_urls=[])
        snapshot = _make_snapshot(floors=[floor])
        result = validate_thread_snapshot(snapshot)
        assert any("image flag but no image urls" in w for w in result.warnings)


class TestWarnings:
    def test_needs_review_is_warning(self):
        title = _make_title(needs_review=True)
        snapshot = _make_snapshot(title=title)
        result = validate_thread_snapshot(snapshot)
        assert any("title needs review" in w for w in result.warnings)

    def test_negative_image_count_is_error(self):
        snapshot = _make_snapshot(image_count=-1)
        result = validate_thread_snapshot(snapshot)
        assert any("image_count must not be negative" in e for e in result.errors)
