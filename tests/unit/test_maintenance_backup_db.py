from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from yamibo_mcp.maintenance.backup_db import _pg_dump_connection_arg, backup_database


def test_backup_database_uses_sqlite_backup(tmp_path: Path):
    db_path = tmp_path / "forum.db"
    dest_dir = tmp_path / "backups"

    backup_path = backup_database(
        backend="sqlite",
        db_path=db_path,
        db_url=None,
        dest_dir=dest_dir,
        keep_count=2,
    )

    assert backup_path.suffix == ".sqlite3"
    assert backup_path.exists()


def test_backup_database_uses_pg_dump_for_postgres(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "forum.db"
    dest_dir = tmp_path / "backups"
    calls = {}

    def fake_run(cmd, *, check):
        calls["cmd"] = cmd
        calls["check"] = check
        backup_target = Path(cmd[cmd.index("--file") + 1])
        backup_target.write_text("pgdump", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("subprocess.run", fake_run)

    backup_path = backup_database(
        backend="postgres",
        db_path=db_path,
        db_url="postgresql://yamibo:secret@db.example.com:5432/yamibo",
        dest_dir=dest_dir,
        keep_count=2,
    )

    assert backup_path.suffix == ".pgdump"
    assert calls["check"] is True
    assert calls["cmd"][0] == "pg_dump"
    assert calls["cmd"][-1] == "postgresql://yamibo:secret@db.example.com:5432/yamibo"


def test_pg_dump_connection_arg_normalizes_sqlalchemy_postgres_driver_url():
    assert (
        _pg_dump_connection_arg("postgresql+psycopg://yamibo:secret@127.0.0.1:5432/yamibo")
        == "postgresql://yamibo:secret@127.0.0.1:5432/yamibo"
    )
