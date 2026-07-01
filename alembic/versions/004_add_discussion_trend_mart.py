from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004_add_discussion_trend_mart"
down_revision = "003_add_jobs_parent_created_index"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def _table_kwargs() -> dict[str, str]:
    return {"schema": _schema()}


def upgrade() -> None:
    schema = _schema()
    schema_prefix = f'"{schema}".'

    op.create_table(
        "discussion_index_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("forum_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["forum_id"], [f"{schema}.forums.forum_id"], name="fk_discussion_index_runs_forum_id_forums"),
        sa.CheckConstraint("start_date <= end_date", name="ck_discussion_index_runs_window_order"),
        **_table_kwargs(),
    )

    op.create_table(
        "discussion_current_indexes",
        sa.Column("forum_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("current_run_id", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("forum_id", "start_date", "end_date", "version", name="pk_discussion_current_indexes"),
        sa.ForeignKeyConstraint(["current_run_id"], [f"{schema}.discussion_index_runs.run_id"], name="fk_discussion_current_indexes_current_run_id_runs"),
        **_table_kwargs(),
    )

    op.create_table(
        "discussion_topics",
        sa.Column("topic_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("forum_id", sa.Integer(), nullable=False),
        sa.Column("topic_key", sa.Text(), nullable=False),
        sa.Column("topic_label", sa.Text(), nullable=False),
        sa.Column("topic_kind", sa.Text(), nullable=False),
        sa.Column("topic_source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(8, 4), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["run_id"], [f"{schema}.discussion_index_runs.run_id"], name="fk_discussion_topics_run_id_runs"),
        sa.UniqueConstraint("run_id", "topic_key", name="uq_discussion_topics_run_topic_key"),
        sa.UniqueConstraint("run_id", "topic_label", name="uq_discussion_topics_run_topic_label"),
        **_table_kwargs(),
    )

    op.create_table(
        "discussion_topic_assignments",
        sa.Column("assignment_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("forum_id", sa.Integer(), nullable=False),
        sa.Column("tid", sa.Integer(), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("floor_no", sa.Integer(), nullable=True),
        sa.Column("bucket_date", sa.Date(), nullable=True),
        sa.Column("assignment_score", sa.Numeric(8, 4), nullable=False),
        sa.Column("assignment_source", sa.Text(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["run_id"], [f"{schema}.discussion_index_runs.run_id"], name="fk_discussion_topic_assignments_run_id_runs"),
        sa.ForeignKeyConstraint(["topic_id"], [f"{schema}.discussion_topics.topic_id"], name="fk_discussion_topic_assignments_topic_id_topics"),
        sa.UniqueConstraint("run_id", "topic_id", "tid", "pid", "floor_no", name="uq_discussion_topic_assignments_unit"),
        **_table_kwargs(),
    )

    op.create_table(
        "discussion_partition_daily",
        sa.Column("partition_daily_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("forum_id", sa.Integer(), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("thread_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("post_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("active_user_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("new_thread_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["run_id"], [f"{schema}.discussion_index_runs.run_id"], name="fk_discussion_partition_daily_run_id_runs"),
        sa.UniqueConstraint("run_id", "forum_id", "bucket_date", name="uq_discussion_partition_daily_bucket"),
        **_table_kwargs(),
    )

    op.create_table(
        "discussion_topic_daily",
        sa.Column("topic_daily_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("forum_id", sa.Integer(), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("thread_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("post_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("active_user_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("assignment_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["run_id"], [f"{schema}.discussion_index_runs.run_id"], name="fk_discussion_topic_daily_run_id_runs"),
        sa.ForeignKeyConstraint(["topic_id"], [f"{schema}.discussion_topics.topic_id"], name="fk_discussion_topic_daily_topic_id_topics"),
        sa.UniqueConstraint("run_id", "topic_id", "bucket_date", name="uq_discussion_topic_daily_bucket"),
        **_table_kwargs(),
    )

    op.create_table(
        "discussion_user_daily",
        sa.Column("user_daily_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("forum_id", sa.Integer(), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("user_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("thread_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("post_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("topic_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["run_id"], [f"{schema}.discussion_index_runs.run_id"], name="fk_discussion_user_daily_run_id_runs"),
        sa.UniqueConstraint("run_id", "forum_id", "bucket_date", "user_key", name="uq_discussion_user_daily_bucket"),
        **_table_kwargs(),
    )

    op.create_table(
        "discussion_report_runs",
        sa.Column("report_run_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("report_kind", sa.Text(), nullable=False),
        sa.Column("report_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("report_markdown", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("artifacts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["run_id"], [f"{schema}.discussion_index_runs.run_id"], name="fk_discussion_report_runs_run_id_runs"),
        sa.UniqueConstraint("run_id", "report_kind", name="uq_discussion_report_runs_kind"),
        **_table_kwargs(),
    )

    op.create_table(
        "discussion_rag_chunk_topics",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_id", sa.Text(), nullable=False),
        sa.Column("tid", sa.Integer(), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("floor_no", sa.Integer(), nullable=True),
        sa.Column("score", sa.Numeric(8, 4), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["run_id"], [f"{schema}.discussion_index_runs.run_id"], name="fk_discussion_rag_chunk_topics_run_id_runs"),
        sa.ForeignKeyConstraint(["topic_id"], [f"{schema}.discussion_topics.topic_id"], name="fk_discussion_rag_chunk_topics_topic_id_topics"),
        sa.UniqueConstraint("run_id", "topic_id", "chunk_id", name="uq_discussion_rag_chunk_topics_chunk"),
        **_table_kwargs(),
    )

    op.create_index("idx_discussion_index_runs_forum_window", "discussion_index_runs", ["forum_id", "start_date", "end_date", "version"], schema=schema)
    op.create_index("idx_discussion_index_runs_status", "discussion_index_runs", ["status"], schema=schema)
    op.create_index("idx_discussion_topics_run_forum", "discussion_topics", ["run_id", "forum_id"], schema=schema)
    op.create_index("idx_discussion_topic_assignments_run_topic", "discussion_topic_assignments", ["run_id", "topic_id"], schema=schema)
    op.create_index("idx_discussion_topic_daily_run_forum_date", "discussion_topic_daily", ["run_id", "forum_id", "bucket_date"], schema=schema)
    op.create_index("idx_discussion_partition_daily_run_forum_date", "discussion_partition_daily", ["run_id", "forum_id", "bucket_date"], schema=schema)
    op.create_index("idx_discussion_user_daily_run_forum_date", "discussion_user_daily", ["run_id", "forum_id", "bucket_date"], schema=schema)
    op.create_index("idx_discussion_report_runs_run_kind", "discussion_report_runs", ["run_id", "report_kind"], schema=schema)
    op.create_index("idx_discussion_rag_chunk_topics_run_topic", "discussion_rag_chunk_topics", ["run_id", "topic_id"], schema=schema)
    op.create_index("idx_discussion_rag_chunk_topics_chunk_id", "discussion_rag_chunk_topics", ["chunk_id"], schema=schema)


def downgrade() -> None:
    schema = _schema()
    for table in [
        "discussion_rag_chunk_topics",
        "discussion_report_runs",
        "discussion_user_daily",
        "discussion_topic_daily",
        "discussion_partition_daily",
        "discussion_topic_assignments",
        "discussion_topics",
        "discussion_current_indexes",
        "discussion_index_runs",
    ]:
        op.drop_table(table, schema=schema)
