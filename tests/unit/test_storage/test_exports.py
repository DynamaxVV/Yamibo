import json
import zipfile
from pathlib import Path

import pytest

from yamibo_mcp.storage.exports import (
    ExportPrecheckError,
    export_thread_txt,
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


def _setup_novel_archive(paths: StoragePaths, tid: int):
    thread_dir = paths.thread_dir(tid)
    thread_dir.mkdir(parents=True, exist_ok=True)
    (thread_dir / "context.md").write_text("# novel\n", encoding="utf-8")
    metadata = {
        "tid": tid,
        "url": f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}",
        "missing_image_urls": [],
        "archived_images": {},
        "non_export_images": {},
        "floors": [
            {"pid": 40852497, "floor_no": 1, "pub_time": "2023-11-15 22:52", "content": "作品名：测试小说\n作者：作者甲\n简介：...", "publisher": "zyq102"},
            {"pid": 40852498, "floor_no": 2, "pub_time": "2023-11-15 22:54", "content": "第1话 最讨厌的妹妹\n正文" * 100, "publisher": "zyq102"},
            {"pid": 40852499, "floor_no": 3, "pub_time": "2023-11-15 22:55", "content": "感谢大家支持，我会继续更新", "publisher": "zyq102"},
            {"pid": 40852500, "floor_no": 4, "pub_time": "2023-11-16 11:50", "content": "第2话 复仇的初吻\n正文" * 100, "publisher": "zyq102"},
        ],
    }
    (thread_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")


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

    def test_datetime_input_is_not_stale(self):
        from datetime import datetime, timezone
        recent = datetime.now(timezone.utc)
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


class TestExportThreadTxt:
    def test_creates_txt_and_manifest(self, tmp_path):
        paths = StoragePaths(tmp_path, novel_txt_export_dir=tmp_path / "novel_exports")
        (tmp_path / "novel_exports").mkdir(parents=True, exist_ok=True)
        _setup_novel_archive(paths, 3001)
        result = export_thread_txt(
            paths,
            3001,
            title="夺走了最讨厌的妹妹的初吻",
            source_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=3001",
            forum_name="轻小说/译文区",
            translator="zyq102",
        )
        assert result.export_path.exists()
        assert result.manifest_path.exists()
        text = result.export_path.read_text(encoding="utf-8")
        assert "夺走了最讨厌的妹妹的初吻" in text
        assert "第1话 最讨厌的妹妹" in text
        assert "感谢大家支持" not in text
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        assert manifest["filtered_floors"]
        assert manifest["needs_full_regenerate"] is False

    def test_second_export_only_appends_new_body_floors(self, tmp_path):
        paths = StoragePaths(tmp_path, novel_txt_export_dir=tmp_path / "novel_exports")
        (tmp_path / "novel_exports").mkdir(parents=True, exist_ok=True)
        _setup_novel_archive(paths, 3002)
        first = export_thread_txt(
            paths,
            3002,
            title="测试小说",
            source_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=3002",
            forum_name="轻小说/译文区",
            translator="zyq102",
        )
        metadata_path = paths.thread_metadata(3002)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["floors"].append(
            {"pid": 40852501, "floor_no": 5, "pub_time": "2023-11-16 12:00", "content": "第3话 新章节\n正文" * 100, "publisher": "zyq102"}
        )
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        second = export_thread_txt(
            paths,
            3002,
            title="测试小说",
            source_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=3002",
            forum_name="轻小说/译文区",
            translator="zyq102",
        )
        assert first.appended_floors == 3
        assert second.appended_floors == 1
        text = second.export_path.read_text(encoding="utf-8")
        assert "第3话 新章节" in text

    def test_export_cleans_edit_note_from_existing_archive_content(self, tmp_path):
        paths = StoragePaths(tmp_path, novel_txt_export_dir=tmp_path / "novel_exports")
        (tmp_path / "novel_exports").mkdir(parents=True, exist_ok=True)
        _setup_novel_archive(paths, 3003)
        metadata_path = paths.thread_metadata(3003)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["floors"][1]["content"] = (
            "本帖最后由 zyq102 于 2023-11-23 17:01 编辑\n\n"
            + ("第1话 最讨厌的妹妹\n正文" * 100)
        )
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

        result = export_thread_txt(
            paths,
            3003,
            title="测试小说",
            source_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=3003",
            forum_name="轻小说/译文区",
            translator="zyq102",
        )

        text = result.export_path.read_text(encoding="utf-8")
        assert "本帖最后由 zyq102 于 2023-11-23 17:01 编辑" not in text
        assert "第1话 最讨厌的妹妹" in text
