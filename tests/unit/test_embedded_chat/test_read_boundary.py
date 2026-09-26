import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from yamibo_mcp.config import load_settings
from yamibo_mcp.services.embedded_chat import mcp, read_boundary


def restricted(scope=None):
    tool = object.__new__(mcp.RestrictedTools)
    tool.settings = load_settings()
    tool.policy = SimpleNamespace(run_id='run')
    run = {'discussion_scope_id': 'scope', 'discussion_scope': scope or {
        'mode': 'selected', 'forum_ids': [5], 'tids': [123], 'pids': [456],
        'start_at': '2026-09-01T00:00:00+00:00', 'end_at': '2026-09-02T00:00:00+00:00',
    }, 'parent_id': 'session', 'input': '分析选中的讨论'}

    @contextmanager
    def transaction():
        yield None

    tool.store = SimpleNamespace(get=lambda *a, **k: run if a[0] == 'runs' else {'messages': []},
                                 transaction=transaction, guard=lambda *a: run)
    return tool


@pytest.mark.parametrize('name', [
    name for name, (_, handler) in mcp.PUBLIC_TOOLS.items()
    if getattr(handler, '__capability_metadata__', {}).get('effect') in {'read_only', 'remote_read'}
    and name not in {'read_job', 'wait_for_job', 'read_forum_profiles'}
])
def test_every_other_public_reader_rejected_before_handler(monkeypatch, name):
    def handler(*, start_page: int = 1):
        pytest.fail('out-of-scope reader executed')
    handler.__capability_metadata__ = {'effect': 'read_only'}
    monkeypatch.setitem(mcp.PUBLIC_TOOLS, name, ('test', handler))
    with pytest.raises(ValueError, match='SCOPED_READER_REQUIRED'):
        asyncio.run(restricted().public_call(name, {}))


@pytest.mark.parametrize('extra', [{'tids': [123]}, {'pids': [456]}, {'start_at': '2026-01-01'}, {'end_at': '2026-01-02'}, {'report_revision': 'r'}])
def test_remote_discovery_cannot_ignore_any_frozen_dimension(extra):
    tool = restricted({'mode': 'discovery', 'forum_ids': [5], **extra})
    with pytest.raises(ValueError, match='SCOPED_READER_REQUIRED'):
        tool.require_scoped_public_read('browse_forum_page', {'forum_id': 5})


def test_remote_discovery_requires_frozen_forum_and_ready_scope():
    tool = restricted({'mode': 'discovery', 'forum_ids': [5]})
    tool.require_scoped_public_read('search_forum_threads', {'forum_id': 5})
    with pytest.raises(ValueError, match='FORUM_SCOPE_MISMATCH'):
        tool.require_scoped_public_read('search_forum_threads', {'forum_id': 30})
    tool.store.get('runs', 'run')['scope_pending'] = True
    with pytest.raises(ValueError, match='DISCUSSION_SCOPE_UNAVAILABLE'):
        tool.require_scoped_public_read('read_forum_profiles', {})


def test_legacy_events_cannot_return_arbitrary_payloads():
    with pytest.raises(ValueError, match='TOOL_OR_ARGUMENT_NOT_ALLOWED'):
        restricted().reads('read_job_events', {'job_id': 'job'})


def test_job_status_selects_no_evidence_columns(db, monkeypatch):
    from yamibo_mcp.db.repositories.jobs import JobsRepository
    job = JobsRepository(db).create('thread_archive', tid=123)
    db.execute("UPDATE jobs SET status='succeeded', artifacts_json=?, error_message=? WHERE job_id=?",
               ('{"body":"outside source"}', 'outside error content', job.job_id))
    monkeypatch.setattr(read_boundary, 'connect', lambda *a, **k: SimpleNamespace(execute=db.execute, close=lambda: None))
    result = read_boundary.job_status(None, job.job_id)
    assert result['data']['result_ready'] is None
    assert result['data']['result_verification'] == 'unavailable_in_scoped_status'
    assert result['data']['tid'] == 123
    assert 'outside' not in str(result)
    assert 'artifacts' not in result['data']


def test_work_file_read_does_not_require_forum_scope(monkeypatch):
    async def invoke(self, name, args, handler, **kwargs):
        return await handler()

    monkeypatch.setattr(mcp.RestrictedTools, 'invoke', invoke)
    monkeypatch.setattr(mcp.RestrictedTools, 'discussion_scope', lambda self: pytest.fail('work file required forum scope'))
    monkeypatch.setattr(mcp.WorkFiles, 'read', lambda self, file_id, offset=0: {
        'file_id': file_id, 'revision': 1, 'content': '572313'[offset:], 'next_offset': None,
    })
    server = mcp.build_restricted_server(load_settings(), 'run')
    result = asyncio.run(server._tool_manager.call_tool('read_work_file', {'file_id': 'f', 'offset': 2}))
    assert result['ok'] is True
    assert result['data']['content'] == '2313'
    assert result['data']['source'] == 'work_file'
    assert 'receipt_id' not in result['data']


@pytest.mark.parametrize('name,arguments', [
    ('read_job', {'job_id': 'j'}),
    ('wait_for_job', {'job_id': 'j', 'include_events': True}),
])
def test_job_bridge_never_calls_body_or_events_reader(monkeypatch, name, arguments):
    original = mcp.PUBLIC_TOOLS[name][1]
    from functools import wraps

    @wraps(original)
    def forbidden(**kwargs):
        pytest.fail('public job reader loaded arbitrary artifact bodies')

    monkeypatch.setitem(mcp.PUBLIC_TOOLS, name, ('test', forbidden))
    monkeypatch.setattr(mcp, 'job_status', lambda settings, job_id: {
        'ok': True, 'data': {'job_id': job_id, 'is_terminal': True, 'status': 'succeeded'},
    })
    result = asyncio.run(restricted().public_call(name, arguments))
    assert result['data']['job_id'] == 'j'
    assert 'events' not in result['data']


def test_file_listing_uses_workspace_discovery(monkeypatch):
    async def invoke(self, name, args, handler, **kwargs):
        return await handler()

    monkeypatch.setattr(mcp.RestrictedTools, 'invoke', invoke)
    monkeypatch.setattr(mcp.WorkFiles, 'listing', lambda self: [
        {'file_id': 'f', 'name': 'result.md', 'revision': 1, 'source': 'user', 'session_id': ''},
    ])
    server = mcp.build_restricted_server(load_settings(), 'run')
    result = asyncio.run(server._tool_manager.call_tool('list_work_files', {}))
    assert result['data'][0]['file_id'] == 'f'
    assert result['data'][0]['source'] == 'user'


@pytest.mark.parametrize('status,error_code,classification', [
    ('partial', None, 'use_partial_result'),
    ('running', None, 'continue_waiting'),
    ('retrying', None, 'automatic_retry'),
    ('paused', 'REMOTE_MAINTENANCE', 'automatic_recovery'),
    ('failed', 'REMOTE_LOGIN_REQUIRED', 'user_action_required'),
])
def test_scoped_job_status_preserves_recovery_without_claiming_artifact_readiness(
    db, monkeypatch, status, error_code, classification,
):
    from yamibo_mcp.db.repositories.jobs import JobsRepository

    job = JobsRepository(db).create('thread_archive', tid=123)
    db.execute('UPDATE jobs SET status=?, error_code=? WHERE job_id=?', (status, error_code, job.job_id))
    monkeypatch.setattr(read_boundary, 'connect', lambda *a, **k: SimpleNamespace(execute=db.execute, close=lambda: None))
    data = read_boundary.job_status(None, job.job_id)['data']
    assert data['result_ready'] is None
    assert data['recovery']['classification'] == classification
    assert 'do not create a duplicate Job' in data['result_verification_message']
    if status == 'partial':
        assert data['is_terminal'] is True
        assert data['recovery']['retryable'] is False
        assert 'not been checked' in data['recovery']['message']


def test_legacy_reads_are_only_job_status():
    assert set(mcp.READS) == {'read_job'}
    with pytest.raises(ValueError, match='TOOL_OR_ARGUMENT_NOT_ALLOWED'):
        restricted().reads('read_forum_profiles', {})
