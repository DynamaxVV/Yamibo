from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError


def _provider(content='OK'):
    provider = MagicMock()
    provider.return_value.__enter__.return_value.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    return provider


def test_probe_draft_makes_completion_without_saving(client, test_settings):
    before = test_settings.config_path.read_bytes() if test_settings.config_path.exists() else None
    provider = _provider()
    with patch('openai.OpenAI', provider):
        response = client.post('/api/settings/model-test', json={
            'base_url': 'http://localhost:8317/v1', 'model': 'test-model', 'api_key': 'secret-draft',
        })
    assert response.status_code == 200
    assert response.json()['ok'] is True
    assert 'secret-draft' not in response.text
    assert provider.call_args.kwargs == dict(base_url='http://localhost:8317/v1', api_key='secret-draft', timeout=30, max_retries=0)
    assert provider.return_value.__enter__.return_value.chat.completions.create.call_args.kwargs['model'] == 'test-model'
    after = test_settings.config_path.read_bytes() if test_settings.config_path.exists() else None
    assert before == after


@pytest.mark.parametrize('status,code', [(401, 'AUTH_ERROR'), (403, 'AUTH_ERROR'), (404, 'NOT_FOUND'), (429, 'RATE_LIMITED'), (500, 'PROVIDER_ERROR')])
def test_probe_safe_http_error(client, status, code):
    provider = _provider()
    provider.return_value.__enter__.return_value.chat.completions.create.side_effect = APIStatusError(
        'upstream secret-draft', response=httpx.Response(status, request=httpx.Request('POST', 'http://model')), body={'secret': 'secret-draft'},
    )
    with patch('openai.OpenAI', provider):
        response = client.post('/api/settings/model-test', json={})
    assert response.json()['error_code'] == code
    assert response.json()['http_status'] == status
    assert 'secret-draft' not in response.text


@pytest.mark.parametrize('error,code', [(APITimeoutError(request=httpx.Request('POST', 'http://model')), 'TIMEOUT'), (APIConnectionError(request=httpx.Request('POST', 'http://model')), 'CONNECTION_ERROR')])
def test_probe_transport_error(client, error, code):
    provider = _provider()
    provider.return_value.__enter__.return_value.chat.completions.create.side_effect = error
    with patch('openai.OpenAI', provider):
        response = client.post('/api/settings/model-test', json={})
    assert response.json()['error_code'] == code


def test_probe_empty_output_is_not_success(client):
    with patch('openai.OpenAI', _provider(None)):
        response = client.post('/api/settings/model-test', json={})
    assert response.json()['error_code'] == 'EMPTY_RESPONSE'


@pytest.mark.parametrize('body', [{'base_url': 'file:///etc/passwd'}, {'base_url': 'http://user:secret@host'}, {'model': ''}, {'api_key': 123}])
def test_probe_rejects_bad_input_before_network(client, body):
    with patch('openai.OpenAI') as provider:
        response = client.post('/api/settings/model-test', json=body)
    assert response.status_code == 400
    provider.assert_not_called()


def test_probe_blank_key_reuses_saved_key(client, test_settings):
    import json
    test_settings.config_path.write_text(json.dumps({'llm': {'api_key': 'saved-secret'}}))
    provider = _provider()
    with patch.dict('os.environ', {}, clear=True), patch('openai.OpenAI', provider):
        response = client.post('/api/settings/model-test', json={'api_key': ''})
    assert response.json()['ok'] is True
    assert provider.call_args.kwargs['api_key'] == 'saved-secret'
    assert 'saved-secret' not in response.text


def test_probe_region_rejection(client):
    provider = _provider()
    provider.return_value.__enter__.return_value.chat.completions.create.side_effect = APIStatusError(
        'User location is not supported for the API use. secret',
        response=httpx.Response(400, request=httpx.Request('POST', 'http://model')), body=None,
    )
    with patch('openai.OpenAI', provider):
        response = client.post('/api/settings/model-test', json={})
    assert response.json()['error_code'] == 'REGION_UNSUPPORTED'
    assert 'secret' not in response.text
