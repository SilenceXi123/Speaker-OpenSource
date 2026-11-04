from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from typing import Optional

from fastapi import HTTPException, Request, Response, status

from app.core.config import settings


PASSWORD_SCHEME = 'pbkdf2_sha256'


def hash_admin_password(password: str, *, salt: Optional[str] = None, iterations: int = 600_000) -> str:
    normalized_password = str(password or '')
    if not normalized_password:
        raise ValueError('password is empty')
    normalized_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        'sha256',
        normalized_password.encode('utf-8'),
        normalized_salt.encode('utf-8'),
        iterations,
    )
    encoded_digest = urlsafe_b64encode(digest).decode('utf-8').rstrip('=')
    return f'{PASSWORD_SCHEME}${iterations}${normalized_salt}${encoded_digest}'


def verify_admin_password(password: str, password_hash: str) -> bool:
    parts = str(password_hash or '').split('$', 3)
    if len(parts) != 4 or parts[0] != PASSWORD_SCHEME:
        return False
    _, iterations_text, salt, expected_digest = parts
    try:
        iterations = int(iterations_text)
    except ValueError:
        return False
    calculated = hash_admin_password(password, salt=salt, iterations=iterations)
    return hmac.compare_digest(calculated, password_hash)


def authenticate_admin(username: str, password: str) -> str:
    ensure_admin_auth_configured()
    normalized_username = str(username or '').strip()
    if not hmac.compare_digest(normalized_username, settings.admin_username.strip()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='账号或密码错误')
    if not verify_admin_password(password, settings.admin_password_hash.strip()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='账号或密码错误')
    return normalized_username


def write_admin_session_cookie(response: Response, username: str) -> None:
    token = build_admin_session_token(username)
    response.set_cookie(
        key=settings.admin_session_cookie_name,
        value=token,
        max_age=settings.admin_session_max_age_seconds,
        httponly=True,
        samesite='lax',
        secure=bool(settings.admin_session_cookie_secure),
        path='/',
    )


def clear_admin_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.admin_session_cookie_name,
        path='/',
        secure=bool(settings.admin_session_cookie_secure),
        samesite='lax',
    )


def require_admin_session(request: Request) -> str:
    ensure_admin_auth_configured()
    token = request.cookies.get(settings.admin_session_cookie_name, '')
    username = parse_admin_session_token(token)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='管理员登录已失效，请重新登录')
    return username


def ensure_admin_auth_configured() -> None:
    if settings.admin_auth_is_configured:
        return
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='管理员登录尚未配置')


def build_admin_session_token(username: str) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.admin_session_max_age_seconds)
    payload = {
        'username': str(username or '').strip(),
        'exp': int(expires_at.timestamp()),
    }
    encoded_payload = _urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
    signature = _sign(encoded_payload)
    return f'{encoded_payload}.{signature}'


def parse_admin_session_token(token: str) -> Optional[str]:
    payload_token, _, signature = str(token or '').partition('.')
    if not payload_token or not signature:
        return None
    expected_signature = _sign(payload_token)
    if not hmac.compare_digest(signature, expected_signature):
        return None
    try:
        payload = json.loads(_urlsafe_b64decode(payload_token).decode('utf-8'))
    except Exception:
        return None
    username = str(payload.get('username') or '').strip()
    expires_at = int(payload.get('exp') or 0)
    if not username or expires_at <= int(datetime.now(timezone.utc).timestamp()):
        return None
    return username


def _sign(payload_token: str) -> str:
    digest = hmac.new(
        settings.admin_session_secret.encode('utf-8'),
        payload_token.encode('utf-8'),
        hashlib.sha256,
    ).digest()
    return _urlsafe_b64encode(digest)


def _urlsafe_b64encode(value: bytes) -> str:
    return urlsafe_b64encode(value).decode('utf-8').rstrip('=')


def _urlsafe_b64decode(value: str) -> bytes:
    padding = '=' * (-len(value) % 4)
    return urlsafe_b64decode((value + padding).encode('utf-8'))
