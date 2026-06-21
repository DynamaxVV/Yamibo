from __future__ import annotations

import json

import pytest

from yamibo_mcp.server.resources import (
    forums_index_uri,
    forum_summary_uri,
    job_events_uri,
    parse_resource_uri,
    thread_assets_uri,
    thread_context_uri,
    thread_diagnostics_uri,
    thread_metadata_uri,
    thread_posts_uri,
    thread_summary_uri,
    thread_update_check_uri,
)


# --- URI helpers ---

class TestNewUriHelpers:
    def test_forums_index_uri(self):
        assert forums_index_uri() == "yamibo://forums/index"

    def test_forum_summary_uri(self):
        assert forum_summary_uri(30) == "yamibo://forums/30/summary"

    def test_thread_summary_uri(self):
        assert thread_summary_uri(123) == "yamibo://threads/123/summary"

    def test_thread_diagnostics_uri(self):
        assert thread_diagnostics_uri(456) == "yamibo://threads/456/diagnostics"

    def test_thread_posts_uri(self):
        assert thread_posts_uri(789) == "yamibo://threads/789/posts"

    def test_thread_assets_uri(self):
        assert thread_assets_uri(101) == "yamibo://threads/101/assets"

    def test_thread_update_check_uri(self):
        assert thread_update_check_uri(102) == "yamibo://threads/102/update-check"

    def test_job_events_uri(self):
        assert job_events_uri("sync_thread_abc") == "yamibo://jobs/sync_thread_abc/events"


# --- parse_resource_uri ---

class TestParseNewResourceUris:
    def test_parse_forums_index(self):
        root, tid, kind = parse_resource_uri("yamibo://forums/index")
        assert root == "forums"
        assert tid is None
        assert kind == "index"

    def test_parse_forum_summary(self):
        root, tid, kind = parse_resource_uri("yamibo://forums/30/summary")
        assert root == "forums"
        assert tid == 30
        assert kind == "summary"

    def test_parse_thread_summary(self):
        root, tid, kind = parse_resource_uri("yamibo://threads/123/summary")
        assert root == "threads"
        assert tid == 123
        assert kind == "summary"

    def test_parse_thread_diagnostics(self):
        root, tid, kind = parse_resource_uri("yamibo://threads/456/diagnostics")
        assert root == "threads"
        assert tid == 456
        assert kind == "diagnostics"

    def test_parse_thread_posts(self):
        root, tid, kind = parse_resource_uri("yamibo://threads/789/posts")
        assert root == "threads"
        assert tid == 789
        assert kind == "posts"

    def test_parse_thread_assets(self):
        root, tid, kind = parse_resource_uri("yamibo://threads/101/assets")
        assert root == "threads"
        assert tid == 101
        assert kind == "assets"

    def test_parse_thread_update_check(self):
        root, tid, kind = parse_resource_uri("yamibo://threads/102/update-check")
        assert root == "threads"
        assert tid == 102
        assert kind == "update-check"

    def test_parse_job_events(self):
        root, tid, kind = parse_resource_uri("yamibo://jobs/sync_thread_abc/events")
        assert root == "jobs"
        assert tid is None
        assert kind == "sync_thread_abc/events"

    def test_old_uris_still_work(self):
        root, tid, kind = parse_resource_uri("yamibo://threads/123/context")
        assert root == "threads"
        assert tid == 123
        assert kind == "context"

        root, tid, kind = parse_resource_uri("yamibo://series/index")
        assert root == "series"
        assert tid is None
        assert kind == "index"

        root, tid, kind = parse_resource_uri("yamibo://series/5/chapters")
        assert root == "series"
        assert tid == 5
        assert kind == "chapters"

    def test_unsupported_uri_raises(self):
        with pytest.raises(ValueError, match="unsupported resource uri"):
            parse_resource_uri("yamibo://unknown/123/bar")


# --- read_resource (integration with DB) ---

class TestReadForumsIndex:
    def test_forums_index_returns_all_forums(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db):
            result = read_resource("yamibo://forums/index")
        assert result["exists"] is True
        data = json.loads(result["text"])
        forum_ids = [f["forum_id"] for f in data]
        assert 30 in forum_ids
        assert 55 in forum_ids


class TestReadForumSummary:
    def test_forum_summary_returns_forum(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db):
            result = read_resource("yamibo://forums/30/summary")
        assert result["exists"] is True
        data = json.loads(result["text"])
        assert data["forum_id"] == 30
        assert data["content_kind"] == "comic"

    def test_forum_summary_not_found(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db):
            result = read_resource("yamibo://forums/999/summary")
        assert result["exists"] is False
        assert "not found" in result["error"]


class TestReadThreadSummary:
    def test_thread_summary_compact(self, db):
        """Thread summary should NOT read full context.md."""
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        from yamibo_mcp.db.repositories.threads import ThreadsRepository
        from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
        # Seed a thread
        title = TitleSnapshot(
            raw_title="[G] Test", display_title="Test",
            group_name="G", author_guess="A",
            core_title_guess="Test", normalized_core_title="test",
            series_key="test", title_aliases=[],
            chapter_name=None, chapter_index=None, chapter_index_end=None,
            chapter_title=None, subtitle=None, tags=[],
            confidence=0.9, needs_review=False, parser_version="v1",
        )
        snap = ThreadSnapshot(
            tid=999, url=None, page_type="thread_detail",
            raw_title="[G] Test", display_title="Test",
            title=title, publisher="u", publisher_uid="1",
            pub_time=None, permission=0,
            floors=[FloorSnapshot(pid=9991, tid=999, floor_no=1,
                                  publisher="u", content="x", pub_time=None,
                                  has_images=False)],
            image_count=0,
        )
        ThreadsRepository(db).upsert_snapshot(snap)
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db):
            result = read_resource("yamibo://threads/999/summary")
        assert result["exists"] is True
        data = json.loads(result["text"])
        assert data["tid"] == 999
        assert data["archive_status"] is not None
        assert "diagnostics" in data["resources"]
        assert "posts" in data["resources"]
        assert "assets" in data["resources"]

    def test_thread_summary_not_found(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db):
            result = read_resource("yamibo://threads/99999/summary")
        assert result["exists"] is False


class TestReadThreadDiagnostics:
    def test_diagnostics_reports_status(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        from yamibo_mcp.db.repositories.threads import ThreadsRepository
        from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
        title = TitleSnapshot(
            raw_title="[G] Test", display_title="Test",
            group_name="G", author_guess="A",
            core_title_guess="Test", normalized_core_title="test",
            series_key="test", title_aliases=[],
            chapter_name=None, chapter_index=None, chapter_index_end=None,
            chapter_title=None, subtitle=None, tags=[],
            confidence=0.9, needs_review=False, parser_version="v1",
        )
        snap = ThreadSnapshot(
            tid=888, url=None, page_type="thread_detail",
            raw_title="[G] Test", display_title="Test",
            title=title, publisher="u", publisher_uid="1",
            pub_time=None, permission=0,
            floors=[FloorSnapshot(pid=8881, tid=888, floor_no=1,
                                  publisher="u", content="x", pub_time=None,
                                  has_images=False)],
            image_count=0,
        )
        ThreadsRepository(db).upsert_snapshot(snap)
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db):
            result = read_resource("yamibo://threads/888/diagnostics")
        assert result["exists"] is True
        data = json.loads(result["text"])
        assert data["tid"] == 888
        assert "archive_status" in data
        assert "next_actions" in data
        assert "warnings" in data
        # No secrets
        text = result["text"].lower()
        assert "cookie" not in text
        assert "password" not in text
        assert "api_key" not in text


class TestReadThreadUpdateCheck:
    def test_update_check_resource_returns_json(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db), \
             patch("yamibo_mcp.server.resource_handlers.check_thread_updates", return_value={"tid": 1, "status": "up_to_date"}):
            result = read_resource("yamibo://threads/1/update-check")
        assert result["exists"] is True
        data = json.loads(result["text"])
        assert data["status"] == "up_to_date"


class TestReadJobEvents:
    def test_job_events_returns_ordered_events(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        from yamibo_mcp.db.repositories.jobs import JobsRepository
        repo = JobsRepository(db)
        job = repo.create("noop")
        repo.update_stage(job.job_id, "stage1")
        repo.update_stage(job.job_id, "stage2")
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db):
            result = read_resource(f"yamibo://jobs/{job.job_id}/events")
        assert result["exists"] is True
        data = json.loads(result["text"])
        event_ids = [e["event_id"] for e in data]
        assert event_ids == sorted(event_ids)
        assert len(data) >= 3


class TestReadThreadPosts:
    def test_posts_returns_content_blocks(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        from yamibo_mcp.db.repositories.threads import ThreadsRepository
        from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
        from yamibo_mcp.domain.models import ContentBlock, FloorSnapshot, ThreadSnapshot, TitleSnapshot
        title = TitleSnapshot(
            raw_title="[G] Test", display_title="Test",
            group_name="G", author_guess="A",
            core_title_guess="Test", normalized_core_title="test",
            series_key="test", title_aliases=[],
            chapter_name=None, chapter_index=None, chapter_index_end=None,
            chapter_title=None, subtitle=None, tags=[],
            confidence=0.9, needs_review=False, parser_version="v1",
        )
        snap = ThreadSnapshot(
            tid=777, url=None, page_type="thread_detail",
            raw_title="[G] Test", display_title="Test",
            title=title, publisher="u", publisher_uid="1",
            pub_time=None, permission=0,
            floors=[FloorSnapshot(pid=7771, tid=777, floor_no=1,
                                  publisher="u", content="x", pub_time=None,
                                  has_images=False)],
            image_count=0,
        )
        ThreadsRepository(db).upsert_snapshot(snap)
        ContentBlocksRepository(db).upsert_blocks(777, [
            ContentBlock(block_id="b1", pid=7771, order_index=0, block_type="text", text="hello"),
        ])
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db):
            result = read_resource("yamibo://threads/777/posts")
        assert result["exists"] is True
        data = json.loads(result["text"])
        assert len(data) == 1
        assert data[0]["block_type"] == "text"


class TestReadThreadAssets:
    def test_assets_returns_asset_list(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        from yamibo_mcp.db.repositories.threads import ThreadsRepository
        from yamibo_mcp.db.repositories.assets import AssetsRepository
        from yamibo_mcp.domain.models import AssetSnapshot, FloorSnapshot, ThreadSnapshot, TitleSnapshot
        title = TitleSnapshot(
            raw_title="[G] Test", display_title="Test",
            group_name="G", author_guess="A",
            core_title_guess="Test", normalized_core_title="test",
            series_key="test", title_aliases=[],
            chapter_name=None, chapter_index=None, chapter_index_end=None,
            chapter_title=None, subtitle=None, tags=[],
            confidence=0.9, needs_review=False, parser_version="v1",
        )
        snap = ThreadSnapshot(
            tid=666, url=None, page_type="thread_detail",
            raw_title="[G] Test", display_title="Test",
            title=title, publisher="u", publisher_uid="1",
            pub_time=None, permission=0,
            floors=[FloorSnapshot(pid=6661, tid=666, floor_no=1,
                                  publisher="u", content="x", pub_time=None,
                                  has_images=False)],
            image_count=0,
        )
        ThreadsRepository(db).upsert_snapshot(snap)
        AssetsRepository(db).upsert_assets(666, [
            AssetSnapshot(asset_id="a1", tid=666, pid=6661, asset_type="image",
                          remote_url="http://example.com/1.jpg", local_path=None,
                          exportable=True, required=True, status="pending"),
        ])
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db):
            result = read_resource("yamibo://threads/666/assets")
        assert result["exists"] is True
        data = json.loads(result["text"])
        assert len(data) == 1
        assert data[0]["asset_id"] == "a1"
        assert data[0]["required"] is True


class TestSecretScrub:
    def test_diagnostics_no_secrets(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        settings.cookie_file = "/tmp/cookies.txt"
        settings.cookie_value = "secret_cookie_12345"
        settings.api_key = "sk-12345"
        settings.login_password = "hunter2"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db):
            result = read_resource("yamibo://threads/999/diagnostics")
        text = result["text"].lower() if result.get("text") else ""
        assert "secret_cookie" not in text
        assert "sk-12345" not in text
        assert "hunter2" not in text


class TestDiagnosticsWithAssets:
    def test_diagnostics_reports_missing_required_assets(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        from yamibo_mcp.db.repositories.threads import ThreadsRepository
        from yamibo_mcp.db.repositories.assets import AssetsRepository
        from yamibo_mcp.domain.models import AssetSnapshot, FloorSnapshot, ThreadSnapshot, TitleSnapshot
        title = TitleSnapshot(
            raw_title="[G] Test", display_title="Test",
            group_name="G", author_guess="A",
            core_title_guess="Test", normalized_core_title="test",
            series_key="test", title_aliases=[],
            chapter_name=None, chapter_index=None, chapter_index_end=None,
            chapter_title=None, subtitle=None, tags=[],
            confidence=0.9, needs_review=False, parser_version="v1",
        )
        snap = ThreadSnapshot(
            tid=555, url=None, page_type="thread_detail",
            raw_title="[G] Test", display_title="Test",
            title=title, publisher="u", publisher_uid="1",
            pub_time=None, permission=0,
            floors=[FloorSnapshot(pid=5551, tid=555, floor_no=1,
                                  publisher="u", content="x", pub_time=None,
                                  has_images=False)],
            image_count=2,
        )
        ThreadsRepository(db).upsert_snapshot(snap, archive_status="partial",
                                               missing_image_urls=["http://example.com/missing.jpg"])
        AssetsRepository(db).upsert_assets(555, [
            AssetSnapshot(asset_id="a_ok", tid=555, pid=5551, asset_type="image",
                          remote_url="http://example.com/ok.jpg", local_path="images/ok.jpg",
                          exportable=True, required=True, status="downloaded"),
            AssetSnapshot(asset_id="a_miss", tid=555, pid=5551, asset_type="image",
                          remote_url="http://example.com/miss.jpg", local_path=None,
                          exportable=True, required=True, status="missing"),
        ])
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db):
            result = read_resource("yamibo://threads/555/diagnostics")
        assert result["exists"] is True
        data = json.loads(result["text"])
        assert data["required_assets_count"] == 2
        assert data["missing_required_assets_count"] == 1
        assert any("missing 1 required assets" in w for w in data["warnings"])

    def test_diagnostics_no_missing_required(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.resource_handlers import read_resource
        from yamibo_mcp.db.repositories.threads import ThreadsRepository
        from yamibo_mcp.db.repositories.assets import AssetsRepository
        from yamibo_mcp.domain.models import AssetSnapshot, FloorSnapshot, ThreadSnapshot, TitleSnapshot
        title = TitleSnapshot(
            raw_title="[G] Test", display_title="Test",
            group_name="G", author_guess="A",
            core_title_guess="Test", normalized_core_title="test",
            series_key="test", title_aliases=[],
            chapter_name=None, chapter_index=None, chapter_index_end=None,
            chapter_title=None, subtitle=None, tags=[],
            confidence=0.9, needs_review=False, parser_version="v1",
        )
        snap = ThreadSnapshot(
            tid=444, url=None, page_type="thread_detail",
            raw_title="[G] Test", display_title="Test",
            title=title, publisher="u", publisher_uid="1",
            pub_time=None, permission=0,
            floors=[FloorSnapshot(pid=4441, tid=444, floor_no=1,
                                  publisher="u", content="x", pub_time=None,
                                  has_images=False)],
            image_count=1,
        )
        ThreadsRepository(db).upsert_snapshot(snap, archive_status="complete")
        AssetsRepository(db).upsert_assets(444, [
            AssetSnapshot(asset_id="a_ok", tid=444, pid=4441, asset_type="image",
                          remote_url="http://example.com/ok.jpg", local_path="images/ok.jpg",
                          exportable=True, required=True, status="downloaded"),
        ])
        settings = MagicMock()
        settings.db_path = ":memory:"
        settings.data_dir = "/tmp"
        settings.export_dir = "/tmp/exports"
        with patch("yamibo_mcp.server.resource_handlers.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.resource_handlers.connect", return_value=db):
            result = read_resource("yamibo://threads/444/diagnostics")
        data = json.loads(result["text"])
        assert data["required_assets_count"] == 1
        assert data["missing_required_assets_count"] == 0
        assert not any("required assets" in w for w in data["warnings"])


class TestThreadSummaryResources:
    def test_summary_includes_new_resource_uris(self, db):
        from unittest.mock import patch, MagicMock
        from yamibo_mcp.server.schemas import thread_summary_payload
        from yamibo_mcp.db.repositories.threads import ThreadsRepository
        from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
        title = TitleSnapshot(
            raw_title="[G] Test", display_title="Test",
            group_name="G", author_guess="A",
            core_title_guess="Test", normalized_core_title="test",
            series_key="test", title_aliases=[],
            chapter_name=None, chapter_index=None, chapter_index_end=None,
            chapter_title=None, subtitle=None, tags=[],
            confidence=0.9, needs_review=False, parser_version="v1",
        )
        snap = ThreadSnapshot(
            tid=333, url=None, page_type="thread_detail",
            raw_title="[G] Test", display_title="Test",
            title=title, publisher="u", publisher_uid="1",
            pub_time=None, permission=0,
            floors=[FloorSnapshot(pid=3331, tid=333, floor_no=1,
                                  publisher="u", content="x", pub_time=None,
                                  has_images=False)],
            image_count=0,
        )
        ThreadsRepository(db).upsert_snapshot(snap)
        row = ThreadsRepository(db).get_thread(333)
        payload = thread_summary_payload(row)
        resources = payload["resources"]
        assert "summary" in resources
        assert "diagnostics" in resources
        assert "posts" in resources
        assert "assets" in resources
        assert resources["summary"] == "yamibo://threads/333/summary"
        assert resources["diagnostics"] == "yamibo://threads/333/diagnostics"
