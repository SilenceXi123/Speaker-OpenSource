import secrets

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.services.admin_auth import parse_admin_session_token


def require_v2_alpha_access(request: Request) -> None:
    if not settings.speaker_v2_shadow_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='资源不存在')

    session_token = request.cookies.get(settings.admin_session_cookie_name, '')
    if settings.admin_auth_is_configured and parse_admin_session_token(session_token):
        return

    expected_token = settings.speaker_v2_alpha_token.strip()
    provided_token = request.headers.get('X-Speaker-Alpha-Token', '')
    if not expected_token or not provided_token or not secrets.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='无权访问 Alpha 功能')
