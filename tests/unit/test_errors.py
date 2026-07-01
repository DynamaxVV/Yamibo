import pytest

from yamibo_mcp.errors import (
    LoginRequiredError,
    RemoteAccessPausedError,
    RemoteFetchError,
    RemoteMaintenanceError,
    ThreadPermissionRequiredError,
    UnexpectedPageError,
    classify_error,
    reclassify_error_code,
)


class TestClassifyErrorRemoteFetch:
    def test_http_404_from_status_code(self):
        exc = RemoteFetchError("fail", details={"status_code": 404})
        assert classify_error(exc) == "REMOTE_HTTP_404"

    def test_http_404_from_message(self):
        exc = RemoteFetchError(
            "failed to fetch url after 3 attempt(s): HTTP Error 404: Not Found",
            details={"last_error_message": "HTTP Error 404: Not Found"},
        )
        assert classify_error(exc) == "REMOTE_HTTP_404"

    def test_http_403(self):
        exc = RemoteFetchError("fail", details={"status_code": 403})
        assert classify_error(exc) == "REMOTE_HTTP_403"

    def test_http_500(self):
        exc = RemoteFetchError("fail", details={"status_code": 500})
        assert classify_error(exc) == "REMOTE_HTTP_5XX"

    def test_http_502(self):
        exc = RemoteFetchError("fail", details={"status_code": 502})
        assert classify_error(exc) == "REMOTE_HTTP_5XX"

    def test_http_other(self):
        exc = RemoteFetchError(
            "fail", details={"status_code": 418, "last_error_message": "HTTP Error 418: I'm a teapot"}
        )
        assert classify_error(exc) == "REMOTE_HTTP_XXX"

    def test_soft_block(self):
        exc = RemoteFetchError("soft block (CF challenge / CAPTCHA) detected for url")
        assert classify_error(exc) == "REMOTE_SOFT_BLOCK"

    def test_soft_block_in_details_message(self):
        exc = RemoteFetchError(
            "generic fetch error",
            details={"last_error_message": "soft block (CF challenge) detected"},
        )
        assert classify_error(exc) == "REMOTE_SOFT_BLOCK"

    def test_timeout_from_last_error_type(self):
        exc = RemoteFetchError(
            "failed to fetch url: timed out",
            details={"last_error_type": "TimeoutError"},
        )
        assert classify_error(exc) == "REMOTE_TIMEOUT"

    def test_timeout_from_message(self):
        exc = RemoteFetchError("failed to fetch url after 3 attempt(s) with timeout=30.0s: timed out")
        assert classify_error(exc) == "REMOTE_TIMEOUT"

    def test_connection_error_from_last_error_type_urlerror(self):
        exc = RemoteFetchError(
            "failed to fetch url",
            details={"last_error_type": "URLError", "last_error_message": "Connection refused"},
        )
        assert classify_error(exc) == "REMOTE_CONNECTION_ERROR"

    def test_connection_error_from_last_error_type_remote_disconnected(self):
        exc = RemoteFetchError(
            "failed to fetch url",
            details={"last_error_type": "RemoteDisconnected"},
        )
        assert classify_error(exc) == "REMOTE_CONNECTION_ERROR"

    def test_connection_error_from_message(self):
        exc = RemoteFetchError("Connection reset by peer")
        assert classify_error(exc) == "REMOTE_CONNECTION_ERROR"

    def test_fallback(self):
        exc = RemoteFetchError("some unknown fetch problem")
        assert classify_error(exc) == "REMOTE_FETCH_FAILED"

    def test_no_details(self):
        exc = RemoteFetchError("something went wrong")
        assert classify_error(exc) == "REMOTE_FETCH_FAILED"


class TestClassifyErrorUnexpectedPage:
    def test_thread_deleted_255(self):
        exc = UnexpectedPageError(
            "expected thread detail page but got prompt page for url: 本帖已经删除，错误权限代码255"
        )
        assert classify_error(exc) == "THREAD_DELETED"

    def test_thread_deleted_30_is_permission(self):
        exc = UnexpectedPageError(
            "expected thread detail page but got prompt page for url: 本帖已经删除，错误权限代码30"
        )
        assert classify_error(exc) == "REMOTE_THREAD_PERMISSION_REQUIRED"

    def test_thread_deleted_40_is_permission(self):
        exc = UnexpectedPageError(
            "expected thread detail page but got prompt page for url: 本帖已经删除，错误权限代码40"
        )
        assert classify_error(exc) == "REMOTE_THREAD_PERMISSION_REQUIRED"

    def test_thread_deleted_no_code_is_permission(self):
        exc = UnexpectedPageError(
            "expected thread detail page but got prompt page for url: 本帖已经删除"
        )
        assert classify_error(exc) == "REMOTE_THREAD_PERMISSION_REQUIRED"

    def test_group_access_denied(self):
        exc = UnexpectedPageError(
            "expected thread detail page but got prompt page for url: 抱歉，您没有权限访问该群组"
        )
        assert classify_error(exc) == "GROUP_ACCESS_DENIED"

    def test_user_group_upgrade(self):
        exc = UnexpectedPageError(
            "expected thread detail page but got prompt page for url: 抱歉，您需要升级您所在的用户组后才能访问该版块，详细请 点击这里查看 。"
        )
        assert classify_error(exc) == "REMOTE_THREAD_PERMISSION_REQUIRED"

    def test_fallback(self):
        exc = UnexpectedPageError("expected thread detail page but got something_else for url")
        assert classify_error(exc) == "UNEXPECTED_REMOTE_PAGE"


class TestClassifyErrorOther:
    def test_login_required(self):
        exc = LoginRequiredError("login required for url")
        assert classify_error(exc) == "REMOTE_LOGIN_REQUIRED"

    def test_remote_maintenance(self):
        exc = RemoteMaintenanceError("remote maintenance for url")
        assert classify_error(exc) == "REMOTE_MAINTENANCE"

    def test_remote_access_paused(self):
        exc = RemoteAccessPausedError("access paused")
        assert classify_error(exc) == "REMOTE_ACCESS_PAUSED"

    def test_thread_permission_required(self):
        exc = ThreadPermissionRequiredError("thread requires read permission above 30")
        assert classify_error(exc) == "REMOTE_THREAD_PERMISSION_REQUIRED"

    def test_value_error(self):
        exc = ValueError("invalid something")
        assert classify_error(exc) == "INVALID_ARGUMENT"

    def test_file_not_found(self):
        exc = FileNotFoundError("no such file")
        assert classify_error(exc) == "LOCAL_ARCHIVE_NOT_FOUND"

    def test_unknown_exception(self):
        exc = RuntimeError("something unexpected")
        assert classify_error(exc) == "INTERNAL_ERROR"


class TestReclassifyErrorCode:
    def test_remote_fetch_http_404(self):
        assert (
            reclassify_error_code(
                "RemoteFetchError",
                "failed to fetch url after 3 attempt(s) with timeout=30.0s: HTTP Error 404: Not Found",
            )
            == "REMOTE_HTTP_404"
        )

    def test_remote_fetch_http_500(self):
        assert (
            reclassify_error_code(
                "RemoteFetchError",
                "failed to fetch url after 3 attempt(s): HTTP Error 500: Internal Server Error",
            )
            == "REMOTE_HTTP_5XX"
        )

    def test_remote_fetch_soft_block(self):
        assert (
            reclassify_error_code(
                "RemoteFetchError",
                "soft block (CF challenge / CAPTCHA) detected for url",
            )
            == "REMOTE_SOFT_BLOCK"
        )

    def test_remote_fetch_timeout(self):
        assert (
            reclassify_error_code("RemoteFetchError", "failed to fetch url: timed out")
            == "REMOTE_TIMEOUT"
        )

    def test_remote_fetch_connection_error(self):
        assert (
            reclassify_error_code("RemoteFetchError", "Connection refused")
            == "REMOTE_CONNECTION_ERROR"
        )

    def test_remote_fetch_fallback(self):
        assert (
            reclassify_error_code("RemoteFetchError", "some unknown error")
            == "REMOTE_FETCH_FAILED"
        )

    def test_unexpected_page_deleted_255(self):
        assert (
            reclassify_error_code(
                "UnexpectedPageError",
                "expected thread detail page but got prompt page for url: 本帖已经删除，错误权限代码255",
            )
            == "THREAD_DELETED"
        )

    def test_unexpected_page_deleted_30_is_permission(self):
        assert (
            reclassify_error_code(
                "UnexpectedPageError",
                "expected thread detail page but got prompt page for url: 本帖已经删除，错误权限代码30",
            )
            == "REMOTE_THREAD_PERMISSION_REQUIRED"
        )

    def test_unexpected_page_group_denied(self):
        assert (
            reclassify_error_code(
                "UnexpectedPageError",
                "expected thread detail page but got prompt page for url: 抱歉，您没有权限访问该群组",
            )
            == "GROUP_ACCESS_DENIED"
        )

    def test_unexpected_page_upgrade(self):
        assert (
            reclassify_error_code(
                "UnexpectedPageError",
                "expected thread detail page but got prompt page for url: 抱歉，您需要升级您所在的用户组后才能访问该版块",
            )
            == "REMOTE_THREAD_PERMISSION_REQUIRED"
        )

    def test_unexpected_page_fallback(self):
        assert (
            reclassify_error_code(
                "UnexpectedPageError",
                "expected thread detail page but got something_else for url",
            )
            == "UNEXPECTED_REMOTE_PAGE"
        )

    def test_login_required(self):
        assert (
            reclassify_error_code("LoginRequiredError", "could not parse login form")
            == "REMOTE_LOGIN_REQUIRED"
        )

    def test_already_fine_grained_passthrough(self):
        assert reclassify_error_code("REMOTE_HTTP_404", "some message") == "REMOTE_HTTP_404"
        assert reclassify_error_code("CANCELLED", "some message") == "CANCELLED"

    def test_thread_deleted_30_reclassified_to_permission(self):
        """第一次 backfill 后已是 THREAD_DELETED，二次运行应纠正为 REMOTE_THREAD_PERMISSION_REQUIRED。"""
        assert (
            reclassify_error_code(
                "THREAD_DELETED",
                "expected thread detail page but got prompt page for url: 本帖已经删除，错误权限代码30",
            )
            == "REMOTE_THREAD_PERMISSION_REQUIRED"
        )

    def test_thread_deleted_255_stays(self):
        assert (
            reclassify_error_code(
                "THREAD_DELETED",
                "expected thread detail page but got prompt page for url: 本帖已经删除，错误权限代码255",
            )
            == "THREAD_DELETED"
        )

    def test_none_message(self):
        assert reclassify_error_code("RemoteFetchError", None) == "REMOTE_FETCH_FAILED"
