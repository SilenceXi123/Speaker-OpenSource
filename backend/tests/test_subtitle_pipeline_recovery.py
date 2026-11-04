from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.job import Job
from app.models.subtitle_pipeline_run import SubtitlePipelineRun
from app.services.subtitle_pipeline_tasks import recover_nonterminal_runs


def _factory(tmp_path):
    engine = create_engine(
        f'sqlite:///{tmp_path / "pipeline-recovery.db"}',
        connect_args={'check_same_thread': False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _run(
    session: Session,
    *,
    key: str,
    stage: str,
    pipeline_version: str = 'speaker-v2',
    lease_expires_at: datetime | None,
) -> SubtitlePipelineRun:
    status = 'canceled' if stage == 'canceled' else 'running'
    run = SubtitlePipelineRun(
        project_id='project-1',
        job_id=f'job-{key}',
        run_sequence=1,
        submission_idempotency_key=f'submit-{key}',
        pipeline_version=pipeline_version,
        stage=stage,
        status=status,
        artifacts={'schema_version': 1},
        lease_owner='old-worker' if lease_expires_at else None,
        lease_expires_at=lease_expires_at,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_recovery_only_enqueues_unclaimed_or_expired_nonterminal_v2_runs(tmp_path) -> None:
    factory = _factory(tmp_path)
    now = datetime(2026, 7, 16, 9, 0, 0)
    with factory() as session:
        session.add_all([
            Job(source_url=f'https://example.invalid/{index}.wav')
            for index in range(498)
        ])
        session.commit()
        expired = _run(
            session,
            key='expired',
            stage='asr_running',
            lease_expires_at=now - timedelta(seconds=1),
        )
        unclaimed = _run(
            session,
            key='unclaimed',
            stage='queued',
            lease_expires_at=None,
        )
        _run(
            session,
            key='active',
            stage='asr_running',
            lease_expires_at=now + timedelta(seconds=30),
        )
        _run(
            session,
            key='canceled',
            stage='canceled',
            lease_expires_at=now - timedelta(seconds=1),
        )
        _run(
            session,
            key='legacy',
            stage='asr_running',
            pipeline_version='speaker-v1',
            lease_expires_at=now - timedelta(seconds=1),
        )

        queued: list[str] = []
        recovered = recover_nonterminal_runs(session, enqueue=queued.append, now=now)

        assert set(recovered) == {expired.id, unclaimed.id}
        assert set(queued) == {expired.id, unclaimed.id}
        assert session.scalar(select(func.count()).select_from(SubtitlePipelineRun)) == 5
