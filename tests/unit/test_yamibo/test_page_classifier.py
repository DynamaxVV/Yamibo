import pytest

from yamibo_mcp.yamibo.page_classifier import PageType, classify_html


class TestClassifyMaintenance:
    def test_maintenance_marker(self):
        html = '<div>百合会每日维护</div>'
        result = classify_html(html)
        assert result.page_type == PageType.REMOTE_MAINTENANCE

    def test_maintenance_alt_text(self):
        html = '<img alt="每日维护" />'
        result = classify_html(html)
        assert result.page_type == PageType.REMOTE_MAINTENANCE


class TestClassifyLoginRequired:
    @pytest.mark.parametrize("marker", [
        "您尚未登录",
        "没有权限访问该版块",
        "用户登录",
        "<title>登录",
    ], ids=str)
    def test_login_markers(self, marker):
        result = classify_html(f"<html>{marker}</html>")
        assert result.page_type == PageType.LOGIN_REQUIRED

    def test_pg_logging_class(self):
        html = '<div class="pg_logging">form</div>'
        result = classify_html(html)
        assert result.page_type == PageType.LOGIN_REQUIRED


class TestClassifyThreadDetail:
    def test_thread_subject_double_quotes(self):
        html = '<span id="thread_subject">标题</span>'
        result = classify_html(html)
        assert result.page_type == PageType.THREAD_DETAIL

    def test_thread_subject_single_quotes(self):
        html = "<span id='thread_subject'>标题</span>"
        result = classify_html(html)
        assert result.page_type == PageType.THREAD_DETAIL


class TestClassifyForumList:
    def test_threadlisttableid(self):
        html = '<table id="threadlisttableid">...</table>'
        result = classify_html(html)
        assert result.page_type == PageType.FORUM_LIST

    def test_normalthread(self):
        html = '<tbody id="normalthread_123">...</tbody>'
        result = classify_html(html)
        assert result.page_type == PageType.FORUM_LIST

    def test_xst_class(self):
        html = '<a class="s xst">title</a>'
        result = classify_html(html)
        assert result.page_type == PageType.FORUM_LIST


class TestClassifySearchResult:
    def test_pbw_and_xs3(self):
        html = '<li class="pbw"><h3 class="xs3">result</h3></li>'
        result = classify_html(html)
        assert result.page_type == PageType.SEARCH_RESULT

    def test_search_title(self):
        html = '<title>搜索结果</title>'
        result = classify_html(html)
        assert result.page_type == PageType.SEARCH_RESULT


class TestClassifyUnknown:
    def test_empty_html(self):
        result = classify_html("")
        assert result.page_type == PageType.UNKNOWN

    def test_unrecognized_page(self):
        result = classify_html("<html><body>nothing here</body></html>")
        assert result.page_type == PageType.UNKNOWN
