from __future__ import annotations

import runpy
from pathlib import Path


def _load_script():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "validate_shadow_readonly.py"
    return runpy.run_path(str(script_path))


class _FakeThreadRepo:
    def __init__(self, rows):
        self.rows = rows

    def search_threads(self, query, *, limit=50, forum_id=None):
        return self.rows.get(query, [])[:limit]


class _FakeVectorRepo:
    def __init__(self, rows):
        self.rows = rows

    def search(self, *, query_embedding, top_k, **_kwargs):
        return self.rows.get(tuple(query_embedding), [])[:top_k]


def test_shadow_validation_compare_helpers():
    script = _load_script()

    thread_signature = script["_thread_search_signature"]
    vector_signature = script["_vector_search_signature"]

    thread_repo = _FakeThreadRepo(
        {
            "same": [
                {"tid": 1, "display_title": "Title A", "raw_title": "Title A"},
                {"tid": 2, "display_title": "Title B", "raw_title": "Title B"},
            ],
            "diff": [{"tid": 3, "display_title": "Wrong", "raw_title": "Wrong"}],
        }
    )
    vector_repo = _FakeVectorRepo(
        {
            (0.1, 0.2): [
                {"chunk_id": "chunk-a", "vector_distance": 0.0},
                {"chunk_id": "chunk-b", "vector_distance": 0.2},
            ]
        }
    )

    assert thread_signature(thread_repo, "same", top_k=2) == [
        {"tid": 1, "title": "Title A"},
        {"tid": 2, "title": "Title B"},
    ]
    assert thread_signature(thread_repo, "diff", top_k=1) == [
        {"tid": 3, "title": "Wrong"},
    ]
    assert vector_signature(vector_repo, [0.1, 0.2], top_k=2) == [
        {"chunk_id": "chunk-a", "distance": 0.0},
        {"chunk_id": "chunk-b", "distance": 0.2},
    ]
