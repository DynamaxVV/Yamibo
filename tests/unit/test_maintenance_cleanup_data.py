from __future__ import annotations

import sqlite3
from pathlib import Path

from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.maintenance.cleanup_data import cleanup_orphan_thread_dirs, remove_thread_dir


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / 'test.db'))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    migrate(conn)
    return conn


def test_remove_thread_dir_deletes_existing_thread_folder(tmp_path: Path):
    data_dir = tmp_path / 'data'
    thread_dir = data_dir / 'threads' / '123'
    (thread_dir / 'images').mkdir(parents=True, exist_ok=True)
    (thread_dir / 'metadata.json').write_text('{}', encoding='utf-8')

    removed = remove_thread_dir(data_dir=data_dir, tid=123, dry_run=False)

    assert removed == thread_dir
    assert not thread_dir.exists()


def test_cleanup_orphan_thread_dirs_removes_untracked_dirs(tmp_path: Path):
    data_dir = tmp_path / 'data'
    keep_dir = data_dir / 'threads' / '123'
    orphan_dir = data_dir / 'threads' / '999'
    (keep_dir / 'images').mkdir(parents=True, exist_ok=True)
    (orphan_dir / 'images').mkdir(parents=True, exist_ok=True)

    conn = _make_conn(tmp_path)
    try:
        conn.execute('INSERT INTO threads (tid, raw_title) VALUES (?, ?)', (123, 'keep'))
        conn.commit()

        removed = cleanup_orphan_thread_dirs(data_dir=data_dir, conn=conn, dry_run=False)

        assert removed == [orphan_dir]
        assert keep_dir.exists()
        assert not orphan_dir.exists()
    finally:
        conn.close()
