import pytest

from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.domain.models import TitleSnapshot


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


class TestResolveForTitle:
    def test_creates_new_series(self, db):
        repo = SeriesRepository(db)
        title = _make_title(series_key="新漫画", core_title_guess="新漫画")
        series_id, needs_review = repo.resolve_for_title(title)
        assert series_id > 0
        series = repo.get_series(series_id)
        assert series is not None
        assert series["series_key"] == "新漫画"
        assert series["canonical_title"] == "新漫画"

    def test_reuses_existing_series_by_key(self, db):
        repo = SeriesRepository(db)
        title1 = _make_title(series_key="同一漫画", core_title_guess="同一漫画")
        id1, _ = repo.resolve_for_title(title1)
        title2 = _make_title(series_key="同一漫画", core_title_guess="同一漫画",
                             chapter_name="第2话", chapter_index=2.0)
        id2, _ = repo.resolve_for_title(title2)
        assert id1 == id2

    def test_creates_separate_series_for_different_authors(self, db):
        repo = SeriesRepository(db)
        title1 = _make_title(series_key="同名漫画", author_guess="作者A")
        id1, _ = repo.resolve_for_title(title1)
        title2 = _make_title(series_key="同名漫画", author_guess="作者B")
        id2, _ = repo.resolve_for_title(title2)
        assert id1 != id2

    def test_aliases_accumulate(self, db):
        repo = SeriesRepository(db)
        title1 = _make_title(series_key="漫画别名", title_aliases=["别名1"])
        repo.resolve_for_title(title1)
        title2 = _make_title(series_key="漫画别名", title_aliases=["别名2"])
        repo.resolve_for_title(title2)
        series = repo.get_series(1)
        import json
        aliases = json.loads(series["aliases_json"])
        assert "别名1" in aliases
        assert "别名2" in aliases


class TestListSeries:
    def test_list_returns_created(self, db):
        repo = SeriesRepository(db)
        repo.resolve_for_title(_make_title(series_key="系列A"))
        repo.resolve_for_title(_make_title(series_key="系列B"))
        rows = repo.list_series()
        keys = [r["series_key"] for r in rows]
        assert "系列A" in keys
        assert "系列B" in keys

    def test_list_respects_limit(self, db):
        repo = SeriesRepository(db)
        for i in range(5):
            repo.resolve_for_title(_make_title(series_key=f"limit_{i}"))
        rows = repo.list_series(limit=2)
        assert len(rows) == 2


class TestConfirmSeriesReview:
    def test_confirm_clears_needs_review(self, db):
        repo = SeriesRepository(db)
        title = _make_title(series_key="待确认系列", needs_review=True)
        series_id, _ = repo.resolve_for_title(title)
        before, after = repo.confirm_series_review(series_id)
        assert after["needs_review"] == 0

    def test_confirm_nonexistent_raises(self, db):
        repo = SeriesRepository(db)
        with pytest.raises(ValueError, match="series not found"):
            repo.confirm_series_review(99999)


class TestMergeSeries:
    def test_merge_moves_threads(self, db):
        from yamibo_mcp.db.repositories.threads import ThreadsRepository
        from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot as TS

        series_repo = SeriesRepository(db)
        threads_repo = ThreadsRepository(db)

        # Create two series
        t1 = _make_title(series_key="源系列", core_title_guess="源系列")
        t2 = _make_title(series_key="目标系列", core_title_guess="目标系列")
        src_id, _ = series_repo.resolve_for_title(t1)
        tgt_id, _ = series_repo.resolve_for_title(t2)

        # Create a thread in source series
        snap = ThreadSnapshot(
            tid=9999, url=None, page_type="thread_detail",
            raw_title="[A组] 源系列 第1话", display_title="源系列 第1话",
            title=t1, publisher="u", publisher_uid="1",
            pub_time=None, permission=0,
            floors=[FloorSnapshot(pid=99990, tid=9999, floor_no=1,
                                  publisher="u", content="c", pub_time=None,
                                  has_images=False)],
        )
        threads_repo.upsert_snapshot(snap)

        # Merge
        before, after = series_repo.merge_series(src_id, tgt_id)
        assert series_repo.get_series(src_id) is None
        row = threads_repo.get_thread(9999)
        assert row["series_id"] == tgt_id

    def test_merge_same_id_raises(self, db):
        repo = SeriesRepository(db)
        title = _make_title(series_key="自合并")
        sid, _ = repo.resolve_for_title(title)
        with pytest.raises(ValueError, match="must differ"):
            repo.merge_series(sid, sid)


class TestDeleteSeries:
    def test_delete_empty_series(self, db):
        repo = SeriesRepository(db)
        title = _make_title(series_key="空系列")
        sid, _ = repo.resolve_for_title(title)
        before, after = repo.delete_series(sid)
        assert repo.get_series(sid) is None
        assert after == {"series_id": sid, "deleted": True}

    def test_delete_nonexistent_raises(self, db):
        repo = SeriesRepository(db)
        with pytest.raises(ValueError, match="series not found"):
            repo.delete_series(99999)

    def test_delete_nonempty_raises(self, db):
        from yamibo_mcp.db.repositories.threads import ThreadsRepository
        from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot

        series_repo = SeriesRepository(db)
        threads_repo = ThreadsRepository(db)
        title = _make_title(series_key="非空系列")
        sid, _ = series_repo.resolve_for_title(title)
        snap = ThreadSnapshot(
            tid=8888, url=None, page_type="thread_detail",
            raw_title="[A组] 非空系列 第1话", display_title="非空系列 第1话",
            title=title, publisher="u", publisher_uid="1",
            pub_time=None, permission=0,
            floors=[FloorSnapshot(pid=88880, tid=8888, floor_no=1,
                                  publisher="u", content="c", pub_time=None,
                                  has_images=False)],
        )
        threads_repo.upsert_snapshot(snap)
        with pytest.raises(ValueError, match="still has"):
            series_repo.delete_series(sid)
