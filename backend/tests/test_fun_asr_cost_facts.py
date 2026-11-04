from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.api.v1.transcriptions import _retry_provider_task_if_transient
from app.schemas.provider_cost import ProviderCallContext
from app.services.fun_asr import FunAsrClient, FunAsrError
from app.services.transcription_tasks import _fetch_transcription_payload_parts


def _context() -> ProviderCallContext:
    return ProviderCallContext(
        project_id='project-1',
        job_id='job-1',
        pipeline_scope='subtitle_legacy',
    )


def _response(payload: dict, status_code: int = 200) -> SimpleNamespace:
    return SimpleNamespace(status_code=status_code, json=lambda: payload, text='')


def test_submit_records_audio_usage_after_provider_accepts(monkeypatch) -> None:
    recorded = []
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        httpx,
        'post',
        lambda *args, **kwargs: _response(
            {'output': {'task_id': 'task-123'}, 'request_id': 'req-123'}
        ),
    )
    monkeypatch.setattr(
        'app.services.fun_asr.record_legacy_provider_observation',
        lambda context, **kwargs: recorded.append((context, kwargs)),
    )

    result = FunAsrClient().submit_transcription(
        file_url='https://example.com/audio.wav',
        language_hints=['zh'],
        speaker_count=2,
        provider_context=_context(),
        observation_key='fun-asr:job-1:0',
        audio_seconds=12.5,
    )

    assert result == {'task_id': 'task-123', 'request_id': 'req-123'}
    context, call = recorded[0]
    observation = call['observed_call']
    assert context.job_id == 'job-1'
    assert call['operation'] == 'transcription_submit'
    assert observation.idempotency_key == 'fun-asr:job-1:0'
    assert observation.attempt == 1
    assert observation.status == 'completed'
    assert observation.request_id == 'req-123'
    assert observation.usage == {'audio_seconds': 12.5}
    assert observation.meters[0].meter == 'audio_seconds'
    assert float(observation.meters[0].quantity) == 12.5
    assert observation.value == result


def test_submit_records_unmetered_audio_when_duration_is_unknown(monkeypatch) -> None:
    recorded = []
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        httpx,
        'post',
        lambda *args, **kwargs: _response(
            {'output': {'task_id': 'task-123'}, 'request_id': 'req-123'}
        ),
    )
    monkeypatch.setattr(
        'app.services.fun_asr.record_legacy_provider_observation',
        lambda context, **kwargs: recorded.append((context, kwargs)),
    )

    FunAsrClient().submit_transcription(
        file_url='https://example.com/audio.wav',
        language_hints=['zh'],
        speaker_count=2,
        provider_context=_context(),
        observation_key='fun-asr:job-1:0',
    )

    observation = recorded[0][1]['observed_call']
    assert observation.usage is None
    assert len(observation.meters) == 1
    assert observation.meters[0].meter == 'audio_seconds'
    assert observation.meters[0].quantity is None


def test_failed_submit_records_attempt_without_request_id(monkeypatch) -> None:
    recorded = []
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        httpx,
        'post',
        lambda *args, **kwargs: _response(
            {'code': 'ServiceUnavailable', 'message': 'try later'},
            status_code=503,
        ),
    )
    monkeypatch.setattr(
        'app.services.fun_asr.record_legacy_provider_observation',
        lambda context, **kwargs: recorded.append((context, kwargs)),
    )

    with pytest.raises(FunAsrError, match='try later'):
        FunAsrClient().submit_transcription(
            file_url='https://example.com/audio.wav',
            language_hints=['zh'],
            speaker_count=2,
            provider_context=_context(),
            observation_key='fun-asr:job-1:0',
            attempt=2,
            audio_seconds=12.5,
        )

    observation = recorded[0][1]['observed_call']
    assert observation.attempt == 2
    assert observation.status == 'failed'
    assert observation.request_id is None
    assert observation.usage is None
    assert observation.meters == []


def test_query_and_cancel_do_not_record_transcription_cost(monkeypatch) -> None:
    recorded = []
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(httpx, 'get', lambda *args, **kwargs: _response({'output': {}}))
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs: _response({'output': {}}))
    monkeypatch.setattr(
        'app.services.fun_asr.record_legacy_provider_observation',
        lambda context, **kwargs: recorded.append((context, kwargs)),
    )

    client = FunAsrClient()
    client.query_task('task-123')
    client.cancel_task('task-123')

    assert recorded == []


def test_cancel_keeps_already_recorded_submit_usage(monkeypatch) -> None:
    recorded = []
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        httpx,
        'post',
        lambda url, *args, **kwargs: _response(
            {'output': {'task_id': 'task-123'}, 'request_id': 'req-123'}
            if url.endswith('/transcription')
            else {'output': {'task_status': 'CANCELED'}}
        ),
    )
    monkeypatch.setattr(
        'app.services.fun_asr.record_legacy_provider_observation',
        lambda context, **kwargs: recorded.append((context, kwargs)),
    )

    client = FunAsrClient()
    client.submit_transcription(
        file_url='https://example.com/audio.wav',
        language_hints=['zh'],
        speaker_count=2,
        provider_context=_context(),
        observation_key='fun-asr:job-1:0',
        audio_seconds=12.5,
    )
    client.cancel_task('task-123')

    assert len(recorded) == 1
    observation = recorded[0][1]['observed_call']
    assert observation.status == 'completed'
    assert float(observation.meters[0].quantity) == 12.5


def test_fetch_records_download_evidence_without_cost_meter(monkeypatch) -> None:
    recorded = []
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(httpx, 'get', lambda *args, **kwargs: _response({'transcripts': []}))
    monkeypatch.setattr(
        'app.services.fun_asr.record_legacy_provider_observation',
        lambda context, **kwargs: recorded.append((context, kwargs)),
    )

    result = FunAsrClient().fetch_transcription_payload(
        'https://example.com/result.json',
        provider_context=_context(),
        observation_key='fun-asr:job-1:0:result',
    )

    assert result == {'transcripts': []}
    observation = recorded[0][1]['observed_call']
    assert recorded[0][1]['operation'] == 'transcription_result_fetch'
    assert observation.status == 'completed'
    assert observation.usage is None
    assert observation.meters == []
    assert observation.value is None


def test_observation_writer_failure_does_not_change_provider_result(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        httpx,
        'post',
        lambda *args, **kwargs: _response(
            {'output': {'task_id': 'task-123'}, 'request_id': 'req-123'}
        ),
    )

    def fail_writer(*args, **kwargs):
        raise RuntimeError('facts unavailable')

    monkeypatch.setattr('app.services.fun_asr.record_legacy_provider_observation', fail_writer)

    result = FunAsrClient().submit_transcription(
        file_url='https://example.com/audio.wav',
        language_hints=['zh'],
        speaker_count=2,
        provider_context=_context(),
        observation_key='fun-asr:job-1:0',
        audio_seconds=12.5,
    )

    assert result == {'task_id': 'task-123', 'request_id': 'req-123'}


def test_transient_retry_uses_next_attempt_for_same_chunk(monkeypatch) -> None:
    submitted = {}

    class FakeClient:
        def submit_transcription(self, **kwargs):
            submitted.update(kwargs)
            return {'task_id': 'task-retry', 'request_id': 'req-retry'}

    monkeypatch.setattr(settings, 'dashscope_asr_transient_retry_limit', 3)

    result = _retry_provider_task_if_transient(
        client=FakeClient(),
        entry={
            'task_id': 'task-old',
            'file_url': 'https://example.com/audio.wav',
            'duration_seconds': 12.5,
            'chunk_index': 4,
            'provider_retry_count': 1,
        },
        provider_output={
            'output': {
                'task_status': 'FAILED',
                'code': 'INSTANCE_POOL_EXHAUSTED',
            }
        },
        provider_context=_context(),
    )

    assert result is not None
    assert result['provider_retry_count'] == 2
    assert submitted['provider_context'].job_id == 'job-1'
    assert submitted['observation_key'] == 'fun-asr:job-1:4'
    assert submitted['attempt'] == 3
    assert submitted['audio_seconds'] == 12.5


def test_failed_retry_records_attempt_without_mutating_business_retry_state(monkeypatch) -> None:
    attempts = []

    class FakeClient:
        def submit_transcription(self, **kwargs):
            attempts.append(kwargs['attempt'])
            raise FunAsrError('temporary submit failure')

    monkeypatch.setattr(settings, 'dashscope_asr_transient_retry_limit', 3)
    entry = {
        'task_id': 'task-old',
        'file_url': 'https://example.com/audio.wav',
        'chunk_index': 4,
        'provider_retry_count': 1,
    }
    provider_output = {
        'output': {
            'task_status': 'FAILED',
            'code': 'INSTANCE_POOL_EXHAUSTED',
        }
    }

    with pytest.raises(FunAsrError, match='temporary submit failure'):
        _retry_provider_task_if_transient(
            client=FakeClient(),
            entry=entry,
            provider_output=provider_output,
            provider_context=_context(),
        )

    assert attempts == [3]
    assert entry['provider_retry_count'] == 1


def test_finalize_passes_download_evidence_identity_without_billing_usage() -> None:
    fetched = {}

    class FakeClient:
        def fetch_transcription_payload(self, transcription_url: str, **kwargs) -> dict:
            fetched.update(url=transcription_url, **kwargs)
            return {'transcripts': []}

    result = _fetch_transcription_payload_parts(
        FakeClient(),
        [
            {
                'transcription_url': 'https://example.com/result.json',
                'chunk_index': 4,
                'offset_ms': 0,
                'speaker_offset': 0,
            }
        ],
        provider_context=_context(),
    )

    assert result[0][0] == {'transcripts': []}
    assert fetched['provider_context'].job_id == 'job-1'
    assert fetched['observation_key'] == 'fun-asr:job-1:4:result'
