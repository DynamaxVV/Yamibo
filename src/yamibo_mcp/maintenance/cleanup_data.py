from __future__ import annotations

import argparse
import sqlite3
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from yamibo_mcp.config import load_settings


def _older_than(path: Path, *, hours: int) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < cutoff


def cleanup_stale_staging(*, staging_root: Path, older_than_hours: int, dry_run: bool) -> list[Path]:
    removed: list[Path] = []
    if not staging_root.exists():
        return removed
    for child in sorted(staging_root.iterdir()):
        if child.is_dir() and _older_than(child, hours=older_than_hours):
            removed.append(child)
            if not dry_run:
                shutil.rmtree(child)
    return removed


def cleanup_tmp_exports(*, exports_dir: Path, dry_run: bool) -> list[Path]:
    removed: list[Path] = []
    if not exports_dir.exists():
        return removed
    for child in sorted(exports_dir.glob("*.tmp")):
        removed.append(child)
        if not dry_run:
            child.unlink(missing_ok=True)
    return removed


def remove_thread_dir(*, data_dir: Path, tid: int, dry_run: bool) -> Path | None:
    thread_dir = data_dir / "threads" / str(tid)
    if not thread_dir.exists():
        return None
    if not dry_run:
        shutil.rmtree(thread_dir)
    return thread_dir


def cleanup_orphan_thread_dirs(*, data_dir: Path, conn: sqlite3.Connection, dry_run: bool) -> list[Path]:
    removed: list[Path] = []
    thread_root = data_dir / "threads"
    if not thread_root.exists():
        return removed
    db_tids = {
        int(row[0])
        for row in conn.execute("SELECT tid FROM threads").fetchall()
    }
    for child in sorted(thread_root.iterdir()):
        if not child.is_dir() or not child.name.isdigit():
            continue
        tid = int(child.name)
        if tid in db_tids:
            continue
        removed.append(child)
        if not dry_run:
            shutil.rmtree(child)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Cleanup stale Yamibo staging directories, temp exports, and orphan thread dirs.")
    parser.add_argument("--staging-older-than-hours", type=int)
    parser.add_argument("--remove-orphan-thread-dirs", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    try:
        staging_root = settings.data_dir / "staging" / "jobs"
        exports_dir = settings.data_dir / "exports"
        older_than_hours = (
            settings.cleanup_staging_older_than_hours
            if args.staging_older_than_hours is None
            else args.staging_older_than_hours
        )

        removed_staging = cleanup_stale_staging(
            staging_root=staging_root,
            older_than_hours=older_than_hours,
            dry_run=args.dry_run,
        )
        removed_tmp_exports = cleanup_tmp_exports(exports_dir=exports_dir, dry_run=args.dry_run)
        removed_orphan_threads = (
            cleanup_orphan_thread_dirs(data_dir=settings.data_dir, conn=conn, dry_run=args.dry_run)
            if args.remove_orphan_thread_dirs
            else []
        )

        print(f"dry_run={args.dry_run}")
        print(f"removed_staging_count={len(removed_staging)}")
        for path in removed_staging:
            print(f"staging={path}")
        print(f"removed_tmp_exports_count={len(removed_tmp_exports)}")
        for path in removed_tmp_exports:
            print(f"tmp_export={path}")
        print(f"removed_orphan_thread_dirs_count={len(removed_orphan_threads)}")
        for path in removed_orphan_threads:
            print(f"orphan_thread_dir={path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
