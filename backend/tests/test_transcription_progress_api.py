from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import Base
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.project import Project
from app.models.subtitle_pipeline_run import SubtitlePipelineRun
from app.services.subtitle_pipeline_runs import (
    create_pipeline_run_before_asr,
    transition_pipeline_run,
    update_pipeline_run_metrics,
)
from main import app


def _database():
    engine = create_engine(
        'sqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_progress_api_returns_real_counters_and_is_read_only(monkeypatch) -> None:
    engine, factory = _database()
    with factory() as session:
        project = Project(id='project-1', name='Alpha', name_key='alpha')
        job = Job(
            id='job-1',
            source_url='https://example.invalid/audio.wav',
            project_id=project.id,
        )
        session.add_all([project, job])
        session.commit()
        run = create_pipeline_run_before_asr(
            session,
            job=job,
            submission_idempotency_key='submit-1',
        )
        transition_pipeline_run(session, run.id, 'asr_running')
        update_pipeline_run_metrics(
            session,
            run.id,
            asr_total_chunks=4,
            asr_completed_chunks=1,
            asr_provider_status='RUNNING',
            ai_total_windows=3,
            ai_completed_windows=0,
            ai_failed_windows=0,
            ai_degraded_windows=0,
            validator_phase='pending',
            validator_issue_count=0,
            validator_p0_count=0,
            warnings=[],
        )
        before_job_updated = job.updated_at
        before_run_updated = run.updated_at
        before_events = session.scalar(select(func.count()).select_from(JobEvent))

    monkeypatch.setattr('app.api.v2.transcriptions.SessionLocal', factory)
    monkeypatch.setattr(settings, 'speaker_v2_shadow_enabled', False)

    response = TestClient(app).get(
        '/api/v2/projects/project-1/transcriptions/jobs/job-1/progress',
    )

    assert response.status_code == 200
    body = response.json()
    assert body['stage'] == 'asr_running'
    assert body['status'] == 'running'
    assert body['asr']['completed_chunks'] == 1
    assert body['asr']['total_chunks'] == 4
    assert body['ai']['total_windows'] == 3
    assert body['validation']['phase'] == 'pending'
    assert body['terminal'] is False
    assert 'percentage' not in body
    assert 'progress' not in body

    with factory() as session:
        assert session.get(Job, 'job-1').updated_at == before_job_updated
        assert session.get(SubtitlePipelineRun, run.id).updated_at == before_run_updated
        assert session.scalar(select(func.count()).select_from(JobEvent)) == before_events


def test_progress_api_hides_cross_project_runs(monkeypatch) -> None:
    _, factory = _database()
    with factory() as session:
        project = Project(id='project-1', name='Alpha', name_key='alpha')
        job = Job(
            id='job-1',
            source_url='https://example.invalid/audio.wav',
            project_id=project.id,
        )
        session.add_all([project, job])
        session.commit()
        create_pipeline_run_before_asr(
            session,
            job=job,
            submission_idempotency_key='submit-1',
        )

    monkeypatch.setattr('app.api.v2.transcriptions.SessionLocal', factory)
    monkeypatch.setattr(settings, 'speaker_v2_shadow_enabled', False)

    response = TestClient(app).get(
        '/api/v2/projects/project-2/transcriptions/jobs/job-1/progress',
    )

    assert response.status_code == 404
