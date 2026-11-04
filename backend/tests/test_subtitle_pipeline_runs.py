from __future__ import annotations

from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.job import Job
from app.models.cost_event import CostEvent
from app.models.provider_call import ProviderCall
from app.models.subtitle_pipeline_run import SubtitlePipelineRun
from app.services.jobs import abort_job
from app.services.subtitle_pipeline_runs import (
    bind_raw_snapshot_hash,
    create_pipeline_run_before_asr,
    set_pipeline_run_artifacts,
    transition_pipeline_run,
    update_pipeline_run_metrics,
)


def _engine():
    engine = create_engine(
        'sqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _job(session: Session, *, project_id: str = 'project-1') -> Job:
    job = Job(
        source_url='https://example.invalid/audio.wav',
        project_id=project_id,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_run_is_created_before_asr_and_submission_key_is_idempotent() -> None:
    with Session(_engine()) as session:
        job = _job(session)

        first = create_pipeline_run_before_asr(
            session,
            job=job,
            submission_idempotency_key='submit-1',
        )
        repeated = create_pipeline_run_before_asr(
            session,
            job=job,
            submission_idempotency_key='submit-1',
        )

        assert first.id == repeated.id
        assert first.run_sequence == 1
        assert first.raw_snapshot_hash is None
        assert first.stage == 'queued'
        assert session.scalar(select(func.count()).select_from(SubtitlePipelineRun)) == 1


def test_raw_snapshot_binding_is_cas_and_same_snapshot_can_replan() -> None:
    with Session(_engine()) as session:
        job = _job(session)
        first = create_pipeline_run_before_asr(
            session,
            job=job,
            submission_idempotency_key='submit-1',
        )

        bound = bind_raw_snapshot_hash(
            session,
            run_id=first.id,
            raw_snapshot_hash='raw-sha-1',
            expected_lock_version=0,
        )
        assert bound.raw_snapshot_hash == 'raw-sha-1'
        assert bound.lock_version == 1

        try:
            bind_raw_snapshot_hash(
                session,
                run_id=first.id,
                raw_snapshot_hash='raw-sha-2',
                expected_lock_version=0,
            )
        except RuntimeError as error:
            assert 'CAS' in str(error)
        else:
            raise AssertionError('stale CAS must fail')

        second = create_pipeline_run_before_asr(
            session,
            job=job,
            submission_idempotency_key='submit-2',
        )
        bind_raw_snapshot_hash(
            session,
            run_id=second.id,
            raw_snapshot_hash='raw-sha-1',
            expected_lock_version=0,
        )
        assert second.run_sequence == 2


def test_state_machine_metrics_and_run_artifacts_are_persisted() -> None:
    with Session(_engine()) as session:
        run = create_pipeline_run_before_asr(
            session,
            job=_job(session),
            submission_idempotency_key='submit-1',
        )

        transition_pipeline_run(session, run.id, 'asr_running')
        update_pipeline_run_metrics(
            session,
            run.id,
            asr_total_chunks=3,
            asr_completed_chunks=2,
            asr_provider_status='RUNNING',
            ai_total_windows=4,
            ai_completed_windows=2,
            ai_failed_windows=1,
            ai_degraded_windows=1,
            validator_phase='pending',
            validator_issue_count=2,
            validator_p0_count=1,
            warnings=['window 3 degraded'],
        )
        set_pipeline_run_artifacts(
            session,
            run.id,
            artifacts={'schema_version': 1, 'evidence': {'raw_snapshot_hash': 'raw-sha-1'}},
            artifact_hash='artifact-sha-1',
            artifact_refs={'raw': 'raw-sha-1'},
        )
        transition_pipeline_run(session, run.id, 'evidence_matching')
        transition_pipeline_run(session, run.id, 'ai_planning')
        transition_pipeline_run(session, run.id, 'cue_validating')
        finished = transition_pipeline_run(session, run.id, 'ready_with_warnings')

        assert finished.status == 'ready_with_warnings'
        assert finished.asr_completed_chunks == 2
        assert finished.ai_failed_windows == 1
        assert finished.validator_p0_count == 1
        assert finished.warning_count == 1
        assert finished.artifact_hash == 'artifact-sha-1'
        assert finished.asr_started_at is not None
        assert finished.evidence_started_at is not None
        assert finished.ai_started_at is not None
        assert finished.validation_started_at is not None
        assert finished.completed_at is not None

        try:
            transition_pipeline_run(session, run.id, 'ai_planning')
        except RuntimeError as error:
            assert '状态转换' in str(error)
        else:
            raise AssertionError('terminal run cannot restart')


def test_abort_cancels_active_run_without_deleting_provider_facts() -> None:
    engine = _engine()
    with Session(engine) as session:
        job = _job(session)
        run = create_pipeline_run_before_asr(
            session,
            job=job,
            submission_idempotency_key='submit-1',
        )
        transition_pipeline_run(session, run.id, 'asr_running')
        provider_call = ProviderCall(
            project_id=job.project_id,
            job_id=job.id,
            run_id=run.id,
            provider='dashscope',
            model='fun-asr',
            operation='transcribe',
            idempotency_key='provider-1',
            attempt=1,
        )
        session.add(provider_call)
        session.flush()
        session.add(CostEvent(
            provider_call_id=provider_call.id,
            project_id=job.project_id,
            job_id=job.id,
            run_id=run.id,
            meter='audio_seconds',
            cost_basis='list_price',
            measurement_status='measured',
            quantity=Decimal('1'),
            unit='second',
            amount_microunits=100,
            currency='CNY',
        ))
        session.commit()

        aborted = abort_job(session, job.id)

        assert aborted.status == 'aborted'
        assert session.get(SubtitlePipelineRun, run.id).status == 'canceled'
        assert session.scalar(select(func.count()).select_from(ProviderCall)) == 1
        assert session.scalar(select(func.count()).select_from(CostEvent)) == 1
