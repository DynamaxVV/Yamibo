import pytest

from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.rag.chunker import RagChunk
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot


def _make_rag_chunk(*, tid: int, pid: int | None, floor_no: int | None, chunk_id: str, text_hash: str) -> RagChunk:
    return RagChunk(
        chunk_id=chunk_id,
        tid=tid,
        pid=pid,
        floor_no=floor_no,
        chunk_type="floor",
        forum_id=55,
        content_kind="comic",
        series_id=1,
        series_key="测试漫画",
        chapter_index=1.0,
        publisher="user1",
        pub_time="2025-01-01T00:00:00",
        title="测试漫画 第1话",
        metadata_text="测试漫画",
        text="测试内容",
        text_hash=text_hash,
        source_uri=f"yamibo://threads/{tid}/posts#floor={floor_no}" if floor_no is not None else f"yamibo://threads/{tid}",
        source_tid=tid,
        source_pid=pid,
        source_floor_no=floor_no,
        cleaner_version="anime-cleaner-1.2",
        chunker_version="anime-chunker-1.2",
        materializer_version="anime-rag-materializer-1.2",
        source_hash=f"hash:{tid}",
        generated_at="2025-01-01T00:00:00Z",
        quality_flags=[],
    )


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
        pid=pid,
        tid=tid,
        floor_no=floor_no,
        publisher="user1",
        content="测试内容",
        pub_time="2025-01-01T00:00:00",
        has_images=False,
        image_urls=[],
    )
    defaults.update(overrides)
    return FloorSnapshot(**defaults)


def _make_snapshot(tid=999, title=None, floors=None, **overrides) -> ThreadSnapshot:
    title = title or _make_title()
    floors = floors or [_make_floor(tid=tid)]
    defaults = dict(
        tid=tid,
        url=f"https://bbs.yamibo.com/thread-{tid}-1-1.html",
        page_type="comic",
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher="user1",
        publisher_uid="12345",
        pub_time="2025-01-01T00:00:00",
        permission=0,
        floors=floors,
        image_count=0,
    )
    defaults.update(overrides)
    return ThreadSnapshot(**defaults)


class TestUpsertAndGetThread:
    def test_round_trip(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        snapshot = _make_snapshot(tid=1001)
        # Act
        repo.upsert_snapshot(snapshot)
        row = repo.get_thread(1001)
        # Assert
        assert row is not None
        assert row["tid"] == 1001
        assert row["display_title"] == "测试漫画 第1话"
        assert row["publisher"] == "user1"

    def test_get_nonexistent_returns_none(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        # Act
        row = repo.get_thread(99999)
        # Assert
        assert row is None

    def test_upsert_updates_existing(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        snapshot = _make_snapshot(tid=1002)
        repo.upsert_snapshot(snapshot)
        new_title = _make_title(display_title="更新后的标题")
        updated = _make_snapshot(tid=1002, title=new_title)
        # Act
        repo.upsert_snapshot(updated)
        row = repo.get_thread(1002)
        # Assert
        assert row["display_title"] == "更新后的标题"

    def test_upsert_persists_forum_id_and_content_fields(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        snapshot = _make_snapshot(tid=1003, image_count=0)
        # Act
        repo.upsert_snapshot(snapshot, forum_id=55)
        row = repo.get_thread(1003)
        # Assert
        assert row["forum_id"] == 55
        assert row["content_kind"] == "novel"
        assert row["primary_media_type"] == "text"

    @pytest.mark.parametrize(
        ("forum_id", "series_key", "canonical_title"),
        [
            (5, "forum_anime", "动漫区"),
            (33, "forum_sea", "海域区"),
        ],
    )
    def test_discussion_forums_use_fixed_series(self, db, forum_id, series_key, canonical_title):
        repo = ThreadsRepository(db)
        snapshot = _make_snapshot(tid=1004 + forum_id, title=_make_title(series_key=None))

        repo.upsert_snapshot(snapshot, forum_id=forum_id)

        thread = repo.get_thread(snapshot.tid)
        series = db.execute("SELECT * FROM series WHERE series_id = ?", (thread["series_id"],)).fetchone()
        assert series["series_key"] == series_key
        assert series["canonical_title"] == canonical_title
        assert thread["needs_series_review"] in (False, 0)

    def test_unknown_forum_uses_review_quarantine_series(self, db):
        repo = ThreadsRepository(db)
        snapshot = _make_snapshot(tid=1030, title=_make_title(series_key=None))

        repo.upsert_snapshot(snapshot, forum_id=13)

        thread = repo.get_thread(snapshot.tid)
        series = db.execute("SELECT * FROM series WHERE series_id = ?", (thread["series_id"],)).fetchone()
        assert series["series_key"] == "quarantine_forum_13"
        assert series["canonical_title"] == "待确认：贴图区"
        assert series["needs_review"] in (True, 1)
        assert thread["needs_series_review"] in (True, 1)

    def test_update_archive_metadata_creates_missing_title_parse_row(self, db):
        repo = ThreadsRepository(db)
        snapshot = _make_snapshot(tid=1100)
        repo.upsert_snapshot(snapshot, forum_id=55)
        db.execute("DELETE FROM title_parse WHERE tid = ?", (1100,))

        repo.update_archive_metadata(
            1100,
            display_title="人工标题",
            chapter_name="特别篇",
            chapter_index=2.0,
            author_guess="作者B",
            group_name="C组",
        )

        thread_row = repo.get_thread(1100)
        title_row = repo.get_title_parse(1100)
        assert thread_row["display_title"] == "人工标题"
        assert title_row is not None
        assert title_row["display_title"] == "人工标题"
        assert title_row["chapter_name"] == "特别篇"
        assert title_row["series_key"] == "测试漫画"


class TestTitleParseAndFloors:
    def test_upsert_creates_title_parse(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        snapshot = _make_snapshot(tid=2001)
        # Act
        repo.upsert_snapshot(snapshot)
        tp = repo.get_title_parse(2001)
        # Assert
        assert tp is not None
        assert tp["core_title_guess"] == "测试漫画"
        assert tp["group_name"] == "A组"

    def test_upsert_creates_floors(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        f1 = _make_floor(pid=3001, tid=3000, floor_no=1)
        f2 = _make_floor(pid=3002, tid=3000, floor_no=2, content="第二层")
        snapshot = _make_snapshot(tid=3000, floors=[f1, f2])
        # Act
        repo.upsert_snapshot(snapshot)
        floors = repo.list_floors(3000)
        # Assert
        assert len(floors) == 2
        assert floors[0]["floor_no"] == 1
        assert floors[1]["floor_no"] == 2
        assert floors[1]["content"] == "第二层"

    def test_list_floors_uses_pid_as_stable_tiebreaker(self, db):
        repo = ThreadsRepository(db)
        snapshot = _make_snapshot(
            tid=3004,
            floors=[
                _make_floor(pid=3202, tid=3004, floor_no=1),
                _make_floor(pid=3201, tid=3004, floor_no=2),
            ],
        )
        repo.upsert_snapshot(snapshot)
        # Simulate legacy corrupt data without weakening the write contract.
        db.execute("UPDATE floors SET floor_no = 1 WHERE tid = ? AND pid = ?", (3004, 3201))

        floors = repo.list_floors(3004)

        assert [floor["pid"] for floor in floors] == [3201, 3202]

    def test_upsert_rejects_noncanonical_floor_sequence(self, db):
        repo = ThreadsRepository(db)
        snapshot = _make_snapshot(
            tid=3005,
            floors=[
                _make_floor(pid=3301, tid=3005, floor_no=1),
                _make_floor(pid=3302, tid=3005, floor_no=1),
            ],
        )

        with pytest.raises(ValueError, match="invalid floor sequence"):
            repo.upsert_snapshot(snapshot)

    def test_upsert_removes_stale_floors_for_same_thread(self, db):
        repo = ThreadsRepository(db)
        repo.upsert_snapshot(
            _make_snapshot(
                tid=3003,
                floors=[
                    _make_floor(pid=3101, tid=3003, floor_no=1),
                    _make_floor(pid=3102, tid=3003, floor_no=2),
                ],
            )
        )

        repo.upsert_snapshot(
            _make_snapshot(
                tid=3003,
                floors=[_make_floor(pid=3101, tid=3003, floor_no=1, content="仅楼主")],
            )
        )

        floors = repo.list_floors(3003)
        assert len(floors) == 1
        assert floors[0]["pid"] == 3101
        assert floors[0]["content"] == "仅楼主"


class TestDeleteThread:
    def test_delete_removes_all_related_data(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        snapshot = _make_snapshot(tid=4001, floors=[_make_floor(pid=4002, tid=4001)])
        repo.upsert_snapshot(snapshot)
        RagChunksRepository(db).replace_thread_chunks(
            tid=4001,
            chunks=[
                _make_rag_chunk(
                    tid=4001,
                    pid=4002,
                    floor_no=1,
                    chunk_id="thread:4001:floor:1:part:1",
                    text_hash="hash-1",
                ),
            ],
            embedding_model="text-embedding-3-small",
            embedding_dimensions=512,
        )
        db.execute(
            "INSERT INTO assets (asset_id, tid, pid, asset_type, remote_url, local_path, exportable, required, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("asset-4001", 4001, 4002, "image", "https://example.com/a.jpg", None, 0, 0, "ready"),
        )
        db.execute(
            "INSERT INTO content_blocks (tid, pid, order_index, block_type, text, asset_id, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (4001, 4002, 1, "text", "测试内容", None, "{}"),
        )
        db.commit()
        assert repo.get_thread(4001) is not None
        # Act
        before, after = repo.delete_thread(4001)
        # Assert
        assert repo.get_thread(4001) is None
        assert repo.get_title_parse(4001) is None
        assert repo.list_floors(4001) == []
        assert db.execute("SELECT COUNT(*) FROM rag_chunks WHERE tid = 4001").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM assets WHERE tid = 4001").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM content_blocks WHERE tid = 4001").fetchone()[0] == 0
        assert after == {"tid": 4001, "deleted": True}
        assert before["thread"]["tid"] == 4001

    def test_delete_nonexistent_raises(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        # Act & Assert
        with pytest.raises(ValueError, match="thread not found"):
            repo.delete_thread(99999)

    def test_postgres_backend_skips_sqlite_fts_tables(self, db):
        repo = ThreadsRepository(db)
        db.execute("DROP TABLE thread_fts")
        db.execute("DROP TABLE rag_chunks_fts")
        db.commit()
        db.backend = "postgres"

        snapshot = _make_snapshot(tid=4010, floors=[_make_floor(pid=4011, tid=4010)])
        repo.upsert_snapshot(snapshot, forum_id=55)
        RagChunksRepository(db).replace_thread_chunks(
            tid=4010,
            chunks=[
                _make_rag_chunk(
                    tid=4010,
                    pid=4011,
                    floor_no=1,
                    chunk_id="thread:4010:floor:1:part:1",
                    text_hash="hash-4010",
                ),
            ],
            embedding_model="text-embedding-3-small",
            embedding_dimensions=512,
        )

        assert repo.get_thread(4010) is not None
        before, after = repo.delete_thread(4010)
        assert before["thread"]["tid"] == 4010
        assert after == {"tid": 4010, "deleted": True}


class TestMarkExported:
    def test_mark_exported_sets_fields(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        snapshot = _make_snapshot(tid=5001)
        repo.upsert_snapshot(snapshot)
        # Act
        repo.mark_exported(5001, "/exports/test.zip")
        row = repo.get_thread(5001)
        # Assert
        assert row["is_exported"] == 1
        assert row["export_path"] == "/exports/test.zip"

    def test_mark_exported_nonexistent_raises(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        # Act & Assert
        with pytest.raises(ValueError, match="thread not found"):
            repo.mark_exported(99999, "/exports/x.zip")


class TestListThreads:
    def test_list_threads_returns_inserted(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        repo.upsert_snapshot(_make_snapshot(tid=6001))
        repo.upsert_snapshot(_make_snapshot(tid=6002))
        # Act
        rows = repo.list_threads()
        # Assert
        tids = [r["tid"] for r in rows]
        assert 6001 in tids
        assert 6002 in tids

    def test_list_threads_respects_limit(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        for i in range(5):
            repo.upsert_snapshot(_make_snapshot(tid=7000 + i))
        # Act
        rows = repo.list_threads(limit=2)
        # Assert
        assert len(rows) == 2


class TestListThreadsPage:
    def test_list_threads_page_paginates_and_sorts(self, db):
        repo = ThreadsRepository(db)
        rows = [
            (6001, "2025-01-03 10:00:00", "complete"),
            (6002, "2025-01-02 10:00:00", "complete"),
            (6003, "2025-01-01 10:00:00", "partial"),
            (6004, "2025-01-04 10:00:00", "complete"),
        ]
        for tid, sync_time, archive_status in rows:
            repo.upsert_snapshot(_make_snapshot(tid=tid))
            db.execute(
                "UPDATE threads SET forum_id = ?, sync_time = ?, pub_time = ?, archive_status = ? WHERE tid = ?",
                (55, sync_time, sync_time, archive_status, tid),
            )
        db.commit()

        page1 = repo.list_threads_page(page=1, page_size=2, forum_id=55, sort_key="sync_time", sort_dir="desc")
        page2 = repo.list_threads_page(page=2, page_size=2, forum_id=55, sort_key="sync_time", sort_dir="desc")
        complete_only = repo.list_threads_page(
            page=1, page_size=10, forum_id=55, archive_status="complete",
            sort_key="sync_time", sort_dir="desc",
        )

        assert page1["total_count"] == 4
        assert page1["total_pages"] == 2
        assert [row["tid"] for row in page1["items"]] == [6004, 6001]
        assert [row["tid"] for row in page2["items"]] == [6002, 6003]
        assert complete_only["total_count"] == 3
        assert [row["tid"] for row in complete_only["items"]] == [6004, 6001, 6002]

    def test_list_threads_page_sorts_by_remote_last_reply_and_exposes_remote_fields(self, db):
        repo = ThreadsRepository(db)
        for tid in (6101, 6102):
            repo.upsert_snapshot(_make_snapshot(tid=tid))
        db.execute(
            """
            UPDATE threads
            SET forum_id = ?, sync_time = ?, pub_time = ?, local_reply_count = ?,
                remote_last_reply_at = ?, remote_reply_count = ?, remote_last_replier = ?
            WHERE tid = ?
            """,
            (55, "2025-01-03 10:00:00", "2025-01-03 10:00:00", 3, "2026-07-05T10:00:00+00:00", 4, "user-a", 6101),
        )
        db.execute(
            """
            UPDATE threads
            SET forum_id = ?, sync_time = ?, pub_time = ?, local_reply_count = ?,
                remote_last_reply_at = ?, remote_reply_count = ?, remote_last_replier = ?
            WHERE tid = ?
            """,
            (55, "2025-01-04 10:00:00", "2025-01-04 10:00:00", 5, "2026-07-06T10:00:00+00:00", 6, "user-b", 6102),
        )
        db.commit()

        page = repo.list_threads_page(page=1, page_size=10, forum_id=55, sort_key="remote_last_reply_at", sort_dir="desc")

        assert [row["tid"] for row in page["items"]] == [6102, 6101]
        assert page["items"][0]["remote_reply_count"] == 6
        assert page["items"][0]["local_reply_count"] == 5
        assert page["items"][0]["floor_count"] == 1

    def test_list_threads_page_defaults_to_latest_reply_time_with_floor_fallback(self, db):
        repo = ThreadsRepository(db)
        repo.upsert_snapshot(_make_snapshot(
            tid=6111,
            floors=[_make_floor(pid=61111, tid=6111, pub_time="2026-07-08T10:00:00+00:00")],
        ))
        for tid in (6112, 6113):
            repo.upsert_snapshot(_make_snapshot(tid=tid))
        db.execute(
            "UPDATE threads SET forum_id = ?, remote_last_reply_at = ? WHERE tid = ?",
            (55, None, 6111),
        )
        db.execute(
            "UPDATE threads SET forum_id = ?, remote_last_reply_at = ? WHERE tid = ?",
            (55, "2026-07-09T10:00:00+00:00", 6112),
        )
        db.execute(
            "UPDATE threads SET forum_id = ?, remote_last_reply_at = ? WHERE tid = ?",
            (55, "2026-07-07T10:00:00+00:00", 6113),
        )
        db.commit()

        page = repo.list_threads_page(page=1, page_size=10, forum_id=55)

        assert [row["tid"] for row in page["items"]] == [6112, 6111, 6113]

    def test_postgres_list_sort_uses_indexable_timestamp_columns(self, db):
        repo = ThreadsRepository(db)
        repo.conn.backend = "postgres"

        assert repo._thread_list_order_clause("sync_time", "desc") == "t.sync_time DESC NULLS LAST, t.tid DESC"
        assert repo._thread_list_order_clause("remote_last_reply_at", "desc") == "t.remote_last_reply_at DESC NULLS LAST, t.tid DESC"

    def test_list_threads_page_sorts_by_reply_count_with_fallback_counts(self, db):
        repo = ThreadsRepository(db)
        repo.upsert_snapshot(
            _make_snapshot(
                tid=6201,
                floors=[
                    _make_floor(pid=62011, tid=6201, floor_no=1),
                    _make_floor(pid=62012, tid=6201, floor_no=2),
                    _make_floor(pid=62013, tid=6201, floor_no=3),
                ],
            )
        )
        repo.upsert_snapshot(_make_snapshot(tid=6202))
        repo.upsert_snapshot(_make_snapshot(tid=6203))
        db.execute(
            """
            UPDATE threads
            SET forum_id = ?, sync_time = ?, pub_time = ?, local_reply_count = ?, remote_reply_count = ?
            WHERE tid = ?
            """,
            (55, "2025-01-03 10:00:00", "2025-01-03 10:00:00", None, None, 6201),
        )
        db.execute(
            """
            UPDATE threads
            SET forum_id = ?, sync_time = ?, pub_time = ?, local_reply_count = ?, remote_reply_count = ?
            WHERE tid = ?
            """,
            (55, "2025-01-04 10:00:00", "2025-01-04 10:00:00", 5, None, 6202),
        )
        db.execute(
            """
            UPDATE threads
            SET forum_id = ?, sync_time = ?, pub_time = ?, local_reply_count = ?, remote_reply_count = ?
            WHERE tid = ?
            """,
            (55, "2025-01-05 10:00:00", "2025-01-05 10:00:00", 1, 8, 6203),
        )
        db.commit()

        asc_page = repo.list_threads_page(page=1, page_size=10, forum_id=55, sort_key="reply_count", sort_dir="asc")
        desc_page = repo.list_threads_page(page=1, page_size=10, forum_id=55, sort_key="reply_count", sort_dir="desc")

        assert [row["tid"] for row in asc_page["items"]] == [6201, 6202, 6203]
        assert [row["tid"] for row in desc_page["items"]] == [6203, 6202, 6201]

    def test_list_threads_page_exposes_last_floor_fallback_for_last_reply_time(self, db):
        repo = ThreadsRepository(db)
        repo.upsert_snapshot(
            _make_snapshot(
                tid=6301,
                floors=[
                    _make_floor(pid=63011, tid=6301, floor_no=1, pub_time="2025-01-01 10:00:00", publisher="u1"),
                    _make_floor(pid=63012, tid=6301, floor_no=2, pub_time="2025-01-03 12:00:00", publisher="u2"),
                ],
            )
        )
        db.execute(
            """
            UPDATE threads
            SET forum_id = ?, remote_last_reply_at = NULL, remote_last_reply_at_raw = NULL, remote_last_replier = NULL
            WHERE tid = ?
            """,
            (55, 6301),
        )
        db.commit()

        page = repo.list_threads_page(page=1, page_size=10, forum_id=55, sort_key="sync_time", sort_dir="desc")

        assert page["items"][0]["last_floor_pub_time"] == "2025-01-03 12:00:00"
        assert page["items"][0]["last_floor_publisher"] == "u2"

    def test_upsert_snapshot_persists_last_floor_reply_defaults(self, db):
        repo = ThreadsRepository(db)
        repo.upsert_snapshot(
            _make_snapshot(
                tid=6351,
                floors=[
                    _make_floor(pid=63511, tid=6351, floor_no=1, pub_time="2025-01-01 10:00:00", publisher="u1"),
                    _make_floor(pid=63512, tid=6351, floor_no=2, pub_time="2025-01-03 12:00:00", publisher="u2"),
                ],
            ),
            forum_id=55,
        )
        db.commit()

        row = repo.get_thread(6351)

        assert row["local_reply_count"] == 1
        assert row["remote_last_reply_at_raw"] == "2025-01-03 12:00:00"
        assert row["remote_last_reply_at"] == "2025-01-03 12:00:00"
        assert row["remote_last_replier"] == "u2"

    def test_backfill_local_reply_metadata_uses_floor_defaults(self, db):
        repo = ThreadsRepository(db)
        repo.upsert_snapshot(
            _make_snapshot(
                tid=6401,
                floors=[
                    _make_floor(pid=64011, tid=6401, floor_no=1, pub_time="2025-01-01 10:00:00", publisher="u1"),
                    _make_floor(pid=64012, tid=6401, floor_no=2, pub_time="2025-01-03 12:00:00", publisher="u2"),
                    _make_floor(pid=64013, tid=6401, floor_no=3, pub_time="2025-01-04 13:00:00", publisher="u3"),
                ],
            )
        )
        db.execute(
            """
            UPDATE threads
            SET local_reply_count = NULL, remote_last_reply_at_raw = NULL, remote_last_reply_at = NULL, remote_last_replier = NULL
            WHERE tid = ?
            """,
            (6401,),
        )
        db.commit()

        result = repo.backfill_local_reply_metadata()
        db.commit()
        row = repo.get_thread(6401)

        assert result["thread_count"] >= 1
        assert result["local_reply_count_updates"] >= 1
        assert result["last_reply_updates"] >= 1
        assert row["local_reply_count"] == 2
        assert row["remote_last_reply_at_raw"] == "2025-01-04 13:00:00"
        assert row["remote_last_reply_at"] == "2025-01-04 13:00:00"
        assert row["remote_last_replier"] == "u3"


class TestSearchThreads:
    def test_search_by_display_title(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        title = _make_title(display_title="星辰之歌 第3话", core_title_guess="星辰之歌")
        repo.upsert_snapshot(_make_snapshot(tid=8001, title=title))
        # Act
        results = repo.search_threads("星辰之歌")
        # Assert
        assert len(results) >= 1
        assert any(r["tid"] == 8001 for r in results)

    def test_search_empty_query_returns_all(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        repo.upsert_snapshot(_make_snapshot(tid=9001))
        # Act
        results = repo.search_threads("")
        # Assert
        assert len(results) >= 1

    def test_search_with_tilde_falls_back_to_like(self, db):
        # Arrange
        repo = ThreadsRepository(db)
        title = _make_title(display_title="100天后就辞职的面包屋打工 51~60", core_title_guess="100天后就辞职的面包屋打工")
        repo.upsert_snapshot(_make_snapshot(tid=9002, title=title))
        # Act
        results = repo.search_threads("面包屋打工 51~60")
        # Assert
        assert any(r["tid"] == 9002 for r in results)
