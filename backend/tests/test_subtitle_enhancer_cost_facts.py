from __future__ import annotations

from app.core.config import settings
from app.schemas.provider_cost import ProviderCallContext
from app.services import subtitle_enhancer


def _context() -> ProviderCallContext:
    return ProviderCallContext(
        project_id='project-1',
        job_id='job-1',
        pipeline_scope='subtitle_legacy',
    )


def _response(*, usage=None):
    class FakeResponse:
        status_code = 200
        text = '{}'

        @staticmethod
        def json():
            payload = {
                'id': 'deepseek-request-1',
                'choices': [
                    {
                        'message': {
                            'content': '{"segments":[{"segmentId":"seg-1","text":"测试字幕"}]}'
                        }
                    }
                ],
            }
            if usage is not None:
                payload['usage'] = usage
            return payload

    return FakeResponse()


def test_deepseek_batch_records_reported_usage_and_request_id(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'test-key')
    monkeypatch.setattr(settings, 'subtitle_enhancer_model', 'deepseek-v4-flash')
    monkeypatch.setattr(
        subtitle_enhancer.httpx,
        'post',
        lambda *_args, **_kwargs: _response(
            usage={
                'prompt_tokens': 120,
                'completion_tokens': 30,
                'total_tokens': 150,
                'prompt_cache_hit_tokens': 80,
                'prompt_cache_miss_tokens': 40,
            }
        ),
    )
    monkeypatch.setattr(
        subtitle_enhancer,
        'record_legacy_provider_observation',
        lambda context, **kwargs: captured.update(context=context, **kwargs),
    )

    result = subtitle_enhancer._request_enhanced_batch(
        [{'segmentId': 'seg-1', 'text': '测试字幕'}],
        provider_context=_context(),
        observation_key='deepseek:job-1:window-1',
        attempt=2,
    )

    observation = captured['observed_call']
    assert result['seg-1']['text'] == '测试字幕'
    assert captured['provider'] == 'deepseek'
    assert captured['model'] == 'deepseek-v4-flash'
    assert captured['operation'] == 'chat_completion'
    assert observation.idempotency_key == 'deepseek:job-1:window-1'
    assert observation.attempt == 2
    assert observation.request_id == 'deepseek-request-1'
    assert observation.usage == {
        'prompt_tokens': 120,
        'completion_tokens': 30,
        'total_tokens': 150,
        'prompt_cache_hit_tokens': 80,
        'prompt_cache_miss_tokens': 40,
    }
    assert {meter.meter: meter.quantity for meter in observation.meters} == {
        'output_tokens': 30,
        'cache_hit_tokens': 80,
        'cache_miss_tokens': 40,
    }


def test_deepseek_batch_marks_missing_usage_unmetered_without_estimating(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'test-key')
    monkeypatch.setattr(subtitle_enhancer.httpx, 'post', lambda *_args, **_kwargs: _response())
    monkeypatch.setattr(
        subtitle_enhancer,
        'record_legacy_provider_observation',
        lambda context, **kwargs: captured.update(context=context, **kwargs),
    )

    subtitle_enhancer._request_enhanced_batch(
        [{'segmentId': 'seg-1', 'text': '这段文字不能拿来估算 token'}],
        provider_context=_context(),
        observation_key='deepseek:job-1:window-2',
    )

    observation = captured['observed_call']
    assert observation.usage is None
    assert {meter.meter: meter.quantity for meter in observation.meters} == {
        'input_tokens': None,
        'output_tokens': None,
    }


def test_deepseek_fact_writer_failure_keeps_legacy_result(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'subtitle_enhancer_api_key', 'test-key')
    monkeypatch.setattr(subtitle_enhancer.httpx, 'post', lambda *_args, **_kwargs: _response())

    def broken_writer(*_args, **_kwargs):
        raise RuntimeError('writer unavailable Authorization: Bearer secret')

    monkeypatch.setattr(subtitle_enhancer, 'record_legacy_provider_observation', broken_writer)

    result = subtitle_enhancer._request_enhanced_batch(
        [{'segmentId': 'seg-1', 'text': '测试字幕'}],
        provider_context=_context(),
        observation_key='deepseek:job-1:window-3',
    )

    assert result['seg-1']['text'] == '测试字幕'
