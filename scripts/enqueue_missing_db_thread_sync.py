from __future__ import annotations

import argparse
import json
from pathlib import Path

from yamibo_mcp.application.archive_commands import create_thread_archive_batch_jobs
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.threads import ThreadsRepository


def _iter_missing_db_tids(*, thread_root: Path) -> list[int]:
    settings = load_settings()
    conn = connect(settings)
    try:
        repo = ThreadsRepository(conn)
        missing: list[int] = []
        for meta_path in sorted(thread_root.glob("*/metadata.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(meta, dict):
                continue
            tid_raw = meta.get("tid")
            try:
                tid = int(tid_raw)
            except (TypeError, ValueError):
                continue
            if meta.get("context_format_version") == "obsidian-md-v2":
                continue
            if repo.get_thread(tid) is not None:
                continue
            missing.append(tid)
        return missing
    finally:
        conn.close()


def _chunked(values: list[int], size: int) -> list[list[int]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enqueue sync_thread jobs in batches for thread dirs that exist on disk but are missing in DB."
    )
    parser.add_argument("--batch-size", type=int, default=100, help="Number of tids per create-sync-thread-batch-jobs call.")
    parser.add_argument("--start-batch", type=int, default=1, help="1-based batch index to start from.")
    parser.add_argument("--max-batches", type=int, default=0, help="Maximum number of batches to enqueue. 0 means all.")
    parser.add_argument("--base-url", default=None, help="Optional forum base URL override.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned batches without writing jobs.")
    args = parser.parse_args()

    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be > 0")
    if args.start_batch <= 0:
        raise SystemExit("--start-batch must be > 0")
    if args.max_batches < 0:
        raise SystemExit("--max-batches must be >= 0")

    settings = load_settings()
    tids = _iter_missing_db_tids(thread_root=settings.data_dir / "threads")
    batches = _chunked(tids, args.batch_size)
    selected_batches = batches[args.start_batch - 1:]
    if args.max_batches:
        selected_batches = selected_batches[:args.max_batches]

    print(
        json.dumps(
            {
                "missing_tid_count": len(tids),
                "batch_size": args.batch_size,
                "total_batches": len(batches),
                "selected_batch_count": len(selected_batches),
                "start_batch": args.start_batch,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )

    for batch_index, batch in enumerate(selected_batches, start=args.start_batch):
        preview = {
            "batch_index": batch_index,
            "tid_count": len(batch),
            "tid_first": batch[0],
            "tid_last": batch[-1],
            "sample_tids": batch[:10],
        }
        if args.dry_run:
            print(json.dumps(preview, ensure_ascii=False))
            continue

        result = create_thread_archive_batch_jobs(tids=batch, base_url=args.base_url)
        payload = result.data or {}
        print(
            json.dumps(
                {
                    **preview,
                    "created_count": payload.get("created_count", 0),
                    "reused_count": payload.get("reused_count", 0),
                    "created_job_ids": payload.get("created_job_ids", []),
                    "reused_job_ids": payload.get("reused_job_ids", []),
                },
                ensure_ascii=False,
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
