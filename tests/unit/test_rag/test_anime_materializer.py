from __future__ import annotations

import json

from yamibo_mcp.rag.anime_dry_run import build_anime_dry_run_thread
from yamibo_mcp.rag.anime_materializer import (
    MATERIALIZER_VERSION,
    _build_thread_materialization,
    _write_thread_artifacts,
)
from yamibo_mcp.storage.paths import StoragePaths


def test_thread_materializer_writes_cleaned_json_markdown_and_preview(tmp_path):
    thread_row = {
        "tid": 1001,
        "raw_title": "[情报] 官网资料",
        "display_title": "[情报] 官网资料",
        "publisher": "alice",
        "pub_time": "2026-01-01T00:00:00",
        "forum_id": 5,
        "content_kind": "thread",
        "category": "[情报]",
        "series_id": None,
        "archive_status": "partial",
    }
    floor_rows = [
        {
            "pid": 2001,
            "tid": 1001,
            "floor_no": 1,
            "publisher": "alice",
            "pub_time": "2026-01-01T00:00:00",
            "content": "官网 https://example.com/a https://example.com/b https://example.com/c https://example.com/d",
            "has_images": False,
        }
    ]
    result = build_anime_dry_run_thread(thread_row=thread_row, floor_rows=floor_rows)
    materialized = _build_thread_materialization(result=result, thread_row=thread_row)

    context_path = tmp_path / "threads" / "1001" / "context.md"
    metadata_path = tmp_path / "threads" / "1001" / "metadata.json"
    context_path.parent.mkdir(parents=True)
    context_path.write_text("canonical context", encoding="utf-8")
    metadata_path.write_text('{"canonical": true}', encoding="utf-8")

    _write_thread_artifacts(paths=StoragePaths(tmp_path), tid=1001, materialized=materialized)

    json_path = tmp_path / "threads" / "1001" / f"rag_cleaned.{MATERIALIZER_VERSION}.json"
    md_path = tmp_path / "threads" / "1001" / f"rag_cleaned.{MATERIALIZER_VERSION}.md"
    preview_path = tmp_path / "threads" / "1001" / f"rag_chunks.preview.{MATERIALIZER_VERSION}.jsonl"
    marker_path = tmp_path / "threads" / "1001" / f"rag_materialized.{MATERIALIZER_VERSION}.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    preview_rows = [json.loads(line) for line in preview_path.read_text(encoding="utf-8").splitlines()]
    marker = json.loads(marker_path.read_text(encoding="utf-8"))

    assert json_path.exists()
    assert md_path.exists()
    assert preview_path.exists()
    assert marker_path.exists()
    assert context_path.read_text(encoding="utf-8") == "canonical context"
    assert metadata_path.read_text(encoding="utf-8") == '{"canonical": true}'
    assert payload["archive_status"] == "partial"
    assert payload["floors"][0]["quality_flags"] == ["url_heavy"]
    assert preview_rows
    assert marker["complete"] is True
    assert set(marker["artifacts"]) == {json_path.name, md_path.name, preview_path.name}
    assert preview_rows[0]["schema_version"] == 1
    assert "官网资料" in md_path.read_text(encoding="utf-8")
