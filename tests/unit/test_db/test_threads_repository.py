import pytest

from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.rag.chunker import RagChunk
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot


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
                RagChunk(
                    chunk_id="thread:4001:floor:1:part:1",
                    tid=4001,
                    pid=4002,
                    floor_no=1,
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
                    text_hash="hash-1",
                    source_uri="yamibo://threads/4001/posts#floor=1",
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
