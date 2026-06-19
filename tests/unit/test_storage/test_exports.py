import json
import zipfile
from pathlib import Path

import pytest

from yamibo_mcp.storage.exports import (
    ExportPrecheckError,
    export_thread_zip,
    inspect_export_readiness,
    is_thread_stale,
)
from yamibo_mcp.storage.paths import StoragePaths


def _setup_thread_archive(paths: StoragePaths, tid: int, *, with_images=True, missing_urls=None):
    thread_dir = paths.thread_dir(tid)
    thread_dir.mkdir(parents=True, exist_ok=True)
    (paths.thread_images_dir(tid)).mkdir(parents=True, exist_ok=True)

    if with_images:
        img = paths.thread_images_dir(tid) / "floor_001_01.jpg"
        img.write_bytes(b"\xff\xd8\xfffakejpg")

    (thread_dir / "context.md").write_text("# test\n", encoding="utf-8")
    metadata = {
        "tid": tid,
        "missing_image_urls": missing_urls or [],
        "archived_images": {str(tid * 10): ["images/floor_001_01.jpg"]} if with_images else {},
        "non_export_images": {},
    }
    (thread_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8",
    )


class TestInspectExportReadiness:
    def test_ready_when_complete(self, tmp_path):
        paths = StoragePaths(tmp_path)
        _setup_thread_archive(paths, 1001)
        result = inspect_export_readiness(paths, 1001)
        assert result.context_exists is True
        assert result.metadata_exists is True
        assert result.images_complete is True
        assert result.missing_image_count == 0

    def test_missing_images(self, tmp_path):
        paths = StoragePaths(tmp_path)
        _setup_thread_archive(paths, 1002, missing_urls=["http://img/x.jpg"])
        result = inspect_export_readiness(paths, 1002)
        assert result.images_complete is False
        assert result.missing_image_count == 1

    def test_missing_thread_dir_raises(self, tmp_path):
        paths = StoragePaths(tmp_path)
        with pytest.raises(ExportPrecheckError, match="not found"):
            inspect_export_readiness(paths, 9999)

    def test_missing_context_raises(self, tmp_path):
        paths = StoragePaths(tmp_path)
        thread_dir = paths.thread_dir(1003)
        thread_dir.mkdir(parents=True, exist_ok=True)
        (thread_dir / "metadata.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ExportPrecheckError, match="context markdown not found"):
            inspect_export_readiness(paths, 1003)


class TestIsThreadStale:
    def test_none_is_stale(self):
        assert is_thread_stale(None, stale_after_hours=24) is True

    def test_empty_is_stale(self):
        assert is_thread_stale("", stale_after_hours=24) is True

    def test_recent_is_not_stale(self):
        from datetime import datetime, timezone
        recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
        assert is_thread_stale(recent, stale_after_hours=24) is False

    def test_old_is_stale(self):
        assert is_thread_stale("2000-01-01T00:00:00+00:00", stale_after_hours=24) is True


class TestExportThreadZip:
    def test_creates_zip(self, tmp_path):
        paths = StoragePaths(tmp_path)
        _setup_thread_archive(paths, 2001)
        export_path = export_thread_zip(paths, 2001)
        assert export_path.exists()
        assert export_path.suffix == ".zip"
        with zipfile.ZipFile(export_path) as zf:
            names = zf.namelist()
            assert any("context.md" in n for n in names)
            assert any("metadata.json" in n for n in names)

    def test_missing_images_raises(self, tmp_path):
        paths = StoragePaths(tmp_path)
        _setup_thread_archive(paths, 2002, missing_urls=["http://img/x.jpg"])
        with pytest.raises(ExportPrecheckError, match="missing"):
            export_thread_zip(paths, 2002)

    def test_custom_series_dirname(self, tmp_path):
        paths = StoragePaths(tmp_path)
        _setup_thread_archive(paths, 2003)
        export_path = export_thread_zip(paths, 2003, series_name="我的系列")
        assert "我的系列" in str(export_path)

    def test_zip_contains_images(self, tmp_path):
        paths = StoragePaths(tmp_path)
        _setup_thread_archive(paths, 2004, with_images=True)
        export_path = export_thread_zip(paths, 2004)
        with zipfile.ZipFile(export_path) as zf:
            names = zf.namelist()
            assert any("images" in n for n in names)
