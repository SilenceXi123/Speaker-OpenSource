from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.job import Job
from main import app


@pytest.fixture()
def job_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = create_engine(
        'sqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    monkeypatch.setattr('app.api.v1.transcriptions.SessionLocal', session_factory)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_job_summaries_exclude_large_detail_fields(job_session: Session) -> None:
    job = Job(
        id='job-summary-1',
        kind='transcription',
        status='completed',
        source_url='https://example.com/source.wav',
        source_export_path='/Volumes/Edit/source.wav',
        export_frame_rate='25',
        project_id='project-1',
        program_name='启动性能项目',
        pipeline_artifacts={'snapshot': 'x' * 1_000_000},
        script_context={'reference_lines': ['台本内容'] * 1000},
        processing_metrics={'debug_payload': 'y' * 100_000},
        provider_task_meta=[{'task_id': 'provider-task-1', 'payload': 'z' * 100_000}],
    )
    job_session.add(job)
    job_session.commit()

    response = TestClient(app).get('/api/v1/transcriptions/jobs/summaries')

    assert response.status_code == 200
    assert len(response.content) < 10_000
    assert response.json() == [{
        'id': 'job-summary-1',
        'kind': 'transcription',
        'status': 'completed',
        'source_url': 'https://example.com/source.wav',
        'source_local_path': None,
        'source_export_path': '/Volumes/Edit/source.wav',
        'export_frame_rate': '25',
        'project_id': 'project-1',
        'program_name': '启动性能项目',
        'video_source_url': None,
        'video_source_local_path': None,
        'video_source_export_path': None,
        'render_status': 'idle',
        'render_progress': 0,
        'render_message': None,
        'processing_progress': 0,
        'processing_message': None,
        'error_message': None,
        'created_at': job.created_at.isoformat(),
        'updated_at': job.updated_at.isoformat(),
    }]


def test_legacy_job_list_does_not_load_large_job_fields(job_session: Session) -> None:
    job = Job(
        id='legacy-job-1',
        kind='transcription',
        status='completed',
        source_url='https://example.com/source.wav',
        export_frame_rate='25',
        pipeline_artifacts={'snapshot': 'x' * 1_000_000},
        script_context={'reference_lines': ['台本内容'] * 1000},
        processing_metrics={'debug_payload': 'y' * 100_000},
        provider_task_meta=[{'task_id': 'provider-task-1', 'payload': 'z' * 100_000}],
    )
    job_session.add(job)
    job_session.commit()

    response = TestClient(app).get('/api/v1/transcriptions/jobs')

    assert response.status_code == 200
    assert len(response.content) < 10_000
    assert response.json()[0]['id'] == 'legacy-job-1'
    assert response.json()[0]['pipeline_artifacts'] is None
