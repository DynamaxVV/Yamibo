from __future__ import annotations

import pytest

from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.domain.models import AssetSnapshot


def _make_asset(asset_id="asset_001", tid=999, pid=1001, **overrides):
    defaults = dict(
        asset_id=asset_id,
        tid=tid,
        pid=pid,
        asset_type="image",
        remote_url="http://img/test.jpg",
        local_path=None,
        exportable=True,
        required=True,
        status="pending",
    )
    defaults.update(overrides)
    return AssetSnapshot(**defaults)


class TestUpsertAssets:
    def test_upsert_and_list_round_trip(self, db):
        # Arrange
        repo = AssetsRepository(db)
        assets = [
            _make_asset(asset_id="a1", remote_url="http://img/a.jpg"),
            _make_asset(asset_id="a2", remote_url="http://img/b.jpg", required=False),
        ]
        # Act
        repo.upsert_assets(tid=999, assets=assets)
        rows = repo.list_assets(999)
        # Assert
        assert len(rows) == 2
        asset_ids = {r["asset_id"] for r in rows}
        assert "a1" in asset_ids
        assert "a2" in asset_ids

    def test_preserves_required_exportable_status(self, db):
        # Arrange
        repo = AssetsRepository(db)
        asset = _make_asset(asset_id="req1", required=True, exportable=False, status="downloaded")
        # Act
        repo.upsert_assets(tid=888, assets=[asset])
        rows = repo.list_assets(888)
        # Assert
        assert rows[0]["required"] == 1
        assert rows[0]["exportable"] == 0
        assert rows[0]["status"] == "downloaded"

    def test_preserves_local_path_and_remote_url(self, db):
        # Arrange
        repo = AssetsRepository(db)
        asset = _make_asset(asset_id="lp1", remote_url="http://img/x.jpg", local_path="images/floor_001_01.jpg")
        # Act
        repo.upsert_assets(tid=777, assets=[asset])
        rows = repo.list_assets(777)
        # Assert
        assert rows[0]["remote_url"] == "http://img/x.jpg"
        assert rows[0]["local_path"] == "images/floor_001_01.jpg"

    def test_upsert_replaces_existing(self, db):
        # Arrange
        repo = AssetsRepository(db)
        repo.upsert_assets(tid=666, assets=[_make_asset(asset_id="old", status="pending")])
        # Act
        repo.upsert_assets(tid=666, assets=[_make_asset(asset_id="new", status="downloaded")])
        rows = repo.list_assets(666)
        # Assert
        assert len(rows) == 1
        assert rows[0]["asset_id"] == "new"
        assert rows[0]["status"] == "downloaded"

    def test_empty_assets_clears_existing(self, db):
        # Arrange
        repo = AssetsRepository(db)
        repo.upsert_assets(tid=555, assets=[_make_asset()])
        # Act
        repo.upsert_assets(tid=555, assets=[])
        rows = repo.list_assets(555)
        # Assert
        assert len(rows) == 0


class TestGetAsset:
    def test_get_existing_asset(self, db):
        repo = AssetsRepository(db)
        repo.upsert_assets(tid=444, assets=[_make_asset(asset_id="findme")])
        row = repo.get_asset("findme")
        assert row is not None
        assert row["asset_id"] == "findme"

    def test_get_nonexistent_returns_none(self, db):
        repo = AssetsRepository(db)
        assert repo.get_asset("nope") is None


class TestDeleteAssets:
    def test_delete_assets(self, db):
        repo = AssetsRepository(db)
        repo.upsert_assets(tid=333, assets=[_make_asset()])
        repo.delete_assets(333)
        assert repo.list_assets(333) == []


class TestAssetTypes:
    def test_shared_asset_type(self, db):
        repo = AssetsRepository(db)
        asset = _make_asset(asset_id="shared1", asset_type="shared", required=False, exportable=False)
        repo.upsert_assets(tid=222, assets=[asset])
        row = repo.list_assets(222)[0]
        assert row["asset_type"] == "shared"
        assert row["required"] == 0

    def test_attachment_asset_type(self, db):
        repo = AssetsRepository(db)
        asset = _make_asset(asset_id="att1", asset_type="attachment")
        repo.upsert_assets(tid=111, assets=[asset])
        row = repo.list_assets(111)[0]
        assert row["asset_type"] == "attachment"
