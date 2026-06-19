from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from yamibo_mcp.config import load_settings


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def backup_database(*, db_path: Path, dest_dir: Path, keep_count: int) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    backup_path = dest_dir / f"forum_{_timestamp()}.sqlite3"
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    backups = sorted(dest_dir.glob("forum_*.sqlite3"))
    if keep_count > 0 and len(backups) > keep_count:
        for old in backups[: len(backups) - keep_count]:
            old.unlink(missing_ok=True)
    return backup_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a timestamped SQLite backup.")
    parser.add_argument("--db")
    parser.add_argument("--dest-dir")
    parser.add_argument("--keep-count", type=int)
    parser.add_argument("--copy-cookie", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    db_path = settings.db_path if args.db is None else Path(args.db).expanduser()
    dest_dir = settings.backup_dir if args.dest_dir is None else Path(args.dest_dir).expanduser()
    keep_count = settings.backup_keep_count if args.keep_count is None else args.keep_count
    backup_path = backup_database(db_path=db_path, dest_dir=dest_dir, keep_count=keep_count)
    print(f"backup_created={backup_path}")

    if args.copy_cookie and settings.cookie_file.exists():
        cookie_target = dest_dir / f"cookie_{_timestamp()}.txt"
        shutil.copy2(settings.cookie_file, cookie_target)
        print(f"cookie_copied={cookie_target}")


if __name__ == "__main__":
    main()
