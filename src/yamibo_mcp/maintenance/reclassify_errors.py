from __future__ import annotations

import argparse
from collections import Counter

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.errors import reclassify_error_code

_OLD_CODES = {"RemoteFetchError", "UnexpectedPageError", "LoginRequiredError"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将历史失败任务的 error_code 从异常类名重新分类为结构化代码。"
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览变更，不写入数据库")
    args = parser.parse_args()

    settings = load_settings()
    conn = connect(settings)
    try:
        rows = conn.execute(
            "SELECT job_id, error_code, error_message FROM jobs WHERE status = 'failed' AND error_code IS NOT NULL"
        ).fetchall()

        to_update: list[tuple[str, str, str]] = []  # (job_id, old_code, new_code)
        skipped = 0

        for row in rows:
            old_code = str(row["error_code"])
            new_code = reclassify_error_code(old_code, row["error_message"])
            if new_code != old_code:
                to_update.append((str(row["job_id"]), old_code, new_code))
            else:
                skipped += 1

        if not to_update:
            print("所有失败任务的 error_code 已是最新分类，无需更新。")
            return

        changes = Counter(f"{old} -> {new}" for _, old, new in to_update)
        print(f"待更新: {len(to_update)} 条 (跳过 {skipped} 条已是细粒度或无法分类)")
        print()
        print("变更明细:")
        for change, count in changes.most_common():
            print(f"  {change:55s} {count:4d}")

        if args.dry_run:
            print(f"\n[dry-run] 未写入数据库。运行不加 --dry-run 以执行更新。")
            return

        for job_id, old_code, new_code in to_update:
            conn.execute(
                "UPDATE jobs SET error_code = ? WHERE job_id = ?",
                (new_code, job_id),
            )
        conn.commit()
        print(f"\n已更新 {len(to_update)} 条失败任务的 error_code。")

        # 打印更新后的分布
        print("\n更新后 error_code 分布:")
        dist_rows = conn.execute(
            "SELECT error_code, COUNT(*) as cnt FROM jobs WHERE status = 'failed' AND error_code IS NOT NULL GROUP BY error_code ORDER BY cnt DESC"
        ).fetchall()
        for r in dist_rows:
            print(f"  {r['error_code']:40s} {r['cnt']:5d}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
