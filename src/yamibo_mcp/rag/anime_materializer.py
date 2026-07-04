from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.rag.anime_dry_run import (
    ANIME_FORUM_ID,
    LANE_CHUNK_POLICIES,
    QUOTE_HEAVY_MIN_CHARS,
    QUOTE_HEAVY_RATIO,
    QUOTE_KEEP_PREVIEW_CHARS,
    VALID_ARCHIVE_STATUSES,
    AnimeDryRunChunk,
    EvidenceLane,
    FloorDryRunResult,
    ThreadDryRunResult,
    build_anime_dry_run_thread,
)
from yamibo_mcp.storage.atomic import atomic_write_text
from yamibo_mcp.storage.paths import StoragePaths


MATERIALIZER_VERSION = "1.1"
CLEANER_VERSION = "anime-cleaner-1.1"
CHUNKER_VERSION = "anime-chunker-1.1"
REPORT_BASENAME = "anime-rag-cleaned-corpus-stats.1.1"
SUPPORTED_MATERIALIZER_VERSIONS = ("1.1", "1.2")
_VALID_STATUS_SQL = "('complete', 'partial')"
_THREAD_BATCH_SIZE = 500

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_QUOTE_MARKER_RE = re.compile(
    r"(^|\n)\s*(?:>|引用[:：]|quote[:：]|原帖由|.{1,40}\s+(?:发表于|在\s+\d{4}[/-]))",
    re.IGNORECASE,
)
_ENGLISH_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'\-]{2,}\b")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_QUOTE_HEADER_RESIDUE_RE = re.compile(
    r"(?:原帖由.{0,80}发表|.{1,50}\s+发表于\s+\d{4}-\d{1,2}-\d{1,2}|.{1,50}\s+在\s+\d{4}[/-]\d{1,2}[/-]\d{1,2}.{0,30}发表)",
    re.IGNORECASE,
)
_ASCII_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_\-]{2,}\b")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")

_TARGET_THREAD_SELECT = """
    SELECT
      t.tid, t.raw_title, t.display_title, t.publisher, t.pub_time, t.forum_id,
      t.content_kind, t.category, t.series_id, t.archive_status,
      COUNT(f.pid) AS floor_count,
      COUNT(f.pid) FILTER (WHERE NULLIF(BTRIM(COALESCE(f.content, '')), '') IS NOT NULL) AS nonempty_floor_count
    FROM threads t
    LEFT JOIN floors f ON f.tid = t.tid
"""


def materialize_anime_cleaned_corpus(
    *,
    forum_id: int = ANIME_FORUM_ID,
    limit: int | None = None,
    tid: int | None = None,
    output_dir: Path | None = None,
    settings: Settings | None = None,
    version: str = MATERIALIZER_VERSION,
) -> dict[str, Any]:
    settings = settings or load_settings()
    if settings.db_backend != "postgres":
        raise ValueError("anime cleaner materializer is PostgreSQL-first and requires database.backend=postgres")
    _validate_version(version)

    output_dir = output_dir or (settings.project_root / ".omx" / "reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(settings.data_dir)

    thread_rows = _load_target_threads_from_db(settings=settings, forum_id=forum_id, limit=limit, tid=tid)
    totals = _new_totals(
        forum_id=forum_id,
        requested_limit=limit,
        requested_tid=tid,
        data_dir=settings.data_dir,
        version=version,
    )
    examples: list[dict[str, Any]] = []

    for index in range(0, len(thread_rows), _THREAD_BATCH_SIZE):
        batch = thread_rows[index : index + _THREAD_BATCH_SIZE]
        floors_by_tid = _load_floors_from_db(settings=settings, tids=[int(row["tid"]) for row in batch])
        for thread_row in batch:
            result = build_anime_dry_run_thread(
                thread_row=thread_row,
                floor_rows=floors_by_tid.get(int(thread_row["tid"]), []),
                include_low_signal=False,
                structured_cleaning=version == "1.2",
            )
            materialized = _build_thread_materialization(result=result, thread_row=thread_row, version=version)
            _write_thread_artifacts(paths=paths, tid=result.tid, materialized=materialized)
            _accumulate_totals(totals, materialized, examples)

    stats = _finalize_totals(totals, examples=examples)
    report_basename = _report_basename(limit=limit, tid=tid, version=version)
    json_path = output_dir / f"{report_basename}.json"
    md_path = output_dir / f"{report_basename}.md"
    atomic_write_text(json_path, json.dumps(stats, ensure_ascii=False, indent=2, default=_json_default) + "\n")
    atomic_write_text(md_path, render_corpus_stats_markdown(stats))
    stats["artifacts"] = {"json": str(json_path), "markdown": str(md_path)}
    return stats


def compare_anime_cleaner_versions(
    *,
    forum_id: int = ANIME_FORUM_ID,
    sample_size: int = 1000,
    output_dir: Path | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or load_settings()
    if settings.db_backend != "postgres":
        raise ValueError("anime cleaner comparison is PostgreSQL-first and requires database.backend=postgres")
    output_dir = output_dir or (settings.project_root / ".omx" / "reports")
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = connect(settings)
    try:
        conn.execute("SET TRANSACTION READ ONLY")
        thread_rows = _sample_target_threads(conn, forum_id=forum_id, sample_size=sample_size)
        floors_by_tid = _load_floors(conn, [int(row["tid"]) for row in thread_rows])
    finally:
        conn.close()

    pair_summaries: list[dict[str, Any]] = []
    token_v11: Counter[str] = Counter()
    token_v12: Counter[str] = Counter()
    cjk_v11: Counter[str] = Counter()
    cjk_v12: Counter[str] = Counter()
    review_candidates: list[dict[str, Any]] = []

    totals = {
        "thread_count": len(thread_rows),
        "floor_count": 0,
        "v1_1_body_lengths": [],
        "v1_2_body_lengths": [],
        "quote_clean_lengths": [],
        "quote_policy_counts": Counter(),
        "body_source_counts": Counter(),
        "quality_flag_counts_v1_2": Counter(),
        "v1_1_quote_header_residual_floors": 0,
        "v1_2_body_quote_header_residual_floors": 0,
        "v1_2_quote_header_residual_floors": 0,
        "yamibo_residual_v1_1": 0,
        "yamibo_residual_v1_2": 0,
        "v1_2_emptied_when_v1_1_nonempty": 0,
        "v1_2_strong_shrink_candidates": 0,
    }

    for thread_row in thread_rows:
        tid = int(thread_row["tid"])
        floor_rows = floors_by_tid.get(tid, [])
        v11 = build_anime_dry_run_thread(thread_row=thread_row, floor_rows=floor_rows, structured_cleaning=False)
        v12 = build_anime_dry_run_thread(thread_row=thread_row, floor_rows=floor_rows, structured_cleaning=True)
        pair_summaries.append(
            {
                "tid": tid,
                "title": (_row_get(thread_row, "display_title") or _row_get(thread_row, "raw_title") or ""),
                "category": _row_get(thread_row, "category"),
                "floor_count": len(floor_rows),
                "v1_1_chunks": len(v11.chunks),
                "v1_2_chunks": len(v12.chunks),
            }
        )
        totals["floor_count"] += len(v12.floors)

        for f11, f12 in zip(v11.floors, v12.floors, strict=False):
            totals["v1_1_body_lengths"].append(f11.clean.cleaned_chars)
            totals["v1_2_body_lengths"].append(f12.clean.cleaned_chars)
            if f12.quote_clean is not None:
                totals["quote_clean_lengths"].append(f12.quote_clean.cleaned_chars)
            totals["quote_policy_counts"][f12.quote_policy] += 1
            totals["body_source_counts"][f12.body_source] += 1
            for flag in _quality_flags_for_floor(f12):
                totals["quality_flag_counts_v1_2"][flag] += 1

            if _QUOTE_HEADER_RESIDUE_RE.search(f11.clean.text):
                totals["v1_1_quote_header_residual_floors"] += 1
            if _QUOTE_HEADER_RESIDUE_RE.search(f12.clean.text):
                totals["v1_2_body_quote_header_residual_floors"] += 1
            if f12.quote_clean is not None and _QUOTE_HEADER_RESIDUE_RE.search(f12.quote_clean.text):
                totals["v1_2_quote_header_residual_floors"] += 1
            if re.search(r"yamibo(?:qe|hk)\d+|yamibohu|yamiboshiho|yamibon\d*", f11.clean.text, re.IGNORECASE):
                totals["yamibo_residual_v1_1"] += 1
            if re.search(r"yamibo(?:qe|hk)\d+|yamibohu|yamiboshiho|yamibon\d*", f12.clean.text, re.IGNORECASE):
                totals["yamibo_residual_v1_2"] += 1

            _update_token_counters(f11.clean.text, ascii_counter=token_v11, cjk_counter=cjk_v11)
            _update_token_counters(f12.clean.text, ascii_counter=token_v12, cjk_counter=cjk_v12)

            if f11.clean.cleaned_chars > 0 and f12.clean.cleaned_chars == 0:
                totals["v1_2_emptied_when_v1_1_nonempty"] += 1
                _append_review_candidate(review_candidates, f11=f11, f12=f12, reason="v1_2_empty")
            elif f11.clean.cleaned_chars >= 40 and f12.clean.cleaned_chars <= max(8, int(f11.clean.cleaned_chars * 0.25)):
                totals["v1_2_strong_shrink_candidates"] += 1
                _append_review_candidate(review_candidates, f11=f11, f12=f12, reason="v1_2_strong_shrink")

    report = {
        "generated_at": _utc_now(),
        "forum_id": forum_id,
        "sample_size": sample_size,
        "valid_archive_statuses": list(VALID_ARCHIVE_STATUSES),
        "versions": {"baseline": "1.1", "candidate": "1.2"},
        "thread_count": totals["thread_count"],
        "floor_count": totals["floor_count"],
        "body_length_distribution": {
            "v1_1": _distribution(totals["v1_1_body_lengths"]),
            "v1_2": _distribution(totals["v1_2_body_lengths"]),
        },
        "quote_isolation": {
            "quote_clean_length_distribution": _distribution(totals["quote_clean_lengths"]),
            "quote_policy_counts": dict(totals["quote_policy_counts"].most_common()),
            "body_source_counts": dict(totals["body_source_counts"].most_common()),
            "quality_flag_counts_v1_2": dict(totals["quality_flag_counts_v1_2"].most_common()),
        },
        "residual_noise": {
            "quote_header_body_v1_1": totals["v1_1_quote_header_residual_floors"],
            "quote_header_body_v1_2": totals["v1_2_body_quote_header_residual_floors"],
            "quote_header_quote_v1_2": totals["v1_2_quote_header_residual_floors"],
            "yamibo_v1_1": totals["yamibo_residual_v1_1"],
            "yamibo_v1_2": totals["yamibo_residual_v1_2"],
        },
        "token_stats": {
            "top_ascii_v1_1": token_v11.most_common(40),
            "top_ascii_v1_2": token_v12.most_common(40),
            "top_cjk_bigrams_v1_1": cjk_v11.most_common(40),
            "top_cjk_bigrams_v1_2": cjk_v12.most_common(40),
        },
        "manual_review": {
            "v1_2_emptied_when_v1_1_nonempty": totals["v1_2_emptied_when_v1_1_nonempty"],
            "v1_2_strong_shrink_candidates": totals["v1_2_strong_shrink_candidates"],
            "examples": review_candidates[:40],
        },
        "sample_threads": pair_summaries[:80],
    }
    basename = f"anime-rag-cleaner-v1.1-v1.2-comparison-{sample_size}"
    json_path = output_dir / f"{basename}.json"
    md_path = output_dir / f"{basename}.md"
    atomic_write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2, default=_json_default) + "\n")
    atomic_write_text(md_path, render_comparison_markdown(report))
    report["artifacts"] = {"json": str(json_path), "markdown": str(md_path)}
    return report


def _load_target_threads_from_db(
    *, settings: Settings, forum_id: int, limit: int | None, tid: int | None
) -> list[Any]:
    conn = connect(settings)
    try:
        conn.execute("SET TRANSACTION READ ONLY")
        return _load_target_threads(conn, forum_id=forum_id, limit=limit, tid=tid)
    finally:
        conn.close()


def _load_floors_from_db(*, settings: Settings, tids: list[int]) -> dict[int, list[Any]]:
    conn = connect(settings)
    try:
        conn.execute("SET TRANSACTION READ ONLY")
        return _load_floors(conn, tids)
    finally:
        conn.close()


def _build_thread_materialization(*, result: ThreadDryRunResult, thread_row: Any, version: str = MATERIALIZER_VERSION) -> dict[str, Any]:
    materializer_version, cleaner_version, chunker_version = _version_labels(version)
    floor_records: list[dict[str, Any]] = []
    chunk_counts_by_floor: Counter[int] = Counter()
    for chunk in result.chunks:
        if chunk.floor_no is not None:
            chunk_counts_by_floor[int(chunk.floor_no)] += 1

    quality_flag_counts: Counter[str] = Counter()
    lane_length_values: dict[str, list[int]] = defaultdict(list)
    quality_flags_by_floor: dict[int, list[str]] = {}
    for floor in result.floors:
        flags = _quality_flags_for_floor(floor)
        quality_flags_by_floor[floor.floor_no] = flags
        for flag in flags:
            quality_flag_counts[flag] += 1
        lane_length_values[floor.lane].append(floor.clean.cleaned_chars)
        floor_records.append(
            {
                "pid": floor.pid,
                "floor_no": floor.floor_no,
                "publisher": floor.publisher,
                "publisher_uid": floor.publisher_uid,
                "pub_time": floor.pub_time,
                "has_images": floor.has_images,
                "lane": floor.lane,
                "lane_confidence": floor.lane_confidence,
                "lane_reasons": list(floor.lane_reasons),
                "quality_flags": flags,
                "body_source": floor.body_source,
                "quote_source": floor.quote_source,
                "quote_policy": floor.quote_policy,
                "quote_heavy_ratio": floor.quote_heavy_ratio,
                "original_chars": floor.clean.original_chars,
                "cleaned_chars": floor.clean.cleaned_chars,
                "raw_content_chars": len(floor.original_text),
                "raw_reply_chars": len(floor.original_reply_text),
                "raw_quote_chars": len(floor.original_quote_text),
                "clean_body_chars": floor.clean.cleaned_chars,
                "clean_quote_chars": floor.quote_clean.cleaned_chars if floor.quote_clean is not None else 0,
                "changed": floor.clean.changed,
                "clean_rules": list(floor.clean.rules),
                "quote_clean_rules": list(floor.quote_clean.rules) if floor.quote_clean is not None else [],
                "chunk_count": chunk_counts_by_floor.get(floor.floor_no, 0),
                "skipped_reason": floor.skipped_reason,
                "cleaned_text": floor.clean.text,
                "cleaned_quote_text": _quote_text_for_output(floor),
                "raw_content_preview": _preview(floor.original_text, limit=180),
                "raw_reply_preview": floor.original_reply_preview,
                "raw_quote_preview": floor.original_quote_preview,
                "clean_body_preview": floor.cleaned_preview,
                "clean_quote_preview": floor.cleaned_quote_preview,
            }
        )
    chunk_records = [
        _chunk_record(
            chunk,
            floor_flags=quality_flags_by_floor.get(chunk.floor_no or -1, []),
            version=version,
        )
        for chunk in result.chunks
    ]

    thread_quality_flags = sorted(quality_flag_counts)
    audit = {
        "floor_lane_counts": dict(Counter(floor.lane for floor in result.floors)),
        "chunk_lane_counts": dict(Counter(chunk.lane for chunk in result.chunks)),
        "quality_flag_counts": dict(quality_flag_counts.most_common()),
        "body_source_counts": dict(Counter(floor.body_source for floor in result.floors)),
        "quote_policy_counts": dict(Counter(floor.quote_policy for floor in result.floors)),
        "lane_length_summary": {
            lane: _distribution(lengths) for lane, lengths in sorted(lane_length_values.items())
        },
        "cleaning": _thread_cleaning_summary(result),
    }
    return {
        "schema_version": 1,
        "materializer_version": materializer_version,
        "cleaner_version": cleaner_version,
        "chunker_version": chunker_version,
        "generated_at": _utc_now(),
        "tid": result.tid,
        "forum_id": int(_row_get(thread_row, "forum_id")),
        "archive_status": _row_get(thread_row, "archive_status"),
        "content_kind": _row_get(thread_row, "content_kind"),
        "title": result.title,
        "category": result.category,
        "publisher": result.publisher,
        "pub_time": result.pub_time,
        "thread_lane": result.thread_lane,
        "thread_confidence": result.thread_confidence,
        "thread_reasons": list(result.thread_reasons),
        "thread_quality_flags": thread_quality_flags,
        "floor_count": result.floor_count,
        "candidate_chunk_count": len(result.chunks),
        "chunk_policy": _chunk_policy_payload(),
        "audit": audit,
        "floors": floor_records,
        "chunks_preview": chunk_records,
    }


def _write_thread_artifacts(*, paths: StoragePaths, tid: int, materialized: dict[str, Any]) -> None:
    version = materialized["materializer_version"]
    json_path = paths.thread_rag_cleaned_json(tid, version)
    md_path = paths.thread_rag_cleaned_markdown(tid, version)
    preview_path = paths.thread_rag_chunks_preview_jsonl(tid, version)
    marker_path = paths.thread_rag_materialized_marker(tid, version)
    if marker_path.exists():
        marker_path.unlink()
    json_content = json.dumps(materialized, ensure_ascii=False, indent=2, default=_json_default) + "\n"
    md_content = render_thread_markdown(materialized)
    jsonl = "\n".join(json.dumps(row, ensure_ascii=False, default=_json_default) for row in materialized["chunks_preview"])
    preview_content = jsonl + ("\n" if jsonl else "")
    atomic_write_text(
        json_path,
        json_content,
    )
    atomic_write_text(md_path, md_content)
    atomic_write_text(preview_path, preview_content)
    marker = {
        "schema_version": 1,
        "materializer_version": materialized["materializer_version"],
        "cleaner_version": materialized["cleaner_version"],
        "chunker_version": materialized["chunker_version"],
        "generated_at": _utc_now(),
        "tid": tid,
        "complete": True,
        "artifacts": {
            json_path.name: {"sha256": _sha256_text(json_content), "bytes": len(json_content.encode("utf-8"))},
            md_path.name: {"sha256": _sha256_text(md_content), "bytes": len(md_content.encode("utf-8"))},
            preview_path.name: {"sha256": _sha256_text(preview_content), "bytes": len(preview_content.encode("utf-8"))},
        },
        "floor_count": materialized["floor_count"],
        "candidate_chunk_count": materialized["candidate_chunk_count"],
    }
    atomic_write_text(
        marker_path,
        json.dumps(marker, ensure_ascii=False, indent=2, default=_json_default) + "\n",
    )


def render_thread_markdown(materialized: dict[str, Any]) -> str:
    lines = [
        f"# RAG 清洗审计：TID {materialized['tid']}",
        "",
        f"- 版本：`{materialized['materializer_version']}` / `{materialized['cleaner_version']}` / `{materialized['chunker_version']}`",
        f"- 标题：{materialized['title']}",
        f"- 分区：`forum_id={materialized['forum_id']}`",
        f"- 归档状态：`{materialized['archive_status']}`",
        f"- 线程 lane：`{materialized['thread_lane']}` ({materialized['thread_confidence']:.2f})",
        f"- 楼层数：{materialized['floor_count']}",
        f"- 候选 chunk：{materialized['candidate_chunk_count']}",
        "",
        "## 1. 审计摘要",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
    ]
    cleaning = materialized["audit"]["cleaning"]
    lines.extend(
        [
            f"| 原始字符 | {cleaning['original_chars']} |",
            f"| 清洗后字符 | {cleaning['cleaned_chars']} |",
            f"| 收缩比例 | {cleaning['shrink_ratio']:.2%} |",
            f"| 变化楼层 | {cleaning['changed_floors']} |",
            f"| 清洗后为空 | {cleaning['emptied_floors']} |",
        ]
    )
    lines.extend(["", "## 2. Lane 与质量 flag", "", "### 2.1 楼层 lane", "", "| lane | 楼层数 |", "|---|---:|"])
    for lane, count in sorted(materialized["audit"]["floor_lane_counts"].items()):
        lines.append(f"| `{lane}` | {count} |")
    lines.extend(["", "### 2.2 质量 flag", "", "| flag | 楼层数 |", "|---|---:|"])
    for flag, count in materialized["audit"]["quality_flag_counts"].items():
        lines.append(f"| `{flag}` | {count} |")
    if not materialized["audit"]["quality_flag_counts"]:
        lines.append("| (none) | 0 |")
    lines.extend(["", "### 2.3 v1.2 结构字段", "", "| 指标 | 计数 |", "|---|---:|"])
    for source, count in materialized["audit"].get("body_source_counts", {}).items():
        lines.append(f"| body_source=`{source}` | {count} |")
    for policy, count in materialized["audit"].get("quote_policy_counts", {}).items():
        lines.append(f"| quote_policy=`{policy}` | {count} |")

    lines.extend(["", "## 3. Chunk preview 策略", "", "| lane | min | max | 默认入索引 |", "|---|---:|---:|---|"])
    for lane, policy in materialized["chunk_policy"].items():
        lines.append(f"| `{lane}` | {policy['min_chars']} | {policy['max_chars']} | {policy['index_candidate']} |")

    lines.extend(["", "## 4. 清洗楼层", ""])
    for floor in materialized["floors"]:
        text = floor["cleaned_text"]
        if not text:
            continue
        preview = _preview(text, limit=800)
        flags = ", ".join(f"`{flag}`" for flag in floor["quality_flags"]) or "-"
        quote_line = ""
        if floor.get("quote_policy") and floor.get("quote_policy") != "none":
            quote_line = f"- quote：`{floor['quote_policy']}`，clean chars={floor.get('clean_quote_chars', 0)}，ratio={floor.get('quote_heavy_ratio', 0)}"
        lines.extend(
            [
                f"### Floor {floor['floor_no']} / PID {floor['pid']} / `{floor['lane']}`",
                "",
                f"- 质量 flag：{flags}",
                f"- body_source：`{floor.get('body_source')}`",
                quote_line,
                f"- 字符：{floor['original_chars']} -> {floor['cleaned_chars']}",
                f"- clean rules：{', '.join(f'`{rule}`' for rule in floor['clean_rules']) or '-'}",
                "",
                "```text",
                preview,
                "```",
                "",
            ]
        )
        if floor.get("cleaned_quote_text"):
            lines.extend(["引用预览：", "", "```text", _preview(floor["cleaned_quote_text"], limit=300), "```", ""])
    return "\n".join(lines)


def render_corpus_stats_markdown(stats: dict[str, Any]) -> str:
    lines = [
        f"# 动漫区 RAG 清洗语料统计与 Chunk Policy {stats['materializer_version']} 评估",
        "",
        f"- 生成时间：{stats['generated_at']}",
        f"- materializer：`{stats['materializer_version']}`",
        f"- cleaner：`{stats['cleaner_version']}`",
        f"- chunker：`{stats['chunker_version']}`",
        f"- 分区：`forum_id={stats['forum_id']}`",
        f"- 有效归档状态：`{', '.join(VALID_ARCHIVE_STATUSES)}`",
        f"- 线程数：{stats['thread_count']}",
        f"- 楼层数：{stats['floor_count']}",
        f"- 候选 chunk：{stats['candidate_chunk_count']}",
        "",
        "## 1. 结论",
        "",
        f"- 本轮产物采用文件级 shadow：每帖写入 `rag_cleaned.{stats['materializer_version']}.json`、`rag_cleaned.{stats['materializer_version']}.md` 与 `rag_chunks.preview.{stats['materializer_version']}.jsonl`，没有写入正式 `rag_chunks` 表。",
        "- 每帖另写 `rag_materialized.1.1.json` 完成 marker，记录三类产物的 hash；正式 shadow index 只应消费 marker 完整且 hash 匹配的线程。",
        "- RAG 目标从 trend-first 调整为 forum-understanding-first：讨论、背景资料、source_text 都保留，但在 chunk policy 与查询侧赋予不同默认权重。",
        "- `source_text`、英文长引用、URL-heavy、quote-heavy 通过质量 flag 保留可检索性，同时避免默认讨论召回池被正文转载或大段引用挤占。",
        "- 1.1 版可以作为 production shadow rebuild 的输入版本；正式索引前应先在查询侧接入 lane/quality flag 权重，而不是继续加强删除型清洗。",
        "",
        "## 2. 全局清洗效果",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 原始字符 | {stats['cleaning']['original_chars']} |",
        f"| 清洗后字符 | {stats['cleaning']['cleaned_chars']} |",
        f"| 收缩比例 | {stats['cleaning']['shrink_ratio']:.2%} |",
        f"| 变化楼层 | {stats['cleaning']['changed_floors']} |",
        f"| 清洗后为空 | {stats['cleaning']['emptied_floors']} |",
        "",
        "### 2.1 清洗规则命中",
        "",
        "| 规则 | 楼层数 |",
        "|---|---:|",
    ]
    for rule, count in stats["cleaning"]["rule_hits"].items():
        lines.append(f"| `{rule}` | {count} |")

    lines.extend(["", "## 3. Lane 分布", "", "### 3.1 线程 lane", "", "| lane | 线程数 |", "|---|---:|"])
    for lane, count in stats["lane_counts"]["threads"].items():
        lines.append(f"| `{lane}` | {count} |")
    lines.extend(["", "### 3.2 楼层 lane", "", "| lane | 楼层数 |", "|---|---:|"])
    for lane, count in stats["lane_counts"]["floors"].items():
        lines.append(f"| `{lane}` | {count} |")
    lines.extend(["", "### 3.3 Chunk lane", "", "| lane | chunk 数 |", "|---|---:|"])
    for lane, count in stats["lane_counts"]["chunks"].items():
        lines.append(f"| `{lane}` | {count} |")

    lines.extend(["", "## 4. 长度分布与质量 flag", "", "### 4.1 Lane 清洗后楼层长度", "", "| lane | count | p50 | p90 | p95 | p99 | max |", "|---|---:|---:|---:|---:|---:|---:|"])
    for lane, row in stats["length_distributions"]["floors_by_lane"].items():
        lines.append(f"| `{lane}` | {row['count']} | {row['p50']} | {row['p90']} | {row['p95']} | {row['p99']} | {row['max']} |")
    lines.extend(["", "### 4.2 Chunk 长度", "", "| lane | count | p50 | p90 | p95 | p99 | max |", "|---|---:|---:|---:|---:|---:|---:|"])
    for lane, row in stats["length_distributions"]["chunks_by_lane"].items():
        lines.append(f"| `{lane}` | {row['count']} | {row['p50']} | {row['p90']} | {row['p95']} | {row['p99']} | {row['max']} |")
    lines.extend(["", "### 4.3 质量 flag 长度", "", "| flag | count | p50 | p90 | p95 | p99 | max |", "|---|---:|---:|---:|---:|---:|---:|"])
    for flag, row in stats["length_distributions"]["floors_by_quality_flag"].items():
        lines.append(f"| `{flag}` | {row['count']} | {row['p50']} | {row['p90']} | {row['p95']} | {row['p99']} | {row['max']} |")
    lines.extend(["", "### 4.4 v1.2 结构字段统计", "", "| 指标 | 计数 |", "|---|---:|"])
    for source, count in stats.get("body_source_counts", {}).items():
        lines.append(f"| body_source=`{source}` | {count} |")
    for policy, count in stats.get("quote_policy_counts", {}).items():
        lines.append(f"| quote_policy=`{policy}` | {count} |")

    lines.extend(["", "## 5. Chunk Policy 1.1", "", "| lane | min | max | 默认入索引 | 设计说明 |", "|---|---:|---:|---|---|"])
    for lane, policy in stats["chunk_policy"].items():
        lines.append(
            f"| `{lane}` | {policy['min_chars']} | {policy['max_chars']} | {policy['index_candidate']} | {policy['rationale']} |"
        )

    lines.extend(
        [
            "",
            "## 6. 查询侧策略建议",
            "",
            "1. `forum_overview` 与 `thread_understanding`：默认检索 `discussion_evidence`、`background_material`、`unknown`，保留 `source_text` 但降低排序权重。",
            "2. `discussion_evidence`：优先 `discussion_evidence`，排除或强降权 `source_text`、`long_english_block`、`quote_heavy`。",
            "3. `background_lookup`：提升 `background_material` 与 `url_heavy`，但仍限制单帖同源 chunk 数，避免官网/新闻转载刷屏。",
            "4. `source_lookup`：显式允许 `source_text`、`long_english_block`、`quote_heavy`，用于查正文、翻译、转载来源。",
            "5. `trend_analysis`：只把趋势作为一种查询 intent，不作为全局默认目标；趋势 evidence 应从讨论 lane 聚合，背景/source 只作解释材料。",
            "",
            "## 7. 进入正式索引前的交接",
            "",
            "- 正式索引任务先校验每帖 `rag_materialized.1.1.json`，再读取 `rag_chunks.preview.1.1.jsonl`，不要重新读取原始 `context.md` 进行二次清洗。",
            "- PostgreSQL shadow rebuild 应使用独立 run id 或 shadow 表；验证召回和噪声排序后，再替换正式 `rag_chunks`。",
            "- 不建议继续用删除规则处理英文长引用、URL-heavy、quote-heavy：这些是内容型噪声，应由 lane、quality flag、查询 intent 和排序权重处理。",
            "- 当前版本的保留标准：核心论坛格式噪声归零或接近归零，内容型噪声可识别、可过滤、可降权，而不是从语料中抹掉。",
            "",
            "## 8. 样例线程",
            "",
            "| tid | title | thread lane | chunks | quality flags |",
            "|---:|---|---|---:|---|",
        ]
    )
    for item in stats["examples"]:
        flags = ", ".join(f"`{flag}`" for flag in item["quality_flags"]) or "-"
        title = str(item["title"]).replace("|", "\\|")
        lines.append(f"| {item['tid']} | {title} | `{item['thread_lane']}` | {item['chunk_count']} | {flags} |")
    lines.append("")
    return "\n".join(lines)


def render_comparison_markdown(report: dict[str, Any]) -> str:
    body = report["body_length_distribution"]
    quote = report["quote_isolation"]
    noise = report["residual_noise"]
    review = report["manual_review"]
    lines = [
        "# Anime RAG Cleaner 1.1 vs 1.2 抽样对比",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 分区：`forum_id={report['forum_id']}`",
        f"- 抽样线程：{report['thread_count']}",
        f"- 覆盖楼层：{report['floor_count']}",
        f"- 有效归档状态：`{', '.join(report['valid_archive_statuses'])}`",
        "",
        "## 1. Body 字符分布",
        "",
        "| 版本 | count | p50 | p90 | p95 | p99 | max | avg |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for version, row in body.items():
        lines.append(
            f"| `{version}` | {row['count']} | {row['p50']} | {row['p90']} | {row['p95']} | {row['p99']} | {row['max']} | {row['avg']} |"
        )

    lines.extend(
        [
            "",
            "## 2. Quote 隔离",
            "",
            "| 指标 | 计数 |",
            "|---|---:|",
        ]
    )
    for key, value in quote["body_source_counts"].items():
        lines.append(f"| body_source=`{key}` | {value} |")
    for key, value in quote["quote_policy_counts"].items():
        lines.append(f"| quote_policy=`{key}` | {value} |")
    for key, value in quote["quality_flag_counts_v1_2"].items():
        lines.append(f"| quality_flag=`{key}` | {value} |")
    qdist = quote["quote_clean_length_distribution"]
    lines.extend(
        [
            "",
            f"- quote 清洗后长度：count={qdist['count']}，p50={qdist['p50']}，p90={qdist['p90']}，p99={qdist['p99']}，max={qdist['max']}",
            "",
            "## 3. 残留噪声",
            "",
            "| 噪声 | v1.1 | v1.2 body | v1.2 quote |",
            "|---|---:|---:|---:|",
            f"| 引用头残留 | {noise['quote_header_body_v1_1']} | {noise['quote_header_body_v1_2']} | {noise['quote_header_quote_v1_2']} |",
            f"| `yamibo*` 残留 | {noise['yamibo_v1_1']} | {noise['yamibo_v1_2']} | - |",
            "",
            "## 4. 高频词残留",
            "",
            "### 4.1 ASCII token",
            "",
            "| v1.1 | count | v1.2 | count |",
            "|---|---:|---|---:|",
        ]
    )
    ascii11 = report["token_stats"]["top_ascii_v1_1"]
    ascii12 = report["token_stats"]["top_ascii_v1_2"]
    for index in range(max(len(ascii11), len(ascii12))):
        left = ascii11[index] if index < len(ascii11) else ("", "")
        right = ascii12[index] if index < len(ascii12) else ("", "")
        lines.append(f"| `{left[0]}` | {left[1]} | `{right[0]}` | {right[1]} |")
    lines.extend(["", "### 4.2 CJK bigram", "", "| v1.1 | count | v1.2 | count |", "|---|---:|---|---:|"])
    cjk11 = report["token_stats"]["top_cjk_bigrams_v1_1"]
    cjk12 = report["token_stats"]["top_cjk_bigrams_v1_2"]
    for index in range(max(len(cjk11), len(cjk12))):
        left = cjk11[index] if index < len(cjk11) else ("", "")
        right = cjk12[index] if index < len(cjk12) else ("", "")
        lines.append(f"| `{left[0]}` | {left[1]} | `{right[0]}` | {right[1]} |")

    lines.extend(
        [
            "",
            "## 5. 人工抽样误伤候选",
            "",
            f"- v1.2 清空但 v1.1 非空：{review['v1_2_emptied_when_v1_1_nonempty']}",
            f"- v1.2 强收缩候选：{review['v1_2_strong_shrink_candidates']}",
            "",
        ]
    )
    for item in review["examples"][:16]:
        lines.extend(
            [
                f"### TID {item['tid']} / Floor {item['floor_no']} / {item['reason']}",
                "",
                f"- v1.1 chars：{item['v1_1_chars']}",
                f"- v1.2 chars：{item['v1_2_chars']}",
                f"- body_source：`{item['body_source']}`",
                "",
                "v1.1:",
                "",
                "```text",
                item["v1_1_preview"],
                "```",
                "",
                "v1.2:",
                "",
                "```text",
                item["v1_2_preview"],
                "```",
                "",
                "raw reply:",
                "",
                "```text",
                item["raw_reply_preview"],
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def _quality_flags_for_floor(floor: FloorDryRunResult) -> list[str]:
    text = floor.clean.text
    flags: list[str] = []
    if floor.lane == "source_text":
        flags.append("source_text")
    if _is_long_english_block(text):
        flags.append("long_english_block")
    if _is_url_heavy(text):
        flags.append("url_heavy")
    if _is_quote_heavy(floor.original_text, text) or (
        floor.quote_heavy_ratio >= QUOTE_HEAVY_RATIO
        and floor.quote_clean is not None
        and floor.quote_clean.cleaned_chars >= QUOTE_HEAVY_MIN_CHARS
    ):
        flags.append("quote_heavy")
    if floor.structured_cleaning and floor.body_source == "content_fallback":
        flags.append("content_fallback")
    if floor.quote_policy == "long_suppressed":
        flags.append("quote_long_suppressed")
    if floor.clean.cleaned_chars >= 1800:
        flags.append("very_long_floor")
    return sorted(dict.fromkeys(flags))


def _is_long_english_block(text: str) -> bool:
    if len(text) < 360:
        return False
    english_words = len(_ENGLISH_WORD_RE.findall(text))
    cjk_chars = len(_CJK_RE.findall(text))
    return english_words >= 80 and english_words * 3 > cjk_chars


def _is_url_heavy(text: str) -> bool:
    urls = len(_URL_RE.findall(text))
    if urls >= 4:
        return True
    return urls >= 2 and len(text) < 1200


def _is_quote_heavy(original_text: str, cleaned_text: str) -> bool:
    quote_markers = len(_QUOTE_MARKER_RE.findall(original_text))
    if quote_markers >= 2:
        return True
    quote_chars = original_text.count("[quote") + original_text.count("<quote") + original_text.count("引用")
    return quote_chars >= 2 and len(cleaned_text) >= 120


def _chunk_record(chunk: AnimeDryRunChunk, *, floor_flags: list[str], version: str = MATERIALIZER_VERSION) -> dict[str, Any]:
    materializer_version, cleaner_version, chunker_version = _version_labels(version)
    return {
        "schema_version": 1,
        "materializer_version": materializer_version,
        "cleaner_version": cleaner_version,
        "chunker_version": chunker_version,
        "chunk_id": chunk.chunk_id.replace("anime-evidence-lane-dry-run-v1", chunker_version),
        "tid": chunk.tid,
        "pid": chunk.pid,
        "floor_no": chunk.floor_no,
        "part_index": chunk.part_index,
        "chunk_type": chunk.chunk_type,
        "lane": chunk.lane,
        "thread_lane": chunk.thread_lane,
        "quality_flags": sorted(set(floor_flags) | set(_quality_flags_for_text(chunk.text, lane=chunk.lane))),
        "original_chars": chunk.original_chars,
        "cleaned_chars": chunk.cleaned_chars,
        "chunk_chars": len(chunk.text),
        "clean_rules": list(chunk.clean_rules),
        "text": chunk.text,
    }


def _quality_flags_for_text(text: str, *, lane: EvidenceLane) -> list[str]:
    flags: list[str] = []
    if lane == "source_text":
        flags.append("source_text")
    if _is_long_english_block(text):
        flags.append("long_english_block")
    if _is_url_heavy(text):
        flags.append("url_heavy")
    if len(text) >= 1800:
        flags.append("very_long_floor")
    return sorted(dict.fromkeys(flags))


def _update_token_counters(text: str, *, ascii_counter: Counter[str], cjk_counter: Counter[str]) -> None:
    for token in _ASCII_TOKEN_RE.findall(text.lower()):
        if token in {"http", "https", "www", "com", "html", "thread"}:
            continue
        ascii_counter[token] += 1
    for run in _CJK_RUN_RE.findall(text):
        for index in range(0, max(len(run) - 1, 0)):
            token = run[index : index + 2]
            if token.strip():
                cjk_counter[token] += 1


def _append_review_candidate(
    review_candidates: list[dict[str, Any]],
    *,
    f11: FloorDryRunResult,
    f12: FloorDryRunResult,
    reason: str,
) -> None:
    if len(review_candidates) >= 80:
        return
    review_candidates.append(
        {
            "reason": reason,
            "tid": f12.tid,
            "pid": f12.pid,
            "floor_no": f12.floor_no,
            "body_source": f12.body_source,
            "quote_policy": f12.quote_policy,
            "v1_1_chars": f11.clean.cleaned_chars,
            "v1_2_chars": f12.clean.cleaned_chars,
            "v1_1_preview": _preview(f11.clean.text, limit=320),
            "v1_2_preview": _preview(f12.clean.text, limit=320),
            "raw_content_preview": _preview(f12.original_text, limit=320),
            "raw_reply_preview": f12.original_reply_preview,
            "raw_quote_preview": f12.original_quote_preview,
        }
    )


def _thread_cleaning_summary(result: ThreadDryRunResult) -> dict[str, Any]:
    original_chars = sum(floor.clean.original_chars for floor in result.floors)
    cleaned_chars = sum(floor.clean.cleaned_chars for floor in result.floors)
    return {
        "original_chars": original_chars,
        "cleaned_chars": cleaned_chars,
        "shrink_ratio": 0.0 if original_chars <= 0 else max((original_chars - cleaned_chars) / original_chars, 0.0),
        "changed_floors": sum(1 for floor in result.floors if floor.clean.changed),
        "emptied_floors": sum(1 for floor in result.floors if floor.clean.original_chars and not floor.clean.text),
        "rule_hits": dict(Counter(rule for floor in result.floors for rule in floor.clean.rules).most_common()),
        "quote_rule_hits": dict(
            Counter(rule for floor in result.floors if floor.quote_clean is not None for rule in floor.quote_clean.rules).most_common()
        ),
        "body_source_counts": dict(Counter(floor.body_source for floor in result.floors)),
        "quote_policy_counts": dict(Counter(floor.quote_policy for floor in result.floors)),
    }


def _new_totals(
    *, forum_id: int, requested_limit: int | None, requested_tid: int | None, data_dir: Path, version: str
) -> dict[str, Any]:
    materializer_version, cleaner_version, chunker_version = _version_labels(version)
    return {
        "generated_at": _utc_now(),
        "materializer_version": materializer_version,
        "cleaner_version": cleaner_version,
        "chunker_version": chunker_version,
        "forum_id": forum_id,
        "valid_archive_statuses": list(VALID_ARCHIVE_STATUSES),
        "requested_limit": requested_limit,
        "requested_tid": requested_tid,
        "data_dir": str(data_dir),
        "thread_count": 0,
        "floor_count": 0,
        "candidate_chunk_count": 0,
        "lane_counts": {"threads": Counter(), "floors": Counter(), "chunks": Counter()},
        "quality_flag_counts": Counter(),
        "body_source_counts": Counter(),
        "quote_policy_counts": Counter(),
        "cleaning": {
            "original_chars": 0,
            "cleaned_chars": 0,
            "changed_floors": 0,
            "emptied_floors": 0,
            "rule_hits": Counter(),
            "quote_rule_hits": Counter(),
        },
        "_floor_lengths_by_lane": defaultdict(list),
        "_chunk_lengths_by_lane": defaultdict(list),
        "_floor_lengths_by_quality_flag": defaultdict(list),
    }


def _accumulate_totals(totals: dict[str, Any], materialized: dict[str, Any], examples: list[dict[str, Any]]) -> None:
    totals["thread_count"] += 1
    totals["floor_count"] += int(materialized["floor_count"])
    totals["candidate_chunk_count"] += int(materialized["candidate_chunk_count"])
    totals["lane_counts"]["threads"][materialized["thread_lane"]] += 1

    for lane, count in materialized["audit"]["floor_lane_counts"].items():
        totals["lane_counts"]["floors"][lane] += count
    for lane, count in materialized["audit"]["chunk_lane_counts"].items():
        totals["lane_counts"]["chunks"][lane] += count
    for flag, count in materialized["audit"]["quality_flag_counts"].items():
        totals["quality_flag_counts"][flag] += count
    totals["body_source_counts"].update(materialized["audit"].get("body_source_counts", {}))
    totals["quote_policy_counts"].update(materialized["audit"].get("quote_policy_counts", {}))

    cleaning = materialized["audit"]["cleaning"]
    totals["cleaning"]["original_chars"] += cleaning["original_chars"]
    totals["cleaning"]["cleaned_chars"] += cleaning["cleaned_chars"]
    totals["cleaning"]["changed_floors"] += cleaning["changed_floors"]
    totals["cleaning"]["emptied_floors"] += cleaning["emptied_floors"]
    totals["cleaning"]["rule_hits"].update(cleaning["rule_hits"])
    totals["cleaning"]["quote_rule_hits"].update(cleaning.get("quote_rule_hits", {}))

    for floor in materialized["floors"]:
        length = int(floor["cleaned_chars"])
        totals["_floor_lengths_by_lane"][floor["lane"]].append(length)
        for flag in floor["quality_flags"]:
            totals["_floor_lengths_by_quality_flag"][flag].append(length)
    for chunk in materialized["chunks_preview"]:
        totals["_chunk_lengths_by_lane"][chunk["lane"]].append(int(chunk["chunk_chars"]))

    if len(examples) < 24 and materialized["thread_quality_flags"]:
        examples.append(
            {
                "tid": materialized["tid"],
                "title": materialized["title"],
                "thread_lane": materialized["thread_lane"],
                "chunk_count": materialized["candidate_chunk_count"],
                "quality_flags": materialized["thread_quality_flags"],
            }
        )


def _finalize_totals(totals: dict[str, Any], *, examples: list[dict[str, Any]]) -> dict[str, Any]:
    original_chars = totals["cleaning"]["original_chars"]
    cleaned_chars = totals["cleaning"]["cleaned_chars"]
    totals["cleaning"]["shrink_ratio"] = 0.0 if original_chars <= 0 else max((original_chars - cleaned_chars) / original_chars, 0.0)
    totals["cleaning"]["rule_hits"] = dict(totals["cleaning"]["rule_hits"].most_common())
    totals["lane_counts"] = {
        "threads": dict(sorted(totals["lane_counts"]["threads"].items())),
        "floors": dict(sorted(totals["lane_counts"]["floors"].items())),
        "chunks": dict(sorted(totals["lane_counts"]["chunks"].items())),
    }
    totals["quality_flag_counts"] = dict(totals["quality_flag_counts"].most_common())
    totals["body_source_counts"] = dict(totals["body_source_counts"].most_common())
    totals["quote_policy_counts"] = dict(totals["quote_policy_counts"].most_common())
    totals["length_distributions"] = {
        "floors_by_lane": {
            lane: _distribution(values) for lane, values in sorted(totals.pop("_floor_lengths_by_lane").items())
        },
        "chunks_by_lane": {
            lane: _distribution(values) for lane, values in sorted(totals.pop("_chunk_lengths_by_lane").items())
        },
        "floors_by_quality_flag": {
            flag: _distribution(values) for flag, values in sorted(totals.pop("_floor_lengths_by_quality_flag").items())
        },
    }
    totals["chunk_policy"] = _chunk_policy_payload()
    totals["examples"] = examples
    return totals


def _chunk_policy_payload() -> dict[str, dict[str, Any]]:
    rationales = {
        "discussion_evidence": "论坛理解主召回池，保持楼层观点粒度，避免过长 chunk 混合多名用户观点。",
        "unknown": "信号不足但可能仍是讨论，按 discussion 近似处理，查询侧低置信降权。",
        "background_material": "资料/新闻/制作信息需要保留上下文，允许略长 chunk。",
        "source_text": "正文/转载/长引用保留完整语义，默认可索引但查询侧按 intent 降权或过滤。",
        "low_signal": "短回复、纯符号、清洗后空内容默认不进入索引，只保留审计统计。",
    }
    return {
        lane: {
            "min_chars": policy.min_chars,
            "max_chars": policy.max_chars,
            "index_candidate": policy.index_candidate,
            "rationale": rationales[lane],
        }
        for lane, policy in sorted(LANE_CHUNK_POLICIES.items())
    }


def _report_basename(*, limit: int | None, tid: int | None, version: str) -> str:
    base = f"anime-rag-cleaned-corpus-stats.{version}"
    if tid is not None:
        return f"{base}.tid-{tid}"
    if limit is not None:
        return f"{base}.limit-{limit}"
    return base


def _version_labels(version: str) -> tuple[str, str, str]:
    _validate_version(version)
    return version, f"anime-cleaner-{version}", f"anime-chunker-{version}"


def _validate_version(version: str) -> None:
    if version not in SUPPORTED_MATERIALIZER_VERSIONS:
        raise ValueError(f"unsupported anime materializer version: {version}")


def _quote_text_for_output(floor: FloorDryRunResult) -> str:
    if floor.quote_clean is None:
        return ""
    if floor.quote_policy == "long_suppressed":
        return _preview(floor.quote_clean.text, limit=QUOTE_KEEP_PREVIEW_CHARS)
    if floor.quote_policy == "preview_only":
        return _preview(floor.quote_clean.text, limit=QUOTE_KEEP_PREVIEW_CHARS)
    return floor.quote_clean.text


def _distribution(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": 0, "p50": 0, "p90": 0, "p95": 0, "p99": 0, "max": 0, "avg": 0}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p50": _percentile(ordered, 0.50),
        "p90": _percentile(ordered, 0.90),
        "p95": _percentile(ordered, 0.95),
        "p99": _percentile(ordered, 0.99),
        "max": ordered[-1],
        "avg": round(mean(ordered), 2),
    }


def _percentile(ordered: list[int], q: float) -> int:
    if not ordered:
        return 0
    index = round((len(ordered) - 1) * q)
    return int(ordered[index])


def _load_target_threads(conn: Any, *, forum_id: int, limit: int | None, tid: int | None) -> list[Any]:
    params: list[Any] = [forum_id]
    where = f"WHERE t.forum_id = ? AND t.archive_status IN {_VALID_STATUS_SQL}"
    if tid is not None:
        where += " AND t.tid = ?"
        params.append(tid)
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT ?"
        params.append(limit)
    rows = conn.execute(
        _TARGET_THREAD_SELECT
        + f"""
        {where}
        GROUP BY t.tid
        ORDER BY t.tid ASC
        {limit_sql}
        """,
        tuple(params),
    ).fetchall()
    return rows


def _sample_target_threads(conn: Any, *, forum_id: int, sample_size: int) -> list[Any]:
    rows = conn.execute(
        _TARGET_THREAD_SELECT
        + f"""
        WHERE t.forum_id = ? AND t.archive_status IN {_VALID_STATUS_SQL}
        GROUP BY t.tid
        ORDER BY random()
        LIMIT ?
        """,
        (forum_id, sample_size),
    ).fetchall()
    return rows


def _load_floors(conn: Any, tids: list[int]) -> dict[int, list[Any]]:
    if not tids:
        return {}
    placeholders = ",".join("?" for _ in tids)
    rows = conn.execute(
        f"""
        SELECT pid, tid, floor_no, publisher, publisher_uid, pub_time, content, has_images, quote_text, reply_text
        FROM floors
        WHERE tid IN ({placeholders})
        ORDER BY tid ASC, floor_no ASC, pid ASC
        """,
        tuple(tids),
    ).fetchall()
    grouped: dict[int, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[int(row["tid"])].append(row)
    return grouped


def _row_get(row: Any, key: str) -> Any:
    if hasattr(row, "get"):
        return row.get(key)
    return row[key]


def _preview(value: str, *, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized[:limit] + ("..." if len(normalized) > limit else "")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "_asdict"):
        return value._asdict()
    if hasattr(value, "__dict__"):
        return asdict(value)
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize anime forum cleaned RAG files without writing DB index rows.")
    parser.add_argument("command", nargs="?", choices=("materialize", "compare"), default="materialize")
    parser.add_argument("--forum-id", type=int, default=ANIME_FORUM_ID)
    parser.add_argument("--limit", type=int, default=None, help="Optional deterministic thread limit for smoke runs.")
    parser.add_argument("--tid", type=int, default=None, help="Materialize a single thread.")
    parser.add_argument("--version", choices=SUPPORTED_MATERIALIZER_VERSIONS, default=MATERIALIZER_VERSION)
    parser.add_argument("--sample-size", type=int, default=1000, help="Thread sample size for compare.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Report output directory; defaults to .omx/reports.")
    args = parser.parse_args(argv)
    if args.command == "compare":
        report = compare_anime_cleaner_versions(
            forum_id=args.forum_id,
            sample_size=args.sample_size,
            output_dir=args.output_dir,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "artifacts": report.get("artifacts", {}),
                    "thread_count": report["thread_count"],
                    "floor_count": report["floor_count"],
                    "residual_noise": report["residual_noise"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    stats = materialize_anime_cleaned_corpus(
        forum_id=args.forum_id,
        limit=args.limit,
        tid=args.tid,
        output_dir=args.output_dir,
        version=args.version,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "artifacts": stats.get("artifacts", {}),
                "thread_count": stats["thread_count"],
                "floor_count": stats["floor_count"],
                "candidate_chunk_count": stats["candidate_chunk_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
