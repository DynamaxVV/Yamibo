"""全局测试配置"""

import os

import pytest

from sqlalchemy import create_engine

from yamibo_mcp.db.connection import connect


def pytest_ignore_collect(collection_path, config):
    """排除备份目录"""
    if "_backup_" in str(collection_path):
        return True


@pytest.fixture
def db(tmp_path, monkeypatch):
    """提供临时 SQLite 数据库，已执行 migrate，测试结束后自动清理。"""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("YAMIBO_DB_BACKEND", "sqlite")
    monkeypatch.delenv("YAMIBO_DB_URL", raising=False)
    conn = connect(db_path)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def pg_engine():
    db_url = os.environ.get("YAMIBO_TEST_PG_URL")
    if db_url:
        engine = create_engine(db_url)
        try:
            yield engine
        finally:
            engine.dispose()
        return

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("YAMIBO_TEST_PG_URL is not set and testcontainers is unavailable")

    try:
        with PostgresContainer("pgvector/pgvector:pg15") as container:
            engine = create_engine(container.get_connection_url())
            try:
                yield engine
            finally:
                engine.dispose()
    except Exception as exc:  # pragma: no cover - depends on local Docker availability
        pytest.skip(f"testcontainers postgres is unavailable: {exc}")


@pytest.fixture(params=["sqlite", "postgres"] if os.environ.get("YAMIBO_TEST_PG_URL") else ["sqlite"])
def backend(request):
    return request.param
