import pytest
from fastapi import HTTPException
from tests.unit.test_embedded_chat.test_runtime import settings, service
from yamibo_mcp.web_fastapi.routers.settings import agent_guidance_get, agent_guidance_update


def test_guidance_save_and_conflict_preserves_latest(service):
    before = agent_guidance_get(service)
    saved = agent_guidance_update({'content': '用户指导', 'revision': before['revision']}, service)
    assert saved['revision'] == before['revision'] + 1
    assert service.files.guidance() == '用户指导'
    with pytest.raises(HTTPException) as exc:
        agent_guidance_update({'content': '陈旧草稿', 'revision': before['revision']}, service)
    assert exc.value.status_code == 409
    assert agent_guidance_get(service) == saved


def test_guidance_rejects_invalid_body(service):
    before = agent_guidance_get(service)
    for body in ({'content': 'x'}, {'content': '字' * 6000, 'revision': before['revision']}, {'content': 'x', 'revision': True}):
        with pytest.raises(HTTPException) as exc:
            agent_guidance_update(body, service)
        assert exc.value.status_code == 422
    assert agent_guidance_get(service) == before


def test_guidance_rejects_nonembedded():
    with pytest.raises(HTTPException) as exc:
        agent_guidance_get(object())
    assert exc.value.status_code == 409


def test_guidance_routes_use_settings_auth_and_origin(service):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yamibo_mcp.web_fastapi.routers.settings import router
    from yamibo_mcp.web_fastapi.deps import get_chat_service
    from yamibo_mcp.web_fastapi.settings_session import install_settings_sessions
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_chat_service] = lambda: service
    install_settings_sessions(app, SimpleNamespace(settings_access_token='test-token'))
    client = TestClient(app)
    assert client.get('/api/settings/agent-guidance').status_code == 401
    assert client.post('/api/settings/agent-guidance', json={}).status_code == 403
    assert client.post('/api/settings/session', headers={'origin': 'http://testserver'}, json={'token': 'test-token'}).status_code == 200
    before = client.get('/api/settings/agent-guidance').json()
    response = client.post('/api/settings/agent-guidance', headers={'origin': 'http://testserver'}, json={'content': '已认证编辑', 'revision': before['revision']})
    assert response.status_code == 200
    assert service.files.guidance() == '已认证编辑'
