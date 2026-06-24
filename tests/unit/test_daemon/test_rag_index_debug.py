from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from yamibo_mcp.daemon.handlers.rag_index import handle_rag_index
from yamibo_mcp.services.llm_client import LLMRequestError


class _FakeJobsRepo:
    def __init__(self):
        self.stages: list[tuple[str, int, int | None]] = []
        self.partial_calls: list[dict[str, object]] = []
        self.fail_calls: list[tuple[str, str]] = []
        self.conn = object()

    def update_stage(self, job_id: str, stage: str, *, progress_current: int, progress_total: int | None = None):
        self.stages.append((stage, progress_current, progress_total))

    def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int) -> None:
        pass

    def partial(self, job_id: str, artifacts: dict[str, object] | None = None) -> None:
        self.partial_calls.append({"job_id": job_id, "artifacts": artifacts})

    def fail(self, job_id: str, error_code: str, error_message: str) -> None:
        self.fail_calls.append((error_code, error_message))

    def succeed(self, job_id: str, artifacts: dict[str, object] | None = None) -> None:
        raise AssertionError("succeed should not be called in this test")


class _FakeThreadsRepoMissing:
    def __init__(self, conn):
        pass

    def get_thread(self, tid: int):
        return None

    def get_title_parse(self, tid: int):
        raise AssertionError("should not be called")

    def list_floors(self, tid: int):
        raise AssertionError("should not be called")


class _FakeThreadsRepoReady:
    def __init__(self, conn):
        pass

    def get_thread(self, tid: int):
        return {
            "tid": tid,
            "page_type": "thread_detail",
            "raw_title": "测试帖子",
            "display_title": "测试帖子",
            "publisher": "作者",
            "pub_time": "2026-01-01",
            "forum_id": 30,
            "content_kind": "comic",
            "series_id": 1,
        }

    def get_title_parse(self, tid: int):
        return {
            "group_name": "组",
            "author_guess": "作者",
            "core_title_guess": "测试帖子",
            "series_key": "测试帖子",
            "chapter_name": None,
            "chapter_title": None,
        }

    def list_floors(self, tid: int):
        return [
            {
                "pid": 1001,
                "tid": tid,
                "floor_no": 1,
                "publisher": "作者",
                "pub_time": "2026-01-01",
                "content": "第一楼内容",
                "quote_text": None,
                "reply_text": None,
            }
        ]


class _FakeChunksRepo:
    def __init__(self, conn):
        self.conn = conn
        self.status_updates: list[tuple[list[str], str]] = []

    def replace_thread_chunks(self, *, tid: int, chunks, embedding_model: str, embedding_dimensions: int):
        return [
            {
                "chunk_id": "thread:42:floor:1:part:1",
                "id": 1,
                "text": "这里有一段很长的文本，用来触发 embedding 失败路径。",
            }
        ]

    def set_embedding_status(self, chunk_ids: list[str], *, status: str, embedding_model: str | None = None, embedding_dimensions: int | None = None) -> None:
        self.status_updates.append((chunk_ids, status))

    def write_index_meta(self, mapping):
        pass


class _FakeProvider:
    model = "text-embedding-3-small"
    dimensions = 512

    def embed_texts(self, texts):
        raise LLMRequestError("embedding response missing data field: keys=['error']")


def _settings(*, debug: bool) -> SimpleNamespace:
    return SimpleNamespace(
        rag_debug_indexing=debug,
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_embedding_provider="openai",
        rag_api_key="rag-test-key",
        rag_base_url="https://api.302.ai/v1",
        rag_chunker_version="rag-chunker-v1",
    )


def test_rag_index_logs_partial_success_context(caplog):
    settings = _settings(debug=True)
    job = SimpleNamespace(job_id="rag_1", tid=42, payload={"embedding_dimensions": 512})
    repo = _FakeJobsRepo()

    with patch("yamibo_mcp.daemon.handlers.rag_index.ThreadsRepository", _FakeThreadsRepoReady), \
         patch("yamibo_mcp.daemon.handlers.rag_index.RagChunksRepository", _FakeChunksRepo), \
         patch("yamibo_mcp.daemon.handlers.rag_index.build_rag_chunks", return_value=[{"chunk_id": "c1", "id": 1, "text": "abc"}]), \
         patch("yamibo_mcp.daemon.handlers.rag_index.build_embedding_provider", return_value=_FakeProvider()):
        with caplog.at_level("WARNING"):
            handle_rag_index(repo, job, "worker-1", 60, settings)

    assert repo.partial_calls
    artifacts = repo.partial_calls[0]["artifacts"]
    assert artifacts["tid"] == 42
    assert artifacts["warning"] == "embedding failed: embedding response missing data field: keys=['error']"
    assert "[RAG-DEBUG] embedding failed:" in caplog.text
    assert "chunk_preview=" in caplog.text
    assert "exc_type='LLMRequestError'" in caplog.text


def test_rag_index_logs_missing_local_archive(caplog):
    settings = _settings(debug=True)
    job = SimpleNamespace(job_id="rag_2", tid=42, payload={"embedding_dimensions": 512})
    repo = _FakeJobsRepo()

    with patch("yamibo_mcp.daemon.handlers.rag_index.ThreadsRepository", _FakeThreadsRepoMissing):
        with caplog.at_level("WARNING"):
            handle_rag_index(repo, job, "worker-1", 60, settings)

    assert repo.fail_calls == [("LOCAL_ARCHIVE_NOT_FOUND", "Thread 42 is not archived locally")]
    assert "[RAG-DEBUG] local archive missing" in caplog.text
    assert "job_id='rag_2'" in caplog.text
    assert "tid=42" in caplog.text
