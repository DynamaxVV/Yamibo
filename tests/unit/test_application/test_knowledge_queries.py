from __future__ import annotations

import json
from unittest.mock import patch

from yamibo_mcp.application.contracts import AgentResult
from yamibo_mcp.application.knowledge_queries import get_knowledge_research


def test_get_knowledge_research_builds_stable_citation_map():
    with patch(
        "yamibo_mcp.application.knowledge_queries.load_settings",
        return_value=type("S", (), {"db_path": ":memory:", "llm_api_key": None})(),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.connect",
    ) as connect_fn, patch(
        "yamibo_mcp.application.knowledge_queries.RagChunksRepository",
    ) as repo_cls, patch(
        "yamibo_mcp.application.knowledge_queries.get_discussion_partition_trends",
        return_value=AgentResult(ok=True, data={"buckets": [{"thread_count": 2, "reply_count": 5}]}),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.get_discussion_topic_trends",
        return_value=AgentResult(ok=True, data={"topics": [{"topic_label": "百合动画"}]}),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.get_discussion_user_trends",
        return_value=AgentResult(ok=True, data={"users": [{"publisher": "alice"}]}),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.get_forum_evidence_pack",
        return_value=AgentResult(
            ok=True,
            data={
                "retrieval_mode": "rag",
                "items": [
                    {
                        "chunk_id": "thread:42:floor:1:part:1",
                        "tid": 42,
                        "pid": 42001,
                        "floor_no": 1,
                        "source_uri": "yamibo://threads/42/posts#floor=1",
                        "display_title": "测试贴",
                        "snippet": "这是证据",
                    }
                ],
                "diversity": {"max_per_tid": 3},
                "retrieval_diagnostics": {},
                "warnings": [],
            },
        ),
    ):
        connect_fn.return_value.close = lambda: None
        repo_cls.return_value.count_floor_coverage_diagnostics.return_value = {
            "rag_full_floor_policy": "all_non_empty_text_floors",
            "eligible_floor_count": 10,
            "indexed_floor_count": 10,
            "skipped_non_empty_floor_count": 0,
            "skipped_reasons": [],
        }
        result = get_knowledge_research(
            question="2014年11月发生了什么？",
            forum_id=55,
            start_date="2014-11-01",
            end_date="2014-11-30",
            intent="general_research",
            retrieval_mode="auto",
        )

    assert result.ok is True
    assert result.data is not None
    item = result.data["evidence_pack"]["items"][0]
    assert item["citation_id"] == "thread:42:floor:1:part:1"
    assert result.data["narrative"]["citation_map"][item["citation_id"]]["tid"] == 42
    assert result.data["coverage_diagnostics"]["skipped_non_empty_floor_count"] == 0


def test_get_knowledge_research_falls_back_to_local_rag_when_trend_pack_unavailable():
    with patch(
        "yamibo_mcp.application.knowledge_queries.load_settings",
        return_value=type("S", (), {"db_path": ":memory:", "llm_api_key": None})(),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.connect",
    ) as connect_fn, patch(
        "yamibo_mcp.application.knowledge_queries.RagChunksRepository",
    ) as repo_cls, patch(
        "yamibo_mcp.application.knowledge_queries.get_discussion_partition_trends",
        return_value=AgentResult(ok=False, error=type("E", (), {"code": "DISCUSSION_TREND_NO_CURRENT_RUN"})()),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.get_forum_evidence_pack",
        return_value=AgentResult(ok=False, error=type("E", (), {"code": "DISCUSSION_TREND_POSTGRES_REQUIRED"})()),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.search_archived_content",
        return_value=AgentResult(
            ok=True,
            data={
                "mode": "hybrid",
                "items": [
                    {
                        "tid": 99,
                        "pid": 99001,
                        "floor_no": 4,
                        "source_uri": "yamibo://threads/99/posts#floor=4",
                        "display_title": "备选证据",
                        "snippet": "本地RAG证据",
                    }
                ],
            },
        ),
    ):
        connect_fn.return_value.close = lambda: None
        repo_cls.return_value.count_floor_coverage_diagnostics.return_value = {
            "rag_full_floor_policy": "all_non_empty_text_floors",
            "eligible_floor_count": 5,
            "indexed_floor_count": 3,
            "skipped_non_empty_floor_count": 2,
            "skipped_reasons": [],
        }
        result = get_knowledge_research(
            question="百合动画讨论变化",
            forum_id=55,
            start_date="2014-11-01",
            end_date="2014-11-30",
            intent="general_research",
            retrieval_mode="auto",
        )

    assert result.ok is True
    assert result.data is not None
    assert result.data["trend"]["available"] is False
    assert result.data["evidence_pack"]["retrieval_mode"] == "hybrid"
    assert "TREND_CONTEXT_UNAVAILABLE" in result.data["warnings"]


def test_get_knowledge_research_infers_query_with_llm():
    with patch(
        "yamibo_mcp.application.knowledge_queries.load_settings",
        return_value=type("S", (), {"db_path": ":memory:", "llm_api_key": "sk-test", "llm_model": "gpt-test", "llm_base_url": "https://example.com/v1"})(),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.connect",
    ) as connect_fn, patch(
        "yamibo_mcp.application.knowledge_queries.RagChunksRepository",
    ) as repo_cls, patch(
        "yamibo_mcp.application.knowledge_queries.openai_compatible_chat",
        return_value={
            "content": json.dumps({
                "forum_id": 5,
                "start_date": "2014-11-01",
                "end_date": "2014-11-30",
                "intent": "trend_context",
                "retrieval_mode": "auto",
            }, ensure_ascii=False),
        },
    ), patch(
        "yamibo_mcp.application.knowledge_queries.get_discussion_partition_trends",
        return_value=AgentResult(ok=False, error=type("E", (), {"code": "DISCUSSION_TREND_NO_CURRENT_RUN"})()),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.get_forum_evidence_pack",
        return_value=AgentResult(ok=False, error=type("E", (), {"code": "DISCUSSION_TREND_POSTGRES_REQUIRED"})()),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.search_archived_content",
        return_value=AgentResult(ok=True, data={"mode": "hybrid", "items": []}),
    ):
        connect_fn.return_value.close = lambda: None
        repo_cls.return_value.count_floor_coverage_diagnostics.return_value = {
            "rag_full_floor_policy": "all_non_empty_text_floors",
            "eligible_floor_count": 1,
            "indexed_floor_count": 1,
            "skipped_non_empty_floor_count": 0,
            "skipped_reasons": [],
        }
        result = get_knowledge_research(question="2014年11月动漫区百合动画讨论有什么变化，为什么？")

    assert result.ok is True
    assert result.data is not None
    assert result.data["query"]["forum_id"] == 5
    assert result.data["query"]["start_date"] == "2014-11-01"
    assert result.data["query"]["inference_source"] == "llm"
    assert "KNOWLEDGE_QUERY_INFERRED_BY_LLM" in result.warnings


def test_get_knowledge_research_infers_query_with_heuristics_without_llm():
    with patch(
        "yamibo_mcp.application.knowledge_queries.load_settings",
        return_value=type("S", (), {"db_path": ":memory:", "llm_api_key": None})(),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.connect",
    ) as connect_fn, patch(
        "yamibo_mcp.application.knowledge_queries.RagChunksRepository",
    ) as repo_cls, patch(
        "yamibo_mcp.application.knowledge_queries.get_discussion_partition_trends",
        return_value=AgentResult(ok=False, error=type("E", (), {"code": "DISCUSSION_TREND_NO_CURRENT_RUN"})()),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.get_forum_evidence_pack",
        return_value=AgentResult(ok=False, error=type("E", (), {"code": "DISCUSSION_TREND_POSTGRES_REQUIRED"})()),
    ), patch(
        "yamibo_mcp.application.knowledge_queries.search_archived_content",
        return_value=AgentResult(ok=True, data={"mode": "hybrid", "items": []}),
    ):
        connect_fn.return_value.close = lambda: None
        repo_cls.return_value.count_floor_coverage_diagnostics.return_value = {
            "rag_full_floor_policy": "all_non_empty_text_floors",
            "eligible_floor_count": 1,
            "indexed_floor_count": 1,
            "skipped_non_empty_floor_count": 0,
            "skipped_reasons": [],
        }
        result = get_knowledge_research(question="2014年11月动漫区百合动画讨论有什么变化，为什么？")

    assert result.ok is True
    assert result.data is not None
    assert result.data["query"]["forum_id"] == 5
    assert result.data["query"]["intent"] == "trend_context"
    assert result.data["query"]["inference_source"] == "heuristic"
    assert "KNOWLEDGE_QUERY_INFERRED_BY_HEURISTICS" in result.warnings
