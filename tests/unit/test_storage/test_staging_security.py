"""测试 staging 目录的安全性检查 — 确保不会删除 staging_root 之外的内容。

安全模型（三层防护）：
1. staging_job_dir() 拒绝 ../、/、\\ — 路径穿越在入口被拦截
2. cleanup_stale_staging() 跳过符号链接 — 防止 symlink 指向外部目录
3. shutil.rmtree 内部 follow_symlinks=False — 双重保险
"""

from pathlib import Path

import pytest

from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.storage.staging import (
    cleanup_stale_staging,
    remove_staging_dir,
)


class TestStagingJobDirSecurity:
    """staging_job_dir 拒绝路径穿越字符 — 第一道防线。"""

    def test_rejects_dot_dot(self):
        with pytest.raises(ValueError, match="Invalid job_id"):
            StoragePaths(Path("/tmp")).staging_job_dir("../../etc")

    def test_rejects_slash(self):
        with pytest.raises(ValueError, match="Invalid job_id"):
            StoragePaths(Path("/tmp")).staging_job_dir("foo/bar")

    def test_rejects_backslash(self):
        with pytest.raises(ValueError, match="Invalid job_id"):
            StoragePaths(Path("/tmp")).staging_job_dir("foo\\bar")

    def test_accepts_valid_job_id(self, tmp_path):
        p = StoragePaths(tmp_path).staging_job_dir("sync_thread_abc123def4567890")
        expected = tmp_path / "staging" / "jobs" / "sync_thread_abc123def4567890"
        assert p == expected
        # 验证是 staging_root 的直接子目录
        assert p.parent == StoragePaths(tmp_path).staging_root


class TestCleanupStaleStagingSafety:
    """cleanup_stale_staging 的安全边界。"""

    def test_skips_non_existent_root(self, tmp_path):
        paths = StoragePaths(tmp_path / "no_such_dir")
        assert cleanup_stale_staging(paths, older_than_hours=0) == 0

    def test_skips_empty_root(self, tmp_path):
        (tmp_path / "staging" / "jobs").mkdir(parents=True)
        paths = StoragePaths(tmp_path)
        assert cleanup_stale_staging(paths, older_than_hours=0) == 0

    def test_skips_symlink(self, tmp_path):
        """符号链接不应被删除 — 第二道防线。"""
        staging_root = tmp_path / "staging" / "jobs"
        staging_root.mkdir(parents=True)
        symlink_path = staging_root / "evil_link"
        symlink_path.symlink_to("/etc")
        paths = StoragePaths(tmp_path)
        result = cleanup_stale_staging(paths, older_than_hours=0)
        assert result == 0
        assert symlink_path.exists()  # 链接本身也没被碰

    def test_deletes_stale_regular_dir(self, tmp_path):
        staging_root = tmp_path / "staging" / "jobs"
        job_dir = staging_root / "old_job"
        job_dir.mkdir(parents=True)
        (job_dir / "dummy.txt").write_text("test")
        paths = StoragePaths(tmp_path)
        # older_than_hours=0 会删除所有目录
        result = cleanup_stale_staging(paths, older_than_hours=0)
        assert result == 1
        assert not job_dir.exists()


class TestRemoveStagingDirSafety:
    """remove_staging_dir 的安全边界。"""

    def test_skips_non_existent(self, tmp_path):
        paths = StoragePaths(tmp_path)
        assert remove_staging_dir(paths, "nonexistent_job") is False

    def test_removes_valid_dir(self, tmp_path):
        paths = StoragePaths(tmp_path)
        job_dir = paths.staging_job_dir("test_cleanup")
        job_dir.mkdir(parents=True)
        (job_dir / "dummy.txt").write_text("test")
        assert remove_staging_dir(paths, "test_cleanup") is True
        assert not job_dir.exists()

    def test_validation_enforced_by_staging_job_dir(self, tmp_path):
        """路径穿越被 staging_job_dir 拦截，remove_staging_dir 天然安全。"""
        paths = StoragePaths(tmp_path)
        with pytest.raises(ValueError):
            remove_staging_dir(paths, "../../etc")
