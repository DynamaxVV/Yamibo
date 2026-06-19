"""全局测试配置"""

import sqlite3

import pytest

from yamibo_mcp.db.migrations import migrate


def pytest_ignore_collect(collection_path, config):
    """排除备份目录"""
    if "_backup_" in str(collection_path):
        return True


@pytest.fixture
def db(tmp_path):
    """提供临时 SQLite 数据库，已执行 migrate，测试结束后自动清理。"""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    yield conn
    conn.close()
