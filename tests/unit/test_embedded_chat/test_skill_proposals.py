from types import SimpleNamespace
import pytest
from yamibo_mcp.services.embedded_chat.skill_proposals import SkillProposals


def test_review_and_stale_proposal(tmp_path):
    settings = SimpleNamespace(data_dir=tmp_path)
    service = SkillProposals(settings)
    original = service.read('forum-search')
    first = service.propose('forum-search', 'new guidance', 'reason', original['revision'])
    stale = service.propose('forum-search', 'other guidance', 'reason', original['revision'])
    assert service.read('forum-search') == original
    service.review(first['id'], approve=True)
    assert SkillProposals(settings).read('forum-search')['content'] == 'new guidance'
    with pytest.raises(ValueError, match='CONFLICT'):
        service.review(stale['id'], approve=True)
    with pytest.raises(ValueError, match='ALREADY_REVIEWED'):
        service.review(first['id'], approve=True)
    service.review(stale['id'], approve=False)
    assert service.read('forum-search')['content'] == 'new guidance'


def test_invalid_paths_and_empty_content(tmp_path):
    service = SkillProposals(SimpleNamespace(data_dir=tmp_path))
    with pytest.raises(ValueError, match='INVALID_PROJECT_SKILL'):
        service.read('../SOUL')
    with pytest.raises(ValueError, match='INVALID_SKILL_PROPOSAL'):
        service.propose('forum-search', '', 'reason', 'revision')
    (service.root / 'state.json').symlink_to(tmp_path / 'other')
    with pytest.raises(ValueError, match='UNSAFE_SKILL_STATE'):
        service.list()


def test_review_api(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yamibo_mcp.web_fastapi.routers.skill_reviews import router
    from yamibo_mcp.web_fastapi.deps import get_settings
    settings = SimpleNamespace(data_dir=tmp_path)
    service = SkillProposals(settings)
    revision = service.read('daily-report')['revision']
    proposal = service.propose('daily-report', 'Reviewed guidance', 'Correction', revision)
    app = FastAPI()
    app.include_router(router)
    from yamibo_mcp.web_fastapi.settings_session import install_settings_sessions
    settings.settings_access_token = "test-review-token"
    install_settings_sessions(app, settings)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        endpoint = f"/api/settings/skill-reviews/{proposal['id']}/review"
        assert client.get('/api/settings/skill-reviews').status_code == 401
        assert client.post(endpoint, json={'approve': True}, headers={'origin': 'http://testserver'}).status_code == 401
        assert service.read('daily-report')['revision'] == revision
        assert client.post('/api/settings/session', json={'token': 'test-review-token'}, headers={'origin': 'http://testserver'}).status_code == 200
        assert client.post(endpoint, json={'approve': True}, headers={'origin': 'https://evil.example'}).status_code == 403
        client.headers['origin'] = 'http://testserver'
        assert client.get('/api/settings/skill-reviews').json()['proposals'][0]['diff']
        assert client.post('/api/settings/skill-reviews/missing/review', json={'approve': True}).status_code == 404
        url = f"/api/settings/skill-reviews/{proposal['id']}/review"
        assert client.post(url, json={'approve': True}).status_code == 200
        assert client.post(url, json={'approve': True}).status_code == 409
    assert service.read('daily-report')['content'] == 'Reviewed guidance'
