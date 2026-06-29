from __future__ import annotations

from sqlalchemy import bindparam, text
from pgvector.sqlalchemy import Vector

from yamibo_mcp.db.repositories.rag_vectors import RagVectorUnavailableError


class PostgresVectorRepository:
    def __init__(self, conn):
        self.conn = conn

    def ensure_schema(self, *, dimensions: int) -> str:
        row = self.conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).fetchone()
        if row is None:
            raise RagVectorUnavailableError("pgvector extension is not installed")
        current = self._read_embedding_dimensions()
        if current is not None and current != dimensions:
            raise RagVectorUnavailableError(
                f"rag_chunks.embedding is vector({current}), expected vector({dimensions})"
            )
        return str(row["extversion"])

    def reset_if_dimensions_changed(self, *, dimensions: int) -> str:
        return self.ensure_schema(dimensions=dimensions)

    def replace_embeddings(self, items: list[tuple[int, list[float]]]) -> None:
        if not items:
            return
        dimensions = len(items[0][1])
        stmt = text(
            """
            UPDATE rag_chunks
            SET embedding = :embedding,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :row_id
            """
        ).bindparams(bindparam("embedding", type_=Vector(dimensions)))
        payload = [{"row_id": row_id, "embedding": embedding} for row_id, embedding in items]
        self.conn.execute(stmt, payload)
        self.conn.commit()

    def delete_thread_embeddings(self, rowids: list[int]) -> None:
        if not rowids:
            return
        stmt = text(
            """
            UPDATE rag_chunks
            SET embedding = NULL,
                embedding_status = 'pending',
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN :rowids
            """
        ).bindparams(bindparam("rowids", expanding=True))
        self.conn.execute(stmt, {"rowids": rowids})
        self.conn.commit()

    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int,
        forum_id: int | None = None,
        content_kind: str | None = None,
        tid: int | None = None,
        series_id: int | None = None,
        floor_start: int | None = None,
        floor_end: int | None = None,
    ):
        dimensions = len(query_embedding)
        filters: list[str] = ["c.embedding IS NOT NULL"]
        params: dict[str, object] = {"query_embedding": query_embedding, "top_k": top_k}
        if forum_id is not None:
            filters.append("c.forum_id = :forum_id")
            params["forum_id"] = forum_id
        if content_kind is not None:
            filters.append("c.content_kind = :content_kind")
            params["content_kind"] = content_kind
        if tid is not None:
            filters.append("c.tid = :tid")
            params["tid"] = tid
        if series_id is not None:
            filters.append("c.series_id = :series_id")
            params["series_id"] = series_id
        if floor_start is not None:
            filters.append("(c.floor_no IS NULL OR c.floor_no >= :floor_start)")
            params["floor_start"] = floor_start
        if floor_end is not None:
            filters.append("(c.floor_no IS NULL OR c.floor_no <= :floor_end)")
            params["floor_end"] = floor_end

        stmt = (
            text(
                f"""
                SELECT c.*, (c.embedding <=> :query_embedding) AS vector_distance
                FROM rag_chunks c
                WHERE {" AND ".join(filters)}
                ORDER BY c.embedding <=> :query_embedding, c.id ASC
                LIMIT :top_k
                """
            ).bindparams(bindparam("query_embedding", type_=Vector(dimensions)))
        )
        return self.conn.execute(stmt, params).fetchall()

    def _read_embedding_dimensions(self) -> int | None:
        row = self.conn.execute(
            text(
                """
                SELECT atttypmod
                FROM pg_attribute
                WHERE attrelid = 'rag_chunks'::regclass
                  AND attname = 'embedding'
                  AND NOT attisdropped
                """
            )
        ).fetchone()
        if row is None:
            return None
        typmod = int(row["atttypmod"] or -1)
        if typmod < 0:
            return None
        return max(typmod - 4, 0)
