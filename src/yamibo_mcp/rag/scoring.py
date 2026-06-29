from __future__ import annotations

from typing import Literal


def normalize_keyword_rank(rank: int) -> float:
    return 1.0 / float(rank + 1)


def normalize_vector_distance(distance: float, *, metric: Literal["legacy", "cosine"] = "legacy") -> float:
    if metric == "cosine":
        return max(0.0, min(1.0, 1.0 - (max(distance, 0.0) / 2.0)))
    return 1.0 / (1.0 + max(distance, 0.0))


def metadata_score(
    *,
    tid_filter: int | None,
    series_id_filter: int | None,
    forum_id_filter: int | None,
    content_kind_filter: str | None,
    row_tid: int,
    row_series_id: int | None,
    row_forum_id: int | None,
    row_content_kind: str | None,
    floor_no: int | None,
) -> float:
    score = 0.0
    if tid_filter is not None and row_tid == tid_filter:
        score += 1.0
    if series_id_filter is not None and row_series_id == series_id_filter:
        score += 0.7
    if forum_id_filter is not None and row_forum_id == forum_id_filter:
        score += 0.4
    if content_kind_filter is not None and row_content_kind == content_kind_filter:
        score += 0.4
    if floor_no == 1:
        score += 0.2
    return min(score, 1.0)


def hybrid_score(*, keyword: float, vector: float, metadata: float) -> float:
    return (vector * 0.45) + (keyword * 0.35) + (metadata * 0.20)
