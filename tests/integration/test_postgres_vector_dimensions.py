import pytest

from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.postgres_vectors import PostgresVectorRepository
from yamibo_mcp.db.repositories.rag_vectors import RagVectorUnavailableError


def test_pgvector_dimensions_match_actual_column(pg_engine):
    with pg_engine.connect() as raw:
        raw.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        raw.exec_driver_sql("CREATE TEMP TABLE rag_chunks (embedding vector(512)) ON COMMIT DROP")
        repo = PostgresVectorRepository(DatabaseConnection(raw, backend="postgres"))
        assert repo._read_embedding_dimensions() == 512
        assert repo.ensure_schema(dimensions=512)
        with pytest.raises(RagVectorUnavailableError, match=r"vector\(512\), expected vector\(32\)"):
            repo.ensure_schema(dimensions=32)
