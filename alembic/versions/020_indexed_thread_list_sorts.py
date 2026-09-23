"""Index common thread list sorts and fill legacy reply timestamps."""

from alembic import context, op


revision = "020_indexed_thread_list_sorts"
down_revision = "019_add_idle_backfill_scan_indexes"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    bind = op.get_bind()
    preparer = bind.dialect.identifier_preparer
    schema = preparer.quote_schema(_schema())

    # Preserve the existing floor-date fallback once, so normal list requests
    # can sort on the indexed thread column without probing floors per row.
    op.execute(
        f"""
        UPDATE {schema}.threads AS t
        SET remote_last_reply_at = (
            SELECT f.pub_time
            FROM {schema}.floors AS f
            WHERE f.tid = t.tid AND f.pub_time IS NOT NULL
            ORDER BY f.floor_no DESC, f.pid DESC
            LIMIT 1
        )
        WHERE t.remote_last_reply_at IS NULL
          AND EXISTS (
              SELECT 1 FROM {schema}.floors AS f
              WHERE f.tid = t.tid AND f.pub_time IS NOT NULL
          )
        """
    )

    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_threads_forum_sync_sort
        ON {schema}.threads (forum_id, sync_time DESC NULLS LAST, tid DESC)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_threads_forum_reply_sort
        ON {schema}.threads (forum_id, remote_last_reply_at DESC NULLS LAST, tid DESC)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_threads_reply_sort
        ON {schema}.threads (remote_last_reply_at DESC NULLS LAST, tid DESC)
        """
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("idx_threads_reply_sort", table_name="threads", schema=schema)
    op.drop_index("idx_threads_forum_reply_sort", table_name="threads", schema=schema)
    op.drop_index("idx_threads_forum_sync_sort", table_name="threads", schema=schema)
