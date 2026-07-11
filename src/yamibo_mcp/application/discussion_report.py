"""Discussion trend V1 report generator.

Step 05 — template-driven JSON + Markdown report artifacts.
No LLM calls. Stores artifacts in discussion_report_runs.
"""

from __future__ import annotations

import logging
from typing import Any

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.application.discussion_trend_support import (
    aggregate_topic_rows,
    aggregate_user_rows,
    build_partition_summary,
    build_rag_evidence_item,
    merge_preferred_evidence_items,
)
from yamibo_mcp.application.discussion_trend_queries import get_forum_evidence_pack
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.discussion_trends import DiscussionTrendRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.structured_logging import emit

LOG = logging.getLogger(__name__)

DEFAULT_VERSION = "trend-v1"

_TREND_REPORT_KIND = "trend_report"
_RESEARCH_REPORT_KIND = "forum_research"


# -- report generators --------------------------------------------------


def generate_trend_report(
    repo: DiscussionTrendRepository,
    run: Any,
    forum_id: int,
    start_date: str,
    end_date: str,
    version: str,
    period: str,
    conn: Any | None = None,
) -> tuple[dict[str, Any], str]:
    """Generate a trend report JSON dict and Markdown string."""
    partition_rows = repo.get_partition_daily(run.run_id, forum_id)
    topic_rows = repo.get_topic_daily_with_labels(run.run_id, forum_id)
    user_rows = repo.get_user_daily(run.run_id, forum_id)

    warnings: list[str] = list(run.warnings_json) if run.warnings_json else []

    partition_summary = build_partition_summary(partition_rows)

    # -- topic summary --
    ranked_topics = aggregate_topic_rows(topic_rows)
    topic_degraded = "TOPIC_QUALITY_INSUFFICIENT" in warnings

    # -- user summary --
    users = aggregate_user_rows(user_rows)

    top_users = sorted(
        users,
        key=lambda u: u["total_post_count"],
        reverse=True,
    )[:20]

    # -- evidence section (optional, best-effort) --
    evidence: dict[str, Any] | None = None
    evidence_warning: str | None = None
    if conn is not None and ranked_topics:
        evidence, evidence_warning = _collect_trend_evidence(
            conn=conn,
            repo=repo,
            run=run,
            forum_id=forum_id,
            start_date=start_date,
            end_date=end_date,
            version=version,
            ranked_topics=ranked_topics[:3],
        )
        if evidence_warning:
            warnings.append(evidence_warning)

    # -- data notes --
    data_notes = {
        "generator": "template-v1",
        "period": period,
        "methodology": (
            "Daily aggregation of threads and floors from the forum archive. "
            "Topics are derived from category tags, title phrase extraction, "
            "and manual seeds. No LLM or embedding was used for topic discovery "
            "or report generation."
        ),
        "limitations": [
            "Topic discovery is rule-based and may miss emerging themes.",
            "User identity is based on forum username/UID only.",
            "Floor-level sentiment and content analysis are not performed.",
        ],
    }

    report_json: dict[str, Any] = {
        "metadata": {
            "forum_id": forum_id,
            "start_date": start_date,
            "end_date": end_date,
            "version": version,
            "period": period,
            "run_id": run.run_id,
            "generated_at": run.completed_at,
            "report_kind": _TREND_REPORT_KIND,
        },
        "coverage": {
            "status": run.status,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
        },
        "partition_summary": partition_summary,
        "topic_summary": {
            "topics": ranked_topics,
            "degraded": topic_degraded,
            "degraded_note": (
                "Topic count below quality threshold; ranking may be incomplete."
                if topic_degraded
                else None
            ),
        },
        "user_summary": {
            "total_active_users": len(users),
            "top_users": top_users,
        },
        "warnings": warnings,
        "data_notes": data_notes,
    }
    if evidence is not None:
        report_json["evidence"] = evidence

    markdown = _render_trend_markdown(
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        period=period,
        partition=partition_summary,
        topics=ranked_topics,
        topic_degraded=topic_degraded,
        users=top_users,
        total_users=len(users),
        warnings=warnings,
        data_notes=data_notes,
        evidence=evidence,
    )

    return report_json, markdown


def generate_forum_research_report(
    repo: DiscussionTrendRepository | None,
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    question: str,
    intent: str,
    evidence_query: str,
    run: Any | None,
    conn: Any,
) -> tuple[dict[str, Any], str]:
    """Generate a forum research report JSON dict and Markdown string."""
    warnings: list[str] = []

    evidence_result = get_forum_evidence_pack(
        forum_id=forum_id,
        query=evidence_query,
        start_date=start_date,
        end_date=end_date,
        top_k=15,
        mode="auto",
        intent=intent,
        require_current_run=False,
    )
    evidence_items: list[dict[str, Any]] = []
    if evidence_result.ok and evidence_result.data is not None:
        evidence_items = list(evidence_result.data.get("items", []))
        warnings.extend(evidence_result.warnings)

    if not evidence_items:
        warnings.append("EVIDENCE_INSUFFICIENT")

    # Trend context (optional)
    trend_context: dict[str, Any] | None = None
    if run is not None and repo is not None:
        try:
            partition_rows = repo.get_partition_daily(run.run_id, forum_id)
            total_posts = sum(r["post_count"] for r in partition_rows)
            total_threads = sum(r["thread_count"] for r in partition_rows)
            trend_context = {
                "run_id": run.run_id,
                "total_days": len(partition_rows),
                "total_post_count": total_posts,
                "total_thread_count": total_threads,
                "status": run.status,
            }
        except Exception:  # noqa: BLE001
            warnings.append("TREND_CONTEXT_UNAVAILABLE")

    # Template interpretation notes
    interpretation_notes = _build_interpretation_notes(intent, evidence_items, warnings)

    report_json: dict[str, Any] = {
        "metadata": {
            "forum_id": forum_id,
            "start_date": start_date,
            "end_date": end_date,
            "version": DEFAULT_VERSION,
            "report_kind": _RESEARCH_REPORT_KIND,
        },
        "question": question,
        "intent": intent,
        "coverage": {
            "evidence_item_count": len(evidence_items),
            "has_trend_context": trend_context is not None,
        },
        "trend_context": trend_context,
        "evidence_summary": {
            "items": evidence_items,
        },
        "interpretation_notes": interpretation_notes,
        "warnings": warnings,
    }

    markdown = _render_research_markdown(
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        question=question,
        intent=intent,
        trend_context=trend_context,
        evidence_items=evidence_items,
        interpretation_notes=interpretation_notes,
        warnings=warnings,
    )

    return report_json, markdown


# -- job creation commands ------------------------------------------------


def create_discussion_trend_report_job(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    version: str = DEFAULT_VERSION,
    period: str = "monthly",
    format: list[str] | None = None,
) -> AgentResult:
    field_errors = _validate_trend_report(
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        version=version,
        period=period,
        format=format,
    )
    if field_errors:
        return AgentResult(
            ok=False,
            error=AgentError(
                code="DISCUSSION_TREND_INVALID_PAYLOAD",
                message="One or more trend report job fields are invalid.",
                agent_hint="Provide forum_id (>0), ISO start_date <= end_date, non-empty version, valid period.",
                retryable=False,
                field_errors=field_errors,
            ),
        )

    payload: dict[str, Any] = {
        "forum_id": int(forum_id),
        "start_date": start_date,
        "end_date": end_date,
        "version": version,
        "period": period,
        "format": format or ["json", "markdown"],
        "report_kind": _TREND_REPORT_KIND,
    }

    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        repo = JobsRepository(conn)
        job = repo.create(JobType.DISCUSSION_TREND_REPORT.value, payload=payload)
    finally:
        conn.close()

    return AgentResult(
        ok=True,
        data={
            "job_id": job.job_id,
            "job_type": JobType.DISCUSSION_TREND_REPORT.value,
            "created": True,
            "forum_id": int(forum_id),
            "start_date": start_date,
            "end_date": end_date,
            "version": version,
            "report_kind": _TREND_REPORT_KIND,
        },
        next_actions=[
            AgentAction(
                tool="read_job",
                args={"job_id": job.job_id},
                reason="Poll the queued discussion trend report job until terminal state.",
            ),
        ],
        side_effects=["job_created"],
    )


def create_forum_research_report_job(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    question: str,
    intent: str = "general_research",
    query: str = "",
    format: list[str] | None = None,
) -> AgentResult:
    field_errors = _validate_research_report(
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        question=question,
        intent=intent,
        query=query,
        format=format,
    )
    if field_errors:
        return AgentResult(
            ok=False,
            error=AgentError(
                code="DISCUSSION_TREND_INVALID_PAYLOAD",
                message="One or more forum research report job fields are invalid.",
                agent_hint="Provide forum_id (>0), ISO dates, non-empty question, valid intent.",
                retryable=False,
                field_errors=field_errors,
            ),
        )

    payload: dict[str, Any] = {
        "forum_id": int(forum_id),
        "start_date": start_date,
        "end_date": end_date,
        "question": question,
        "intent": intent,
        "query": query or question,
        "format": format or ["json", "markdown"],
        "report_kind": _RESEARCH_REPORT_KIND,
    }

    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        repo = JobsRepository(conn)
        job = repo.create(JobType.DISCUSSION_TREND_REPORT.value, payload=payload)
    finally:
        conn.close()

    return AgentResult(
        ok=True,
        data={
            "job_id": job.job_id,
            "job_type": JobType.DISCUSSION_TREND_REPORT.value,
            "created": True,
            "forum_id": int(forum_id),
            "start_date": start_date,
            "end_date": end_date,
            "report_kind": _RESEARCH_REPORT_KIND,
            "question": question,
        },
        next_actions=[
            AgentAction(
                tool="read_job",
                args={"job_id": job.job_id},
                reason="Poll the queued forum research report job until terminal state.",
            ),
        ],
        side_effects=["job_created"],
    )


# -- markdown rendering --------------------------------------------------


def _render_trend_markdown(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    period: str,
    partition: dict[str, Any],
    topics: list[dict[str, Any]],
    topic_degraded: bool,
    users: list[dict[str, Any]],
    total_users: int,
    warnings: list[str],
    data_notes: dict[str, Any],
    evidence: dict[str, Any] | None = None,
) -> str:
    forum_label = _forum_label(forum_id)
    lines: list[str] = []
    lines.append(f"# 论坛趋势报告")
    lines.append("")
    lines.append(f"**分区**: {forum_label} (forum_id={forum_id})")
    lines.append(f"**窗口**: {start_date} ~ {end_date}")
    lines.append(f"**周期**: {period}")
    lines.append("")

    lines.append("## 数据覆盖")
    lines.append("")
    lines.append(f"- 统计天数: {partition['total_days']}")
    lines.append(f"- 总帖数: {partition['total_post_count']}")
    lines.append(f"- 总主题数: {partition['total_thread_count']}")
    lines.append(f"- 新主题数: {partition['total_new_thread_count']}")
    if partition.get("peak_day"):
        pd = partition["peak_day"]
        lines.append(f"- 最活跃日: {pd['bucket_date']} ({pd['post_count']} 帖)")
    lines.append("")

    lines.append("## 分区活跃趋势")
    lines.append("")
    lines.append("| 日期 | 帖数 | 主题数 | 活跃用户 | 新主题 | 回复 |")
    lines.append("|------|------|--------|----------|--------|------|")
    for row in partition.get("daily", [])[:31]:
        lines.append(
            f"| {row['bucket_date']} "
            f"| {row['post_count']} "
            f"| {row['thread_count']} "
            f"| {row['active_user_count']} "
            f"| {row['new_thread_count']} "
            f"| {row['reply_count']} |"
        )
    lines.append("")

    lines.append("## Topic 趋势")
    lines.append("")
    if topic_degraded:
        lines.append("> **降级说明**: 当前窗口符合质量阈值的 topic 数量不足，排名可能不完整。")
        lines.append("")
    if not topics:
        lines.append("（无满足质量阈值的 topic）")
    else:
        for t in topics:
            lines.append(f"### {t['topic_label']}")
            lines.append("")
            lines.append(f"- 类型: {t['topic_kind']} | 来源: {t['topic_source']} | 置信度: {t['confidence']:.2f}")
            lines.append(f"- 关联主题: {t['total_thread_count']} | 关联帖数: {t['total_post_count']} | 参与用户: {t['total_user_count']}")
            lines.append("")
    lines.append("")

    lines.append("## 用户活跃变化")
    lines.append("")
    lines.append(f"（共 {total_users} 活跃用户，以下为前 {len(users)} 位）")
    lines.append("")
    lines.append("| 用户 | 帖数 | 主题数 | Topic 数 |")
    lines.append("|------|------|--------|----------|")
    for u in users:
        lines.append(
            f"| {u['display_name'] or u['user_key']} "
            f"| {u['total_post_count']} "
            f"| {u['total_thread_count']} "
            f"| {u['total_topic_count']} |"
        )
    lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    if evidence and evidence.get("topics"):
        lines.append("## 代表性证据片段")
        lines.append("")
        for topic_ev in evidence["topics"]:
            lines.append(f"### Topic: {topic_ev['topic_label']}")
            lines.append("")
            for item in topic_ev.get("items", [])[:3]:
                title = item.get("display_title") or f"thread/{item['tid']}"
                publisher = item.get("publisher") or "?"
                lines.append(f"**{title}** (floor #{item.get('floor_no', '?')})")
                lines.append("")
                lines.append(f"> {item['snippet']}")
                lines.append("")
                lines.append(f"— {publisher}, {item.get('pub_time', '?')}")
                lines.append("")
        lines.append("")

    lines.append("## 数据说明")
    lines.append("")
    lines.append(f"- 生成方式: {data_notes.get('generator', 'N/A')}")
    lines.append(f"- 方法论: {data_notes.get('methodology', '')}")
    for lim in data_notes.get("limitations", []):
        lines.append(f"- 限制: {lim}")

    return "\n".join(lines)


def _render_research_markdown(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    question: str,
    intent: str,
    trend_context: dict[str, Any] | None,
    evidence_items: list[dict[str, Any]],
    interpretation_notes: dict[str, Any],
    warnings: list[str],
) -> str:
    forum_label = _forum_label(forum_id)
    lines: list[str] = []
    lines.append(f"# 论坛研究报告")
    lines.append("")
    lines.append(f"**研究问题**: {question}")
    lines.append(f"**分区**: {forum_label} (forum_id={forum_id})")
    lines.append(f"**窗口**: {start_date} ~ {end_date}")
    lines.append(f"**研究意图**: {intent}")
    lines.append("")

    lines.append("## 数据覆盖")
    lines.append("")
    lines.append(f"- 证据片段数: {len(evidence_items)}")
    if trend_context:
        lines.append(f"- 趋势上下文: 可用 (run_id={trend_context['run_id']}, {trend_context['total_days']}天, {trend_context['total_post_count']}帖)")
    else:
        lines.append("- 趋势上下文: 不可用")
    lines.append("")

    if trend_context:
        lines.append("## 分区/用户趋势背景")
        lines.append("")
        lines.append(f"- 窗口总帖数: {trend_context['total_post_count']}")
        lines.append(f"- 窗口总主题数: {trend_context['total_thread_count']}")
        lines.append("")

    if evidence_items:
        lines.append("## 代表性证据片段")
        lines.append("")
        for i, item in enumerate(evidence_items[:10], 1):
            title = item.get("display_title") or f"thread/{item['tid']}"
            publisher = item.get("publisher") or "?"
            lines.append(f"### 片段 {i}: {title}")
            lines.append("")
            lines.append(f"> {item['snippet']}")
            lines.append("")
            lines.append(f"— {publisher}, {item.get('pub_time', '?')}, [{item.get('source_uri', '')}]({item.get('source_uri', '')})")
            lines.append("")
    else:
        lines.append("## 代表性证据片段")
        lines.append("")
        lines.append("（未找到匹配证据）")
        lines.append("")

    lines.append("## 模板化观察")
    lines.append("")
    for obs in interpretation_notes.get("observations", []):
        lines.append(f"- {obs}")
    for constraint in interpretation_notes.get("constraints", []):
        lines.append(f"- **限制**: {constraint}")
    lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines)


def _build_interpretation_notes(
    intent: str,
    evidence_items: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    """Template-driven interpretation notes. No LLM."""
    observations: list[str] = []
    constraints: list[str] = []

    if not evidence_items:
        observations.append("当前窗口未检索到与查询显著相关的证据片段。")
        constraints.append("证据不足，无法形成可靠观察。建议扩大窗口或调整查询词。")
        return {"observations": observations, "constraints": constraints}

    intent_observations: dict[str, str] = {
        "general_research": "检索到相关讨论片段，可作为进一步分析的起点。",
        "slang_usage": "以下片段展示了查询词在论坛讨论中的实际使用语境。",
        "community_atmosphere": "以下片段反映了指定时期内的讨论风格和社区互动特征。",
        "topic_investigation": "检索到与调查主题相关的讨论语境，可用于定性分析。",
        "trend_context": "以下片段为趋势数据提供了具体的讨论实例。",
    }
    observations.append(intent_observations.get(intent, intent_observations["general_research"]))

    if len(evidence_items) < 5:
        constraints.append("证据片段较少（<5），结论的泛化性有限。")

    constraints.append("所有观察均基于关键词匹配的证据片段，未经语义或情感分析。")
    constraints.append("片段选择受限于SQL关键词搜索的覆盖范围，可能遗漏相关但用词不同的讨论。")

    return {"observations": observations, "constraints": constraints}


# -- validation ----------------------------------------------------------


def _validate_trend_report(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    version: str,
    period: str,
    format: list[str] | None,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(forum_id, int) or isinstance(forum_id, bool) or forum_id <= 0:
        errors.append({"field": "forum_id", "message": "forum_id must be a positive integer"})
    if not isinstance(start_date, str) or not _is_iso_date(start_date):
        errors.append({"field": "start_date", "message": "start_date must be an ISO date (YYYY-MM-DD)"})
    if not isinstance(end_date, str) or not _is_iso_date(end_date):
        errors.append({"field": "end_date", "message": "end_date must be an ISO date (YYYY-MM-DD)"})
    if (
        isinstance(start_date, str)
        and isinstance(end_date, str)
        and _is_iso_date(start_date)
        and _is_iso_date(end_date)
        and start_date > end_date
    ):
        errors.append({"field": "start_date", "message": "start_date must be <= end_date"})
    if not isinstance(version, str) or not version:
        errors.append({"field": "version", "message": "version must be a non-empty string"})
    if period not in ("daily", "monthly", "custom"):
        errors.append({"field": "period", "message": "period must be 'daily', 'monthly', or 'custom'"})
    return errors


def _validate_research_report(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    question: str,
    intent: str,
    query: str,
    format: list[str] | None,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(forum_id, int) or isinstance(forum_id, bool) or forum_id <= 0:
        errors.append({"field": "forum_id", "message": "forum_id must be a positive integer"})
    if not isinstance(start_date, str) or not _is_iso_date(start_date):
        errors.append({"field": "start_date", "message": "start_date must be an ISO date (YYYY-MM-DD)"})
    if not isinstance(end_date, str) or not _is_iso_date(end_date):
        errors.append({"field": "end_date", "message": "end_date must be an ISO date (YYYY-MM-DD)"})
    if (
        isinstance(start_date, str)
        and isinstance(end_date, str)
        and _is_iso_date(start_date)
        and _is_iso_date(end_date)
        and start_date > end_date
    ):
        errors.append({"field": "start_date", "message": "start_date must be <= end_date"})
    if not isinstance(question, str) or not question.strip():
        errors.append({"field": "question", "message": "question must be a non-empty string"})
    valid_intents = {"general_research", "slang_usage", "community_atmosphere", "topic_investigation", "trend_context"}
    if intent not in valid_intents:
        errors.append({"field": "intent", "message": f"intent must be one of {sorted(valid_intents)}"})
    return errors


def _is_iso_date(value: str) -> bool:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    yyyy, mm, dd = value.split("-")
    return yyyy.isdigit() and mm.isdigit() and dd.isdigit() and 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31


def _forum_label(forum_id: int) -> str:
    return {5: "动漫区", 33: "海域区"}.get(forum_id, f"forum_{forum_id}")


def _collect_trend_evidence(
    *,
    conn: Any,
    repo: DiscussionTrendRepository,
    run: Any,
    forum_id: int,
    start_date: str,
    end_date: str,
    version: str,
    ranked_topics: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    """Collect evidence snippets for top topics in a trend report. Best-effort."""
    evidence_topics: list[dict[str, Any]] = []
    any_success = False
    rag_failures = 0

    for topic in ranked_topics:
        tid = topic["topic_id"]
        try:
            # Try SQL evidence (fast, always works)
            items = repo.get_topic_floor_evidence(
                run_id=run.run_id,
                topic_id=tid,
                forum_id=forum_id,
                top_k=5,
            )
            # Also try RAG for richer snippets
            rag_items: list[dict[str, Any]] = []
            try:
                settings = load_settings()
                chunks_repo = RagChunksRepository(conn)
                rag_rows = chunks_repo.keyword_search(
                    query=topic["topic_label"],
                    top_k=3,
                    forum_id=forum_id,
                )
                for row in rag_rows:
                    rag_item = build_rag_evidence_item(row)
                    rag_item.pop("score_parts", None)
                    rag_items.append(rag_item)
            except Exception:
                rag_failures += 1

            # Merge: prefer RAG items, pad with SQL items
            merged = merge_preferred_evidence_items(
                rag_items,
                items,
                top_k=5,
            )

            if merged:
                evidence_topics.append({
                    "topic_id": tid,
                    "topic_label": topic["topic_label"],
                    "topic_key": topic["topic_key"],
                    "items": merged,
                })
                any_success = True
        except Exception:
            pass

    if not evidence_topics:
        warn = "DISCUSSION_EVIDENCE_PARTIAL" if any_success or ranked_topics else None
        emit(LOG, logging.WARNING, "trend.evidence_collected",
             f"Evidence collection for forum_id={forum_id}: {len(evidence_topics)} topics with evidence",
             result="degraded" if warn else "success", status="ok",
             forum_id=forum_id, run_id=run.run_id,
             warning_codes=[warn] if warn else None)
        return None, warn

    warning: str | None = None
    if rag_failures > 0 or len(evidence_topics) < len(ranked_topics):
        warning = "DISCUSSION_EVIDENCE_PARTIAL"

    emit(LOG, logging.INFO, "trend.evidence_collected",
         f"Evidence collected for forum_id={forum_id}: {len(evidence_topics)}/{len(ranked_topics)} topics, rag_failures={rag_failures}",
         result="degraded" if warning else "success", status="ok",
         forum_id=forum_id, run_id=run.run_id,
         warning_codes=[warning] if warning else None)

    return {"topics": evidence_topics}, warning
