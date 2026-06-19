from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from yamibo_mcp.domain.models import ThreadSnapshot
from yamibo_mcp.storage.atomic import atomic_write_text
from yamibo_mcp.storage.paths import StoragePaths


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
