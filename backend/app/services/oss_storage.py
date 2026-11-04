from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote, urlparse

import oss2
from fastapi import HTTPException, UploadFile

from app.core.config import settings
from app.schemas.provider_cost import ProviderCallContext, ProviderCallObservation, ProviderMeterObservation
from app.schemas.upload import UploadResponse
from app.services.provider_costs import record_legacy_provider_observation


logger = logging.getLogger(__name__)


async def upload_file_to_oss(
    file: UploadFile,
    *,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
) -> UploadResponse:
    if not settings.oss_is_configured:
        raise HTTPException(status_code=400, detail='OSS 配置不完整')

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail='上传文件为空')

    object_key = _build_object_key(file.filename, settings.oss_upload_prefix)
    return _put_bytes_to_oss(
        payload=payload,
        object_key=object_key,
        content_type=file.content_type or 'application/octet-stream',
        provider_context=provider_context,
        observation_key=observation_key,
    )


def upload_local_file_to_oss(
    file_path: Path,
    prefix: str,
    content_type: str,
    *,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
) -> UploadResponse:
    if not settings.oss_is_configured:
        raise HTTPException(status_code=400, detail='OSS 配置不完整')
    if not file_path.exists():
        raise HTTPException(status_code=400, detail='待上传文件不存在')

    payload = file_path.read_bytes()
    object_key = _build_object_key(file_path.name, prefix)
    return _put_bytes_to_oss(
        payload=payload,
        object_key=object_key,
        content_type=content_type,
        provider_context=provider_context,
        observation_key=observation_key,
    )


def build_public_url(object_key: str) -> str:
    base = settings.oss_public_base_url.rstrip('/')
    return f'{base}/{quote(object_key)}'


def owned_oss_object_key(public_url: Optional[str]) -> Optional[str]:
    raw_url = str(public_url or '').strip()
    base_url = str(settings.oss_public_base_url or '').strip().rstrip('/')
    if not raw_url or not base_url:
        return None
    candidate = urlparse(raw_url)
    base = urlparse(base_url)
    candidate_scheme = candidate.scheme.lower()
    base_scheme = base.scheme.lower()
    try:
        candidate_port = candidate.port or (
            443 if candidate_scheme == 'https' else 80
        )
        base_port = base.port or (443 if base_scheme == 'https' else 80)
    except ValueError:
        return None
    if (
        base_scheme not in {'http', 'https'}
        or candidate_scheme not in {'http', 'https'}
        or candidate_scheme != base_scheme
        or (candidate.hostname or '').lower() != (base.hostname or '').lower()
        or candidate_port != base_port
    ):
        return None
    base_path = unquote(base.path).rstrip('/')
    candidate_path = unquote(candidate.path)
    if base_path and not candidate_path.startswith(f'{base_path}/'):
        return None
    relative_path = candidate_path[len(base_path):].lstrip('/')
    if (
        not relative_path
        or any(part in {'.', '..'} for part in relative_path.split('/'))
    ):
        return None
    return relative_path


def delete_oss_object(
    object_key: str,
    *,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
    object_size_bytes: Optional[int] = None,
) -> None:
    normalized_key = str(object_key or '').strip()
    if not normalized_key or not settings.oss_is_configured:
        return
    auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
    bucket = oss2.Bucket(auth, f'https://{settings.oss_region}.aliyuncs.com', settings.oss_bucket)
    started_at = _utcnow()
    try:
        result = bucket.delete_object(normalized_key)
    except Exception as exc:
        _record_oss_observation(
            provider_context,
            operation='delete_object',
            observation_key=observation_key,
            status='failed',
            started_at=started_at,
            error=exc,
        )
        raise
    size = _safe_size(object_size_bytes)
    usage = {'request_count': 1}
    meters = [ProviderMeterObservation(meter='request_count', quantity=1, unit='request')]
    if size is not None:
        usage['object_bytes'] = size
        meters.insert(0, ProviderMeterObservation(meter='storage_bytes', quantity=size, unit='byte'))
    _record_oss_observation(
        provider_context,
        operation='delete_object',
        observation_key=observation_key,
        status='completed',
        request_id=_result_request_id(result),
        usage=usage,
        meters=meters,
        started_at=started_at,
    )


def _build_object_key(filename: str, prefix: str) -> str:
    safe_name = Path(filename).name.replace(' ', '_')
    stamp = datetime.utcnow().strftime('%Y%m%d/%H%M%S')
    return f"{prefix.rstrip('/')}/{stamp}-{safe_name}"


def _put_bytes_to_oss(
    payload: bytes,
    object_key: str,
    content_type: str,
    *,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
) -> UploadResponse:
    auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
    bucket = oss2.Bucket(auth, f'https://{settings.oss_region}.aliyuncs.com', settings.oss_bucket)
    started_at = _utcnow()
    try:
        result = bucket.put_object(object_key, payload, headers={'Content-Type': content_type})
        if result.status >= 300:
            raise HTTPException(status_code=502, detail='上传 OSS 失败')
    except Exception as exc:
        _record_oss_observation(
            provider_context,
            operation='put_object',
            observation_key=observation_key,
            status='failed',
            started_at=started_at,
            error=exc,
        )
        raise

    size = len(payload)
    _record_oss_observation(
        provider_context,
        operation='put_object',
        observation_key=observation_key,
        status='completed',
        request_id=_result_request_id(result),
        usage={'object_bytes': size, 'request_count': 1},
        meters=[
            ProviderMeterObservation(meter='storage_bytes', quantity=size, unit='byte'),
            ProviderMeterObservation(meter='request_count', quantity=1, unit='request'),
        ],
        started_at=started_at,
    )

    return UploadResponse(
        object_key=object_key,
        public_url=build_public_url(object_key),
        content_type=content_type,
        size_bytes=len(payload),
    )


def _record_oss_observation(
    context: Optional[ProviderCallContext],
    *,
    operation: str,
    observation_key: Optional[str],
    status: str,
    request_id: Optional[str] = None,
    usage: Optional[dict[str, int]] = None,
    meters: Optional[list[ProviderMeterObservation]] = None,
    started_at: Optional[datetime] = None,
    error: Optional[Exception] = None,
) -> None:
    if context is None or not observation_key:
        return
    try:
        record_legacy_provider_observation(
            context,
            provider='aliyun_oss',
            model=settings.oss_bucket or 'unknown-bucket',
            operation=operation,
            observed_call=ProviderCallObservation(
                idempotency_key=observation_key,
                status=status,
                request_id=request_id,
                usage=usage,
                meters=meters or [],
                started_at=started_at,
                completed_at=_utcnow(),
                error_code=type(error).__name__ if error else None,
                error_summary=f'{type(error).__name__}: OSS operation failed' if error else None,
            ),
        )
    except Exception as exc:  # pragma: no cover - observation must stay fail-open
        logger.warning('OSS provider observation skipped (%s)', type(exc).__name__)


def _result_request_id(result) -> Optional[str]:
    return str(getattr(result, 'request_id', None) or '').strip() or None


def _safe_size(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        size = int(value)
    except (TypeError, ValueError):
        return None
    return size if size >= 0 else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
