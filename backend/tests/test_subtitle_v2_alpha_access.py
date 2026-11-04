from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.v2.alpha_access import require_v2_alpha_access
from app.core.config import Settings, settings
from app.services.admin_auth import build_admin_session_token


def _client() -> TestClient:
    app = FastAPI()

    @app.get('/alpha', dependencies=[Depends(require_v2_alpha_access)])
    def alpha_probe() -> dict[str, bool]:
        return {'ok': True}

    return TestClient(app)


def test_alpha_access_settings_are_disabled_by_default() -> None:
    isolated = Settings(_env_file=None)

    assert isolated.speaker_v2_shadow_enabled is False
    assert isolated.speaker_v2_alpha_token == ''


def test_alpha_access_returns_404_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'speaker_v2_shadow_enabled', False)
    monkeypatch.setattr(settings, 'speaker_v2_alpha_token', 'configured-secret')

    response = _client().get('/alpha', headers={'X-Speaker-Alpha-Token': 'configured-secret'})

    assert response.status_code == 404
    assert 'configured-secret' not in response.text


def test_alpha_access_returns_same_403_for_missing_and_wrong_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'speaker_v2_shadow_enabled', True)
    monkeypatch.setattr(settings, 'speaker_v2_alpha_token', 'configured-secret')
    client = _client()

    missing = client.get('/alpha')
    wrong = client.get('/alpha', headers={'X-Speaker-Alpha-Token': 'incoming-secret'})

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert missing.json() == wrong.json()
    assert 'configured-secret' not in wrong.text
    assert 'incoming-secret' not in wrong.text


def test_alpha_access_accepts_correct_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'speaker_v2_shadow_enabled', True)
    monkeypatch.setattr(settings, 'speaker_v2_alpha_token', 'configured-secret')

    response = _client().get('/alpha', headers={'X-Speaker-Alpha-Token': 'configured-secret'})

    assert response.status_code == 200
    assert response.json() == {'ok': True}


def test_alpha_access_accepts_valid_admin_session(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'speaker_v2_shadow_enabled', True)
    monkeypatch.setattr(settings, 'speaker_v2_alpha_token', 'configured-secret')
    monkeypatch.setattr(settings, 'admin_username', 'admin')
    monkeypatch.setattr(settings, 'admin_password_hash', 'configured-password-hash')
    monkeypatch.setattr(settings, 'admin_session_secret', 'session-secret')
    monkeypatch.setattr(settings, 'admin_session_cookie_name', 'speaker_admin_session')
    monkeypatch.setattr(settings, 'admin_session_max_age_seconds', 3600)
    client = _client()
    client.cookies.set(settings.admin_session_cookie_name, build_admin_session_token('admin'))

    response = client.get('/alpha')

    assert response.status_code == 200
    assert response.json() == {'ok': True}


def test_empty_configured_token_never_allows_empty_header(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'speaker_v2_shadow_enabled', True)
    monkeypatch.setattr(settings, 'speaker_v2_alpha_token', '')

    response = _client().get('/alpha', headers={'X-Speaker-Alpha-Token': ''})

    assert response.status_code == 403
