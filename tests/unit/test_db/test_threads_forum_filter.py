from __future__ import annotations

import pytest

from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot


def _make_title(series_key="test", display_title="Test", **overrides):
    defaults = dict(
        raw_title="[G] Test ch01",
        display_title=display_title,
        group_name="G",
        author_guess="A",
        core_title_guess=display_title,
        normalized_core_title=display_title,
        series_key=series_key,
        title_aliases=[],
        chapter_name="ch01",
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


def _make_snapshot(tid=999, forum_id=30, title=None, **overrides):
    title = title or _make_title()
    defaults = dict(
        tid=tid, url=None, page_type="thread_detail",
        raw_title=title.raw_title, display_title=title.display_title,
        title=title, publisher="user1", publisher_uid="1",
        pub_time="2025-01-01", permission=0,
        floors=[FloorSnapshot(pid=tid * 10 + 1, tid=tid, floor_no=1, publisher="user1",
                              content="content", pub_time=None, has_images=False)],
        image_count=0,
    )
    defaults.update(overrides)
    snap = ThreadSnapshot(**defaults)
    return snap


class TestSearchThreadsForumId:
    def test_search_without_forum_id_returns_all(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        title30 = _make_title(display_title="Comic Title", series_key="comickey")
        title55 = _make_title(display_title="Novel Title", series_key="novelkey")
        snap30 = _make_snapshot(tid=70001, forum_id=30, title=title30)
        snap55 = _make_snapshot(tid=70002, forum_id=55, title=title55)
        repo.upsert_snapshot(snap30)
        repo.upsert_snapshot(snap55)
        db.execute("UPDATE threads SET forum_id = 30 WHERE tid = 70001")
        db.execute("UPDATE threads SET forum_id = 55 WHERE tid = 70002")
        db.commit()
        # Act
        results = repo.search_threads("Title")
        # Assert
        tids = {r["tid"] for r in results}
        assert 70001 in tids
        assert 70002 in tids

    def test_search_with_forum_id_filters(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        title30 = _make_title(display_title="Comic Only", series_key="comic_only")
        title55 = _make_title(display_title="Novel Only", series_key="novel_only")
        repo.upsert_snapshot(_make_snapshot(tid=80001, forum_id=30, title=title30))
        repo.upsert_snapshot(_make_snapshot(tid=80002, forum_id=55, title=title55))
        db.execute("UPDATE threads SET forum_id = 30 WHERE tid = 80001")
        db.execute("UPDATE threads SET forum_id = 55 WHERE tid = 80002")
        db.commit()
        # Act
        results = repo.search_threads("Only", forum_id=30)
        # Assert
        tids = {r["tid"] for r in results}
        assert 80001 in tids
        assert 80002 not in tids

    def test_search_with_forum_id_no_match(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        title = _make_title(display_title="XTitle", series_key="xtitlekey")
        repo.upsert_snapshot(_make_snapshot(tid=90001, forum_id=30, title=title))
        db.execute("UPDATE threads SET forum_id = 30 WHERE tid = 90001")
        db.commit()
        # Act
        results = repo.search_threads("XTitle", forum_id=55)
        # Assert
        assert len(results) == 0


class TestListThreadsForumId:
    def test_list_without_forum_id_returns_all(self, db):
        repo = ThreadsRepository(db)
        repo.upsert_snapshot(_make_snapshot(tid=60001, forum_id=30))
        repo.upsert_snapshot(_make_snapshot(tid=60002, forum_id=55))
        db.execute("UPDATE threads SET forum_id = 30 WHERE tid = 60001")
        db.execute("UPDATE threads SET forum_id = 55 WHERE tid = 60002")
        db.commit()
        rows = repo.list_threads()
        tids = {r["tid"] for r in rows}
        assert 60001 in tids
        assert 60002 in tids

    def test_list_with_forum_id_filters(self, db):
        repo = ThreadsRepository(db)
        repo.upsert_snapshot(_make_snapshot(tid=50001, forum_id=30))
        repo.upsert_snapshot(_make_snapshot(tid=50002, forum_id=55))
        db.execute("UPDATE threads SET forum_id = 30 WHERE tid = 50001")
        db.execute("UPDATE threads SET forum_id = 55 WHERE tid = 50002")
        db.commit()
        rows = repo.list_threads(forum_id=55)
        tids = {r["tid"] for r in rows}
        assert 50002 in tids
        assert 50001 not in tids

    def test_list_with_unknown_forum_id_empty(self, db):
        repo = ThreadsRepository(db)
        repo.upsert_snapshot(_make_snapshot(tid=40001, forum_id=30))
        db.execute("UPDATE threads SET forum_id = 30 WHERE tid = 40001")
        db.commit()
        rows = repo.list_threads(forum_id=999)
        assert len(rows) == 0


class TestUpsertSnapshotWithForumId:
    def test_thread_has_forum_id_after_upsert_and_update(self, db):
        repo = ThreadsRepository(db)
        snap = _make_snapshot(tid=30001, forum_id=55)
        repo.upsert_snapshot(snap)
        db.execute("UPDATE threads SET forum_id = 55 WHERE tid = 30001")
        db.commit()
        row = repo.get_thread(30001)
        assert row["forum_id"] == 55
