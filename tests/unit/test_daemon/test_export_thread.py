from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from yamibo_mcp.daemon.handlers.export_thread import (
    _thread_missing_image_urls,
    handle_export_thread,
)
from yamibo_mcp.daemon.handlers.image_backfill import _foreground_work_available
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.storage.paths import StoragePaths


def _settings(tmp_path: Path) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    export_dir = tmp_path / "exports"
    novel_export_dir = tmp_path / "novel_exports"
    data_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)
    novel_export_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        data_dir=data_dir,
        export_dir=export_dir,
        novel_txt_export_dir=novel_export_dir,
        export_default_strategy="sync_if_stale",
        export_stale_after_hours=24,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
    )


def _write_partial_comic_archive(settings: SimpleNamespace, tid: int, url: str) -> None:
    paths = StoragePaths(
        settings.data_dir,
        export_dir=settings.export_dir,
        novel_txt_export_dir=settings.novel_txt_export_dir,
    )
    thread_dir = paths.thread_dir(tid)
    thread_dir.mkdir(parents=True, exist_ok=True)
    paths.thread_context(tid).write_text("# comic\n", encoding="utf-8")
    paths.thread_metadata(tid).write_text(
        json.dumps(
            {
                "tid": tid,
                "missing_image_urls": [url],
                "missing_shared_image_urls": [],
                "archived_images": {},
                "non_export_images": {},
                "floors": [
                    {
                        "pid": tid * 10 + 1,
                        "floor_no": 1,
                        "remote_image_urls": [url],
                        "image_slots": [
                            {"remote_url": url, "local_path": None, "status": "missing"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_export_waits_for_first_floor_backfill_then_exports(db, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    tid = 653971
    url = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=missing"
    _write_partial_comic_archive(settings, tid, url)
    db.execute(
        """
        INSERT INTO threads (
            tid, raw_title, display_title, sync_time, archive_status,
            missing_images_json, forum_id, content_kind, context_path
        ) VALUES (?, 'comic', 'comic', '2099-01-01T00:00:00+00:00', 'partial', ?, 5, 'comic', ?)
        """,
        (tid, json.dumps([url]), f"threads/{tid}/context.md"),
    )
    db.commit()

    repo = JobsRepository(db)
    export_job = repo.create(
        "export_thread",
        tid=tid,
        payload={"tid": tid, "strategy": "sync_if_stale"},
    )
    export_job = repo.acquire(export_job.job_id, "worker-1", 300)

    def _finish_sync(repo, sync_job, *_args):
        repo.succeed(sync_job.job_id, {"fake": True})

    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.export_thread.handle_sync_thread",
        _finish_sync,
    )

    handle_export_thread(repo, export_job, "worker-1", 300, settings)

    waiting = repo.get(export_job.job_id)
    backfill = _latest_child(repo, export_job.job_id, "image_backfill")
    assert waiting.status == "retrying"
    assert waiting.error_code == "EXPORT_PRECHECK_FAILED"
    assert backfill is not None
    assert backfill.status == "queued"
    assert backfill.payload["mode"] == "reconcile_missing"
    assert backfill.payload["dry_run"] is False
    assert backfill.payload["include_first_floor"] is True
    assert not list(settings.export_dir.rglob("*.zip"))

    paths = StoragePaths(
        settings.data_dir,
        export_dir=settings.export_dir,
        novel_txt_export_dir=settings.novel_txt_export_dir,
    )
    image_path = paths.thread_images_dir(tid) / "floor_001_01.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"fake-image")
    paths.thread_metadata(tid).write_text(
        json.dumps(
            {
                "tid": tid,
                "missing_image_urls": [],
                "missing_shared_image_urls": [],
                "archived_images": {str(tid * 10 + 1): ["images/floor_001_01.jpg"]},
                "non_export_images": {},
                "floors": [
                    {
                        "pid": tid * 10 + 1,
                        "floor_no": 1,
                        "remote_image_urls": [url],
                        "image_slots": [
                            {
                                "remote_url": url,
                                "local_path": "images/floor_001_01.jpg",
                                "status": "content",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    db.execute(
        "UPDATE threads SET archive_status = 'complete', missing_images_json = '[]' WHERE tid = ?",
        (tid,),
    )
    db.commit()
    repo.succeed(backfill.job_id, {"downloaded_image_count": 1})
    db.execute(
        "UPDATE jobs SET lease_until = '2000-01-01T00:00:00+00:00' WHERE job_id = ?",
        (export_job.job_id,),
    )
    db.commit()

    retry = repo.acquire(export_job.job_id, "worker-2", 300)
    handle_export_thread(repo, retry, "worker-2", 300, settings)

    finished = repo.get(export_job.job_id)
    assert finished.status == "succeeded"
    assert finished.artifacts["export_format"] == "zip"
    assert Path(finished.artifacts["export_path"]).exists()


def test_export_missing_image_jsonb_list_is_read_without_type_error():
    url = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=1"
    row = {"missing_images_json": [url]}
    assert _thread_missing_image_urls(row) == [url]


def test_export_backfill_child_ignores_its_waiting_parent_for_foreground_gate(db):
    repo = JobsRepository(db)
    export_job = repo.create("export_thread", tid=1, payload={"strategy": "sync_if_stale"})
    backfill_job = repo.create(
        "image_backfill",
        tid=1,
        payload={"mode": "reconcile_missing", "include_first_floor": True},
        parent_job_id=export_job.job_id,
    )

    assert _foreground_work_available(repo, backfill_job.job_id) is False

    repo.create("sync_thread", tid=2)
    assert _foreground_work_available(repo, backfill_job.job_id) is True


def _latest_child(repo: JobsRepository, parent_job_id: str, job_type: str):
    row = repo.conn.execute(
        """
        SELECT job_id
        FROM jobs
        WHERE parent_job_id = ? AND job_type = ?
        ORDER BY created_at DESC, job_id DESC
        LIMIT 1
        """,
        (parent_job_id, job_type),
    ).fetchone()
    return None if row is None else repo.get(row["job_id"])
