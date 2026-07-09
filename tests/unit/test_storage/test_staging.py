import json
from pathlib import Path

from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.storage.staging import (
    write_staging_failure,
    write_staging_snapshot,
    write_staging_title_parse_log,
)


def _make_snapshot(tid=999):
    title = TitleSnapshot(
        raw_title="t", display_title="t", group_name=None,
        author_guess=None, core_title_guess="t", normalized_core_title="t",
        series_key="t", title_aliases=[], chapter_name=None,
        chapter_index=None, chapter_index_end=None, chapter_title=None,
        subtitle=None, tags=[], confidence=0.9, needs_review=False,
        parser_version="title-v1",
    )
    floor = FloorSnapshot(pid=1, tid=tid, floor_no=1, publisher="u",
                          content="c", pub_time=None, has_images=False)
    return ThreadSnapshot(
        tid=tid, url=None, page_type="thread_detail",
        raw_title="t", display_title="t", title=title,
        publisher="u", publisher_uid="1", pub_time=None,
        permission=0, floors=[floor],
    )


class TestWriteStagingSnapshot:
    def test_creates_snapshot_json(self, tmp_path):
        paths = StoragePaths(tmp_path)
        write_staging_snapshot(paths, "job_001", _make_snapshot())
        path = tmp_path / "staging" / "jobs" / "job_001" / "snapshot.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["tid"] == 999

    def test_creates_parent_directories(self, tmp_path):
        paths = StoragePaths(tmp_path)
        # 真实 job_id 格式为 {job_type}_{uuid_hex}，不含路径分隔符
        write_staging_snapshot(paths, "sync_thread_abc123def4567890", _make_snapshot())
        path = tmp_path / "staging" / "jobs" / "sync_thread_abc123def4567890" / "snapshot.json"
        assert path.exists()


class TestWriteStagingFailure:
    def test_creates_failure_json(self, tmp_path):
        paths = StoragePaths(tmp_path)
        write_staging_failure(paths, "job_003", {"error": "timeout", "stage": "download"})
        path = tmp_path / "staging" / "jobs" / "job_003" / "failure.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["error"] == "timeout"
        assert data["stage"] == "download"


class TestWriteStagingTitleParseLog:
    def test_creates_title_parse_log_json(self, tmp_path):
        paths = StoragePaths(tmp_path)
        write_staging_title_parse_log(paths, "job_004", {"raw": "title", "llm": {"used": True}})
        path = tmp_path / "staging" / "jobs" / "job_004" / "title_parse_log.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["raw"] == "title"
        assert data["llm"]["used"] is True
