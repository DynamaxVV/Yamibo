from __future__ import annotations

import pytest

from yamibo_mcp.yamibo.waf_challenge import (
    WafChallengeError,
    extract_nox_script_url,
    parse_cookie_header,
    solve_nox_challenge,
    solve_nox_challenge_if_present,
)


def test_extract_nox_script_url_requires_same_origin():
    html = '<script src="/waf/nox_current.js"></script>'

    assert extract_nox_script_url(
        html,
        page_url="https://bbs.yamibo.com/plugin.php?id=zqlj_sign",
    ) == "https://bbs.yamibo.com/waf/nox_current.js"

    with pytest.raises(WafChallengeError):
        extract_nox_script_url(
            '<script src="https://example.com/nox_current.js"></script>',
            page_url="https://bbs.yamibo.com/plugin.php?id=zqlj_sign",
        )


def test_parse_cookie_header_deduplicates_cookie_names():
    assert parse_cookie_header("sid=old; nox_jst_v1=old; nox_jst_v1=new") == [
        ("sid", "old"),
        ("nox_jst_v1", "new"),
    ]


def test_solve_nox_challenge_executes_without_browser():
    result = solve_nox_challenge(
        "document.cookie = 'nox_jst_v1=generated';",
        page_url="https://bbs.yamibo.com/plugin.php?id=zqlj_sign",
        cookie_header="sid=session",
        user_agent="test-agent",
    )

    assert parse_cookie_header(result) == [
        ("sid", "session"),
        ("nox_jst_v1", "generated"),
    ]


def test_solve_nox_challenge_if_present_fetches_advertised_script():
    html = '<script src="/static/nox_current.js"></script>'
    fetched: list[str] = []

    cookies = solve_nox_challenge_if_present(
        html,
        page_url="https://bbs.yamibo.com/plugin.php?id=zqlj_sign",
        user_agent="test-agent",
        cookie_pairs=[],
        fetch_script=lambda url: fetched.append(url) or "document.cookie = 'nox_jst_v1=generated';",
    )

    assert fetched == ["https://bbs.yamibo.com/static/nox_current.js"]
    assert cookies == [("nox_jst_v1", "generated")]
