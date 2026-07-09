from __future__ import annotations

from alembic import op, context
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def _table_kwargs() -> dict[str, str]:
    return {"schema": _schema()}


def upgrade() -> None:
    schema = _schema()
    schema_prefix = f'"{schema}".'
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "series",
        sa.Column("series_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("canonical_title", sa.Text(), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("series_key", sa.Text(), nullable=False, unique=True),
        sa.Column("alias_keys_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("aliases_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("author_guess", sa.Text(), nullable=True),
        sa.Column("creator_key", sa.Text(), nullable=True),
        sa.Column("merge_confidence", sa.Float(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **_table_kwargs(),
    )

    op.create_table(
        "forums",
        sa.Column("forum_id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("content_kind", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("name_en", sa.Text(), nullable=True),
        **_table_kwargs(),
    )

    op.create_table(
        "threads",
        sa.Column("tid", sa.Integer(), primary_key=True),
        sa.Column("series_id", sa.Integer(), sa.ForeignKey(f"{schema}.series.series_id" if schema else "series.series_id"), nullable=True),
        sa.Column("page_type", sa.Text(), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("raw_title", sa.Text(), nullable=False),
        sa.Column("display_title", sa.Text(), nullable=True),
        sa.Column("content_preview", sa.Text(), nullable=True),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("publisher_uid", sa.Text(), nullable=True),
        sa.Column("pub_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pid", sa.Integer(), nullable=True),
        sa.Column("permission", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_finished", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("image_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("context_path", sa.Text(), nullable=True),
        sa.Column("archive_status", sa.Text(), nullable=False, server_default=sa.text("'stale'")),
        sa.Column("validation_status", sa.Text(), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("validation_errors_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("missing_images_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("needs_title_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("needs_series_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_exported", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("export_path", sa.Text(), nullable=True),
        sa.Column("forum_id", sa.Integer(), nullable=True),
        sa.Column("content_kind", sa.Text(), nullable=True),
        sa.Column("primary_media_type", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        **_table_kwargs(),
    )

    op.create_table(
        "jobs",
        sa.Column("job_id", sa.Text(), primary_key=True),
        sa.Column("parent_job_id", sa.Text(), nullable=True),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("tid", sa.Integer(), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("progress_current", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("resumable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("artifacts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        **_table_kwargs(),
    )

    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("before_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **_table_kwargs(),
    )

    op.create_table(
        "assets",
        sa.Column("asset_id", sa.Text(), primary_key=True),
        sa.Column("tid", sa.Integer(), sa.ForeignKey(f"{schema}.threads.tid" if schema else "threads.tid"), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("asset_type", sa.Text(), nullable=False),
        sa.Column("remote_url", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("exportable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.Text(), nullable=False),
        **_table_kwargs(),
    )

    op.create_table(
        "content_blocks",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("tid", sa.Integer(), sa.ForeignKey(f"{schema}.threads.tid" if schema else "threads.tid"), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("asset_id", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        **_table_kwargs(),
    )

    op.create_table(
        "job_events",
        sa.Column("event_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("job_id", sa.Text(), sa.ForeignKey(f"{schema}.jobs.job_id" if schema else "jobs.job_id"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **_table_kwargs(),
    )

    op.create_table(
        "floors",
        sa.Column("pid", sa.Integer(), primary_key=True),
        sa.Column("tid", sa.Integer(), sa.ForeignKey(f"{schema}.threads.tid" if schema else "threads.tid"), nullable=False),
        sa.Column("floor_no", sa.Integer(), nullable=False),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("publisher_uid", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("pub_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("has_images", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("quote_text", sa.Text(), nullable=True),
        sa.Column("reply_text", sa.Text(), nullable=True),
        **_table_kwargs(),
    )

    op.create_table(
        "catalog",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("tid", sa.Integer(), sa.ForeignKey(f"{schema}.threads.tid" if schema else "threads.tid"), nullable=False),
        sa.Column("chapter_name", sa.Text(), nullable=True),
        sa.Column("target_tid", sa.Integer(), nullable=True),
        sa.Column("target_url", sa.Text(), nullable=True),
        sa.Column("resolve_status", sa.Text(), nullable=False, server_default=sa.text("'resolved'")),
        sa.UniqueConstraint("tid", "target_tid", "chapter_name", name="idx_catalog_unique"),
        **_table_kwargs(),
    )

    op.create_table(
        "title_parse",
        sa.Column("tid", sa.Integer(), sa.ForeignKey(f"{schema}.threads.tid" if schema else "threads.tid"), primary_key=True),
        sa.Column("raw_title", sa.Text(), nullable=False),
        sa.Column("display_title", sa.Text(), nullable=True),
        sa.Column("group_name", sa.Text(), nullable=True),
        sa.Column("author_guess", sa.Text(), nullable=True),
        sa.Column("core_title_guess", sa.Text(), nullable=True),
        sa.Column("normalized_core_title", sa.Text(), nullable=True),
        sa.Column("series_key", sa.Text(), nullable=True),
        sa.Column("title_aliases_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("chapter_name", sa.Text(), nullable=True),
        sa.Column("chapter_index", sa.Float(), nullable=True),
        sa.Column("chapter_index_end", sa.Float(), nullable=True),
        sa.Column("chapter_title", sa.Text(), nullable=True),
        sa.Column("subtitle", sa.Text(), nullable=True),
        sa.Column("tags_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        **_table_kwargs(),
    )

    op.create_table(
        "sync_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("tid", sa.Integer(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("floors_added", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("images_added", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pages_fetched", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("validation_status", sa.Text(), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("errors_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **_table_kwargs(),
    )

    op.create_table(
        "rag_index_meta",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        **_table_kwargs(),
    )

    op.create_table(
        "rag_chunks",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("chunk_id", sa.Text(), nullable=False, unique=True),
        sa.Column("tid", sa.Integer(), sa.ForeignKey(f"{schema}.threads.tid" if schema else "threads.tid"), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("floor_no", sa.Integer(), nullable=True),
        sa.Column("chunk_type", sa.Text(), nullable=False),
        sa.Column("forum_id", sa.Integer(), nullable=True),
        sa.Column("content_kind", sa.Text(), nullable=True),
        sa.Column("series_id", sa.Integer(), nullable=True),
        sa.Column("series_key", sa.Text(), nullable=True),
        sa.Column("chapter_index", sa.Float(), nullable=True),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("pub_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("metadata_text", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("source_tid", sa.Integer(), nullable=True),
        sa.Column("source_pid", sa.Integer(), nullable=True),
        sa.Column("source_floor_no", sa.Integer(), nullable=True),
        sa.Column("cleaner_version", sa.Text(), nullable=True),
        sa.Column("chunker_version", sa.Text(), nullable=True),
        sa.Column("materializer_version", sa.Text(), nullable=True),
        sa.Column("source_hash", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.Text(), nullable=True),
        sa.Column("quality_flags", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(512), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=True),
        sa.Column("embedding_status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **_table_kwargs(),
    )

    op.create_index("idx_jobs_status_lease", "jobs", ["status", "lease_until"], schema=schema)
    op.create_index("idx_jobs_tid_type", "jobs", ["tid", "job_type"], schema=schema)
    op.create_index("idx_jobs_updated_at", "jobs", ["updated_at"], schema=schema)
    op.create_index("idx_jobs_parent_created", "jobs", ["parent_job_id", "created_at"], schema=schema)
    op.create_index("idx_threads_series_id", "threads", ["series_id"], schema=schema)
    op.create_index("idx_threads_archive_status", "threads", ["archive_status"], schema=schema)
    op.create_index("idx_threads_sync_time", "threads", ["sync_time"], schema=schema)
    op.create_index("idx_threads_search_vector", "threads", ["search_vector"], schema=schema, postgresql_using="gin")
    op.create_index("idx_threads_raw_title_trgm", "threads", ["raw_title"], schema=schema, postgresql_using="gin", postgresql_ops={"raw_title": "gin_trgm_ops"})
    op.create_index("idx_threads_display_title_trgm", "threads", ["display_title"], schema=schema, postgresql_using="gin", postgresql_ops={"display_title": "gin_trgm_ops"})
    op.create_index("idx_threads_content_preview_trgm", "threads", ["content_preview"], schema=schema, postgresql_using="gin", postgresql_ops={"content_preview": "gin_trgm_ops"})
    op.create_index("idx_threads_publisher_trgm", "threads", ["publisher"], schema=schema, postgresql_using="gin", postgresql_ops={"publisher": "gin_trgm_ops"})
    op.create_index("idx_content_blocks_tid_order", "content_blocks", ["tid", "order_index"], schema=schema)
    op.create_index("idx_assets_tid", "assets", ["tid"], schema=schema)
    op.create_index("idx_job_events_job_id_event_id", "job_events", ["job_id", "event_id"], schema=schema)
    op.create_index("idx_job_events_created_at", "job_events", ["created_at"], schema=schema)
    op.create_index("idx_floors_tid_floor", "floors", ["tid", "floor_no"], schema=schema)
    op.create_index("idx_rag_chunks_tid", "rag_chunks", ["tid"], schema=schema)
    op.create_index("idx_rag_chunks_pid", "rag_chunks", ["pid"], schema=schema)
    op.create_index("idx_rag_chunks_series", "rag_chunks", ["series_id"], schema=schema)
    op.create_index("idx_rag_chunks_forum_kind", "rag_chunks", ["forum_id", "content_kind"], schema=schema)
    op.create_index("idx_rag_chunks_embedding_status", "rag_chunks", ["embedding_status"], schema=schema)
    op.create_index("idx_rag_chunks_embedding_hnsw", "rag_chunks", ["embedding"], schema=schema, postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"})

    op.execute(
        sa.text(
            f"""
            INSERT INTO {schema_prefix}forums (forum_id, name, content_kind, base_url, enabled, name_en)
            VALUES
              (30, '漫画区', 'comic', 'https://bbs.yamibo.com', true, 'Comic'),
              (55, '轻小说区', 'novel', 'https://bbs.yamibo.com', true, 'Novel'),
              (5, '动漫区', 'discussion', 'https://bbs.yamibo.com', true, 'Anime'),
              (33, '海域区', 'discussion', 'https://bbs.yamibo.com', true, 'Watercooler'),
              (13, '贴图区', 'discussion', 'https://bbs.yamibo.com', true, 'Image Board'),
              (16, '管理版', 'discussion', 'https://bbs.yamibo.com', true, 'Admin'),
              (19, '资源交流区', 'discussion', 'https://bbs.yamibo.com', true, 'Resources'),
              (44, '游戏区', 'discussion', 'https://bbs.yamibo.com', true, 'Games'),
              (49, '文学区', 'discussion', 'https://bbs.yamibo.com', true, 'Literature'),
              (370, '使用指南', 'discussion', 'https://bbs.yamibo.com', true, 'Guide'),
              (379, '影视区', 'discussion', 'https://bbs.yamibo.com', true, 'Film & TV')
            ON CONFLICT (forum_id) DO UPDATE
            SET name = EXCLUDED.name,
                content_kind = EXCLUDED.content_kind,
                base_url = EXCLUDED.base_url,
                enabled = EXCLUDED.enabled,
                name_en = EXCLUDED.name_en
            """
        )
    )


def downgrade() -> None:
    schema = _schema()
    for table in [
        "rag_chunks",
        "rag_index_meta",
        "sync_runs",
        "title_parse",
        "catalog",
        "floors",
        "job_events",
        "content_blocks",
        "assets",
        "audit_events",
        "jobs",
        "threads",
        "forums",
        "series",
    ]:
        op.drop_table(table, schema=schema)
