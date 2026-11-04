from __future__ import annotations

from app.core.config import settings
from app.schemas.provider_cost import ProviderCallContext
from app.services import cosyvoice_voice_clone, qwen_voice_clone


def _context() -> ProviderCallContext:
    return ProviderCallContext(project_id='project-1', pipeline_scope='project_asset')


class FakeResponse:
    status_code = 200
    text = '{}'

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_qwen_create_and_synthesis_use_distinct_cost_operations(monkeypatch) -> None:
    observations = []
    responses = iter(
        [
            FakeResponse({'request_id': 'qwen-create-1', 'output': {'voice': 'voice-1'}}),
            FakeResponse(
                {
                    'request_id': 'qwen-synthesis-1',
                    'output': {'audio': {'url': 'https://example.com/audio.wav'}},
                    'usage': {'characters': 18},
                }
            ),
        ]
    )
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(qwen_voice_clone.httpx, 'post', lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(
        qwen_voice_clone,
        'record_legacy_provider_observation',
        lambda context, **kwargs: observations.append((context, kwargs)),
    )

    qwen_voice_clone.create_qwen_voice(
        sample_public_url='https://example.com/sample.wav',
        target_model='qwen3-tts-vc-2026-01-22',
        voice_name='demo',
        provider_context=_context(),
        observation_key='qwen:create:profile-1',
    )
    qwen_voice_clone.synthesize_qwen_voice(
        target_model='qwen3-tts-vc-2026-01-22',
        voice='voice-1',
        text='需要生成的文本',
        provider_context=_context(),
        observation_key='qwen:synthesize:record-1',
    )

    create = observations[0][1]
    synthesis = observations[1][1]
    assert create['provider'] == synthesis['provider'] == 'qwen'
    assert create['model'] == synthesis['model'] == 'qwen3-tts-vc-2026-01-22'
    assert create['operation'] == 'create_voice'
    assert synthesis['operation'] == 'synthesize_voice'
    assert create['observed_call'].meters[0].meter == 'voice_count'
    assert create['observed_call'].meters[0].quantity is None
    assert synthesis['observed_call'].usage == {'characters': 18}
    assert synthesis['observed_call'].meters[0].meter == 'characters'
    assert synthesis['observed_call'].meters[0].quantity == 18


def test_cosyvoice_uses_same_fact_shape_with_own_provider_and_model(monkeypatch) -> None:
    observations = []
    responses = iter(
        [
            FakeResponse({'request_id': 'cosy-create-1', 'output': {'voice_id': 'voice-2'}}),
            FakeResponse(
                {
                    'request_id': 'cosy-synthesis-1',
                    'output': {'audio_url': 'https://example.com/audio.mp3'},
                    'usage': {'audio_seconds': 3.25},
                }
            ),
        ]
    )
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(cosyvoice_voice_clone.httpx, 'post', lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(
        cosyvoice_voice_clone,
        'record_legacy_provider_observation',
        lambda context, **kwargs: observations.append((context, kwargs)),
    )

    cosyvoice_voice_clone.create_cosyvoice_voice(
        sample_public_url='https://example.com/sample.wav',
        target_model='cosyvoice-v3.5-plus',
        voice_name='demo',
        provider_context=_context(),
        observation_key='cosy:create:profile-1',
    )
    cosyvoice_voice_clone.synthesize_cosyvoice_voice(
        target_model='cosyvoice-v3.5-plus',
        voice_id='voice-2',
        text='需要生成的文本',
        provider_context=_context(),
        observation_key='cosy:synthesize:record-1',
    )

    create = observations[0][1]
    synthesis = observations[1][1]
    assert create['provider'] == synthesis['provider'] == 'cosyvoice'
    assert create['model'] == synthesis['model'] == 'cosyvoice-v3.5-plus'
    assert create['operation'] == 'create_voice'
    assert synthesis['operation'] == 'synthesize_voice'
    assert create['observed_call'].model_dump().keys() == synthesis['observed_call'].model_dump().keys()
    assert synthesis['observed_call'].usage == {'audio_seconds': 3.25}
    assert synthesis['observed_call'].meters[0].meter == 'audio_seconds'


def test_voice_fact_writer_failure_keeps_provider_response(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        qwen_voice_clone.httpx,
        'post',
        lambda *_args, **_kwargs: FakeResponse(
            {'request_id': 'qwen-create-2', 'output': {'voice': 'voice-3'}}
        ),
    )
    monkeypatch.setattr(
        qwen_voice_clone,
        'record_legacy_provider_observation',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('token=do-not-log')),
    )

    result = qwen_voice_clone.create_qwen_voice(
        sample_public_url='https://example.com/sample.wav',
        target_model='qwen3-tts-vc-2026-01-22',
        voice_name='demo',
        provider_context=_context(),
        observation_key='qwen:create:writer-failure',
    )

    assert result['output']['voice'] == 'voice-3'
