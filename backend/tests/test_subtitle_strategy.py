from __future__ import annotations

from types import SimpleNamespace

from app.core.config import settings
from app.services.qwen_filetrans import QwenFileTranscriptionError
from app.services.subtitle_strategy import apply_subtitle_strategy, normalize_subtitle_strategy


def test_normalize_subtitle_strategy_defaults_to_enhanced() -> None:
    assert normalize_subtitle_strategy(None) == 'enhanced'
    assert normalize_subtitle_strategy('unknown') == 'enhanced'


def test_apply_subtitle_strategy_returns_original_segments_for_standard() -> None:
    job = SimpleNamespace(subtitle_strategy='standard', source_url='https://example.com/source.wav')
    segments = [{'segmentId': 'seg-1', 'text': '大家好'}]

    result, applied, error_message = apply_subtitle_strategy(job, segments)

    assert result == segments
    assert applied is False
    assert error_message is None


def test_apply_subtitle_strategy_pro_falls_back_to_enhanced_when_second_pass_fails(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(
        'app.services.subtitle_strategy.fetch_filetrans_reference_sentences',
        lambda _file_url: (_ for _ in ()).throw(QwenFileTranscriptionError('第二路超时')),
    )

    def fake_enhance(segments, reference_sentences=None, **_kwargs):
        assert reference_sentences is None
        updated = [dict(segments[0], subtitleParts=['大家好', '欢迎来到节目'])]
        return updated, True, None

    monkeypatch.setattr('app.services.subtitle_strategy.enhance_subtitle_segments', fake_enhance)

    job = SimpleNamespace(subtitle_strategy='pro', source_url='https://example.com/source.wav')
    segments = [{'segmentId': 'seg-1', 'text': '大家好欢迎来到节目'}]

    result, applied, error_message = apply_subtitle_strategy(job, segments)

    assert applied is True
    assert result[0]['subtitleParts'] == ['大家好', '欢迎来到节目']
    assert '专业模式第二路识别失败' in error_message


def test_apply_subtitle_strategy_passes_english_language_profile_from_provider_meta(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    observed_language_profiles: list[str] = []

    def fake_enhance(segments, reference_sentences=None, language_profile='zh', **_kwargs):
        assert reference_sentences is None
        observed_language_profiles.append(language_profile)
        updated = [dict(segments[0], subtitleParts=['I never give up', 'on this dream'])]
        return updated, True, None

    monkeypatch.setattr('app.services.subtitle_strategy.enhance_subtitle_segments', fake_enhance)

    job = SimpleNamespace(
        subtitle_strategy='enhanced',
        source_url='https://example.com/source.wav',
        provider_task_meta=[{'language_hints': ['en']}],
    )
    segments = [{'segmentId': 'seg-1', 'text': 'I never give up on this dream'}]

    result, applied, error_message = apply_subtitle_strategy(job, segments)

    assert applied is True
    assert error_message is None
    assert result[0]['subtitleParts'] == ['I never give up', 'on this dream']
    assert observed_language_profiles == ['en']
