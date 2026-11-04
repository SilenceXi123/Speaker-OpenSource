from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.v1 import admin as admin_api
from app.db.session import Base
from app.models.job import Job
from app.models.job_event import JobEvent  # noqa: F401
from app.models.speaker_memory import SpeakerMemory  # noqa: F401
from app.services.job_events import append_job_event, count_job_events, list_job_events
from app.services.jobs import create_job, update_job_failure, update_job_processing


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_append_job_event_records_structured_trace() -> None:
    session = _build_session()
    job = Job(
        id='job-event-unit',
        kind='transcription',
        status='processing',
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
        program_key='项目-A',
        program_name='项目-A',
        created_at=datetime(2026, 6, 20, 10, 0, 0),
    )
    session.add(job)
    session.commit()

    event = append_job_event(
        session,
        job=job,
        event_type='processing_update',
        stage='subtitle_enhancing',
        status='subtitle_enhancing',
        progress=93,
        message='DeepSeek 字幕增强 4/19 批失败',
        details={'failed_batches': 4, 'total_batches': 19},
    )

    events = list_job_events(session, job.id)

    assert event.job_id == job.id
    assert event.program_name == '项目-A'
    assert event.progress == 93
    assert events[0].event_type == 'processing_update'
    assert events[0].details['failed_batches'] == 4


def test_job_status_helpers_append_job_events() -> None:
    session = _build_session()
    job = create_job(
        session=session,
        source_url='https://example.com/status.wav',
        source_media_kind='audio',
        program_key='项目-B',
        program_name='项目-B',
    )

    update_job_processing(session, job.id, 'subtitle_enhancing', 94, '字幕优化中')
    update_job_failure(session, job.id, '百炼任务状态异常: FAILED')

    events = list_job_events(session, job.id)

    assert [event.event_type for event in events] == [
        'job_created',
        'processing_update',
        'job_failed',
    ]
    assert events[-1].status == 'failed'
    assert events[-1].message == '百炼任务状态异常: FAILED'


def test_list_job_events_returns_latest_events_when_limit_is_reached() -> None:
    session = _build_session()
    job = Job(
        id='job-event-limit',
        kind='transcription',
        status='processing',
        source_url='https://example.com/limit.wav',
        source_media_kind='audio',
        created_at=datetime(2026, 6, 20, 10, 0, 0),
    )
    session.add(job)
    session.commit()

    for index in range(205):
        append_job_event(
            session,
            job=job,
            event_type=f'event_{index:03d}',
            stage='test',
            status='processing',
            progress=index % 100,
        )

    events = list_job_events(session, job.id, limit=200)

    assert count_job_events(session, job.id) == 205
    assert len(events) == 200
    assert events[0].event_type == 'event_005'
    assert events[-1].event_type == 'event_204'


def test_admin_job_events_returns_events(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        id='job-event-admin',
        kind='transcription',
        status='completed',
        source_url='https://example.com/admin.wav',
        source_media_kind='audio',
        program_key='项目-C',
        program_name='项目-C',
        created_at=datetime(2026, 6, 20, 11, 0, 0),
    )
    session.add(job)
    session.commit()
    append_job_event(
        session,
        job=job,
        event_type='pipeline_artifact_checked',
        stage='pipeline_artifacts',
        status='completed',
        progress=100,
        message='管理员检查链路',
        details={'cue_hash': 'a' * 64},
    )
    monkeypatch.setattr(admin_api, 'SessionLocal', lambda: session)

    response = admin_api.get_admin_job_events(job.id)

    assert response.job_id == job.id
    assert response.total == 1
    assert response.events[0].event_type == 'pipeline_artifact_checked'
    assert response.events[0].details['cue_hash'] == 'a' * 64


def test_admin_job_events_reports_missing_job(monkeypatch) -> None:
    session = _build_session()
    missing_job_id = 'missing-job-events'
    monkeypatch.setattr(admin_api, 'SessionLocal', lambda: session)

    with pytest.raises(HTTPException) as exc_info:
        admin_api.get_admin_job_events(missing_job_id)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == '任务不存在'
