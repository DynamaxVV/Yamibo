from __future__ import annotations

import json
from pathlib import Path

from yamibo_mcp.config import Settings
from yamibo_mcp.services import web_chat


class _FakeResponse:
    def __init__(self, chunks: list[bytes], status: int = 200):
        self._chunks = chunks
        self.status = status

    def read(self, size: int = -1) -> bytes:
        if not self._chunks:
            return b''
        return self._chunks.pop(0)


class _FakeConnection:
    instances: list['_FakeConnection'] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.requests = []
        self.response = _FakeResponse([
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
            b'data: [DONE]\n\n',
        ])
        _FakeConnection.instances.append(self)

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        pass



def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        config_path=tmp_path / 'yamibo.local.json',
        data_dir=tmp_path / 'data',
        db_path=tmp_path / 'data' / 'yamibo.db',
        db_backend='sqlite',
        db_url=None,
        db_pool_min=1,
        db_pool_max=5,
        db_pool_timeout=30.0,
        db_connect_timeout=10.0,
        db_schema='public',
        db_ssl_mode='prefer',
        db_ssl_root_cert=None,
        title_hints_path=tmp_path / 'title_hints.json',
        web_host='0.0.0.0',
        web_port=8765,
        worker_id='test',
        jobs_enabled=True,
        worker_poll_seconds=1.0,
        worker_parallelism=1,
        worker_lease_seconds=60,
        worker_heartbeat_seconds=15,
        cookie_file=tmp_path / 'cookies.cookie',
        login_username=None,
        login_password=None,
        use_system_proxy=False,
        image_download_timeout_seconds=30.0,
        image_download_retries=1,
        archive_thread_max_pages=50,
        novel_author_only_max_pages=50,
        novel_author_only_page_delay_seconds=0.5,
        export_dir=tmp_path / 'exports',
        novel_txt_export_dir=tmp_path / 'novels',
        export_default_strategy='cache_only',
        export_stale_after_hours=24,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
        llm_base_url='http://example.com/v1',
        llm_api_key=None,
        llm_model='gpt-4.1-mini',
        chat_transport='cli',
        chat_mcp_sse_url=None,
        hermes_api_key='test-key',
        hermes_model='hermes-agent',
        hermes_host='localhost',
        hermes_port=8642,
        hermes_stream=False,
        rag_enabled=False,
        rag_base_url='http://example.com/v1',
        rag_api_key=None,
        rag_debug_indexing=False,
        rag_embedding_provider='openai',
        rag_embedding_model='text-embedding-3-small',
        rag_embedding_dimensions=512,
        rag_chunker_version='rag-chunker-v1',
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=900,
        rag_hybrid_fts_candidates=50,
        rag_hybrid_vector_candidates=50,
        title_parse_use_llm=True,
        common_scanlation_groups=[],
        common_authors=[],
        backup_dir=tmp_path / 'backups',
        backup_keep_count=20,
        cleanup_staging_older_than_hours=48,
        request_timeout_seconds=30.0,
        request_interval_seconds=1.0,
        request_interval_jitter_seconds=0.5,
        account_pool=(),
        proxy_pool=SimpleNamespace(),
        cookie_refresh_interval_hours=24.0,
        image_backfill_enabled=False,
        image_backfill_dry_run=False,
        image_backfill_forum_id=0,
        image_backfill_auto_interval_seconds=0.0,
        image_backfill_daily_limit=0,
        image_backfill_max_pages=0,
        image_backfill_fixed_after=None,
    )



def test_run_chat_turn_stream_persists_final_message(tmp_path, monkeypatch):
    settings = _make_settings(tmp_path)
    monkeypatch.setattr(web_chat.http.client, 'HTTPConnection', _FakeConnection)
    monkeypatch.setattr(web_chat.http.client, 'HTTPSConnection', _FakeConnection)

    result = web_chat.run_chat_turn_stream(settings, session_id=None, message='Hello!', history=[])

    assert result['stream'] is True
    assert result['assistant_message'] == 'Hello'
    sessions = json.loads((tmp_path / 'data' / 'chat' / 'sessions.json').read_text(encoding='utf-8'))
    assert len(sessions) == 1
    assert sessions[0]['messages'][-1]['content'] == 'Hello'


class SimpleNamespace:
    pass
