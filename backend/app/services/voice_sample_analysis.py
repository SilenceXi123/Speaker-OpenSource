from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.services.oss_storage import upload_local_file_to_oss

VOICE_CLONE_READY = 'ready'
VOICE_CLONE_WARNING = 'warning'
VOICE_CLONE_REJECTED = 'rejected'

VOICE_SAMPLE_USAGE_TYPES = ['voiceprint', 'voice_cloning']


@dataclass(frozen=True)
class VoiceCloneReadinessResult:
    status: str
    message: Optional[str]


def analyze_clone_readiness(
    *,
    duration_ms: Optional[int],
    sample_rate_hz: Optional[int],
    channel_count: Optional[int],
) -> VoiceCloneReadinessResult:
    duration_value = int(duration_ms or 0)
    sample_rate_value = int(sample_rate_hz or 0)
    channel_value = int(channel_count or 0)

    issues: list[str] = []
    warnings: list[str] = []

    if duration_value <= 0:
        issues.append('无法识别样本时长')
    elif duration_value < 10_000:
        issues.append('样本时长不足 10 秒')
    elif duration_value > 60_000:
        warnings.append('样本时长超过 60 秒，建议裁到 10-20 秒')

    if sample_rate_value and sample_rate_value < 16_000:
        issues.append('采样率低于 16 kHz')
    if channel_value and channel_value not in {1, 2}:
        warnings.append('声道数异常，建议使用单声道或双声道')

    if issues:
        return VoiceCloneReadinessResult(status=VOICE_CLONE_REJECTED, message='；'.join(issues))
    if warnings:
        return VoiceCloneReadinessResult(status=VOICE_CLONE_WARNING, message='；'.join(warnings))
    return VoiceCloneReadinessResult(status=VOICE_CLONE_READY, message='样本满足声音复刻基础要求')


def ensure_sample_public_url(sample_path: Path) -> Optional[str]:
    if not settings.oss_is_configured:
        return None
    upload = upload_local_file_to_oss(
        file_path=sample_path,
        prefix=settings.oss_voice_sample_prefix,
        content_type='audio/wav',
    )
    return upload.public_url
