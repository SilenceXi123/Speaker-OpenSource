from __future__ import annotations

import json
import re
from typing import Optional

from app.core.config import settings
from app.schemas.provider_cost import ProviderCallContext
from app.services.cosyvoice_voice_clone import CosyVoiceCloneError, _post_with_observation


NEXT_MODEL = 'qwen-audio-3.1-tts-next'


class QwenAudioNextError(CosyVoiceCloneError):
    pass


def next_endpoint() -> str:
    workspace = settings.sfm_workspace_id.strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,127}', workspace):
        raise QwenAudioNextError('缺少或无效的 SFM_WORKSPACE_ID，请配置 Speaker 现有百炼业务空间 ID')
    if settings.voice_cloning_region != 'cn-beijing':
        raise QwenAudioNextError('Qwen-Audio Next 当前仅接通北京地域')
    return f'https://{workspace}.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer'


def synthesize_qwen_audio_next_voice(
    *,
    sample_public_url: str,
    text: str,
    instruction: Optional[str] = None,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
    attempt: int = 1,
) -> dict:
    endpoint = next_endpoint()
    if not sample_public_url.strip():
        raise QwenAudioNextError('当前参考音频不可用，请重新上传')
    # Quote the exact script separately from delivery directions and reference speech.
    prompt = f'仅由 @voice1 朗读以下台词，不添加参考录音中的其他话，不添加音乐或环境音。语气要求：{str(instruction or "自然清晰").strip()}。台词：{json.dumps(text, ensure_ascii=False)}'
    if not text.strip() or len(prompt) > 3000:
        raise QwenAudioNextError('朗读内容与语气合计不能超过模型的 3000 字符限制')
    return _post_with_observation(
        endpoint, {'model': NEXT_MODEL, 'input': {
            'text_prompt': prompt, 'references': [{'audio_url': sample_public_url}],
            'format': 'wav', 'sample_rate': 24000, 'channels': 1,
        }},
        timeout=300, label='Qwen-Audio Next 音频生成接口', target_model=NEXT_MODEL,
        operation='synthesize_voice', provider_context=provider_context,
        observation_key=observation_key, attempt=attempt,
        provider='qwen_audio_next', error_type=QwenAudioNextError,
    )
