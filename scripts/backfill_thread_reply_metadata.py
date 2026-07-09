from __future__ import annotations

import argparse
import json
import sys

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.threads import ThreadsRepository


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill thread reply metadata from archived floors.",
    )
    parser.add_argument(
        "--overwrite-last-reply",
        action="store_true",
        help="Overwrite existing last-reply fields with the last archived floor values.",
    )
    args = parser.parse_args()

    settings = load_settings()
    conn = connect(settings)
    try:
        migrate(conn)
        result = ThreadsRepository(conn).backfill_local_reply_metadata(
            overwrite_last_reply=args.overwrite_last_reply,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
