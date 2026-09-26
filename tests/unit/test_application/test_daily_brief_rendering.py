from yamibo_mcp.application.daily_brief_service import _render_markdown


def test_topics_render_concrete_claims_and_clickable_sources():
    report = {
        'facts': {'target_day': '2023-08-28', 'candidates': [
            {'tid': 10, 'title': '旧专楼', 'activity_kind': 'old_thread', 'target_day_pid_count': 2, 'participant_count': 2},
        ]},
        'source_receipts': [{'receipt_id': 'r', 'tid': 10, 'pid': 101}],
        'editorial': {'topics': [{'title': '续作讨论', 'claims': [
            {'text': '用户质疑续作消息的来源。', 'kind': 'opinion', 'temporal_role': 'target_day', 'citations': [{'receipt_id': 'r'}]},
        ]}]},
    }
    text = _render_markdown(report)
    assert '用户质疑续作消息的来源' in text
    assert 'goto=findpost&ptid=10&pid=101' in text
    assert '[来源 1]' in text
    assert 'TID 10' not in text and 'PID 101' not in text
    assert '旧帖新讨论' in text
    assert '当日讨论 · 用户观点' not in text
    assert '核查来源：' in text
    assert '累计回复数' in text


def test_legacy_recommendations_are_not_discarded():
    text = _render_markdown({'facts': {'target_day': '2023-08-28'}, 'editorial': {'recommendations': [
        {'tid': 10, 'summary': '具体内容', 'reason': '推荐理由', 'citations': []},
    ]}})
    assert '具体内容' in text
    assert '推荐理由' not in text


def test_single_board_identity_is_explicit_even_without_candidates():
    for forum_id, name, subtitle in [(33, '海域区', '话题与生活交流'), (5, '动漫区', '作品资讯与剧情讨论'), (30, '漫画区', '漫画更新与读者讨论')]:
        text = _render_markdown({'facts': {'target_day': '2026-07-06', 'coverage': {'requested_forum_ids': [forum_id]}}})
        assert text.startswith(f'# {name}日报｜2026-07-06')
        assert f'{name} · {subtitle}' in text
