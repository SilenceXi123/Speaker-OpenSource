from fastapi import HTTPException
from fastapi.responses import Response

from app.core.config import settings
from app.services.admin_auth import (
    authenticate_admin,
    build_admin_session_token,
    clear_admin_session_cookie,
    hash_admin_password,
    parse_admin_session_token,
    verify_admin_password,
    write_admin_session_cookie,
)


def test_hash_and_verify_admin_password() -> None:
    password_hash = hash_admin_password('secret-pass', salt='fixed-salt', iterations=1000)

    assert verify_admin_password('secret-pass', password_hash) is True
    assert verify_admin_password('wrong-pass', password_hash) is False


def test_authenticate_admin_accepts_matching_username_and_password(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'admin_username', 'admin')
    monkeypatch.setattr(settings, 'admin_password_hash', hash_admin_password('demo-pass', salt='salt-1', iterations=1000))
    monkeypatch.setattr(settings, 'admin_session_secret', 'session-secret')

    assert authenticate_admin('admin', 'demo-pass') == 'admin'


def test_authenticate_admin_raises_for_invalid_password(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'admin_username', 'admin')
    monkeypatch.setattr(settings, 'admin_password_hash', hash_admin_password('demo-pass', salt='salt-1', iterations=1000))
    monkeypatch.setattr(settings, 'admin_session_secret', 'session-secret')

    try:
        authenticate_admin('admin', 'bad-pass')
    except HTTPException as error:
        assert error.status_code == 401
        assert error.detail == '账号或密码错误'
    else:
        raise AssertionError('expected HTTPException')


def test_admin_session_token_roundtrip(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'admin_session_secret', 'session-secret')
    monkeypatch.setattr(settings, 'admin_session_max_age_seconds', 3600)

    token = build_admin_session_token('admin')

    assert parse_admin_session_token(token) == 'admin'


def test_admin_session_cookie_helpers(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'admin_session_secret', 'session-secret')
    monkeypatch.setattr(settings, 'admin_session_max_age_seconds', 3600)
    monkeypatch.setattr(settings, 'admin_session_cookie_name', 'speaker_admin_session')
    monkeypatch.setattr(settings, 'admin_session_cookie_secure', False)

    response = Response()
    write_admin_session_cookie(response, 'admin')

    set_cookie_header = response.headers.get('set-cookie', '')
    assert 'speaker_admin_session=' in set_cookie_header
    assert 'HttpOnly' in set_cookie_header

    clear_admin_session_cookie(response)
    cleared_header = response.headers.getlist('set-cookie')[-1]
    assert 'Max-Age=0' in cleared_header
