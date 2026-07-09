from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from yamibo_mcp.domain.models import ThreadSnapshot
from yamibo_mcp.storage.atomic import atomic_write_text
from yamibo_mcp.storage.paths import StoragePaths

LOG = logging.getLogger(__name__)


def write_staging_snapshot(paths: StoragePaths, job_id: str, snapshot: ThreadSnapshot) -> None:
    staging_dir = paths.staging_job_dir(job_id)
    staging_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(staging_dir / "snapshot.json", json.dumps(asdict(snapshot), ensure_ascii=False, indent=2))


def write_staging_failure(paths: StoragePaths, job_id: str, payload: dict[str, Any]) -> None:
    staging_dir = paths.staging_job_dir(job_id)
    staging_dir.mkdir(parents=True, exist_ok=True)
    # 失败报告单独落在 staging，方便排查同步为什么没有进入正式归档。
    atomic_write_text(staging_dir / "failure.json", json.dumps(payload, ensure_ascii=False, indent=2))


def write_staging_title_parse_log(paths: StoragePaths, job_id: str, payload: dict[str, Any]) -> None:
    staging_dir = paths.staging_job_dir(job_id)
    staging_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(staging_dir / "title_parse_log.json", json.dumps(payload, ensure_ascii=False, indent=2))


def remove_staging_dir(paths: StoragePaths, job_id: str) -> bool:
    """删除指定 job 的 staging 目录。materialize 成功后调用，返回是否成功删除。

    安全性：job_id 已由 staging_job_dir() 校验（拒绝 ../、/、\\），
    保证结果路径是 staging_root 的直接子目录，不会逃逸。
    """
    staging_dir = paths.staging_job_dir(job_id)
    if not staging_dir.exists():
        return False
    try:
        shutil.rmtree(staging_dir)
        return True
    except OSError:
        LOG.debug("Failed to remove staging dir %s", staging_dir, exc_info=True)
    return False


def cleanup_stale_staging(paths: StoragePaths, older_than_hours: int = 48) -> int:
    """清理超过指定小时数的 staging 目录。返回删除的目录数。

    安全边界：
    - iterdir() 只在 staging_root 内枚举，天然不会逃逸
    - 显式跳过符号链接，防止 symlink 指向外部目录
    - shutil.rmtree 内部使用 follow_symlinks=False，双重保险
    """
    staging_root = paths.staging_root
    if not staging_root.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    removed = 0
    for child in staging_root.iterdir():
        if not child.is_dir():
            continue
        if child.is_symlink():
            LOG.warning("Skipping symlink in staging root: %s", child)
            continue
        try:
            mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                shutil.rmtree(child)
                removed += 1
            except OSError:
                LOG.debug("Failed to remove stale staging dir %s", child, exc_info=True)
    if removed:
        LOG.info("Cleaned up %d stale staging dirs (older than %dh)", removed, older_than_hours)
    return removed
