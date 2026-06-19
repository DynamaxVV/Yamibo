from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate


def _remove_child(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink(missing_ok=True)


def reset_data_dir(*, data_dir: Path) -> list[Path]:
    removed: list[Path] = []
    if not data_dir.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        return removed
    for child in sorted(data_dir.iterdir()):
        removed.append(child)
        _remove_child(child)
    data_dir.mkdir(parents=True, exist_ok=True)
    return removed


def initialize_empty_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        migrate(conn)
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset the current Yamibo data directory for debugging.")
    parser.add_argument("--yes", action="store_true", help="Actually delete the contents of data_dir.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be removed without deleting anything.")
    parser.add_argument("--no-reinit-db", action="store_true", help="Do not recreate an empty SQLite database after reset.")
    args = parser.parse_args()

    settings = load_settings()
    data_dir = settings.data_dir
    export_dir = settings.export_dir
    db_path = settings.db_path

    print(f"data_dir={data_dir}")
    print(f"db_path={db_path}")
    print(f"export_dir={export_dir}")

    if export_dir != data_dir / "exports" and not export_dir.is_relative_to(data_dir):
        print("note=export_dir is outside data_dir and will not be deleted by this command")
    if db_path != data_dir / "forum.db" and not db_path.is_relative_to(data_dir):
        print("note=db_path is outside data_dir and will not be deleted by this command")

    existing = sorted(data_dir.iterdir()) if data_dir.exists() else []
    print(f"entries_in_data_dir={len(existing)}")
    for child in existing:
        print(f"would_remove={child}")

    if args.dry_run:
        print("dry_run=true")
        return
    if not args.yes:
        raise SystemExit("Refusing to delete data_dir without --yes")

    removed = reset_data_dir(data_dir=data_dir)
    print(f"removed_count={len(removed)}")
    for child in removed:
        print(f"removed={child}")

    if args.no_reinit_db:
        print("db_reinitialized=false")
        return

    initialize_empty_database(db_path)
    print(f"db_reinitialized=true")
    print(f"db_created={db_path}")


if __name__ == "__main__":
    main()
