from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import logging
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.schemas.provider_cost import ProviderCallContext, ProviderCallObservation, ProviderMeterObservation
from app.services.provider_costs import record_legacy_provider_observation


logger = logging.getLogger(__name__)


class QwenVoiceCloneError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None, code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def create_qwen_voice(
    *,
    sample_public_url: str,
    target_model: str,
    voice_name: str,
    language_hints: Optional[list[str]] = None,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
    attempt: int = 1,
) -> dict[str, Any]:
    input_payload: dict[str, Any] = {
        'action': 'create',
        'target_model': target_model,
        'preferred_name': _normalize_preferred_name(voice_name),
        'audio': {
            'data': sample_public_url,
        },
    }
    language = _normalize_language(language_hints)
    if language:
        input_payload['language'] = language
    return _post_customization(
        {
            'model': 'qwen-voice-enrollment',
            'input': input_payload,
        },
        target_model=target_model,
        operation='create_voice',
        provider_context=provider_context,
        observation_key=observation_key,
        attempt=attempt,
    )


def delete_qwen_voice(
    voice: str,
    *,
    target_model: str = 'qwen-voice-enrollment',
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
) -> dict[str, Any]:
    return _post_customization(
        {
            'model': 'qwen-voice-enrollment',
            'input': {
                'action': 'delete',
                'voice': voice,
            },
        },
        target_model=target_model,
        operation='delete_voice',
        provider_context=provider_context,
        observation_key=observation_key,
    )


def synthesize_qwen_voice(
    *,
    target_model: str,
    voice: str,
    text: str,
    instruction: Optional[str] = None,
    language_hints: Optional[list[str]] = None,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
    attempt: int = 1,
) -> dict[str, Any]:
    input_payload: dict[str, Any] = {
        'text': text,
        'voice': voice,
    }
    normalized_instruction = str(instruction or '').strip()
    if normalized_instruction and supports_qwen_instructions(target_model):
        input_payload['instructions'] = normalized_instruction
        input_payload['optimize_instructions'] = True
    language_type = _normalize_language_type(language_hints)
    if language_type:
        input_payload['language_type'] = language_type
    return _post_generation(
        {
            'model': target_model,
            'input': input_payload,
        },
        target_model=target_model,
        operation='synthesize_voice',
        provider_context=provider_context,
        observation_key=observation_key,
        attempt=attempt,
    )


def supports_qwen_instructions(target_model: str) -> bool:
    normalized = str(target_model or '').strip().lower()
    return 'qwen3-tts' in normalized


def _post_customization(
    payload: dict[str, Any],
    *,
    target_model: str,
    operation: str,
    provider_context: Optional[ProviderCallContext],
    observation_key: Optional[str],
    attempt: int = 1,
) -> dict[str, Any]:
    return _post_with_observation(
        _resolve_customization_url(),
        payload,
        timeout=90,
        label='千问声音复刻接口',
        target_model=target_model,
        operation=operation,
        provider_context=provider_context,
        observation_key=observation_key,
        attempt=attempt,
    )


def _post_generation(
    payload: dict[str, Any],
    *,
    target_model: str,
    operation: str,
    provider_context: Optional[ProviderCallContext],
    observation_key: Optional[str],
    attempt: int = 1,
) -> dict[str, Any]:
    return _post_with_observation(
        _resolve_generation_url(),
        payload,
        timeout=120,
        label='千问语音合成接口',
        target_model=target_model,
        operation=operation,
        provider_context=provider_context,
        observation_key=observation_key,
        attempt=attempt,
    )


def _post_with_observation(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int,
    label: str,
    target_model: str,
    operation: str,
    provider_context: Optional[ProviderCallContext],
    observation_key: Optional[str],
    attempt: int,
) -> dict[str, Any]:
    started_at = _utcnow()
    try:
        response = httpx.post(
            url,
            headers={
                'Authorization': f'Bearer {settings.dashscope_api_key}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=timeout,
        )
        data = _parse_response(response, label)
    except Exception as exc:
        _record_voice_observation(
            provider_context,
            target_model=target_model,
            operation=operation,
            observation_key=observation_key,
            attempt=attempt,
            status='failed',
            started_at=started_at,
            error=exc,
        )
        raise
    usage = _voice_usage(data)
    _record_voice_observation(
        provider_context,
        target_model=target_model,
        operation=operation,
        observation_key=observation_key,
        attempt=attempt,
        status='completed',
        request_id=_request_id(data),
        usage=usage,
        meters=_voice_meters(operation, usage),
        started_at=started_at,
    )
    return data


def _record_voice_observation(
    context: Optional[ProviderCallContext],
    *,
    target_model: str,
    operation: str,
    observation_key: Optional[str],
    attempt: int,
    status: str,
    request_id: Optional[str] = None,
    usage: Optional[dict[str, int | float]] = None,
    meters: Optional[list[ProviderMeterObservation]] = None,
    started_at: Optional[datetime] = None,
    error: Optional[Exception] = None,
) -> None:
    if context is None or not observation_key:
        return
    try:
        record_legacy_provider_observation(
            context,
            provider='qwen',
            model=target_model,
            operation=operation,
            observed_call=ProviderCallObservation(
                idempotency_key=observation_key,
                attempt=attempt,
                status=status,
                request_id=request_id,
                usage=usage,
                meters=meters or [],
                started_at=started_at,
                completed_at=_utcnow(),
                error_code=type(error).__name__ if error else None,
                error_summary=f'{type(error).__name__}: Qwen voice request failed' if error else None,
            ),
        )
    except Exception as exc:  # pragma: no cover - observation must stay fail-open
        logger.warning('Qwen voice observation skipped (%s)', type(exc).__name__)


def _voice_usage(data: dict[str, Any]) -> Optional[dict[str, int | float]]:
    raw_usage = data.get('usage')
    if not isinstance(raw_usage, dict):
        return None
    usage = {
        key: value
        for key in ('voice_count', 'characters', 'audio_seconds', 'input_tokens', 'output_tokens')
        if (value := _nonnegative_number(raw_usage.get(key))) is not None
    }
    return usage or None


def _voice_meters(
    operation: str,
    usage: Optional[dict[str, int | float]],
) -> list[ProviderMeterObservation]:
    if operation == 'create_voice':
        quantity = usage.get('voice_count') if usage else None
        return [ProviderMeterObservation(meter='voice_count', quantity=quantity, unit='voice')]
    mapping = (
        ('characters', 'characters', 'character'),
        ('audio_seconds', 'audio_seconds', 'second'),
        ('input_tokens', 'input_tokens', 'token'),
        ('output_tokens', 'output_tokens', 'token'),
    )
    meters = [
        ProviderMeterObservation(meter=meter, quantity=Decimal(str(usage[key])), unit=unit)
        for key, meter, unit in mapping
        if usage and key in usage
    ]
    return meters or [ProviderMeterObservation(meter='characters', quantity=None, unit='character')]


def _nonnegative_number(value: Any) -> Optional[int | float]:
    if isinstance(value, bool):
        return None
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not quantity.is_finite() or quantity < 0:
        return None
    return int(quantity) if quantity == quantity.to_integral_value() else float(quantity)


def _request_id(data: dict[str, Any]) -> Optional[str]:
    return str(data.get('request_id') or data.get('request-id') or data.get('id') or '').strip() or None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_response(response: httpx.Response, label: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise QwenVoiceCloneError(f'{label}返回了非 JSON 响应: {response.text[:500]}') from exc
    if response.status_code >= 400 or data.get('code'):
        message = str(data.get('message') or data.get('code') or response.text[:500]).strip()
        if 'maximum voice-clone voice limit exceeded' in message.lower():
            message = '云端复刻音色数量已达上限，请清理不再使用的云端音色后重试'
        raise QwenVoiceCloneError(f'{label}请求失败: {message}',
            status_code=response.status_code, code=data.get('code'))
    return data


def _resolve_customization_url() -> str:
    base_url = settings.dashscope_base_url.rstrip('/')
    if settings.voice_cloning_region == 'intl':
        return 'https://dashscope-intl.aliyuncs.com/api/v1/services/audio/tts/customization'
    return f'{base_url}/api/v1/services/audio/tts/customization'


def _resolve_generation_url() -> str:
    base_url = settings.dashscope_base_url.rstrip('/')
    if settings.voice_cloning_region == 'intl':
        return 'https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
    return f'{base_url}/api/v1/services/aigc/multimodal-generation/generation'


def _normalize_preferred_name(value: str) -> str:
    normalized = ''.join(char.lower() for char in str(value or '') if char.isascii() and (char.isalnum() or char == '_'))
    normalized = normalized[:16]
    if not normalized:
        return 'voiceclone'
    return normalized


def _normalize_language(language_hints: Optional[list[str]]) -> Optional[str]:
    if not language_hints:
        return None
    value = str(language_hints[0] or '').strip().lower()
    aliases = {
        'zh-cn': 'zh',
        'cn': 'zh',
        'chinese': 'zh',
        'en-us': 'en',
        'english': 'en',
        'jp': 'ja',
        'japanese': 'ja',
        'kr': 'ko',
        'korean': 'ko',
    }
    value = aliases.get(value, value)
    if value in {'zh', 'en', 'de', 'it', 'pt', 'es', 'ja', 'ko', 'fr', 'ru'}:
        return value
    return None


def _normalize_language_type(language_hints: Optional[list[str]]) -> Optional[str]:
    language = _normalize_language(language_hints)
    return {
        'zh': 'Chinese',
        'en': 'English',
        'de': 'German',
        'it': 'Italian',
        'pt': 'Portuguese',
        'es': 'Spanish',
        'ja': 'Japanese',
        'ko': 'Korean',
        'fr': 'French',
        'ru': 'Russian',
    }.get(language or '')
