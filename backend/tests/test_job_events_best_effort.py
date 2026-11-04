from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.job import Job
from app.models.job_event import JobEvent
from app.services.job_events import append_job_event
from app.services.jobs import create_job, update_job_failure, update_job_processing, update_job_status


def _session_factory(tmp_path):
    engine = create_engine(
        f'sqlite:///{tmp_path / "job-events.db"}',
        connect_args={'check_same_thread': False},
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_event_commit_failure_does_not_break_job_state_changes(tmp_path, caplog) -> None:
    SessionFactory = _session_factory(tmp_path)

    def fail_event_commit(session: Session) -> None:
        if any(isinstance(record, JobEvent) for record in session.new):
            raise RuntimeError('simulated event commit failure with secret-token-value')

    event.listen(Session, 'before_commit', fail_event_commit)
    caplog.set_level(logging.WARNING, logger='app.services.job_events')
    try:
        with SessionFactory() as session:
            job = create_job(
                session,
                source_url='https://example.com/best-effort.wav',
                source_media_kind='audio',
            )
            update_job_processing(session, job.id, 'processing', 40, '处理中')
            update_job_failure(session, job.id, '供应商暂时失败')
            update_job_status(session, job.id, 'completed')

        with SessionFactory() as observer:
            persisted = observer.get(Job, job.id)
            assert persisted is not None
            assert persisted.status == 'completed'
            assert list(observer.scalars(select(JobEvent))) == []
    finally:
        event.remove(Session, 'before_commit', fail_event_commit)

    assert 'job event write failed' in caplog.text
    assert 'secret-token-value' not in caplog.text


def test_event_write_does_not_commit_callers_pending_transaction(tmp_path) -> None:
    SessionFactory = _session_factory(tmp_path)
    with SessionFactory() as session:
        job = Job(
            id='existing-job',
            kind='transcription',
            status='processing',
            source_url='https://example.com/existing.wav',
            source_media_kind='audio',
            created_at=datetime(2026, 7, 16, 10, 0, 0),
        )
        session.add(job)
        session.commit()

        pending = Job(
            id='pending-job',
            kind='transcription',
            status='pending',
            source_url='https://example.com/pending.wav',
            source_media_kind='audio',
            created_at=datetime(2026, 7, 16, 10, 1, 0),
        )
        session.add(pending)

        append_job_event(
            session,
            job=job,
            event_type='processing_update',
            stage='processing',
            status='processing',
            progress=40,
        )

        with SessionFactory() as observer:
            assert observer.get(Job, pending.id) is None
            events = list(observer.scalars(select(JobEvent).where(JobEvent.job_id == job.id)))
            assert len(events) == 1


def test_event_cleanup_failures_do_not_escape(tmp_path, monkeypatch, caplog) -> None:
    SessionFactory = _session_factory(tmp_path)
    with SessionFactory() as session:
        job = Job(
            id='cleanup-failure-job',
            kind='transcription',
            status='processing',
            source_url='https://example.com/cleanup-failure.wav',
            source_media_kind='audio',
            created_at=datetime(2026, 7, 16, 10, 2, 0),
        )
        session.add(job)
        session.commit()

        class BrokenEventSession:
            def add(self, record) -> None:
                pass

            def commit(self) -> None:
                raise RuntimeError('commit-secret-value')

            def rollback(self) -> None:
                raise RuntimeError('rollback-secret-value')

            def close(self) -> None:
                raise RuntimeError('close-secret-value')

        monkeypatch.setattr('app.services.job_events.Session', lambda **kwargs: BrokenEventSession())
        caplog.set_level(logging.WARNING, logger='app.services.job_events')

        result = append_job_event(
            session,
            job=job,
            event_type='processing_update',
            stage='processing',
            status='processing',
            progress=40,
        )

    assert result is None
    assert 'job event write failed' in caplog.text
    assert 'RuntimeError' in caplog.text
    assert 'commit-secret-value' not in caplog.text
    assert 'rollback-secret-value' not in caplog.text
    assert 'close-secret-value' not in caplog.text
