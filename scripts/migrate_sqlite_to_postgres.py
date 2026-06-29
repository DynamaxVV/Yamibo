from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from collections.abc import Sequence
from datetime import datetime, timezone
import re
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, bindparam, create_engine, text
from pgvector.sqlalchemy import Vector

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.alembic_runner import upgrade_postgres_schema
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.rebuild_search_indexes import rebuild_search_indexes

logger = logging.getLogger(__name__)

_TABLE_ORDER: tuple[tuple[str, str | None], ...] = (
    ("forums", None),
    ("series", "series_id"),
    ("threads", None),
    ("title_parse", None),
    ("jobs", None),
    ("job_events", "event_id"),
    ("audit_events", None),
    ("content_blocks", "id"),
    ("assets", None),
    ("floors", None),
    ("catalog", "id"),
    ("sync_runs", None),
    ("rag_index_meta", None),
    ("rag_chunks", "id"),
)

_JSON_COLUMNS = {
    "payload_json",
    "artifacts_json",
    "before_json",
    "after_json",
    "alias_keys_json",
    "aliases_json",
    "validation_errors_json",
    "missing_images_json",
    "title_aliases_json",
    "tags_json",
    "warnings_json",
    "errors_json",
    "metadata_json",
}

_BOOLEAN_COLUMNS = {
    "enabled",
    "needs_review",
    "is_finished",
    "exportable",
    "required",
    "has_images",
    "needs_title_review",
    "needs_series_review",
    "is_exported",
    "resumable",
}

_DATETIME_COLUMNS = {
    "applied_at",
    "heartbeat_at",
    "lease_until",
    "cancel_requested_at",
    "paused_at",
    "created_at",
    "updated_at",
    "finished_at",
    "pub_time",
    "sync_time",
    "indexed_at",
}

_SEQUENCE_COLUMNS = (
    ("series", "series_id"),
    ("content_blocks", "id"),
    ("job_events", "event_id"),
    ("catalog", "id"),
    ("rag_chunks", "id"),
)

_VECTOR_UPDATE_BATCH_SIZE = 1000


def _sqlite_path(value: str | None, *, default: Path) -> Path:
    if value in {None, ""}:
        return default
    return Path(value).expanduser()


def _parse_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text_value = str(value).strip()
    if not text_value:
        return None
    if text_value.endswith("Z"):
        text_value = text_value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text_value)
    except ValueError:
        parsed = _parse_loose_datetime(text_value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


_LOOSE_DATETIME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{1,2}-\d{1,2})(?:[ T](?P<time>\d{1,2}:\d{1,2}(?::\d{1,2}(?:\.\d+)?)?))?(?P<tz>[+-]\d{2}:?\d{2})?$"
)


def _parse_loose_datetime(text_value: str) -> datetime:
    match = _LOOSE_DATETIME_RE.match(text_value)
    if not match:
        raise ValueError(f"Invalid isoformat string: {text_value!r}")
    year_str, month_str, day_str = match.group("date").split("-")
    parts = [int(year_str), int(month_str), int(day_str)]
    time_part = match.group("time") or "00:00:00"
    time_fields = time_part.split(":")
    hour = int(time_fields[0])
    minute = int(time_fields[1])
    second = 0
    microsecond = 0
    if len(time_fields) >= 3:
        second_part = time_fields[2]
        if "." in second_part:
            second_text, micro_text = second_part.split(".", 1)
            second = int(second_text)
            microsecond = int((micro_text + "000000")[:6])
        else:
            second = int(second_part)
    tz_part = match.group("tz")
    if tz_part is None:
        return datetime(*parts, hour, minute, second, microsecond)
    if tz_part == "Z":
        tz_part = "+00:00"
    if ":" not in tz_part:
        tz_part = f"{tz_part[:3]}:{tz_part[3:]}"
    return datetime.fromisoformat(
        f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}T{hour:02d}:{minute:02d}:{second:02d}"
        f"{f'.{microsecond:06d}' if microsecond else ''}{tz_part}"
    )


def _parse_json(value: Any) -> Any:
    if value in {None, ""}:
        return None
    if isinstance(value, (dict, list)):
        return _sanitize_json_value(value)
    return _sanitize_json_value(json.loads(str(value).replace("\x00", "")))


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_json_value(item) for key, item in value.items()}
    return value


def _coerce_scalar(column_name: str, value: Any) -> Any:
    if value is None:
        return None
    if column_name in _JSON_COLUMNS:
        return _parse_json(value)
    if column_name in _BOOLEAN_COLUMNS:
        return bool(value)
    if column_name in _DATETIME_COLUMNS:
        return _parse_datetime(value)
    if isinstance(value, str):
        return value.replace("\x00", "")
    return value


def _coerce_row(table: Table, row: sqlite3.Row) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for column in table.columns:
        if column.name not in row.keys():
            continue
        value = row[column.name]
        if column.name == "embedding" and value is not None:
            payload[column.name] = _coerce_vector(value)
        else:
            payload[column.name] = _coerce_scalar(column.name, value)
    return payload


def _coerce_vector(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, list):
        return [float(item) for item in value]
    if isinstance(value, tuple):
        return [float(item) for item in value]
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        if len(value) % 4 != 0:
            raise ValueError(f"embedding blob length {len(value)} is not divisible by 4")
        import struct

        return list(struct.unpack(f"{len(value) // 4}f", value))
    return [float(item) for item in value]


def _source_columns(source: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in source.execute(f'PRAGMA table_info("{table}")')]


def _table_exists(source: sqlite3.Connection, table: str) -> bool:
    row = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _read_source_vectors(source: sqlite3.Connection) -> list[tuple[int, list[float]]]:
    if not _table_exists(source, "rag_chunk_vec"):
        return []
    try:
        import sqlite_vec

        source.enable_load_extension(True)
        sqlite_vec.load(source)
    except Exception:
        pass
    finally:
        try:
            source.enable_load_extension(False)
        except Exception:
            pass
    try:
        rows = source.execute("SELECT rowid, embedding FROM rag_chunk_vec ORDER BY rowid").fetchall()
    except sqlite3.OperationalError as exc:
        if "no such module: vec0" in str(exc):
            return []
        raise
    return [(int(row[0]), _coerce_vector(row[1])) for row in rows if row[1] is not None]


def _missing_job_ids(source: sqlite3.Connection) -> list[str]:
    rows = source.execute(
        """
        SELECT DISTINCT e.job_id
        FROM job_events e
        LEFT JOIN jobs j ON j.job_id = e.job_id
        WHERE j.job_id IS NULL
        ORDER BY e.job_id
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def _repair_missing_jobs(source: sqlite3.Connection, target_conn: DatabaseConnection, *, schema: str) -> int:
    missing_job_ids = _missing_job_ids(source)
    if not missing_job_ids:
        return 0

    repaired_rows: list[dict[str, Any]] = []
    for job_id in missing_job_ids:
        events = source.execute(
            """
            SELECT event_type, status, stage, payload_json, created_at
            FROM job_events
            WHERE job_id = ?
            ORDER BY event_id
            """,
            (job_id,),
        ).fetchall()
        if not events:
            continue
        first_event = events[0]
        last_event = events[-1]
        created_payload = _parse_json(first_event[3]) or {}
        last_payload = _parse_json(last_event[3]) or {}
        worker_id = None
        if isinstance(last_payload, dict):
            worker_id = last_payload.get("worker_id")
        if worker_id is None and len(events) > 1:
            for event in events:
                payload = _parse_json(event[3]) or {}
                if isinstance(payload, dict) and payload.get("worker_id"):
                    worker_id = payload["worker_id"]
                    break
        job_type = "_".join(job_id.split("_")[:2]) if "_" in job_id else job_id
        created_at = _parse_datetime(first_event[4])
        updated_at = _parse_datetime(last_event[4]) or created_at
        artifacts = {}
        if isinstance(last_payload, dict) and "artifacts" in last_payload:
            artifacts = last_payload.get("artifacts") or {}
        repaired_rows.append(
            {
                "job_id": job_id,
                "parent_job_id": None,
                "job_type": job_type,
                "tid": created_payload.get("tid") if isinstance(created_payload, dict) else None,
                "payload_json": json.dumps(created_payload if isinstance(created_payload, dict) else {}, ensure_ascii=False),
                "status": last_event[1] or "succeeded",
                "stage": last_event[2],
                "progress_current": int(last_payload.get("progress_current", 0)) if isinstance(last_payload, dict) else 0,
                "progress_total": last_payload.get("progress_total") if isinstance(last_payload, dict) else None,
                "worker_id": worker_id,
                "heartbeat_at": None,
                "lease_until": None,
                "retry_count": 0,
                "max_retries": 3,
                "resumable": True,
                "cancel_requested_at": None,
                "paused_at": None,
                "error_code": None,
                "error_message": None,
                "artifacts_json": json.dumps(artifacts, ensure_ascii=False),
                "created_at": created_at,
                "updated_at": updated_at,
                "finished_at": _parse_datetime(last_event[4]) if last_event[1] in {"succeeded", "failed", "cancelled"} else None,
            }
        )

    if repaired_rows:
        target_conn.execute(
            text(
                f"""
                INSERT INTO "{schema}"."jobs" (
                  job_id, parent_job_id, job_type, tid, payload_json, status, stage,
                  progress_current, progress_total, worker_id, heartbeat_at, lease_until,
                  retry_count, max_retries, resumable, cancel_requested_at, paused_at,
                  error_code, error_message, artifacts_json, created_at, updated_at, finished_at
                ) VALUES (
                  :job_id, :parent_job_id, :job_type, :tid, :payload_json, :status, :stage,
                  :progress_current, :progress_total, :worker_id, :heartbeat_at, :lease_until,
                  :retry_count, :max_retries, :resumable, :cancel_requested_at, :paused_at,
                  :error_code, :error_message, :artifacts_json, :created_at, :updated_at, :finished_at
                )
                """
            ),
            repaired_rows,
        )
    return len(repaired_rows)


def _table_rows(source: sqlite3.Connection, table: str, columns: Sequence[str]) -> list[sqlite3.Row]:
    if not columns:
        return []
    quoted = ", ".join(f'"{column}"' for column in columns)
    return list(source.execute(f'SELECT {quoted} FROM "{table}" ORDER BY 1'))


def _truncate_target(target_conn: DatabaseConnection, schema: str, tables: Sequence[str]) -> None:
    qualified = ", ".join(f'"{schema}"."{table}"' for table in tables)
    target_conn.execute(f"TRUNCATE {qualified} RESTART IDENTITY CASCADE")


def _reflect_table(target_conn: DatabaseConnection, schema: str, table_name: str) -> Table:
    metadata = MetaData(schema=schema)
    return Table(table_name, metadata, autoload_with=target_conn.raw_connection, schema=schema)


def _copy_table(
    source: sqlite3.Connection,
    target_conn: DatabaseConnection,
    *,
    schema: str,
    table_name: str,
) -> int:
    if not _table_exists(source, table_name):
        return 0
    table = _reflect_table(target_conn, schema, table_name)
    source_columns = _source_columns(source, table_name)
    target_columns = [column.name for column in table.columns]
    copy_columns = [column for column in target_columns if column in source_columns and column != "embedding"]
    rows = _table_rows(source, table_name, copy_columns)
    if not rows:
        return 0

    payload = []
    for row in rows:
        payload.append(_coerce_row(table, row))

    target_conn.execute(table.insert(), payload)
    return len(payload)


def _update_embeddings(
    target_conn: DatabaseConnection,
    *,
    schema: str,
    vector_rows: list[tuple[int, list[float]]],
) -> int:
    if not vector_rows:
        return 0
    updated = 0
    for start in range(0, len(vector_rows), _VECTOR_UPDATE_BATCH_SIZE):
        batch = vector_rows[start : start + _VECTOR_UPDATE_BATCH_SIZE]
        dimensions = len(batch[0][1])
        values_sql: list[str] = []
        params: dict[str, Any] = {}
        for index, (row_id, embedding) in enumerate(batch):
            row_key = f"row_id_{index}"
            embedding_key = f"embedding_{index}"
            values_sql.append(f"(:{row_key}, CAST(:{embedding_key} AS vector({dimensions})))")
            params[row_key] = row_id
            params[embedding_key] = embedding
        stmt = text(
            f"""
            UPDATE "{schema}"."rag_chunks" AS c
            SET embedding = v.embedding,
                updated_at = CURRENT_TIMESTAMP
            FROM (VALUES {', '.join(values_sql)}) AS v(row_id, embedding)
            WHERE c.id = v.row_id
            """
        )
        target_conn.execute(stmt, params)
        updated += len(batch)
    return updated


def _sync_sequences(target_conn: DatabaseConnection, *, schema: str) -> None:
    for table_name, column_name in _SEQUENCE_COLUMNS:
        row = target_conn.execute(
            text(
                f'SELECT pg_get_serial_sequence(:qualified_table, :column_name) AS sequence_name, '
                f'MAX("{column_name}") AS max_id FROM "{schema}"."{table_name}"'
            ),
            {"qualified_table": f"{schema}.{table_name}", "column_name": column_name},
        ).fetchone()
        if row is None:
            continue
        sequence_name = row["sequence_name"]
        max_id = row["max_id"]
        if not sequence_name or max_id is None:
            continue
        target_conn.execute(
            text("SELECT setval(:sequence_name, :max_id, true)"),
            {"sequence_name": sequence_name, "max_id": int(max_id)},
        )


def migrate_sqlite_to_postgres(*, source_db: Path, target_db_url: str, schema: str, resume: bool = False) -> dict[str, Any]:
    start = time.monotonic()
    logger.info("开始迁移 SQLite -> PostgreSQL，source=%s schema=%s resume=%s", source_db, schema, resume)
    source = None
    if not resume:
        source = sqlite3.connect(str(source_db))
        source.row_factory = sqlite3.Row
    engine = create_engine(target_db_url)
    copied_rows: dict[str, int] = {}
    row_counts: dict[str, int] = {}
    try:
        with engine.connect() as raw_target:
            target_conn = DatabaseConnection(raw_target, backend="postgres")
            logger.info("准备目标 schema")
            target_conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            upgrade_postgres_schema(target_conn, schema=schema)
            raw_target.commit()

            if not resume:
                assert source is not None
                with raw_target.begin():
                    logger.info("清空目标表并重建数据")
                    _truncate_target(target_conn, schema, [table for table, _ in reversed(_TABLE_ORDER)])

                    for table_name, _pk_column in _TABLE_ORDER:
                        logger.info("迁移表 %s", table_name)
                        if table_name == "job_events":
                            repaired = _repair_missing_jobs(source, target_conn, schema=schema)
                            if repaired:
                                copied_rows["jobs_repaired"] = repaired
                                logger.info("修复孤儿 jobs 记录: %s", repaired)
                        copied = _copy_table(source, target_conn, schema=schema, table_name=table_name)
                        copied_rows[table_name] = copied
                        logger.info("完成表 %s，写入 %s 行", table_name, copied)

                    vector_rows = _read_source_vectors(source)
                    if vector_rows:
                        logger.info("回填 rag_chunks.embedding，共 %s 条向量", len(vector_rows))
                        _update_embeddings(target_conn, schema=schema, vector_rows=vector_rows)
                        copied_rows["rag_chunk_vec"] = len(vector_rows)
                    else:
                        logger.info("未发现 rag_chunk_vec，跳过向量回填")

                    _sync_sequences(target_conn, schema=schema)
                    logger.info("已同步自增序列")
            else:
                logger.info("跳过源库复制，直接从已存在的目标库继续后续步骤")

            logger.info("重建搜索索引")
            rebuild_report = rebuild_search_indexes(target_conn)
            if rebuild_report.get("overlong_thread_count"):
                logger.warning("发现 %s 条超过 1MB 的贴子，已记录在报告中", rebuild_report["overlong_thread_count"])

            with raw_target.begin():
                logger.info("执行 ANALYZE")
                target_conn.execute(text(f'ANALYZE "{schema}".threads'))
                target_conn.execute(text(f'ANALYZE "{schema}".rag_chunks'))

            for table_name, _ in _TABLE_ORDER:
                row_counts[table_name] = int(
                    target_conn.execute(text(f'SELECT COUNT(*) AS c FROM "{schema}"."{table_name}"')).scalar_one()
                )
    finally:
        if source is not None:
            source.close()
        engine.dispose()

    elapsed_seconds = round(time.monotonic() - start, 3)
    logger.info("迁移完成，耗时 %.3fs", elapsed_seconds)
    return {
        "source_db": str(source_db),
        "target_db_url": target_db_url,
        "schema": schema,
        "resume": resume,
        "elapsed_seconds": elapsed_seconds,
        "copied_rows": copied_rows,
        "row_counts": row_counts,
        "rebuild_report": rebuild_report if "rebuild_report" in locals() else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Yamibo data from SQLite to PostgreSQL.")
    parser.add_argument("--source-db", help="SQLite source database path.")
    parser.add_argument("--target-db-url", help="PostgreSQL target database URL.")
    parser.add_argument("--schema", help="Target PostgreSQL schema.")
    parser.add_argument("--output-json", help="Write the migration report to a file.")
    parser.add_argument("--resume", action="store_true", help="Skip the copy step and continue from the target PostgreSQL database.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    source_db = _sqlite_path(args.source_db, default=settings.db_path)
    target_db_url = args.target_db_url or settings.db_url
    if not target_db_url:
        raise SystemExit("target PostgreSQL URL is required (use --target-db-url or YAMIBO_DB_URL)")
    schema = args.schema or settings.db_schema

    report = migrate_sqlite_to_postgres(source_db=source_db, target_db_url=target_db_url, schema=schema, resume=args.resume)
    report_line = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if args.output_json:
        Path(args.output_json).expanduser().write_text(report_line + "\n", encoding="utf-8")
        logger.info("迁移报告已写入 %s", args.output_json)
    print(report_line)


if __name__ == "__main__":
    main()
