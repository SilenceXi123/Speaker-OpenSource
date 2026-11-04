from __future__ import annotations

import json

from app.core.config import settings
from app.services.subtitle_enhancer import SubtitleEnhancerError, _normalize_subtitle_parts, _request_enhanced_batches, enhance_subtitle_segments


def test_enhance_subtitle_segments_skips_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', False)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 1200,
            'text': '大家好欢迎来到节目',
        }
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert result == segments
    assert applied is False
    assert error_message is None


def test_normalize_subtitle_parts_accepts_english_spaces_and_longer_line() -> None:
    parts = _normalize_subtitle_parts(
        ['I got the power in my soul and we keep', 'running through the night'],
        expected_text='I got the power in my soul and we keep running through the night',
    )

    assert parts == ['I got the power in my soul and we keep', 'running through the night']


def test_normalize_subtitle_parts_rejects_split_common_word_boundary() -> None:
    parts = _normalize_subtitle_parts(
        ['也是东北平原上一颗璀', '璨的绿色明珠'],
        expected_text='也是东北平原上一颗璀璨的绿色明珠',
    )

    assert parts == []


def test_normalize_subtitle_parts_rejects_split_industry_and_reduplicated_words() -> None:
    industry_parts = _normalize_subtitle_parts(
        ['包括很多我们的这个行', '业的观察呀'],
        expected_text='包括很多我们的这个行业的观察呀',
    )
    repeated_parts = _normalize_subtitle_parts(
        ['这样非正式的会议当中慢', '慢地产生的'],
        expected_text='这样非正式的会议当中慢慢地产生的',
    )

    assert industry_parts == []
    assert repeated_parts == []


def test_enhance_subtitle_segments_applies_model_subtitle_parts(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)

    request_payload: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                'choices': [
                    {
                        'message': {
                            'content': (
                                '{"segments":['
                                '{"segmentId":"seg-1","text":"大家好欢迎来到节目今天继续聊字幕断句体验","subtitleParts":["大家好欢迎来到节目","今天继续聊","字幕断句体验"]}'
                                ']}'
                            )
                        }
                    }
                ]
            }

    def fake_post(*args, **kwargs):
        request_payload['url'] = args[0]
        request_payload['headers'] = kwargs.get('headers')
        request_payload['json'] = kwargs.get('json')
        return FakeResponse()

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2200,
            'text': '大家好欢迎来到节目今天继续聊字幕断句体验',
        }
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert applied is True
    assert error_message is None
    assert result[0]['subtitleParts'] == ['大家好欢迎来到节目', '今天继续聊', '字幕断句体验']
    assert request_payload['url'] == 'https://api.deepseek.com/chat/completions'
    assert request_payload['headers'] == {
        'Authorization': 'Bearer deepseek-key',
        'Content-Type': 'application/json',
    }
    assert request_payload['json']['model'] == 'deepseek-v4-flash'
    assert request_payload['json']['temperature'] == 0


def test_enhance_subtitle_segments_uses_english_prompt_for_english_profile(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)

    observed_system_prompt = ''

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                'choices': [
                    {
                        'message': {
                            'content': json.dumps({
                                'segments': [
                                    {
                                        'segmentId': 'seg-1',
                                        'text': "I got the power in my soul and we keep running through the night",
                                        'subtitleParts': [
                                            'I got the power in my soul',
                                            'and we keep running through the night',
                                        ],
                                    }
                                ]
                            })
                        }
                    }
                ]
            }

    def fake_post(*args, **kwargs):
        nonlocal observed_system_prompt
        observed_system_prompt = kwargs['json']['messages'][0]['content']
        return FakeResponse()

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 4200,
            'text': 'I got the power in my soul and we keep running through the night',
        }
    ]

    result, applied, error_message = enhance_subtitle_segments(segments, language_profile='en')

    assert applied is True
    assert error_message is None
    assert result[0]['subtitleParts'] == [
        'I got the power in my soul',
        'and we keep running through the night',
    ]
    assert 'Do not translate' in observed_system_prompt
    assert 'do not output Chinese' in observed_system_prompt
    assert 'English word boundaries' in observed_system_prompt
    assert '纯中文每条不得超过' not in observed_system_prompt


def test_enhance_subtitle_segments_prefers_token_boundary_ranges(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)

    observed_prompt = ''

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                'choices': [
                    {
                        'message': {
                            'content': json.dumps({
                                'segments': [
                                    {
                                        'segmentId': 'seg-1',
                                        'text': '包括很多我们的这个行业的观察呀',
                                        'subtitlePartTokenRanges': [
                                            {'startTokenId': 'tok-0', 'endTokenId': 'tok-7'},
                                            {'startTokenId': 'tok-7', 'endTokenId': 'tok-9'},
                                        ],
                                    }
                                ]
                            }, ensure_ascii=False)
                        }
                    }
                ]
            }

    def fake_post(*args, **kwargs):
        nonlocal observed_prompt
        observed_prompt = kwargs['json']['messages'][1]['content']
        return FakeResponse()

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 8000,
            'endMs': 10500,
            'text': '包括很多我们的这个行业的观察呀',
            'words': [
                {'startMs': 8000, 'endMs': 8200, 'text': '包括', 'punctuation': ''},
                {'startMs': 8200, 'endMs': 8400, 'text': '很多', 'punctuation': ''},
                {'startMs': 8400, 'endMs': 8600, 'text': '我们', 'punctuation': ''},
                {'startMs': 8600, 'endMs': 8800, 'text': '的', 'punctuation': ''},
                {'startMs': 8800, 'endMs': 9000, 'text': '这个', 'punctuation': ''},
                {'startMs': 9000, 'endMs': 9100, 'text': '行', 'punctuation': ''},
                {'startMs': 9100, 'endMs': 9200, 'text': '业', 'punctuation': ''},
                {'startMs': 9200, 'endMs': 9300, 'text': '的', 'punctuation': ''},
                {'startMs': 9300, 'endMs': 9600, 'text': '观察', 'punctuation': ''},
                {'startMs': 9600, 'endMs': 9800, 'text': '呀', 'punctuation': ''},
            ],
        }
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert applied is True
    assert error_message is None
    assert result[0]['subtitleParts'] == ['包括很多我们的这个行业的', '观察呀']
    assert result[0]['subtitlePartRanges'] == [
        {'startMs': 8000, 'endMs': 9300},
        {'startMs': 9300, 'endMs': 9800},
    ]
    assert '"tokens"' in observed_prompt
    assert '"tokenId": "tok-5"' in observed_prompt
    assert '"text": "行业"' in observed_prompt


def test_enhance_subtitle_segments_accepts_parts_without_display_punctuation(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                'choices': [
                    {
                        'message': {
                            'content': (
                                '{"segments":['
                                '{"segmentId":"seg-1","text":"大家好，欢迎来到节目，今天继续聊字幕断句体验。",'
                                '"subtitleParts":["大家好欢迎来到节目","今天继续聊","字幕断句体验"]}'
                                ']}'
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', lambda *args, **kwargs: FakeResponse())

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2200,
            'text': '大家好，欢迎来到节目，今天继续聊字幕断句体验。',
        }
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert applied is True
    assert error_message is None
    assert result[0]['subtitleParts'] == ['大家好欢迎来到节目', '今天继续聊', '字幕断句体验']


def test_enhance_subtitle_segments_skips_short_segments_that_do_not_need_planning(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)

    request_count = 0

    def fake_post(*args, **kwargs):
        nonlocal request_count
        request_count += 1
        raise AssertionError('short subtitle segments should not be sent to DeepSeek')

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 500,
            'text': '嗯。',
        },
        {
            'segmentId': 'seg-2',
            'speakerKey': 'speaker_1',
            'startMs': 600,
            'endMs': 1200,
            'text': '对，是的。',
        },
        {
            'segmentId': 'seg-3',
            'speakerKey': 'speaker_0',
            'startMs': 1300,
            'endMs': 2500,
            'text': '这个像一个锅是吧。',
        },
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert result == segments
    assert applied is False
    assert error_message is None
    assert request_count == 0


def test_enhance_subtitle_segments_skips_short_segments_when_reference_matches(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)

    request_count = 0

    def fake_post(*args, **kwargs):
        nonlocal request_count
        request_count += 1
        raise AssertionError('matching short reference text should not force a DeepSeek request')

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 900,
            'text': '对，是的。',
        }
    ]
    reference_sentences = [
        {
            'startMs': 0,
            'endMs': 900,
            'text': '对是的',
        }
    ]

    result, applied, error_message = enhance_subtitle_segments(
        segments,
        reference_sentences=reference_sentences,
    )

    assert result == segments
    assert applied is False
    assert error_message is None
    assert request_count == 0


def test_enhance_subtitle_segments_limits_payload_budget_for_long_jobs(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_segments_per_job', 2, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_retry_segments', 0, raising=False)

    observed_first_payload_count = 0

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {'choices': [{'message': {'content': '{"segments":[]}'}}]}

    def fake_post(*args, **kwargs):
        nonlocal observed_first_payload_count
        if observed_first_payload_count == 0:
            user_content = kwargs['json']['messages'][1]['content']
            observed_first_payload_count = user_content.count('"segmentId"')
        return FakeResponse()

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': f'seg-{index}',
            'speakerKey': 'speaker_0',
            'startMs': index * 1000,
            'endMs': (index + 1) * 1000,
            'text': '大家好欢迎来到节目今天继续聊字幕优化体验',
        }
        for index in range(4)
    ]

    _result, _applied, error_message = enhance_subtitle_segments(segments)

    assert observed_first_payload_count == 2
    assert '跳过 2 段低优先级字幕' in (error_message or '')


def test_enhance_subtitle_segments_sends_all_payloads_when_job_budget_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 10000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 20)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_segments_per_job', 0, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_retry_segments', 0, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_window_seconds', 600, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_window_max_seconds', 780, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_window_min_gap_ms', 800, raising=False)

    observed_payload_counts: list[int] = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {'choices': [{'message': {'content': '{"segments":[]}'}}]}

    def fake_post(*args, **kwargs):
        user_content = kwargs['json']['messages'][1]['content']
        observed_payload_counts.append(user_content.count('"segmentId"'))
        return FakeResponse()

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': f'seg-{index}',
            'speakerKey': 'speaker_0',
            'startMs': index * 1000,
            'endMs': (index + 1) * 1000,
            'text': '大家好欢迎来到节目今天继续聊字幕优化体验',
        }
        for index in range(4)
    ]

    _result, _applied, error_message = enhance_subtitle_segments(segments)

    assert observed_payload_counts == [4]
    assert '低优先级字幕' not in (error_message or '')


def test_enhance_subtitle_segments_splits_requests_on_silence_window_boundary(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 10000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 20)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_segments_per_job', 0, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_retry_segments', 0, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_window_seconds', 600, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_window_max_seconds', 780, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_window_min_gap_ms', 800, raising=False)

    observed_batch_ids: list[list[str]] = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {'choices': [{'message': {'content': '{"segments":[]}'}}]}

    def fake_post(*args, **kwargs):
        user_content = kwargs['json']['messages'][1]['content']
        observed_batch_ids.append([
            segment_id
            for segment_id in ('seg-1', 'seg-2', 'seg-3', 'seg-4')
            if f'"segmentId": "{segment_id}"' in user_content
        ])
        return FakeResponse()

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 3000,
            'text': '大家好欢迎来到节目今天继续聊字幕优化体验',
        },
        {
            'segmentId': 'seg-2',
            'speakerKey': 'speaker_0',
            'startMs': 590000,
            'endMs': 592000,
            'text': '这一段还属于第一个十分钟窗口的内容',
        },
        {
            'segmentId': 'seg-3',
            'speakerKey': 'speaker_1',
            'startMs': 610000,
            'endMs': 612000,
            'text': '这里经过长停顿以后应该开启新的字幕优化窗口',
        },
        {
            'segmentId': 'seg-4',
            'speakerKey': 'speaker_1',
            'startMs': 620000,
            'endMs': 622000,
            'text': '新窗口里的后续内容应该和第三段一起发送',
        },
    ]

    _result, _applied, error_message = enhance_subtitle_segments(segments)

    assert sorted(observed_batch_ids) == [['seg-1', 'seg-2'], ['seg-3', 'seg-4']]
    assert '低优先级字幕' not in (error_message or '')


def test_enhance_subtitle_segments_includes_timing_context_in_deepseek_payload(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 10000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 20)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_segments_per_job', 0, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_retry_segments', 0, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_window_seconds', 600, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_window_max_seconds', 780, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_window_min_gap_ms', 800, raising=False)

    observed_user_content = ''

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {'choices': [{'message': {'content': '{"segments":[]}'}}]}

    def fake_post(*args, **kwargs):
        nonlocal observed_user_content
        observed_user_content = kwargs['json']['messages'][1]['content']
        return FakeResponse()

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 32660,
            'endMs': 35780,
            'text': '但是上面有很多就是挺怀旧的一些。',
        }
    ]

    _result, _applied, _error_message = enhance_subtitle_segments(segments)

    assert '"startMs": 32660' in observed_user_content
    assert '"endMs": 35780' in observed_user_content


def test_enhance_subtitle_segments_limits_retry_payloads(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_segments_per_job', 0, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_retry_segments', 1, raising=False)

    observed_payload_counts: list[int] = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {'choices': [{'message': {'content': '{"segments":[]}'}}]}

    def fake_post(*args, **kwargs):
        user_content = kwargs['json']['messages'][1]['content']
        observed_payload_counts.append(user_content.count('"segmentId"'))
        return FakeResponse()

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': f'seg-{index}',
            'speakerKey': 'speaker_0',
            'startMs': index * 1000,
            'endMs': (index + 1) * 1000,
            'text': '大家好欢迎来到节目今天继续聊字幕优化体验',
        }
        for index in range(3)
    ]

    _result, _applied, error_message = enhance_subtitle_segments(segments)

    assert observed_payload_counts == [3, 1]
    assert '重试预算' in (error_message or '')


def test_enhance_subtitle_segments_skips_retry_when_job_timeout_expires(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_segments_per_job', 0, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_retry_segments', 5, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_job_timeout_seconds', 1, raising=False)

    timeline = iter([0.0, 0.0, 2.0, 2.0])
    call_count = 0

    def fake_perf_counter() -> float:
        return next(timeline, 2.0)

    def fake_request(_batch):
        nonlocal call_count
        call_count += 1
        return {}

    monkeypatch.setattr('app.services.subtitle_enhancer.perf_counter', fake_perf_counter, raising=False)
    monkeypatch.setattr('app.services.subtitle_enhancer._request_enhanced_batch', fake_request)

    segments = [
        {
            'segmentId': f'seg-{index}',
            'speakerKey': 'speaker_0',
            'startMs': index * 1000,
            'endMs': (index + 1) * 1000,
            'text': '大家好欢迎来到节目今天继续聊字幕优化体验',
        }
        for index in range(2)
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert result == segments
    assert applied is False
    assert call_count == 1
    assert '字幕增强总时限' in (error_message or '')


def test_enhance_subtitle_segments_keeps_successful_batches_when_later_batch_fails(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 1)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_segments_per_job', 0, raising=False)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_retry_segments', 0, raising=False)

    def fake_request(batch):
        segment_id = batch[0]['segmentId']
        if segment_id == 'seg-1':
            return {
                'seg-1': {
                    'segmentId': 'seg-1',
                    'text': '大家好欢迎来到节目今天继续聊字幕优化体验',
                    'subtitleParts': ['大家好欢迎来到节目', '今天继续聊', '字幕优化体验'],
                }
            }
        raise SubtitleEnhancerError('DeepSeek batch timeout')

    monkeypatch.setattr('app.services.subtitle_enhancer._request_enhanced_batch', fake_request)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 1000,
            'text': '大家好欢迎来到节目今天继续聊字幕优化体验',
        },
        {
            'segmentId': 'seg-2',
            'speakerKey': 'speaker_0',
            'startMs': 1000,
            'endMs': 2000,
            'text': '我们继续测试长音频字幕优化失败回退能力',
        },
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert applied is True
    assert result[0]['subtitleParts'] == ['大家好欢迎来到节目', '今天继续聊', '字幕优化体验']
    assert result[1] == segments[1]
    assert '部分 DeepSeek 字幕增强批次失败' in (error_message or '')


def test_enhance_subtitle_segments_retries_long_segments_without_accepted_parts(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)

    call_count = 0

    class FakeResponse:
        status_code = 200

        def __init__(self, content: str) -> None:
            self._content = content

        def json(self):
            return {
                'choices': [
                    {
                        'message': {
                            'content': self._content,
                        }
                    }
                ]
            }

    def fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeResponse(
                '{"segments":['
                '{"segmentId":"seg-2","text":"然后像那舞狮的一些岭南文化的一些东西在上面。",'
                '"subtitleParts":["然后像那舞狮的一些","岭南文化的一些东西在上面"]}'
                ']}'
            )
        return FakeResponse(
            '{"segments":['
            '{"segmentId":"seg-1","text":"但是上面有很多就是挺怀旧的一些。",'
            '"subtitleParts":["但是上面有很多就是","挺怀旧的一些"]}'
            ']}'
        )

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 32660,
            'endMs': 35780,
            'text': '但是上面有很多就是挺怀旧的一些。',
        },
        {
            'segmentId': 'seg-2',
            'speakerKey': 'speaker_0',
            'startMs': 39020,
            'endMs': 43020,
            'text': '然后像那舞狮的一些岭南文化的一些东西在上面。',
        },
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert applied is True
    assert error_message is None
    assert call_count == 2
    assert result[0]['subtitleParts'] == ['但是上面有很多就是', '挺怀旧的一些']
    assert result[1]['subtitleParts'] == ['然后像那舞狮的一些', '岭南文化的一些东西在上面']


def test_enhance_subtitle_segments_retries_when_model_splits_before_aspect_particle(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)

    call_count = 0

    class FakeResponse:
        status_code = 200

        def __init__(self, content: str) -> None:
            self._content = content

        def json(self):
            return {
                'choices': [
                    {
                        'message': {
                            'content': self._content,
                        }
                    }
                ]
            }

    def fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeResponse(
                '{"segments":['
                '{"segmentId":"seg-1","text":"佛山古镇呢也与美桃湾一起入选了全省文旅赋能城市更新十大优秀案例。",'
                '"subtitleParts":["佛山古镇呢也与美桃湾一起入选","了全省文旅赋能城市更新","十大优秀案例"]}'
                ']}'
            )
        return FakeResponse(
            '{"segments":['
            '{"segmentId":"seg-1","text":"佛山古镇呢也与美桃湾一起入选了全省文旅赋能城市更新十大优秀案例。",'
            '"subtitleParts":["佛山古镇呢","也与美桃湾一起入选了","全省文旅赋能","城市更新十大优秀案例"]}'
            ']}'
        )

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_1',
            'startMs': 128860,
            'endMs': 134660,
            'text': '佛山古镇呢也与美桃湾一起入选了全省文旅赋能城市更新十大优秀案例。',
        }
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert applied is True
    assert error_message is None
    assert call_count == 2
    assert result[0]['subtitleParts'] == ['佛山古镇呢', '也与美桃湾一起入选了', '全省文旅赋能', '城市更新十大优秀案例']


def test_enhance_subtitle_segments_retries_when_model_splits_mechanical_boundaries(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)

    call_count = 0
    retry_user_payload = ''

    class FakeResponse:
        status_code = 200

        def __init__(self, content: str) -> None:
            self._content = content

        def json(self):
            return {
                'choices': [
                    {
                        'message': {
                            'content': self._content,
                        }
                    }
                ]
            }

    def fake_post(*args, **kwargs):
        nonlocal call_count, retry_user_payload
        call_count += 1
        if call_count == 1:
            return FakeResponse(
                '{"segments":['
                '{"segmentId":"seg-1","text":"佛山古镇呢也与美桃湾一起入选了全省文旅赋能城市更新十大优秀案例。",'
                '"subtitleParts":["佛山古镇呢也与美桃湾一起","入选了全省文旅赋能城市更新十","大优秀案例"]}'
                ']}'
            )
        retry_user_payload = kwargs['json']['messages'][1]['content']
        return FakeResponse(
            '{"segments":['
            '{"segmentId":"seg-1","text":"佛山古镇呢也与美桃湾一起入选了全省文旅赋能城市更新十大优秀案例。",'
            '"subtitleParts":["佛山古镇呢","也与美桃湾一起入选了","全省文旅赋能","城市更新十大优秀案例"]}'
            ']}'
        )

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_1',
            'startMs': 128860,
            'endMs': 134660,
            'text': '佛山古镇呢也与美桃湾一起入选了全省文旅赋能城市更新十大优秀案例。',
        }
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert applied is True
    assert error_message is None
    assert call_count == 2
    assert 'retryHint' in retry_user_payload
    assert result[0]['subtitleParts'] == ['佛山古镇呢', '也与美桃湾一起入选了', '全省文旅赋能', '城市更新十大优秀案例']


def test_enhance_subtitle_segments_retries_when_model_splits_object_before_together(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)

    call_count = 0

    class FakeResponse:
        status_code = 200

        def __init__(self, content: str) -> None:
            self._content = content

        def json(self):
            return {
                'choices': [
                    {
                        'message': {
                            'content': self._content,
                        }
                    }
                ]
            }

    def fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeResponse(
                '{"segments":['
                '{"segmentId":"seg-1","text":"佛山古镇呢也与美桃湾一起入选了全省文旅赋能城市更新十大优秀案例。",'
                '"subtitleParts":["佛山古镇呢也与美桃湾","一起入选了全省文旅赋能","城市更新十大优秀案例"]}'
                ']}'
            )
        return FakeResponse(
            '{"segments":['
            '{"segmentId":"seg-1","text":"佛山古镇呢也与美桃湾一起入选了全省文旅赋能城市更新十大优秀案例。",'
            '"subtitleParts":["佛山古镇呢","也与美桃湾一起入选了","全省文旅赋能","城市更新十大优秀案例"]}'
            ']}'
        )

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_1',
            'startMs': 128860,
            'endMs': 134660,
            'text': '佛山古镇呢也与美桃湾一起入选了全省文旅赋能城市更新十大优秀案例。',
        }
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert applied is True
    assert error_message is None
    assert call_count == 2
    assert result[0]['subtitleParts'] == ['佛山古镇呢', '也与美桃湾一起入选了', '全省文旅赋能', '城市更新十大优秀案例']


def test_enhance_subtitle_segments_falls_back_to_dashscope_key_when_enhancer_key_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'dashscope')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', '')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'dashscope-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', '')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'qwen3.6-plus')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)

    observed_url = ''

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                'choices': [
                    {
                        'message': {
                            'content': '{"segments":[]}'
                        }
                    }
                ]
            }

    def fake_post(*args, **kwargs):
        nonlocal observed_url
        observed_url = args[0]
        return FakeResponse()

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', fake_post)

    segments = [{'segmentId': 'seg-1', 'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1000, 'text': '大家好欢迎来到节目今天继续聊字幕断句体验'}]
    result, applied, error_message = enhance_subtitle_segments(segments)

    assert result == segments
    assert applied is False
    assert error_message is None
    assert observed_url.endswith('/compatible-mode/v1/chat/completions')


def test_enhance_subtitle_segments_repairs_self_intro_parts(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                'choices': [
                    {
                        'message': {
                            'content': (
                                '{"segments":['
                                '{"segmentId":"seg-1","text":"大家好我是来自深圳智映无碍科技的创始人林志鹏","subtitleParts":["大家好我是来自深圳智映无碍","科技的创始人林志鹏"]}'
                                ']}'
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', lambda *args, **kwargs: FakeResponse())

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 3000,
            'text': '大家好我是来自深圳智映无碍科技的创始人林志鹏',
        }
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert applied is True
    assert error_message is None
    assert result[0]['subtitleParts'] == ['大家好我是来自深圳', '智映无碍科技的创始人', '林志鹏']


def test_enhance_subtitle_segments_rejects_risky_bad_boundary(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_enabled', True)
    monkeypatch.setattr(settings, 'subtitle_enhancer_provider', 'deepseek')
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'deepseek-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(settings, 'subtitle_enhancer_timeout_seconds', 30)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_chars', 2000)
    monkeypatch.setattr(settings, 'subtitle_enhancer_max_batch_segments', 10)

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                'choices': [
                    {
                        'message': {
                            'content': (
                                '{"segments":['
                                '{"segmentId":"seg-1","text":"你刚才讲就是说你碰到最悲观的大概十年是吧","subtitleParts":["你刚才讲就是说你碰到最悲","观的大概十年是吧"]}'
                                ']}'
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr('app.services.subtitle_enhancer.httpx.post', lambda *args, **kwargs: FakeResponse())

    segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 2800,
            'text': '你刚才讲就是说你碰到最悲观的大概十年是吧',
        }
    ]

    result, applied, error_message = enhance_subtitle_segments(segments)

    assert result == segments
    assert applied is False
    assert error_message is None


def test_request_enhanced_batches_preserves_batch_order(monkeypatch) -> None:
    monkeypatch.setattr(
        'app.services.subtitle_enhancer._request_enhanced_batch',
        lambda batch: {'batchId': batch[0]['segmentId']},
    )

    result = _request_enhanced_batches([
        [{'segmentId': 'batch-1'}],
        [{'segmentId': 'batch-2'}],
        [{'segmentId': 'batch-3'}],
    ])

    assert result == [
        {'batchId': 'batch-1'},
        {'batchId': 'batch-2'},
        {'batchId': 'batch-3'},
    ]
