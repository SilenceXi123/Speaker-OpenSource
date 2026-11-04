from types import SimpleNamespace

import httpx

from app.core.config import settings
from app.schemas.provider_cost import ProviderCallContext
from app.services.fun_asr import FunAsrClient


def test_submit_transcription_includes_vocabulary_and_speaker_settings(monkeypatch) -> None:
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured['url'] = url
        captured['headers'] = headers
        captured['json'] = json
        captured['timeout'] = timeout
        return SimpleNamespace(
            status_code=200,
            json=lambda: {'output': {'task_id': 'task-123'}, 'request_id': 'req-123'},
            text='',
        )

    monkeypatch.setattr(httpx, 'post', fake_post)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    client = FunAsrClient()

    result = client.submit_transcription(
        file_url='https://example.com/audio.wav',
        language_hints=['zh'],
        speaker_count=2,
        vocabulary_id='vocab-123',
    )

    assert result['task_id'] == 'task-123'
    assert captured['json']['parameters']['diarization_enabled'] is True
    assert captured['json']['parameters']['language_hints'] == ['zh']
    assert captured['json']['parameters']['speaker_count'] == 2
    assert captured['json']['parameters']['vocabulary_id'] == 'vocab-123'


def test_submit_accepts_optional_provider_context_without_changing_request(monkeypatch) -> None:
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured['json'] = json
        return SimpleNamespace(
            status_code=200,
            json=lambda: {'output': {'task_id': 'task-123'}, 'request_id': 'req-123'},
            text='',
        )

    monkeypatch.setattr(httpx, 'post', fake_post)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        'app.services.fun_asr.record_legacy_provider_observation',
        lambda *args, **kwargs: None,
    )

    result = FunAsrClient().submit_transcription(
        file_url='https://example.com/audio.wav',
        language_hints=['zh'],
        speaker_count=2,
        provider_context=ProviderCallContext(
            project_id='project-1',
            job_id='job-1',
            pipeline_scope='subtitle_legacy',
        ),
        observation_key='fun-asr:job-1:0',
        audio_seconds=12.5,
    )

    assert result == {'task_id': 'task-123', 'request_id': 'req-123'}
    assert captured['json']['input']['file_urls'] == ['https://example.com/audio.wav']
