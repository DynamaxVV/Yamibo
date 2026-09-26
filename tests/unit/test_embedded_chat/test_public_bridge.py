import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from yamibo_mcp.services.embedded_chat import mcp
from yamibo_mcp.config import load_settings
from yamibo_mcp.domain.forums import default_forums


def test_every_known_forum_name_and_explicit_id_resolves():
    for profile in default_forums():
        assert mcp.requested_forum_ids(f'搜索{profile.name}') == {profile.forum_id}
        assert mcp.requested_forum_ids(f'搜索 forum_id={profile.forum_id}') == {profile.forum_id}
    assert mcp.requested_forum_ids('比较漫画区和动漫区') == {30, 5}


def test_all_public_capabilities_have_safe_schema_and_guidance(monkeypatch):
    monkeypatch.setenv('YAMIBO_CHAT_RUN_SCOPE_MODE', 'all')
    server = mcp.build_restricted_server(load_settings(), 'test-run')
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert {'discover_public_tools', 'describe_public_tool', 'call_public_tool'} <= tools.keys()
    for name in mcp.PUBLIC_TOOLS:
        assert not set(mcp.public_input_model(name).model_fields) & mcp.UNSAFE_PUBLIC_ARGUMENTS
    assert {'find_discussions', 'read_discussion_source', 'wait_for_jobs'} <= tools.keys()


class Store:
    def __init__(self, op):
        self.op = op
        self.user_input = '在漫画区搜索星灵感应'
        self.scope_forums = [p.forum_id for p in default_forums()]
        self.remote_forums = [30]

    @contextmanager
    def transaction(self):
        yield self

    def guard(self, *args):
        return {}

    def get(self, *args, **kwargs):
        if args[0] == 'runs':
            return {'parent_id': 'session', 'input': self.user_input, 'discussion_scope_id': 'scope',
                    'discussion_scope': {'mode': 'discovery', 'forum_ids': self.scope_forums,
                                         'remote_forum_ids': self.remote_forums}}
        if args[0] == 'sessions':
            return {'messages': []}
        return dict(self.op)

    def save(self, kind, value, conn):
        self.op = dict(value)


def bridge(monkeypatch, name, handler, effect, status='approved'):
    handler.__capability_metadata__ = {'effect': effect}
    monkeypatch.setitem(mcp.PUBLIC_TOOLS, name, ('test', handler))
    instance = object.__new__(mcp.RestrictedTools)
    instance.settings = load_settings()
    instance.store = Store({'id': 'op', 'status': status})
    instance.policy = SimpleNamespace(run_id='run', request=lambda *args: dict(instance.store.op))
    return instance


def test_remote_search_clamps_to_single_page(monkeypatch):
    def handler(*, forum_id: int = 30, start_page: int = 1, end_page: int | None = None,
                posted_on: str | None = None):
        return {'ok': True, 'data': [forum_id, start_page, end_page]}
    instance = bridge(monkeypatch, 'search_forum_threads', handler, 'remote_read')
    result = asyncio.run(instance.public_call('search_forum_threads', {'start_page': 3, 'end_page': 900}))
    assert result['data'] == [30, 3, 3]
    with pytest.raises(ValueError, match='FORUM_SCOPE_MISMATCH'):
        asyncio.run(instance.public_call('search_forum_threads', {'forum_id': 5}))
    with pytest.raises(ValueError, match='POSTED_ON_UNBOUNDED'):
        asyncio.run(instance.public_call('search_forum_threads', {'posted_on': '2026-09-25'}))
    with pytest.raises(ValueError):
        asyncio.run(instance.public_call('search_forum_threads', {'cookie_file': '/secret'}))


def test_named_enabled_comic_forum_can_be_searched_outside_discussion_scope(monkeypatch):
    calls = []

    def handler(*, query: str = '', forum_id: int = 30,
                start_page: int = 1, end_page: int | None = None):
        calls.append((query, forum_id, start_page, end_page))
        return {'ok': True, 'data': {'source': 'forum', 'forum_id': forum_id}}

    instance = bridge(monkeypatch, 'search_forum_threads', handler, 'remote_read')
    instance.store.scope_forums = [5, 13]
    instance.store.remote_forums = [30]
    instance.store.user_input = '请在远端论坛漫画区搜索星灵感应，只报告真实远端结果'
    monkeypatch.setattr(mcp.RestrictedTools, 'forum_is_enabled', lambda self, forum_id: forum_id == 30)

    result = asyncio.run(instance.public_call(
        'search_forum_threads', {'query': '星灵感应', 'forum_id': 30},
    ))

    assert result['data'] == {'source': 'forum', 'forum_id': 30}
    assert calls == [('星灵感应', 30, 1, 1)]


def test_named_but_disabled_forum_cannot_bypass_discussion_scope(monkeypatch):
    def handler(*, query: str = '', forum_id: int = 30,
                start_page: int = 1, end_page: int | None = None):
        pytest.fail('disabled forum reached the remote handler')

    instance = bridge(monkeypatch, 'search_forum_threads', handler, 'remote_read')
    instance.store.scope_forums = [5, 13]
    instance.store.remote_forums = [30]
    instance.store.user_input = '请在漫画区搜索星灵感应'
    monkeypatch.setattr(mcp.RestrictedTools, 'forum_is_enabled', lambda self, forum_id: False)

    with pytest.raises(ValueError, match='FORUM_SCOPE_MISMATCH'):
        asyncio.run(instance.public_call('search_forum_threads', {'forum_id': 30}))


@pytest.mark.parametrize(('name', 'arguments'), [
    ('search_archived_content', {'query': 'x', 'top_k': 10**9}),
    ('get_discussion_topic_trends', {'forum_id': 5, 'start_date': '2026-01-01', 'end_date': '2026-01-02', 'top_k': 10**9}),
    ('create_thread_archive_batch_jobs', {'tids': list(range(1, 202))}),
    ('browse_forum_page', {'page': -1}),
    ('search_archived_content', {'query': 'x' * 501}),
])
def test_public_bridge_rejects_unbounded_arguments_before_execution(name, arguments):
    with pytest.raises(ValueError):
        mcp.validate_public_arguments(name, arguments)


def test_public_call_rejects_large_top_k_before_handler(monkeypatch):
    def handler(*, query: str, top_k: int = 10):
        pytest.fail('unbounded query reached handler')
    instance = bridge(monkeypatch, 'search_archived_content', handler, 'read_only')
    with pytest.raises(ValueError, match='top_k'):
        asyncio.run(instance.public_call('search_archived_content', {'query': 'x', 'top_k': 10**9}))


@pytest.mark.parametrize('forum', default_forums())
def test_remote_search_requires_each_named_forum_id(monkeypatch, forum):
    def handler(*, query: str = '', forum_id: int = 30,
                start_page: int = 1, end_page: int | None = None):
        return {'ok': True, 'data': {'forum_id': forum_id}}
    instance = bridge(monkeypatch, 'search_forum_threads', handler, 'remote_read')
    instance.store.user_input = f'在{forum.name}搜索'
    instance.store.remote_forums = [forum.forum_id]
    assert asyncio.run(instance.public_call('search_forum_threads', {'forum_id': forum.forum_id}))['data']['forum_id'] == forum.forum_id
    with pytest.raises(ValueError, match='FORUM_SCOPE_MISMATCH'):
        asyncio.run(instance.public_call('search_forum_threads', {'forum_id': 999}))


@pytest.mark.parametrize('status', ['denied', 'outcome_unknown'])
def test_unapproved_write_never_executes(monkeypatch, status):
    def handler(*, tid: int):
        pytest.fail('write must not execute')
    instance = bridge(monkeypatch, 'create_thread_archive_job', handler, 'enqueue_job', status)
    result = asyncio.run(instance.public_call('create_thread_archive_job', {'tid': 1}))
    assert result['error']['code'] == status


def test_approved_write_marks_uncertainty_and_replays_receipt(monkeypatch):
    calls = []
    def handler(*, tid: int):
        assert instance.store.op['status'] == 'outcome_unknown'
        calls.append(tid)
        return {'ok': True, 'data': {'job_id': 'job'}}
    instance = bridge(monkeypatch, 'create_thread_archive_job', handler, 'enqueue_job')
    first = asyncio.run(instance.public_call('create_thread_archive_job', {'tid': 1}))
    assert instance.store.op['status'] == 'completed'
    assert asyncio.run(instance.public_call('create_thread_archive_job', {'tid': 1})) == first
    assert calls == [1]


def test_write_exception_keeps_unknown_receipt(monkeypatch):
    def handler(*, tid: int):
        raise RuntimeError('connection lost after submission')
    instance = bridge(monkeypatch, 'create_thread_archive_job', handler, 'enqueue_job')
    with pytest.raises(RuntimeError):
        asyncio.run(instance.public_call('create_thread_archive_job', {'tid': 1}))
    assert instance.store.op['status'] == 'outcome_unknown'


def test_guidance_reads_existing_document_with_pagination(monkeypatch):
    async def invoke(self, name, args, handler, **kwargs):
        assert name == 'read_yamibo_guidance'
        return await handler()
    monkeypatch.setattr(mcp.RestrictedTools, 'invoke', invoke)
    server = mcp.build_restricted_server(load_settings(), 'test-run')
    async def run():
        return await server._tool_manager.call_tool('read_yamibo_guidance', {'topic': 'agent-workflows'})
    result = asyncio.run(run())
    assert result['ok']
    assert result['data']['text']
    assert len(result['data']['text']) <= 12000
    assert 'path' not in result['data']
