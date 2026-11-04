from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.session import Base
from app.models.cost_event import CostEvent
from app.models.provider_call import ProviderCall
from app.models.subtitle_pipeline_run import SubtitlePipelineRun
from app.schemas.provider_cost import ProviderCallContext
from app.services import provider_costs
from app.services.fun_asr import FunAsrClient
from app.services.provider_costs import ProviderCallClaimUnavailableError, ProviderCallExecutionError
from app.services.subtitle_pipeline_runs import (
    PipelineRunLease,
    SubtitlePipelineLeaseError,
    claim_pipeline_run_lease,
)


def _factory(tmp_path):
    engine = create_engine(
        f'sqlite:///{tmp_path / "fun-asr-v2.db"}',
        connect_args={'check_same_thread': False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _run(session: Session) -> SubtitlePipelineRun:
    run = SubtitlePipelineRun(
        project_id='project-1',
        job_id='job-1',
        run_sequence=1,
        submission_idempotency_key='submit-1',
        pipeline_version='speaker-v2',
        stage='queued',
        status='queued',
        artifacts={'schema_version': 1},
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _context(run_id: str) -> ProviderCallContext:
    return ProviderCallContext(
        project_id='project-1',
        job_id='job-1',
        run_id=run_id,
        pipeline_scope='subtitle_v2',
    )


def _response(*, status_code: int = 200):
    payload = (
        {'output': {'task_id': 'task-1'}, 'request_id': 'request-1'}
        if status_code == 200
        else {'code': 'ServiceUnavailable', 'message': 'retry later'}
    )
    return SimpleNamespace(status_code=status_code, json=lambda: payload, text='')


def test_v2_fun_asr_requires_valid_run_lease_before_network(monkeypatch, tmp_path) -> None:
    factory = _factory(tmp_path)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(provider_costs, 'SessionLocal', factory)
    monkeypatch.setattr('app.services.fun_asr.SessionLocal', factory)
    network_calls = 0

    def fake_post(*args, **kwargs):
        nonlocal network_calls
        network_calls += 1
        return _response()

    monkeypatch.setattr(httpx, 'post', fake_post)
    with factory() as session:
        run = _run(session)
        stale = PipelineRunLease(
            run_id=run.id,
            owner='missing-worker',
            lock_version=99,
            expires_at=datetime(2026, 7, 16, 10, 5, 0),
        )
        with pytest.raises(SubtitlePipelineLeaseError):
            FunAsrClient().submit_transcription_v2(
                file_url='https://example.invalid/audio.wav',
                language_hints=['zh'],
                speaker_count=2,
                provider_context=_context(run.id),
                run_lease=stale,
                chunk_index=0,
            )

    assert network_calls == 0


def test_v2_fun_asr_claims_chunk_once_and_retry_does_not_duplicate_cost(
    monkeypatch,
    tmp_path,
) -> None:
    factory = _factory(tmp_path)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(provider_costs, 'SessionLocal', factory)
    monkeypatch.setattr('app.services.fun_asr.SessionLocal', factory)
    now = datetime.utcnow()
    with factory() as creator:
        run_id = _run(creator).id
        lease = claim_pipeline_run_lease(
            creator,
            run_id,
            owner='worker-a',
            now=now,
            ttl_seconds=300,
        )
        assert lease is not None

    network_calls = 0

    def successful_post(*args, **kwargs):
        nonlocal network_calls
        network_calls += 1
        return _response()

    monkeypatch.setattr(httpx, 'post', successful_post)
    with factory() as first_session, factory() as second_session:
        result = FunAsrClient().submit_transcription_v2(
            file_url='https://example.invalid/audio.wav',
            language_hints=['zh'],
            speaker_count=2,
            provider_context=_context(run_id),
            run_lease=lease,
            chunk_index=0,
            audio_seconds=12.5,
        )
        assert result['task_id'] == 'task-1'

        with pytest.raises(ProviderCallClaimUnavailableError):
            FunAsrClient().submit_transcription_v2(
                file_url='https://example.invalid/audio.wav',
                language_hints=['zh'],
                speaker_count=2,
                provider_context=_context(run_id),
                run_lease=lease,
                chunk_index=0,
                audio_seconds=12.5,
            )

    assert network_calls == 1

    responses = iter([_response(status_code=503), _response()])

    def retrying_post(*args, **kwargs):
        nonlocal network_calls
        network_calls += 1
        return next(responses)

    monkeypatch.setattr(httpx, 'post', retrying_post)
    with factory() as session:
        with pytest.raises(ProviderCallExecutionError):
            FunAsrClient().submit_transcription_v2(
                file_url='https://example.invalid/retry.wav',
                language_hints=['zh'],
                speaker_count=2,
                provider_context=_context(run_id),
                run_lease=lease,
                chunk_index=1,
                attempt=1,
                audio_seconds=20,
            )
        FunAsrClient().submit_transcription_v2(
            file_url='https://example.invalid/retry.wav',
            language_hints=['zh'],
            speaker_count=2,
            provider_context=_context(run_id),
            run_lease=lease,
            chunk_index=1,
            attempt=2,
            audio_seconds=20,
        )

        calls = list(session.scalars(
            select(ProviderCall).where(
                ProviderCall.idempotency_key == f'fun-asr-v2:{run_id}:1',
            ).order_by(ProviderCall.attempt)
        ))
        assert [call.attempt for call in calls] == [1, 2]
        assert session.scalar(select(func.count()).select_from(CostEvent)) == 2


def test_legacy_fun_asr_rejects_v2_context_before_network(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    network_calls = 0

    def fake_post(*args, **kwargs):
        nonlocal network_calls
        network_calls += 1
        return _response()

    monkeypatch.setattr(httpx, 'post', fake_post)
    with pytest.raises(ValueError, match='submit_transcription_v2'):
        FunAsrClient().submit_transcription(
            file_url='https://example.invalid/audio.wav',
            language_hints=['zh'],
            speaker_count=2,
            provider_context=_context('run-1'),
        )

    assert network_calls == 0
