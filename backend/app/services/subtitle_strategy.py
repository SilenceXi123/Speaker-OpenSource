from __future__ import annotations

from typing import Any, Callable

from app.core.config import settings
from app.schemas.provider_cost import ProviderCallContext
from app.services.qwen_filetrans import QwenFileTranscriptionError, fetch_filetrans_reference_sentences
from app.services.subtitle_enhancer import enhance_subtitle_segments


DEFAULT_SUBTITLE_STRATEGY = 'enhanced'
VALID_SUBTITLE_STRATEGIES = {'standard', 'enhanced', 'pro', 'evidence'}


def normalize_subtitle_strategy(strategy: str | None) -> str:
    normalized = str(strategy or '').strip().lower() or DEFAULT_SUBTITLE_STRATEGY
    if normalized not in VALID_SUBTITLE_STRATEGIES:
        return DEFAULT_SUBTITLE_STRATEGY
    return normalized


def get_subtitle_strategy_label(strategy: str | None) -> str:
    normalized = normalize_subtitle_strategy(strategy)
    return {
        'standard': '基础保底',
        'enhanced': '默认自动优化',
        'pro': '高精度参考',
        'evidence': '证据字幕',
    }[normalized]


def resolve_job_language_profile(job) -> str:
    for item in getattr(job, 'provider_task_meta', None) or []:
        profile = normalize_language_profile((item or {}).get('language_hints'))
        if profile != 'zh':
            return profile
    return 'zh'


def normalize_language_profile(language_hints) -> str:
    hints: list[str] = []
    if isinstance(language_hints, str):
        hints = [item.strip().lower() for item in language_hints.split(',') if item.strip()]
    elif isinstance(language_hints, (list, tuple)):
        hints = [str(item or '').strip().lower() for item in language_hints if str(item or '').strip()]
    if not hints:
        return 'zh'
    if hints[0].startswith('en') or 'en' in hints or 'english' in hints:
        return 'en'
    return 'zh'


def apply_subtitle_strategy(
    job,
    segments: list[dict[str, Any]],
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    strategy = normalize_subtitle_strategy(getattr(job, 'subtitle_strategy', None))
    if not segments:
        return segments, False, None
    if strategy == 'evidence':
        return segments, False, None
    if strategy == 'standard':
        return segments, False, None
    if not settings.subtitle_enhancer_enabled:
        return segments, False, '字幕增强未开启，已回退 Fun-ASR 原始结果'

    reference_sentences = None
    warnings: list[str] = []
    if strategy == 'pro':
        try:
            reference_sentences = fetch_filetrans_reference_sentences(getattr(job, 'source_url', ''))
        except QwenFileTranscriptionError as exc:
            warnings.append(f'专业模式第二路识别失败，已回退增强模式：{exc}')

    script_context = getattr(job, 'script_context', None)
    enhancer_kwargs: dict[str, Any] = {'reference_sentences': reference_sentences}
    if script_context:
        enhancer_kwargs['script_context'] = script_context
    enhanced_segments, applied, enhancement_error = enhance_subtitle_segments(
        segments,
        progress_callback=progress_callback,
        language_profile=resolve_job_language_profile(job),
        provider_context=_provider_context_for_job(job),
        **enhancer_kwargs,
    )
    if enhancement_error:
        warnings.append(enhancement_error)
    return enhanced_segments, applied, '；'.join(item for item in warnings if item) or None


def _provider_context_for_job(job) -> ProviderCallContext | None:
    project_id = str(getattr(job, 'project_id', None) or '').strip()
    job_id = str(getattr(job, 'id', None) or '').strip()
    if not project_id or not job_id:
        return None
    return ProviderCallContext(
        project_id=project_id,
        job_id=job_id,
        pipeline_scope='subtitle_legacy',
    )
