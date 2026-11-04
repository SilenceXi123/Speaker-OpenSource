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


class CosyVoiceCloneError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None, code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


COSYVOICE_INSTRUCTION_LIMIT = 100


def create_cosyvoice_voice(
    *,
    sample_public_url: str,
    target_model: str,
    voice_name: str,
    language_hints: Optional[list[str]] = None,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
    attempt: int = 1,
) -> dict[str, Any]:
    payload = {
        'model': 'voice-enrollment',
        'input': {
            'action': 'create_voice',
            'target_model': target_model,
            'prefix': _normalize_voice_prefix(voice_name),
            'url': sample_public_url,
            'max_prompt_audio_length': _normalize_max_prompt_audio_length(),
        },
    }
    if _supports_preprocess_option(target_model):
        payload['input']['enable_preprocess'] = bool(settings.voice_cloning_cosyvoice_enable_preprocess)
    if language_hints:
        payload['input']['language_hints'] = [language_hints[0]]
    return _post_customization(
        payload,
        target_model=target_model,
        operation='create_voice',
        provider_context=provider_context,
        observation_key=observation_key,
        attempt=attempt,
    )


def list_cosyvoice_voices(prefix: Optional[str] = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'model': 'voice-enrollment',
        'input': {
            'action': 'list_voice',
        },
    }
    if prefix:
        payload['input']['prefix'] = _normalize_voice_prefix(prefix)
    return _post_customization(
        payload,
        target_model='voice-enrollment',
        operation='list_voices',
        provider_context=None,
        observation_key=None,
    )


def query_cosyvoice_voice(voice_id: str) -> dict[str, Any]:
    return _post_customization(
        {
            'model': 'voice-enrollment',
            'input': {
                'action': 'query_voice',
                'voice_id': voice_id,
            },
        },
        target_model='voice-enrollment',
        operation='query_voice',
        provider_context=None,
        observation_key=None,
    )


def delete_cosyvoice_voice(
    voice_id: str,
    *,
    target_model: str = 'voice-enrollment',
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
) -> dict[str, Any]:
    return _post_customization(
        {
            'model': 'voice-enrollment',
            'input': {
                'action': 'delete_voice',
                'voice_id': voice_id,
            },
        },
        target_model=target_model,
        operation='delete_voice',
        provider_context=provider_context,
        observation_key=observation_key,
    )


def synthesize_cosyvoice_voice(
    *,
    target_model: str,
    voice_id: str,
    text: str,
    instruction: Optional[str] = None,
    audio_format: str = 'mp3',
    sample_rate: int = 22050,
    language_hints: Optional[list[str]] = None,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
    attempt: int = 1,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'model': target_model,
        'input': {
            'text': text,
            'voice': voice_id,
            'format': audio_format,
            'sample_rate': sample_rate,
        },
    }
    normalized_instruction = str(instruction or '').strip()
    if normalized_instruction and supports_cosyvoice_instruction(target_model):
        if weighted_instruction_length(normalized_instruction) > COSYVOICE_INSTRUCTION_LIMIT:
            raise CosyVoiceCloneError('语气提示超过 100 加权字符，请缩短后重试')
        payload['input']['instruction'] = normalized_instruction
    if language_hints:
        payload['input']['language_hints'] = [language_hints[0]]
    return _post_speech_synthesizer(
        payload,
        target_model=target_model,
        operation='synthesize_voice',
        provider_context=provider_context,
        observation_key=observation_key,
        attempt=attempt,
    )


def _post_customization(
    payload: dict[str, Any],
    *,
    target_model: str,
    operation: str,
    provider_context: Optional[ProviderCallContext],
    observation_key: Optional[str],
    attempt: int = 1,
    provider: str = 'cosyvoice',
    error_type: type[CosyVoiceCloneError] = CosyVoiceCloneError,
) -> dict[str, Any]:
    return _post_with_observation(
        _resolve_customization_url(),
        payload,
        timeout=8 if operation == 'query_voice' else 90,
        label='声音复刻接口',
        target_model=target_model,
        operation=operation,
        provider_context=provider_context,
        observation_key=observation_key,
        attempt=attempt,
        provider=provider,
        error_type=error_type,
    )


def _post_speech_synthesizer(
    payload: dict[str, Any],
    *,
    target_model: str,
    operation: str,
    provider_context: Optional[ProviderCallContext],
    observation_key: Optional[str],
    attempt: int = 1,
    provider: str = 'cosyvoice',
    error_type: type[CosyVoiceCloneError] = CosyVoiceCloneError,
) -> dict[str, Any]:
    return _post_with_observation(
        _resolve_speech_synthesizer_url(),
        payload,
        timeout=120,
        label='语音合成接口',
        target_model=target_model,
        operation=operation,
        provider_context=provider_context,
        observation_key=observation_key,
        attempt=attempt,
        provider=provider,
        error_type=error_type,
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
    provider: str = 'cosyvoice',
    error_type: type[CosyVoiceCloneError] = CosyVoiceCloneError,
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
        try:
            data = response.json()
        except ValueError as exc:
            raise error_type(f'{label}返回了非 JSON 响应: {response.text[:500]}') from exc
        if not isinstance(data, dict):
            raise error_type(f'{label}返回了无效 JSON 对象')
        if response.status_code >= 400 or data.get('code'):
            message = str(data.get('message') or data.get('code') or response.text[:500]).strip()
            raise error_type(f'{label}请求失败: {message}',
                status_code=response.status_code, code=data.get('code'))
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
            provider=provider,
        )
        raise
    usage = _voice_usage(data)
    if operation == 'create_voice' and isinstance(data.get('usage'), dict):
        voice_count = _nonnegative_number(data['usage'].get('count'))
        if voice_count is not None:
            usage = {**(usage or {}), 'voice_count': voice_count}
    _record_voice_observation(
        provider_context,
        target_model=target_model,
        operation=operation,
        observation_key=observation_key,
        attempt=attempt,
        status='completed',
        request_id=_request_id(data),
        usage=usage,
        meters=([ProviderMeterObservation(meter=meter, quantity=usage.get(meter) if usage else None, unit='token')
                 for meter in ('input_tokens', 'output_tokens')] if provider == 'qwen_audio_next'
                else _voice_meters(operation, usage)),
        started_at=started_at,
        provider=provider,
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
    provider: str = 'cosyvoice',
) -> None:
    if context is None or not observation_key:
        return
    try:
        record_legacy_provider_observation(
            context,
            provider=provider,
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
                error_summary=f'{type(error).__name__}: {provider} request failed' if error else None,
            ),
        )
    except Exception as exc:  # pragma: no cover - observation must stay fail-open
        logger.warning('%s observation skipped (%s)', provider, type(exc).__name__)


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


def _resolve_customization_url() -> str:
    base_url = settings.dashscope_base_url.rstrip('/')
    if settings.voice_cloning_region == 'intl':
        return 'https://dashscope-intl.aliyuncs.com/api/v1/services/audio/tts/customization'
    return f'{base_url}/api/v1/services/audio/tts/customization'


def _resolve_speech_synthesizer_url() -> str:
    base_url = settings.dashscope_base_url.rstrip('/')
    return f'{base_url}/api/v1/services/audio/tts/SpeechSynthesizer'


def _normalize_voice_prefix(value: str) -> str:
    normalized = ''.join(char.lower() for char in str(value or '') if char.isascii() and char.isalnum())
    normalized = normalized[:10]
    if not normalized:
        return 'voiceclone'
    return normalized


def _normalize_max_prompt_audio_length() -> float:
    value = float(settings.voice_cloning_cosyvoice_max_prompt_audio_length or 20.0)
    return min(30.0, max(3.0, value))


def _supports_preprocess_option(target_model: str) -> bool:
    normalized = str(target_model or '').strip().lower()
    return normalized in {'cosyvoice-v3.5-plus', 'cosyvoice-v3.5-flash', 'cosyvoice-v3-flash'}


def supports_cosyvoice_instruction(target_model: str) -> bool:
    normalized = str(target_model or '').strip().lower()
    return normalized in {'cosyvoice-v3.5-plus', 'cosyvoice-v3.5-flash', 'cosyvoice-v3-flash'}


def _supports_instruction(target_model: str) -> bool:
    return supports_cosyvoice_instruction(target_model)


def weighted_instruction_length(value: str) -> int:
    return sum(2 if _is_cjk_han(char) else 1 for char in value)


def _is_cjk_han(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2EE5F
        or 0x2F800 <= codepoint <= 0x2FA1F
        or 0x30000 <= codepoint <= 0x3347F
    )
