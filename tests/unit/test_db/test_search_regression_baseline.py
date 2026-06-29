from __future__ import annotations

import json
from pathlib import Path


def test_search_regression_baseline_has_expected_shape():
    baseline_path = Path(__file__).resolve().parents[2] / "fixtures" / "search_regression_baseline.json"
    data = json.loads(baseline_path.read_text(encoding="utf-8"))

    assert data["version"] == 1
    assert len(data["thread_search"]) >= 20
    assert len(data["rag_vector_search"]) >= 20

    for entry in data["thread_search"]:
        assert "query" in entry
        assert "expected_tids" in entry
        assert "expected_titles" in entry
        assert entry["top_k"] >= 1

    for entry in data["rag_vector_search"]:
        assert "query" in entry
        assert "expected" in entry
        assert entry["top_k"] >= 1
        for expected in entry["expected"]:
            assert "chunk_id" in expected
            assert "distance" in expected
