from __future__ import annotations

from yamibo_mcp.db.connection import DatabaseConnection, _normalize_postgres_url, transaction


class _Result:
    def fetchone(self):
        return None


class _RawConn:
    def __init__(self):
        self.calls = []

    def execute(self, statement, parameters=None):
        self.calls.append((statement, parameters))
        return _Result()

    def in_transaction(self):
        return True


def test_execute_strips_nul_from_parameters():
    raw = _RawConn()
    conn = DatabaseConnection(raw, backend="postgres")

    conn.execute(
        "UPDATE floors SET content = :content, reply_text = :reply_text WHERE pid = :pid",
        {
            "content": "挖LZ内的见解好精辟啊\x00",
            "reply_text": {"note": "内含\x00字节"},
            "pid": 123,
        },
    )

    _, params = raw.calls[0]
    assert params["content"] == "挖LZ内的见解好精辟啊"
    assert params["reply_text"]["note"] == "内含字节"
    assert params["pid"] == 123


def test_postgres_url_uses_declared_psycopg3_driver():
    assert _normalize_postgres_url(
        "postgresql://yamibo:secret@db.example.com:5432/yamibo"
    ) == "postgresql+psycopg://yamibo:secret@db.example.com:5432/yamibo"


def test_transaction_joins_existing_sqlalchemy_transaction():
    raw = _RawConn()
    conn = DatabaseConnection(raw, backend="postgres")
    with transaction(conn) as joined:
        assert joined is conn
    assert _normalize_postgres_url(
        "postgresql+psycopg://yamibo:secret@db.example.com:5432/yamibo"
    ) == "postgresql+psycopg://yamibo:secret@db.example.com:5432/yamibo"
