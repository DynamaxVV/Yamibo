from __future__ import annotations

import argparse
import shutil
import subprocess
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from yamibo_mcp.config import load_settings


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _pg_dump_connection_arg(db_url: str) -> str:
    url = make_url(db_url)
    drivername = url.drivername
    if drivername.startswith("postgresql+"):
        url = url.set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def backup_database(*, backend: str, db_path: Path, db_url: str | None, dest_dir: Path, keep_count: int) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if backend == "postgres":
        if not db_url:
            raise ValueError("db_url is required when backing up a PostgreSQL database")
        backup_path = dest_dir / f"forum_{_timestamp()}.pgdump"
        subprocess.run(
            ["pg_dump", "--format=custom", "--file", str(backup_path), _pg_dump_connection_arg(db_url)],
            check=True,
        )
        pattern = "forum_*.pgdump"
    else:
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
        pattern = "forum_*.sqlite3"

    backups = sorted(dest_dir.glob(pattern), key=lambda path: path.stat().st_mtime)
    if keep_count > 0 and len(backups) > keep_count:
        for old in backups[: len(backups) - keep_count]:
            old.unlink(missing_ok=True)
    return backup_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a timestamped database backup.")
    parser.add_argument("--db")
    parser.add_argument("--dest-dir")
    parser.add_argument("--keep-count", type=int)
    parser.add_argument("--copy-cookie", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    if settings.db_backend == "postgres":
        db_url = args.db or settings.db_url
        db_path = settings.db_path
    else:
        db_url = None
        db_path = settings.db_path if args.db is None else Path(args.db).expanduser()
    dest_dir = settings.backup_dir if args.dest_dir is None else Path(args.dest_dir).expanduser()
    keep_count = settings.backup_keep_count if args.keep_count is None else args.keep_count
    backup_path = backup_database(
        backend=settings.db_backend,
        db_path=db_path,
        db_url=db_url,
        dest_dir=dest_dir,
        keep_count=keep_count,
    )
    print(f"backup_created={backup_path}")

    if args.copy_cookie and settings.cookie_file.exists():
        cookie_target = dest_dir / f"cookie_{_timestamp()}.txt"
        shutil.copy2(settings.cookie_file, cookie_target)
        print(f"cookie_copied={cookie_target}")


if __name__ == "__main__":
    main()
