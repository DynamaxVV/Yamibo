from __future__ import annotations

import pytest

from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.domain.models import ContentBlock


class TestUpsertBlocks:
    def test_upsert_and_list_round_trip(self, db):
        # Arrange
        repo = ContentBlocksRepository(db)
        blocks = [
            ContentBlock(block_id="b1", pid=1001, order_index=0, block_type="text", text="hello"),
            ContentBlock(block_id="b2", pid=1001, order_index=1, block_type="image", asset_url="http://img/a.jpg"),
        ]
        # Act
        repo.upsert_blocks(tid=999, blocks=blocks)
        rows = repo.list_blocks(999)
        # Assert
        assert len(rows) == 2
        assert rows[0]["block_type"] == "text"
        assert rows[0]["text"] == "hello"
        assert rows[0]["tid"] == 999
        assert rows[0]["pid"] == 1001
        assert rows[0]["order_index"] == 0
        assert rows[1]["block_type"] == "image"
        assert rows[1]["order_index"] == 1

    def test_upsert_preserves_metadata_json(self, db):
        # Arrange
        repo = ContentBlocksRepository(db)
        block = ContentBlock(
            block_id="b_meta", pid=1001, order_index=0,
            block_type="text", text="hi", metadata={"lang": "zh"},
        )
        # Act
        repo.upsert_blocks(tid=888, blocks=[block])
        rows = repo.list_blocks(888)
        # Assert
        import json
        meta = json.loads(rows[0]["metadata_json"])
        assert meta["lang"] == "zh"

    def test_upsert_replaces_existing_blocks(self, db):
        # Arrange
        repo = ContentBlocksRepository(db)
        repo.upsert_blocks(tid=777, blocks=[
            ContentBlock(block_id="old", pid=1001, order_index=0, block_type="text", text="old"),
        ])
        # Act
        repo.upsert_blocks(tid=777, blocks=[
            ContentBlock(block_id="new1", pid=1001, order_index=0, block_type="text", text="new1"),
            ContentBlock(block_id="new2", pid=1001, order_index=1, block_type="text", text="new2"),
        ])
        rows = repo.list_blocks(777)
        # Assert
        assert len(rows) == 2
        assert rows[0]["text"] == "new1"

    def test_empty_blocks_clears_existing(self, db):
        # Arrange
        repo = ContentBlocksRepository(db)
        repo.upsert_blocks(tid=666, blocks=[
            ContentBlock(block_id="x", pid=1001, order_index=0, block_type="text", text="to be cleared"),
        ])
        # Act
        repo.upsert_blocks(tid=666, blocks=[])
        rows = repo.list_blocks(666)
        # Assert
        assert len(rows) == 0

    def test_list_ordered_by_order_index(self, db):
        # Arrange
        repo = ContentBlocksRepository(db)
        repo.upsert_blocks(tid=555, blocks=[
            ContentBlock(block_id="b2", pid=1001, order_index=2, block_type="text", text="third"),
            ContentBlock(block_id="b0", pid=1001, order_index=0, block_type="text", text="first"),
            ContentBlock(block_id="b1", pid=1001, order_index=1, block_type="text", text="second"),
        ])
        # Act
        rows = repo.list_blocks(555)
        # Assert
        assert rows[0]["text"] == "first"
        assert rows[1]["text"] == "second"
        assert rows[2]["text"] == "third"

    def test_unknown_block_type_preserved(self, db):
        # Arrange
        repo = ContentBlocksRepository(db)
        block = ContentBlock(block_id="unk", pid=1001, order_index=0, block_type="unknown")
        # Act
        repo.upsert_blocks(tid=444, blocks=[block])
        rows = repo.list_blocks(444)
        # Assert
        assert rows[0]["block_type"] == "unknown"
        assert rows[0]["text"] is None


class TestDeleteBlocks:
    def test_delete_blocks(self, db):
        repo = ContentBlocksRepository(db)
        repo.upsert_blocks(tid=333, blocks=[
            ContentBlock(block_id="d1", pid=1001, order_index=0, block_type="text", text="gone"),
        ])
        repo.delete_blocks(333)
        assert repo.list_blocks(333) == []
