"""论坛列表解析器测试"""

import pytest
from tests.fixtures.loader import load_forum_page, get_all_forum_pages


FORUM_PAGES = [1, 2, 3, 5, 10, 20]

ALL_CATEGORIES = {
    item["category"]
    for page in get_all_forum_pages()
    for item in page["items"]
}


class TestForumListParser:
    """论坛列表解析器测试"""

    def test_page_structure_has_source_and_items(self):
        # Arrange
        data = load_forum_page(1)

        # Act
        source = data["source"]
        items = data["items"]

        # Assert
        assert source == "forum_page"
        assert isinstance(items, list)
        assert len(items) > 0

    @pytest.mark.parametrize("page", FORUM_PAGES, ids=str)
    def test_page_structure_consistency(self, page):
        # Arrange
        data = load_forum_page(page)

        # Assert
        assert data["source"] == "forum_page"
        assert isinstance(data["page"], int)
        assert isinstance(data["items"], list)

    def test_items_have_required_fields(self):
        # Arrange
        data = load_forum_page(1)
        required_fields = ["tid", "display_title", "category", "publisher", "posted_at"]

        # Act & Assert
        for item in data["items"]:
            for field in required_fields:
                assert field in item, f"Missing field {field} in item {item.get('tid')}"

    def test_tid_is_positive_integer(self):
        # Arrange
        data = load_forum_page(1)

        # Act & Assert
        for item in data["items"]:
            assert isinstance(item["tid"], int)
            assert item["tid"] > 0

    def test_url_contains_tid(self):
        # Arrange
        data = load_forum_page(1)

        # Act & Assert
        for item in data["items"]:
            assert item["url"].startswith("https://bbs.yamibo.com/")
            assert f"tid={item['tid']}" in item["url"]

    @pytest.mark.parametrize("page", FORUM_PAGES, ids=str)
    def test_archived_items_have_series_info(self, page):
        # Arrange
        data = load_forum_page(page)
        archived = [i for i in data["items"] if i.get("archive_status") == "complete"]
        if not archived:
            pytest.skip(f"Page {page} has no archived items")

        # Act & Assert
        for item in archived:
            assert item.get("series_id") is not None
            assert item.get("series_key") is not None
            assert item.get("resources") is not None

    @pytest.mark.parametrize("page", FORUM_PAGES, ids=str)
    def test_unarchived_items_lack_series_info(self, page):
        # Arrange
        data = load_forum_page(page)
        unarchived = [i for i in data["items"] if i.get("archive_status") is None]
        if not unarchived:
            pytest.skip(f"Page {page} has no unarchived items")

        # Act & Assert
        for item in unarchived:
            assert item.get("series_id") is None
            assert item.get("series_key") is None

    @pytest.mark.parametrize("page", [2, 3, 5], ids=str)
    def test_exported_items_have_zip_path(self, page):
        # Arrange
        data = load_forum_page(page)
        exported = [i for i in data["items"] if i.get("export_path") is not None]
        if not exported:
            pytest.skip(f"Page {page} has no exported items")

        # Act & Assert
        for item in exported:
            assert item["export_path"].endswith(".zip")

    @pytest.mark.parametrize("page", FORUM_PAGES, ids=str)
    def test_category_is_valid(self, page):
        # Arrange
        data = load_forum_page(page)

        # Act & Assert
        for item in data["items"]:
            assert item["category"] in ALL_CATEGORIES, f"Invalid category: {item['category']}"

    @pytest.mark.parametrize("page", FORUM_PAGES, ids=str)
    def test_reply_count_is_non_negative_integer(self, page):
        # Arrange
        data = load_forum_page(page)

        # Act & Assert
        for item in data["items"]:
            assert isinstance(item["reply_count"], int)
            assert item["reply_count"] >= 0
