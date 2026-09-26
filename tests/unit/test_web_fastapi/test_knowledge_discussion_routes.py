from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from yamibo_mcp.web_fastapi.app import create_app


def test_discussion_search_route_is_strict_and_uses_discussion_scope(db, test_settings):
    db.execute(
        "INSERT INTO forums(forum_id, name, content_kind, base_url, enabled) VALUES(987654, '讨论', 'discussion', 'https://example.invalid', 1) ON CONFLICT(forum_id) DO UPDATE SET content_kind='discussion', enabled=1"
    )
    db.execute(
        "INSERT INTO forums(forum_id, name, content_kind, base_url, enabled) VALUES(987655, '漫画', 'comic', 'https://example.invalid', 1) ON CONFLICT(forum_id) DO UPDATE SET content_kind='comic', enabled=1"
    )
    db.execute(
        """INSERT INTO threads(tid, page_type, raw_title, pub_time, sync_time, last_pid,
                  archive_status, validation_status, forum_id, content_kind)
           VALUES(987650001, 'discussion', '目标讨论', '2026-09-25', '2026-09-25', 987660001,
                  'complete', 'valid', 987654, 'discussion')"""
    )
    db.execute(
        """INSERT INTO threads(tid, page_type, raw_title, pub_time, sync_time, last_pid,
                  archive_status, validation_status, forum_id, content_kind)
           VALUES(987650002, 'comic', '不应出现', '2026-09-25', '2026-09-25', 987660002,
                  'complete', 'valid', 987655, 'comic')"""
    )
    db.commit()
    db_path = str(db._conn.engine.url.database)
    with patch("yamibo_mcp.application.discussion_discovery_queries.load_settings", return_value=SimpleNamespace(db_path=db_path)):
        app = create_app(test_settings)
        client = TestClient(app)
        response = client.post(
            "/api/knowledge/discussions/search", json={"query": "目标", "tids": [987650001, 987650002]}
        )
        assert response.status_code == 200
        assert [item["tid"] for item in response.json()["items"]] == [987650001]

        invalid_forum = client.post(
            "/api/knowledge/discussions/search", json={"query": "目标", "forum_ids": [987655], "tids": [987650001, 987650002]}
        )
        assert invalid_forum.status_code == 422
        assert invalid_forum.json()["detail"]["code"] == "INVALID_FORUM_SCOPE"
        malformed = client.post(
            "/api/knowledge/discussions/search", json={"query": "目标", "tids": [987650001], "limit": "10"}
        )
        assert malformed.status_code == 422
