from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from typing import Any

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.application.discussion_trend_queries import (
    RAG_SQL_FALLBACK_WARNING,
    get_discussion_partition_trends,
    get_discussion_topic_trends,
    get_discussion_user_trends,
    get_forum_evidence_pack,
)
from yamibo_mcp.application.rag_queries import search_archived_content
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.domain.forums import default_forums
from yamibo_mcp.services.llm_client import LLMRequestError, openai_compatible_chat

DEFAULT_RETRIEVAL_MODES = {"auto", "rag", "sql"}
DEFAULT_INTENTS = {
    "trend_context",
    "topic_investigation",
    "slang_usage",
    "community_atmosphere",
    "general_research",
}
DEFAULT_FORUM_ID = 5
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def get_knowledge_research(
    *,
    question: str,
    forum_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    intent: str | None = None,
    retrieval_mode: str | None = None,
) -> AgentResult:
    if not question.strip():
        return AgentResult(
            ok=False,
            error=AgentError(
                code="INVALID_ARGUMENT",
                message="question must be a non-empty string",
                agent_hint="Provide the research question to investigate.",
            ),
        )

    settings = load_settings()
    inference = _infer_research_query(
        settings,
        question=question.strip(),
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        intent=intent,
        retrieval_mode=retrieval_mode,
    )
    if not inference.ok or inference.data is None:
        return inference

    resolved_query = inference.data
    warnings = list(inference.warnings)
    limitations: list[str] = []

    conn = connect(settings.db_path)
    try:
        coverage_diagnostics = RagChunksRepository(conn).count_floor_coverage_diagnostics()
    finally:
        conn.close()

    trend = _build_trend_payload(
        forum_id=int(resolved_query["forum_id"]),
        start_date=str(resolved_query["start_date"]),
        end_date=str(resolved_query["end_date"]),
        warnings=warnings,
        limitations=limitations,
    )
    evidence_pack = _build_evidence_pack(
        question=question.strip(),
        forum_id=int(resolved_query["forum_id"]),
        start_date=str(resolved_query["start_date"]),
        end_date=str(resolved_query["end_date"]),
        intent=str(resolved_query["intent"]),
        retrieval_mode=str(resolved_query["retrieval_mode"]),
        warnings=warnings,
        limitations=limitations,
    )
    narrative = _build_narrative(
        question=question.strip(),
        trend=trend,
        evidence_pack=evidence_pack,
        warnings=warnings,
        limitations=limitations,
        resolved_query=resolved_query,
    )

    data = {
        "query": resolved_query,
        "trend": trend,
        "evidence_pack": evidence_pack,
        "narrative": narrative,
        "coverage_diagnostics": coverage_diagnostics,
        "warnings": warnings,
        "limitations": limitations,
    }
    return AgentResult(
        ok=True,
        data=data,
        warnings=warnings,
        next_actions=[
            AgentAction(
                tool="create_forum_research_report_job",
                args={
                    "forum_id": int(resolved_query["forum_id"]),
                    "start_date": str(resolved_query["start_date"]),
                    "end_date": str(resolved_query["end_date"]),
                    "question": question.strip(),
                    "intent": str(resolved_query["intent"]),
                    "query": question.strip(),
                },
                reason="Queue a durable research report artifact if you want a persisted report job output.",
            )
        ],
    )


def _infer_research_query(
    settings,
    *,
    question: str,
    forum_id: int | None,
    start_date: str | None,
    end_date: str | None,
    intent: str | None,
    retrieval_mode: str | None,
) -> AgentResult:
    explicit = {
        "forum_id": forum_id,
        "start_date": start_date,
        "end_date": end_date,
        "intent": intent,
        "retrieval_mode": retrieval_mode,
    }
    if all(value not in {None, ""} for value in explicit.values()):
        resolved = {
            "question": question,
            "forum_id": int(forum_id),
            "start_date": str(start_date),
            "end_date": str(end_date),
            "intent": str(intent),
            "retrieval_mode": str(retrieval_mode),
            "inference_source": "explicit",
            "inferred_fields": [],
        }
        return AgentResult(ok=True, data=resolved)

    inferred_payload, inference_warnings = _infer_with_llm(settings, question=question)
    if inferred_payload is None:
        inferred_payload, heuristic_warnings = _infer_with_heuristics(question)
        inference_warnings.extend(heuristic_warnings)

    resolved_forum_id = int(forum_id) if forum_id not in {None, ""} else int(inferred_payload["forum_id"])
    resolved_start_date = str(start_date) if start_date not in {None, ""} else str(inferred_payload["start_date"])
    resolved_end_date = str(end_date) if end_date not in {None, ""} else str(inferred_payload["end_date"])
    resolved_intent = str(intent) if intent not in {None, ""} else str(inferred_payload["intent"])
    resolved_retrieval_mode = (
        str(retrieval_mode) if retrieval_mode not in {None, ""} else str(inferred_payload["retrieval_mode"])
    )

    if resolved_intent not in DEFAULT_INTENTS:
        resolved_intent = "general_research"
        inference_warnings.append("KNOWLEDGE_INTENT_DEFAULTED")
    if resolved_retrieval_mode not in DEFAULT_RETRIEVAL_MODES:
        resolved_retrieval_mode = "auto"
        inference_warnings.append("KNOWLEDGE_RETRIEVAL_MODE_DEFAULTED")

    inferred_fields = [
        key
        for key, value in {
            "forum_id": forum_id,
            "start_date": start_date,
            "end_date": end_date,
            "intent": intent,
            "retrieval_mode": retrieval_mode,
        }.items()
        if value in {None, ""}
    ]
    return AgentResult(
        ok=True,
        data={
            "question": question,
            "forum_id": resolved_forum_id,
            "start_date": resolved_start_date,
            "end_date": resolved_end_date,
            "intent": resolved_intent,
            "retrieval_mode": resolved_retrieval_mode,
            "inference_source": str(inferred_payload.get("inference_source") or "heuristic"),
            "inferred_fields": inferred_fields,
        },
        warnings=_dedupe(inference_warnings),
    )


def _infer_with_llm(settings, *, question: str) -> tuple[dict[str, Any] | None, list[str]]:
    if not settings.llm_api_key:
        return None, ["KNOWLEDGE_LLM_UNAVAILABLE_USING_HEURISTICS"]
    try:
        result = openai_compatible_chat(
            settings,
            system_prompt=_build_inference_prompt(),
            user_prompt=f"研究问题：{question}",
            temperature=0.0,
        )
        payload = _parse_json_payload(str(result["content"]))
        normalized = _normalize_inference_payload(payload)
        normalized["inference_source"] = "llm"
        return normalized, ["KNOWLEDGE_QUERY_INFERRED_BY_LLM"]
    except (LLMRequestError, OSError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None, ["KNOWLEDGE_LLM_INFERENCE_FAILED_USING_HEURISTICS"]


def _infer_with_heuristics(question: str) -> tuple[dict[str, Any], list[str]]:
    today = date.today()
    start_date, end_date = _infer_date_window(question, today=today)
    forum_id = _infer_forum_id(question)
    intent = _infer_intent(question)
    return (
        {
            "forum_id": forum_id,
            "start_date": start_date,
            "end_date": end_date,
            "intent": intent,
            "retrieval_mode": "auto",
            "inference_source": "heuristic",
        },
        ["KNOWLEDGE_QUERY_INFERRED_BY_HEURISTICS"],
    )


def _build_inference_prompt() -> str:
    forums_payload = [
        {
            "forum_id": forum.forum_id,
            "name": forum.name,
            "name_en": forum.name_en,
            "content_kind": forum.content_kind,
        }
        for forum in default_forums()
        if forum.enabled
    ]
    return (
        "你是论坛历史研究参数推断助手。"
        "根据研究问题，推断最合适的 forum_id、start_date、end_date、intent、retrieval_mode。"
        "只返回 JSON，不要 markdown，不要解释。"
        "日期必须是 YYYY-MM-DD。"
        "如果问题提到某个月，则返回该月的第一天和最后一天。"
        "如果问题只提到某一年，则返回该年的 01-01 到 12-31。"
        "如果问题未提时间，默认最近 90 天。"
        "intent 只能是 trend_context / topic_investigation / slang_usage / community_atmosphere / general_research。"
        "retrieval_mode 只能是 auto / rag / sql，默认 auto。"
        "forum_id 必须从候选论坛中选择最相关的一个。"
        f"\n候选论坛：{json.dumps(forums_payload, ensure_ascii=False)}"
    )


def _parse_json_payload(content: str) -> dict[str, object]:
    text = content.strip()
    match = _JSON_BLOCK_RE.search(text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


def _normalize_inference_payload(payload: dict[str, object]) -> dict[str, Any]:
    forum_id = int(payload.get("forum_id") or DEFAULT_FORUM_ID)
    start_date = str(payload.get("start_date") or "")
    end_date = str(payload.get("end_date") or "")
    if not _is_iso_date(start_date) or not _is_iso_date(end_date):
        fallback_start, fallback_end = _infer_date_window("", today=date.today())
        start_date = fallback_start
        end_date = fallback_end
    return {
        "forum_id": forum_id,
        "start_date": start_date,
        "end_date": end_date,
        "intent": str(payload.get("intent") or "general_research"),
        "retrieval_mode": str(payload.get("retrieval_mode") or "auto"),
    }


def _infer_forum_id(question: str) -> int:
    lowered = question.lower()
    if any(token in question for token in ("轻小说", "小说", "novel")):
        return 55
    if any(token in question for token in ("漫画", "汉化", "comic", "manga")):
        return 30
    if any(token in question for token in ("游戏", "galgame", "game")):
        return 44
    if any(token in question for token in ("电影", "影视", "tv", "anime", "动画", "番剧", "动漫")):
        return 379 if any(token in question for token in ("电影", "影视", "tv")) else 5
    if any(token in lowered for token in ("watercooler", "sea")) or "海域" in question:
        return 33
    return DEFAULT_FORUM_ID


def _infer_intent(question: str) -> str:
    lowered = question.lower()
    if any(token in question for token in ("变化", "趋势", "走向", "增长", "下降")):
        return "trend_context"
    if any(token in question for token in ("黑话", "术语", "slang", "梗")):
        return "slang_usage"
    if any(token in question for token in ("氛围", "风气", "态度", "社区")):
        return "community_atmosphere"
    if any(token in lowered for token in ("why", "investigate")) or any(token in question for token in ("为什么", "调查", "分析")):
        return "topic_investigation"
    return "general_research"


def _infer_date_window(question: str, *, today: date) -> tuple[str, str]:
    month_match = re.search(r"(?P<year>20\d{2})\s*年?\s*(?P<month>1[0-2]|0?[1-9])\s*月", question)
    if month_match:
        year = int(month_match.group("year"))
        month = int(month_match.group("month"))
        start = date(year, month, 1)
        next_month = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
        end = next_month - timedelta(days=1)
        return start.isoformat(), end.isoformat()

    year_match = re.search(r"(?P<year>20\d{2})\s*年", question)
    if year_match:
        year = int(year_match.group("year"))
        return date(year, 1, 1).isoformat(), date(year, 12, 31).isoformat()

    default_start = today - timedelta(days=89)
    return default_start.isoformat(), today.isoformat()


def _build_trend_payload(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    warnings: list[str],
    limitations: list[str],
) -> dict[str, Any]:
    partition_result = get_discussion_partition_trends(
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
    )
    if not partition_result.ok or partition_result.data is None:
        warnings.append("TREND_CONTEXT_UNAVAILABLE")
        if partition_result.error is not None:
            limitations.append(partition_result.error.code)
        return {
            "available": False,
            "partition": None,
            "topics": [],
            "users": [],
        }

    topic_result = get_discussion_topic_trends(
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        top_k=8,
    )
    user_result = get_discussion_user_trends(
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        top_k=8,
    )

    if topic_result.warnings:
        warnings.extend(code for code in topic_result.warnings if code not in warnings)

    return {
        "available": True,
        "partition": partition_result.data,
        "topics": [] if not topic_result.ok or topic_result.data is None else topic_result.data.get("topics", []),
        "users": [] if not user_result.ok or user_result.data is None else user_result.data.get("users", []),
    }


def _build_evidence_pack(
    *,
    question: str,
    forum_id: int,
    start_date: str,
    end_date: str,
    intent: str,
    retrieval_mode: str,
    warnings: list[str],
    limitations: list[str],
) -> dict[str, Any]:
    result = get_forum_evidence_pack(
        forum_id=forum_id,
        query=question,
        start_date=start_date,
        end_date=end_date,
        top_k=12,
        mode=retrieval_mode,
        intent=intent,
        require_current_run=False,
    )
    if result.ok and result.data is not None:
        raw_items = list(result.data.get("items", []))
        normalized_items = [_normalize_evidence_item(item, idx) for idx, item in enumerate(raw_items, start=1)]
        citation_map = {item["citation_id"]: _citation_ref(item) for item in normalized_items}
        warnings.extend(code for code in result.warnings if code not in warnings)
        warnings.extend(code for code in result.data.get("warnings", []) if code not in warnings)
        diagnostics = dict(result.data.get("retrieval_diagnostics", {}))
        diagnostics["citation_id_strategy"] = "stable_source_identity"
        return {
            "evidence_pack_id": _build_evidence_pack_id(forum_id, start_date, end_date, question),
            "retrieval_mode": result.data.get("retrieval_mode", retrieval_mode),
            "items": normalized_items,
            "diversity": result.data.get("diversity", {}),
            "diagnostics": diagnostics,
            "citation_map": citation_map,
        }

    if result.error is not None and result.error.code == "DISCUSSION_TREND_POSTGRES_REQUIRED":
        limitations.append(result.error.code)
        warnings.append("TREND_POSTGRES_REQUIRED")

    fallback = search_archived_content(
        query=question,
        mode="hybrid" if retrieval_mode == "auto" else retrieval_mode,
        top_k=12,
        forum_id=forum_id,
    )
    if fallback.ok and fallback.data is not None:
        warnings.append(RAG_SQL_FALLBACK_WARNING if retrieval_mode == "sql" else "KNOWLEDGE_LOCAL_RAG_FALLBACK_USED")
        normalized_items = [_normalize_evidence_item(item, idx) for idx, item in enumerate(fallback.data.get("items", []), start=1)]
        citation_map = {item["citation_id"]: _citation_ref(item) for item in normalized_items}
        return {
            "evidence_pack_id": _build_evidence_pack_id(forum_id, start_date, end_date, question),
            "retrieval_mode": fallback.data.get("mode", "hybrid"),
            "items": normalized_items,
            "diversity": {
                "max_per_tid": None,
                "total_tids": len({item["tid"] for item in normalized_items}),
            },
            "diagnostics": {
                "fallback": "local_rag_search",
                "citation_id_strategy": "stable_source_identity",
            },
            "citation_map": citation_map,
        }

    warnings.append("EVIDENCE_INSUFFICIENT")
    limitations.append("EVIDENCE_UNAVAILABLE")
    return {
        "evidence_pack_id": _build_evidence_pack_id(forum_id, start_date, end_date, question),
        "retrieval_mode": retrieval_mode,
        "items": [],
        "diversity": {},
        "diagnostics": {"fallback": "none"},
        "citation_map": {},
    }


def _build_narrative(
    *,
    question: str,
    trend: dict[str, Any],
    evidence_pack: dict[str, Any],
    warnings: list[str],
    limitations: list[str],
    resolved_query: dict[str, Any],
) -> dict[str, Any]:
    citation_map = dict(evidence_pack.get("citation_map", {}))
    items = list(evidence_pack.get("items", []))
    lines = [
        "# 研究工作区",
        "",
        f"问题：{question}",
        f"推断板块：forum_id={resolved_query['forum_id']}",
        f"推断时间窗：{resolved_query['start_date']} ~ {resolved_query['end_date']}",
        f"推断意图：{resolved_query['intent']} · 检索模式：{resolved_query['retrieval_mode']}",
        "",
    ]
    if trend.get("available") and trend.get("partition"):
        buckets = trend["partition"].get("buckets", [])
        lines.append("## Trend Mart 上下文")
        lines.append(f"- 时间桶数量：{len(buckets)}")
        if buckets:
            lines.append(f"- 总回复量：{sum(int(bucket.get('reply_count', 0) or 0) for bucket in buckets)}")
            lines.append(f"- 总帖子量：{sum(int(bucket.get('thread_count', 0) or 0) for bucket in buckets)}")
        lines.append("")
    else:
        lines.append("## Trend Mart 上下文")
        lines.append("- 当前窗口没有可用趋势索引。")
        lines.append("")

    lines.append("## 证据摘要")
    if items:
        for item in items[:5]:
            citation_id = item["citation_id"]
            snippet = str(item.get("snippet") or item.get("text") or "").strip().replace("\n", " ")
            if len(snippet) > 140:
                snippet = f"{snippet[:137]}..."
            lines.append(f"- [{citation_id}] {item.get('display_title') or item.get('title') or item.get('tid')}：{snippet}")
    else:
        lines.append("- 当前未返回可引用证据。")
    lines.append("")

    if warnings:
        lines.append("## 警告")
        for warning in _dedupe(warnings):
            lines.append(f"- {warning}")
        lines.append("")
    if limitations:
        lines.append("## 限制")
        for limitation in _dedupe(limitations):
            lines.append(f"- {limitation}")
        lines.append("")

    return {
        "format": "markdown",
        "text": "\n".join(lines).strip(),
        "citation_map": citation_map,
        "status": "ready" if items else "insufficient_evidence",
    }


def _normalize_evidence_item(item: dict[str, Any], index: int) -> dict[str, Any]:
    normalized = dict(item)
    citation_id = _build_citation_id(normalized, index)
    normalized["citation_id"] = citation_id
    normalized.setdefault("chunk_id", None)
    normalized.setdefault("pid", None)
    normalized.setdefault("floor_no", None)
    normalized.setdefault("source_uri", None)
    return normalized


def _build_citation_id(item: dict[str, Any], index: int) -> str:
    if item.get("chunk_id"):
        return str(item["chunk_id"])
    stable_parts = [
        str(item.get("tid") or ""),
        str(item.get("pid") or ""),
        str(item.get("floor_no") or ""),
        str(item.get("source_uri") or ""),
        str(item.get("snippet") or item.get("text") or ""),
    ]
    digest = hashlib.sha1("|".join(stable_parts).encode("utf-8")).hexdigest()[:12]
    return f"ev-{digest or index}"


def _citation_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "tid": item.get("tid"),
        "pid": item.get("pid"),
        "floor_no": item.get("floor_no"),
        "source_uri": item.get("source_uri"),
    }


def _build_evidence_pack_id(forum_id: int, start_date: str, end_date: str, question: str) -> str:
    digest = hashlib.sha1(question.encode("utf-8")).hexdigest()[:12]
    return f"knowledge:forum:{forum_id}:{start_date}:{end_date}:{digest}"


def _is_iso_date(value: str) -> bool:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    yyyy, mm, dd = value.split("-")
    if not (yyyy.isdigit() and mm.isdigit() and dd.isdigit()):
        return False
    month = int(mm)
    day = int(dd)
    return 1 <= month <= 12 and 1 <= day <= 31


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
