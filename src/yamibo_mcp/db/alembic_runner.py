from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import text

_BOOTSTRAPPED_POSTGRES_DATABASES: set[tuple[str, str]] = set()
_BOOTSTRAP_LOCK = Lock()
_BOOTSTRAP_TABLES: tuple[str, ...] = (
    "alembic_version",
    "series",
    "forums",
    "threads",
    "jobs",
    "audit_events",
    "assets",
    "content_blocks",
    "job_events",
    "floors",
    "catalog",
    "title_parse",
    "sync_runs",
    "rag_index_meta",
    "rag_chunks",
)


def upgrade_postgres_schema(connection: Any, *, schema: str = "public") -> None:
    raw_connection = getattr(connection, "raw_connection", connection)
    cache_key = (_postgres_connection_key(raw_connection), schema)
    with _BOOTSTRAP_LOCK:
        if cache_key in _BOOTSTRAPPED_POSTGRES_DATABASES:
            return

        script_location = _alembic_script_location()
        if not script_location.exists():  # pragma: no cover - defensive guard
            raise FileNotFoundError(f"Alembic script location not found: {script_location}")

        raw_connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        config = Config()
        config.set_main_option("script_location", str(script_location))
        config.attributes["connection"] = raw_connection
        config.attributes["schema"] = schema
        command.upgrade(config, "head")
        if schema != "public":
            _relocate_bootstrap_tables(raw_connection, schema)
        commit = getattr(raw_connection, "commit", None)
        if commit is not None:
            commit()
        _BOOTSTRAPPED_POSTGRES_DATABASES.add(cache_key)


def _postgres_connection_key(connection: Any) -> str:
    engine = getattr(connection, "engine", None)
    if engine is not None:
        url = getattr(engine, "url", None)
        if url is not None:
            return f"{url.render_as_string(hide_password=True)}::{id(engine)}"
    return f"connection::{id(connection)}"


def _relocate_bootstrap_tables(connection: Any, schema: str) -> None:
    for table_name in _BOOTSTRAP_TABLES:
        in_target = connection.execute(
            text(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = :schema AND table_name = :table_name
                """
            ),
            {"schema": schema, "table_name": table_name},
        ).scalar_one_or_none()
        if in_target is not None:
            continue
        in_public = connection.execute(
            text(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        ).scalar_one_or_none()
        if in_public is None:
            continue
        connection.execute(text(f'ALTER TABLE public."{table_name}" SET SCHEMA "{schema}"'))


@lru_cache(maxsize=1)
def _alembic_script_location() -> Path:
    return Path(__file__).resolve().parents[3] / "alembic"
