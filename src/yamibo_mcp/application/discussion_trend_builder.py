"""Discussion trend V1 mart builder.

V1 scope (per Step 02 spec):
- aggregate threads + floors + title_parse into discussion_partition_daily,
  discussion_user_daily, discussion_topic_daily
- derive topics only from `manual_seed`, `category`, or `title_phrase` sources
- never use floor keyword, RAG, embedding, or LLM for topic discovery
- topic support filtering; downgrade to empty ranking with TOPIC_QUALITY_INSUFFICIENT
  when not enough topics qualify
- all writes bound to a single run_id
- deterministic and re-runnable on the same window
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.discussion_trends import (
    DiscussionTrendRepository,
    ensure_postgres,
)


DEFAULT_TOPIC_THRESHOLDS: dict[str, float | int] = {
    "min_floor_count": 20,
    "min_active_thread_count": 3,
    "min_distinct_user_count": 5,
    "min_confidence_avg": 0.55,
    "min_topics": 3,
}

# V1 minimal stopword set (PONYTAIL: hand-curated; replace with corpus-driven
# stopword module in V1.5).
_TITLE_PHRASE_NOISE: frozenset[str] = frozenset(
    {
        "求助",
        "求",
        "分享",
        "转贴",
        "转载",
        "汇总",
        "整理",
        "更新",
        "完结",
        "连载",
        "推荐",
        "推",
        "新人",
        "提问",
        "问个",
        "讨论",
        "闲聊",
        "请问",
        "怎么",
        "如何",
        "看看",
    }
)

# Minimum length (in characters) for a title_phrase candidate.
_MIN_TITLE_PHRASE_LEN: int = 3

# Confidence assigned to each topic source.
_CONFIDENCE_MANUAL_SEED: float = 0.9
_CONFIDENCE_CATEGORY: float = 0.7
_CONFIDENCE_TITLE_PHRASE_BASE: float = 0.5
_CONFIDENCE_TITLE_PHRASE_STEP: float = 0.05
_CONFIDENCE_TITLE_PHRASE_CAP: float = 0.85
_CONFIDENCE_TITLE_PHRASE_MIN_THREADS_FOR_BONUS: int = 1

# Minimum distinct threads for a title_phrase candidate to even be considered
# (per Step 02: "phrase 必须跨多个 thread 或由 category/manual seed 支撑").
_MIN_THREADS_FOR_TITLE_PHRASE: int = 2


@dataclass(frozen=True)
class BuildInput:
    forum_id: int
    start_date: date
    end_date: date
    version: str = "trend-v1"
    # explicit operator-curated topics: (key, label).
    manual_seed_topics: tuple[tuple[str, str], ...] = ()
    # override the default support thresholds.
    thresholds: dict[str, float | int] | None = None


@dataclass(frozen=True)
class BuildResult:
    run_id: str
    forum_id: int
    start_date: date
    end_date: date
    version: str
    status: str
    metrics: dict[str, Any]
    warnings: list[str]


def build_trend_mart(
    conn: DatabaseConnection,
    *,
    run_id: str,
    payload: BuildInput,
) -> BuildResult:
    """Build the trend mart for the given window. Returns a BuildResult.

    All writes are bound to ``run_id``. The run is marked succeeded at the end
    (or left as ``running`` if an exception escapes — caller decides).
    """
    ensure_postgres(conn)
    _validate_input(payload)
    thresholds = {**DEFAULT_TOPIC_THRESHOLDS, **(payload.thresholds or {})}
    repo = DiscussionTrendRepository(conn)
    start_iso = payload.start_date.isoformat()
    end_iso = payload.end_date.isoformat()

    repo.acquire_window_lock(
        forum_id=payload.forum_id,
        start_date=start_iso,
        end_date=end_iso,
        version=payload.version,
    )
    repo.create_run(
        run_id=run_id,
        forum_id=payload.forum_id,
        start_date=start_iso,
        end_date=end_iso,
        version=payload.version,
        status="running",
    )

    rows = _read_window(conn, payload)
    warnings: list[str] = []

    partition_daily, partition_coverage = _aggregate_partition_daily(rows, warnings, payload.forum_id)
    user_daily = _aggregate_user_daily(rows)
    candidate_topics = _derive_topics(rows, payload.manual_seed_topics)
    qualified_topics, dropped_topics = _filter_topics(candidate_topics, rows, thresholds, warnings)
    topic_id_map = repo.replace_topics(run_id, _topic_rows(qualified_topics, payload.forum_id))
    topic_assignments = _build_assignments(rows, qualified_topics)
    repo.replace_topic_assignments(run_id, _assignment_rows(topic_assignments, run_id, payload.forum_id, topic_id_map))
    topic_daily = _aggregate_topic_daily(rows, topic_assignments, qualified_topics)
    repo.replace_topic_daily(run_id, _topic_daily_rows(topic_daily, run_id, payload.forum_id, topic_id_map))
    repo.replace_user_daily(run_id, _user_daily_rows(rows, user_daily, topic_assignments, run_id, payload.forum_id, topic_id_map))
    repo.replace_partition_daily(run_id, partition_daily)

    metrics = {
        "row_count": len(rows),
        "thread_count": len({r["tid"] for r in rows}),
        "partition_day_count": len(partition_daily),
        "user_day_count": len(user_daily),
        "topic_candidate_count": len(candidate_topics),
        "topic_qualified_count": len(qualified_topics),
        "topic_dropped_count": len(dropped_topics),
        "assignment_count": len(topic_assignments),
        "topic_day_count": len(topic_daily),
        "coverage": partition_coverage,
        "duration_seconds": 0,
    }
    repo.mark_run_succeeded(run_id, metrics_json=metrics, warnings_json=warnings)
    repo.set_current_run(
        forum_id=payload.forum_id,
        start_date=start_iso,
        end_date=end_iso,
        version=payload.version,
        current_run_id=run_id,
    )
    return BuildResult(
        run_id=run_id,
        forum_id=payload.forum_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        version=payload.version,
        status="succeeded",
        metrics=metrics,
        warnings=warnings,
    )


def _validate_input(payload: BuildInput) -> None:
    if not isinstance(payload.forum_id, int) or payload.forum_id <= 0:
        raise ValueError("forum_id must be a positive integer")
    if payload.start_date > payload.end_date:
        raise ValueError("start_date must be <= end_date")


def _read_window(conn: DatabaseConnection, payload: BuildInput) -> list[dict[str, Any]]:
    start_iso = payload.start_date.isoformat()
    end_iso = payload.end_date.isoformat()
    result = conn.execute(
        """
        SELECT
          t.tid,
          t.forum_id,
          t.category,
          t.display_title,
          t.raw_title,
          t.publisher        AS thread_publisher,
          t.publisher_uid    AS thread_publisher_uid,
          t.pub_time         AS thread_pub_time,
          f.pid,
          f.floor_no,
          f.publisher        AS floor_publisher,
          f.publisher_uid    AS floor_publisher_uid,
          f.pub_time         AS floor_pub_time,
          f.quote_text,
          f.reply_text,
          tp.core_title_guess,
          tp.normalized_core_title
        FROM threads t
        LEFT JOIN floors      f  ON f.tid  = t.tid
        LEFT JOIN title_parse tp ON tp.tid = t.tid
        WHERE t.forum_id = :forum_id
          AND DATE(COALESCE(f.pub_time, t.pub_time)) >= :start_date
          AND DATE(COALESCE(f.pub_time, t.pub_time)) <= :end_date
        ORDER BY t.tid ASC, COALESCE(f.pub_time, t.pub_time) ASC NULLS LAST, f.floor_no ASC NULLS LAST
        """,
        {"forum_id": payload.forum_id, "start_date": start_iso, "end_date": end_iso},
    )
    return [dict(row) for row in result.fetchall()]


def _user_key_for(row: dict[str, Any]) -> str:
    uid = row.get("floor_publisher_uid") or row.get("thread_publisher_uid")
    if uid:
        return f"uid:{uid}"
    name = row.get("floor_publisher") or row.get("thread_publisher")
    return f"name:{name or 'unknown'}"


def _display_name_for(row: dict[str, Any]) -> str | None:
    return row.get("floor_publisher") or row.get("thread_publisher")


def _bucket_date_for(row: dict[str, Any]) -> str:
    pub = row.get("floor_pub_time") or row.get("thread_pub_time")
    if pub is None:
        return ""
    if isinstance(pub, datetime):
        return pub.date().isoformat()
    if isinstance(pub, date):
        return pub.isoformat()
    return str(pub)[:10]


def _aggregate_partition_daily(
    rows: list[dict[str, Any]], warnings: list[str], forum_id: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_day: dict[str, dict[str, Any]] = {}
    fallback_count = 0
    quote_count_total = 0
    for row in rows:
        bucket = _bucket_date_for(row)
        if not bucket:
            continue
        if row.get("floor_pub_time") is None and row.get("thread_pub_time") is not None:
            fallback_count += 1
        if row.get("quote_text"):
            quote_count_total += 1
        agg = by_day.setdefault(
            bucket,
            {
                "thread_keys": set(),
                "user_keys": set(),
                "post_count": 0,
                "first_post_count": 0,
                "quote_count": 0,
            },
        )
        agg["thread_keys"].add(row["tid"])
        agg["user_keys"].add(_user_key_for(row))
        if row.get("pid") is not None:
            agg["post_count"] += 1
            if row.get("floor_no") == 1:
                agg["first_post_count"] += 1
            if row.get("quote_text"):
                agg["quote_count"] += 1
        else:
            # thread-only LEFT JOIN row; count as a first post for new_thread
            agg["first_post_count"] += 1
    if fallback_count:
        warnings.append("PUBTIME_FALLBACK")
    coverage = {
        "rows_total": len(rows),
        "rows_with_floor_pub_time": len(rows) - fallback_count,
        "rows_with_thread_pub_time_fallback": fallback_count,
        "rows_with_quote": quote_count_total,
    }
    result: list[dict[str, Any]] = []
    for bucket in sorted(by_day):
        agg = by_day[bucket]
        result.append(
            {
                "forum_id": forum_id,
                "bucket_date": bucket,
                "thread_count": len(agg["thread_keys"]),
                "post_count": agg["post_count"],
                "active_user_count": len(agg["user_keys"]),
                "new_thread_count": agg["first_post_count"],
                "reply_count": max(agg["post_count"] - agg["first_post_count"], 0),
                "metrics_json": {
                    "floor_count": agg["post_count"],
                    "quote_count": agg["quote_count"],
                },
            }
        )
    return result, coverage
def _aggregate_user_daily(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_user_day: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        bucket = _bucket_date_for(row)
        if not bucket:
            continue
        user_key = _user_key_for(row)
        key = (bucket, user_key)
        agg = by_user_day.setdefault(
            key,
            {
                "bucket_date": bucket,
                "user_key": user_key,
                "display_name": _display_name_for(row),
                "thread_keys": set(),
                "post_count": 0,
            },
        )
        agg["thread_keys"].add(row["tid"])
        if row.get("pid") is not None:
            agg["post_count"] += 1
    return [
        {
            "bucket_date": agg["bucket_date"],
            "user_key": agg["user_key"],
            "display_name": agg["display_name"],
            "thread_count": len(agg["thread_keys"]),
            "post_count": agg["post_count"],
            "topic_count": 0,
            "metrics_json": {},
        }
        for agg in (by_user_day[k] for k in sorted(by_user_day))
    ]


def _derive_topics(
    rows: list[dict[str, Any]],
    manual_seed: tuple[tuple[str, str], ...],
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for key, label in manual_seed:
        candidates[f"seed:{key}"] = {
            "topic_key": f"seed:{key}",
            "topic_label": label,
            "topic_kind": "manual_seed",
            "topic_source": "manual_seed",
            "match_terms": [key, label],
        }
    for category in sorted({(r.get("category") or "").strip() for r in rows if (r.get("category") or "").strip()}):
        candidates[f"category:{category}"] = {
            "topic_key": f"category:{category}",
            "topic_label": category,
            "topic_kind": "category",
            "topic_source": "category",
            "match_terms": [category],
        }
    by_phrase: dict[str, set[int]] = {}
    for row in rows:
        phrase = (row.get("normalized_core_title") or row.get("core_title_guess") or "").strip()
        if not phrase:
            continue
        if " " in phrase or "\t" in phrase:
            phrase = phrase.split()[0]
        if len(phrase) < _MIN_TITLE_PHRASE_LEN:
            continue
        if phrase in _TITLE_PHRASE_NOISE:
            continue
        by_phrase.setdefault(phrase, set()).add(row["tid"])
    for phrase, tids in by_phrase.items():
        if len(tids) < _MIN_THREADS_FOR_TITLE_PHRASE:
            continue
        candidates[f"phrase:{phrase}"] = {
            "topic_key": f"phrase:{phrase}",
            "topic_label": phrase,
            "topic_kind": "title_phrase",
            "topic_source": "title_phrase",
            "match_terms": [phrase],
        }
    return candidates


def _support_for_topic(
    candidate: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    source = candidate["topic_source"]
    if source == "manual_seed":
        terms = [t for t in candidate["match_terms"] if t]
        tids = {
            r["tid"]
            for r in rows
            if any(
                term
                and (
                    term == (r.get("category") or "").strip()
                    or term in (r.get("normalized_core_title") or "")
                    or term in (r.get("core_title_guess") or "")
                )
                for term in terms
            )
        }
    elif source == "category":
        cat = candidate["topic_label"]
        tids = {r["tid"] for r in rows if (r.get("category") or "").strip() == cat}
    else:
        phrase = candidate["topic_label"]
        tids = {
            r["tid"]
            for r in rows
            if phrase
            and (
                phrase == (r.get("normalized_core_title") or "").strip()
                or phrase == (r.get("core_title_guess") or "").strip()
            )
        }
    floor_count = 0
    user_keys: set[str] = set()
    for r in rows:
        if r["tid"] in tids and r.get("pid") is not None:
            floor_count += 1
            user_keys.add(_user_key_for(r))
    if source == "manual_seed":
        confidence = _CONFIDENCE_MANUAL_SEED
    elif source == "category":
        confidence = _CONFIDENCE_CATEGORY
    else:
        bonus_threads = max(len(tids) - _CONFIDENCE_TITLE_PHRASE_MIN_THREADS_FOR_BONUS, 0)
        confidence = min(
            _CONFIDENCE_TITLE_PHRASE_BASE + _CONFIDENCE_TITLE_PHRASE_STEP * bonus_threads,
            _CONFIDENCE_TITLE_PHRASE_CAP,
        )
    return {
        "floor_count": floor_count,
        "active_thread_count": len(tids),
        "distinct_user_count": len(user_keys),
        "confidence": confidence,
        "tids": tids,
    }


def _filter_topics(
    candidates: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    thresholds: dict[str, float | int],
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    qualified: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for candidate in candidates.values():
        support = _support_for_topic(candidate, rows)
        record = {
            "topic_key": candidate["topic_key"],
            "topic_label": candidate["topic_label"],
            "topic_kind": candidate["topic_kind"],
            "topic_source": candidate["topic_source"],
            "match_terms": candidate["match_terms"],
            **support,
        }
        if (
            support["floor_count"] >= int(thresholds["min_floor_count"])
            and support["active_thread_count"] >= int(thresholds["min_active_thread_count"])
            and support["distinct_user_count"] >= int(thresholds["min_distinct_user_count"])
            and support["confidence"] >= float(thresholds["min_confidence_avg"])
        ):
            qualified.append(record)
        else:
            dropped.append(record)
    qualified.sort(
        key=lambda c: (-c["floor_count"], -c["active_thread_count"], c["topic_key"])
    )
    if len(qualified) < int(thresholds["min_topics"]):
        warnings.append("TOPIC_QUALITY_INSUFFICIENT")
    return qualified, dropped


def _build_assignments(
    rows: list[dict[str, Any]], qualified_topics: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    tid_rows: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        tid_rows.setdefault(r["tid"], []).append(r)
    assignments: list[dict[str, Any]] = []
    for topic in qualified_topics:
        for tid in sorted(topic["tids"]):
            tid_window_rows = tid_rows.get(tid, [])
            bucket = _earliest_bucket(tid_window_rows)
            assignments.append(
                {
                    "topic_key": topic["topic_key"],
                    "topic_source": topic["topic_source"],
                    "tid": tid,
                    "pid": None,
                    "floor_no": None,
                    "bucket_date": bucket,
                    "assignment_score": 1.0,
                }
            )
    return assignments


def _earliest_bucket(rows: list[dict[str, Any]]) -> str | None:
    buckets = sorted({b for b in (_bucket_date_for(r) for r in rows) if b})
    return buckets[0] if buckets else None


def _topic_rows(
    qualified_topics: list[dict[str, Any]], forum_id: int
) -> list[dict[str, Any]]:
    return [
        {
            "forum_id": forum_id,
            "topic_key": t["topic_key"],
            "topic_label": t["topic_label"],
            "topic_kind": t["topic_kind"],
            "topic_source": t["topic_source"],
            "confidence": t["confidence"],
            "metadata_json": {
                "match_terms": t["match_terms"],
                "floor_count": t["floor_count"],
                "active_thread_count": t["active_thread_count"],
                "distinct_user_count": t["distinct_user_count"],
            },
        }
        for t in qualified_topics
    ]


def _assignment_rows(
    assignments: list[dict[str, Any]],
    run_id: str,
    forum_id: int,
    topic_id_map: dict[str, int],
) -> list[dict[str, Any]]:
    return [
        {
            "run_id": run_id,
            "topic_id": topic_id_map[a["topic_key"]],
            "forum_id": forum_id,
            "tid": a["tid"],
            "pid": a["pid"],
            "floor_no": a["floor_no"],
            "bucket_date": a["bucket_date"],
            "assignment_score": a["assignment_score"],
            "assignment_source": a["topic_source"],
        }
        for a in assignments
    ]


def _aggregate_topic_daily(
    rows: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    qualified_topics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tid_topic: dict[int, list[str]] = {}
    for a in assignments:
        tid_topic.setdefault(a["tid"], []).append(a["topic_key"])
    by_topic_day: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        bucket = _bucket_date_for(r)
        if not bucket:
            continue
        for topic_key in tid_topic.get(r["tid"], []):
            key = (topic_key, bucket)
            agg = by_topic_day.setdefault(
                key,
                {
                    "topic_key": topic_key,
                    "bucket_date": bucket,
                    "thread_keys": set(),
                    "user_keys": set(),
                    "post_count": 0,
                    "assignment_count": 0,
                },
            )
            agg["thread_keys"].add(r["tid"])
            agg["user_keys"].add(_user_key_for(r))
            if r.get("pid") is not None:
                agg["post_count"] += 1
    assignment_counts: dict[tuple[str, str], int] = {}
    for a in assignments:
        if a["bucket_date"] is None:
            continue
        key = (a["topic_key"], a["bucket_date"])
        assignment_counts[key] = assignment_counts.get(key, 0) + 1
    for key, count in assignment_counts.items():
        by_topic_day[key]["assignment_count"] = count
    result: list[dict[str, Any]] = []
    for (topic_key, bucket) in sorted(by_topic_day):
        agg = by_topic_day[(topic_key, bucket)]
        result.append(
            {
                "topic_key": topic_key,
                "bucket_date": bucket,
                "thread_count": len(agg["thread_keys"]),
                "post_count": agg["post_count"],
                "active_user_count": len(agg["user_keys"]),
                "assignment_count": agg["assignment_count"],
                "evidence_count": 0,
                "metrics_json": {},
            }
        )
    return result


def _topic_daily_rows(
    rows: list[dict[str, Any]],
    run_id: str,
    forum_id: int,
    topic_id_map: dict[str, int],
) -> list[dict[str, Any]]:
    return [
        {
            "run_id": run_id,
            "topic_id": topic_id_map[r["topic_key"]],
            "forum_id": forum_id,
            "bucket_date": r["bucket_date"],
            "thread_count": r["thread_count"],
            "post_count": r["post_count"],
            "active_user_count": r["active_user_count"],
            "assignment_count": r["assignment_count"],
            "evidence_count": r["evidence_count"],
            "metrics_json": r["metrics_json"],
        }
        for r in rows
    ]


def _user_daily_rows(
    rows: list[dict[str, Any]],
    user_daily: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    run_id: str,
    forum_id: int,
    topic_id_map: dict[str, int],
) -> list[dict[str, Any]]:
    # topic_count for user_daily: distinct topics the user was active in on that day.
    tid_topics: dict[int, list[int]] = {}
    for a in assignments:
        topic_id = topic_id_map.get(a["topic_key"])
        if topic_id is None:
            continue
        tid_topics.setdefault(a["tid"], []).append(topic_id)
    user_day_topics: dict[tuple[str, str], set[int]] = {}
    for r in rows:
        bucket = _bucket_date_for(r)
        user_key = _user_key_for(r)
        if not bucket or not user_key:
            continue
        for topic_id in tid_topics.get(r["tid"], []):
            user_day_topics.setdefault((bucket, user_key), set()).add(topic_id)
    result: list[dict[str, Any]] = []
    for agg in user_daily:
        bucket = agg["bucket_date"]
        user_key = agg["user_key"]
        result.append(
            {
                "run_id": run_id,
                "forum_id": forum_id,
                "bucket_date": bucket,
                "user_key": user_key,
                "display_name": agg.get("display_name"),
                "thread_count": agg["thread_count"],
                "post_count": agg["post_count"],
                "topic_count": len(user_day_topics.get((bucket, user_key), set())),
                "metrics_json": agg.get("metrics_json", {}),
            }
        )
    return result
