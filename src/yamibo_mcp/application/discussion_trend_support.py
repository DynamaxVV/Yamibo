"""Shared helpers for discussion trend query/report shaping."""

from __future__ import annotations

from typing import Any, Iterable

from yamibo_mcp.rag.scoring import hybrid_score, normalize_keyword_rank, normalize_vector_distance


def build_partition_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_threads = 0
    total_posts = 0
    total_new_threads = 0
    peak_day: dict[str, Any] | None = None
    for row in rows:
        total_threads += row["thread_count"]
        total_posts += row["post_count"]
        total_new_threads += row["new_thread_count"]
        if peak_day is None or row["post_count"] > peak_day["post_count"]:
            peak_day = {
                "bucket_date": row["bucket_date"],
                "post_count": row["post_count"],
                "thread_count": row["thread_count"],
            }

    summary: dict[str, Any] = {
        "total_days": len(rows),
        "total_thread_count": total_threads,
        "total_post_count": total_posts,
        "total_new_thread_count": total_new_threads,
        "daily": rows,
    }
    if peak_day is not None:
        summary["peak_day"] = peak_day
    return summary


def aggregate_topic_rows(
    rows: Iterable[dict[str, Any]], *, include_daily: bool = False
) -> list[dict[str, Any]]:
    topics_map: dict[int, dict[str, Any]] = {}
    for row in rows:
        topic_id = row["topic_id"]
        topic = topics_map.get(topic_id)
        if topic is None:
            topic = {
                "topic_id": topic_id,
                "topic_key": row["topic_key"],
                "topic_label": row["topic_label"],
                "topic_kind": row["topic_kind"],
                "topic_source": row["topic_source"],
                "confidence": row["confidence"],
                "total_thread_count": 0,
                "total_post_count": 0,
                "total_user_count": 0,
                "total_assignment_count": 0,
            }
            if include_daily:
                topic["daily"] = []
            topics_map[topic_id] = topic

        topic["total_thread_count"] += row["thread_count"]
        topic["total_post_count"] += row["post_count"]
        topic["total_user_count"] += row["active_user_count"]
        topic["total_assignment_count"] += row["assignment_count"]

        if include_daily:
            topic["daily"].append(
                {
                    "bucket_date": row["bucket_date"],
                    "thread_count": row["thread_count"],
                    "post_count": row["post_count"],
                    "active_user_count": row["active_user_count"],
                    "assignment_count": row["assignment_count"],
                }
            )

    return sorted(
        topics_map.values(),
        key=lambda topic: topic["total_assignment_count"],
        reverse=True,
    )


def aggregate_user_rows(
    rows: Iterable[dict[str, Any]], *, include_daily: bool = False
) -> list[dict[str, Any]]:
    users_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        user_key = row["user_key"]
        user = users_map.get(user_key)
        if user is None:
            user = {
                "user_key": user_key,
                "display_name": row["display_name"],
                "total_thread_count": 0,
                "total_post_count": 0,
                "total_topic_count": 0,
            }
            if include_daily:
                user["daily"] = []
            users_map[user_key] = user

        user["total_thread_count"] += row["thread_count"]
        user["total_post_count"] += row["post_count"]
        user["total_topic_count"] += row["topic_count"]

        if include_daily:
            user["daily"].append(
                {
                    "bucket_date": row["bucket_date"],
                    "thread_count": row["thread_count"],
                    "post_count": row["post_count"],
                    "topic_count": row["topic_count"],
                }
            )

    return list(users_map.values())


def build_rag_evidence_item(
    row: dict[str, Any], *, keyword_score: float = 0.0, vector_score: float = 0.0
) -> dict[str, Any]:
    text = str(row["text"] or "")
    return {
        "tid": int(row["tid"]),
        "pid": int(row["pid"]) if row["pid"] is not None else None,
        "floor_no": int(row["floor_no"]) if row["floor_no"] is not None else None,
        "display_title": str(row["title"]) if row["title"] else None,
        "publisher": str(row["publisher"]) if row["publisher"] else None,
        "pub_time": str(row["pub_time"]) if row["pub_time"] else None,
        "snippet": text[:220] if len(text) > 220 else text,
        "source_uri": str(row["source_uri"]) if row["source_uri"] else f"yamibo://threads/{row['tid']}",
        "score_parts": {"keyword": keyword_score, "vector": vector_score},
        "score": 0.0,
        "evidence_source": "rag",
    }


def merge_rag_search_rows(
    *,
    keyword_rows: Iterable[dict[str, Any]],
    vector_rows: Iterable[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for rank, row in enumerate(keyword_rows):
        chunk_id = str(row["chunk_id"])
        entry = merged.get(chunk_id)
        if entry is None:
            entry = build_rag_evidence_item(
                row,
                keyword_score=normalize_keyword_rank(rank),
            )
            merged[chunk_id] = entry
        else:
            entry["score_parts"]["keyword"] = normalize_keyword_rank(rank)

    for row in vector_rows:
        chunk_id = str(row["chunk_id"])
        vector_score = normalize_vector_distance(
            float(row["vector_distance"]), metric="cosine",
        )
        entry = merged.get(chunk_id)
        if entry is None:
            entry = build_rag_evidence_item(row, vector_score=vector_score)
            merged[chunk_id] = entry
        else:
            entry["score_parts"]["vector"] = vector_score

    items = list(merged.values())
    for item in items:
        item["score"] = hybrid_score(
            keyword=float(item["score_parts"]["keyword"]),
            vector=float(item["score_parts"]["vector"]),
            metadata=0.5,
        )

    items.sort(key=lambda item: float(item["score"]), reverse=True)
    trimmed = items[:top_k]
    for item in trimmed:
        item.pop("score_parts", None)
        item["score"] = round(float(item["score"]), 4)
    return trimmed


def merge_preferred_evidence_items(
    primary_items: Iterable[dict[str, Any]],
    secondary_items: Iterable[dict[str, Any]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    seen_keys: set[tuple[Any, Any, int]] = set()
    merged: list[dict[str, Any]] = []
    for item in list(primary_items) + list(secondary_items):
        key = (item.get("pid"), item.get("floor_no"), int(item["tid"]))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.append(item)
        if len(merged) >= top_k:
            break
    return merged
