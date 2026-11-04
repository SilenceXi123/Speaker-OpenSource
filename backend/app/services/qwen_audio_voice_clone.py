from __future__ import annotations

from typing import Any, Optional

from app.core.config import settings
from app.schemas.provider_cost import ProviderCallContext
from app.services.cosyvoice_voice_clone import (
    CosyVoiceCloneError,
    _normalize_max_prompt_audio_length,
    _normalize_voice_prefix,
    _post_customization,
    _post_speech_synthesizer,
)


class QwenAudioVoiceCloneError(CosyVoiceCloneError):
    pass


QWEN_AUDIO_MODELS = (
    'qwen-audio-3.0-tts-plus',
    'qwen-audio-3.0-tts-flash',
    'qwen-audio-3.1-tts-flash',
)
# Product limit shared with the request schema; Aliyun documents no Qwen-Audio instruction limit.
QWEN_AUDIO_INSTRUCTION_LIMIT = 1600


def create_qwen_audio_voice(
    *,
    sample_public_url: str,
    target_model: str,
    voice_name: str,
    language_hints: Optional[list[str]] = None,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
    attempt: int = 1,
) -> dict[str, Any]:
    _require_beijing_model(target_model)
    payload = {
        'model': 'voice-enrollment',
        'input': {
            'action': 'create_voice',
            'target_model': target_model,
            'prefix': _normalize_voice_prefix(voice_name),
            'url': sample_public_url,
            'max_prompt_audio_length': _normalize_max_prompt_audio_length(),
            'enable_preprocess': bool(settings.voice_cloning_cosyvoice_enable_preprocess),
        },
    }
    if language_hints:
        payload['input']['language_hints'] = [language_hints[0]]
    return _post_customization(
        payload, target_model=target_model, operation='create_voice',
        provider_context=provider_context, observation_key=observation_key, attempt=attempt,
        provider='qwen_audio', error_type=QwenAudioVoiceCloneError,
    )


def query_qwen_audio_voice(
    voice_id: str,
    *,
    target_model: str = 'voice-enrollment',
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
) -> dict[str, Any]:
    _require_beijing_model()
    return _post_customization(
        {'model': 'voice-enrollment', 'input': {'action': 'query_voice', 'voice_id': voice_id}},
        target_model=target_model, operation='query_voice',
        provider_context=provider_context, observation_key=observation_key,
        provider='qwen_audio', error_type=QwenAudioVoiceCloneError,
    )


def delete_qwen_audio_voice(
    voice_id: str,
    *,
    target_model: str = 'voice-enrollment',
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
) -> dict[str, Any]:
    _require_beijing_model()
    return _post_customization(
        {'model': 'voice-enrollment', 'input': {'action': 'delete_voice', 'voice_id': voice_id}},
        target_model=target_model, operation='delete_voice',
        provider_context=provider_context, observation_key=observation_key,
        provider='qwen_audio', error_type=QwenAudioVoiceCloneError,
    )


def synthesize_qwen_audio_voice(
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
    _require_beijing_model(target_model)
    payload = {
        'model': target_model,
        'input': {'text': text, 'voice': voice_id, 'format': audio_format, 'sample_rate': sample_rate},
    }
    normalized_instruction = str(instruction or '').strip()
    if len(normalized_instruction) > QWEN_AUDIO_INSTRUCTION_LIMIT:
        raise QwenAudioVoiceCloneError('语气提示超过产品限制 1600 字符，请缩短后重试')
    if normalized_instruction:
        payload['input']['instruction'] = normalized_instruction
    if language_hints:
        payload['input']['language_hints'] = [language_hints[0]]
    return _post_speech_synthesizer(
        payload, target_model=target_model, operation='synthesize_voice',
        provider_context=provider_context, observation_key=observation_key, attempt=attempt,
        provider='qwen_audio', error_type=QwenAudioVoiceCloneError,
    )


def _require_beijing_model(target_model: Optional[str] = None) -> None:
    if settings.voice_cloning_region != 'cn-beijing':
        raise QwenAudioVoiceCloneError('Qwen-Audio 当前仅支持北京地域接口')
    if target_model is not None and target_model not in QWEN_AUDIO_MODELS:
        raise QwenAudioVoiceCloneError('当前 Qwen-Audio 模型未接通')
