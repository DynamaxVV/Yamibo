from __future__ import annotations

from yamibo_mcp.web_fastapi import archive_counts


class _CountsRepo:
    def __init__(self):
        self.calls = {"summary": 0, "filtered": 0}

    def archive_count_summary(self):
        self.calls["summary"] += 1
        return {"thread_count": 12, "export_count": 4, "forum_counts": {30: 12}}

    def count_threads_filtered(self, **kwargs):
        self.calls["filtered"] += 1
        return 12


def test_archive_counts_are_shared_between_dashboard_and_forums():
    archive_counts.clear_archive_counts_cache()
    repo = _CountsRepo()

    dashboard_counts = archive_counts.get_archive_counts(repo, database_scope="test-db")
    forum_counts = archive_counts.get_archive_counts(repo, database_scope="test-db")

    assert dashboard_counts == {
        "thread_count": 12,
        "export_count": 4,
        "forum_counts": {30: 12},
    }
    assert forum_counts["forum_counts"] == {30: 12}
    assert repo.calls == {"summary": 1, "filtered": 0}


def test_unsearched_thread_counts_are_reused_by_filter():
    archive_counts.clear_archive_counts_cache()
    repo = _CountsRepo()
    filters = {
        "q": None,
        "forum_id": 30,
        "days": None,
        "archive_status": None,
        "database_scope": "test-db",
    }

    assert archive_counts.get_thread_list_count(repo, **filters) == 12
    assert archive_counts.get_thread_list_count(repo, **filters) == 12
    assert repo.calls["filtered"] == 1


def test_search_counts_are_not_cached():
    archive_counts.clear_archive_counts_cache()
    repo = _CountsRepo()

    for _ in range(2):
        assert archive_counts.get_thread_list_count(
            repo,
            q="archive title",
            forum_id=30,
            days=None,
            archive_status=None,
            database_scope="test-db",
        ) is None

    assert repo.calls["filtered"] == 0
