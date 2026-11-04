from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.subtitle_pipeline_run import SubtitlePipelineRun
from app.services.subtitle_pipeline_runs import (
    SubtitlePipelineLeaseError,
    checkpoint_pipeline_run_with_lease,
    claim_pipeline_run_lease,
)
from app.services.subtitle_pipeline_tasks import claim_and_execute_subtitle_v2_run


def _factory() -> sessionmaker:
    engine = create_engine(
        'sqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _run(session: Session, run_id: str) -> SubtitlePipelineRun:
    run = SubtitlePipelineRun(
        id=run_id,
        project_id='project-1',
        job_id=f'job-{run_id}',
        run_sequence=1,
        submission_idempotency_key=f'submit-{run_id}',
        pipeline_version='speaker-v2',
        stage='evidence_matching',
        status='running',
        artifacts={'schema_version': 1},
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_checkpoint_write_is_fenced_and_updates_artifact_and_metrics_together() -> None:
    factory = _factory()
    now = datetime.now(timezone.utc)
    with factory() as session:
        run = _run(session, 'run-checkpoint')
        lease = claim_pipeline_run_lease(
            session,
            run.id,
            owner='worker-1',
            now=now,
            ttl_seconds=60,
        )
        assert lease is not None

        saved = checkpoint_pipeline_run_with_lease(
            session,
            lease,
            artifacts={
                'schema_version': 1,
                'pipeline_schema_version': 'subtitle-pipeline-v2-run.v1',
            },
            artifact_hash='a' * 64,
            artifact_refs={'raw_snapshot_hash': 'b' * 64},
            metrics={
                'ai_total_windows': 2,
                'ai_completed_windows': 1,
                'ai_failed_windows': 0,
                'ai_degraded_windows': 0,
            },
            now=now + timedelta(seconds=1),
        )

        assert saved.artifact_hash == 'a' * 64
        assert saved.artifact_refs == {'raw_snapshot_hash': 'b' * 64}
        assert saved.ai_completed_windows == 1

        with pytest.raises(SubtitlePipelineLeaseError):
            checkpoint_pipeline_run_with_lease(
                session,
                replace(lease, owner='stale-worker'),
                artifacts={'schema_version': 1, 'tampered': True},
                artifact_hash='c' * 64,
                now=now + timedelta(seconds=2),
            )

        session.expire_all()
        unchanged = session.get(SubtitlePipelineRun, run.id)
        assert unchanged is not None
        assert unchanged.artifact_hash == 'a' * 64
        assert 'tampered' not in unchanged.artifacts


def test_task_claims_before_execution_and_skips_when_another_worker_owns_run() -> None:
    factory = _factory()
    now = datetime.now(timezone.utc)
    with factory() as session:
        held = _run(session, 'run-held')
        free = _run(session, 'run-free')
        existing = claim_pipeline_run_lease(
            session,
            held.id,
            owner='worker-a',
            now=now,
            ttl_seconds=60,
        )
        assert existing is not None
        executed: list[str] = []

        skipped = claim_and_execute_subtitle_v2_run(
            session,
            held.id,
            owner='worker-b',
            execute=lambda _session, lease: executed.append(lease.run_id),
            now=now,
            ttl_seconds=60,
        )
        result = claim_and_execute_subtitle_v2_run(
            session,
            free.id,
            owner='worker-b',
            execute=lambda _session, lease: f'executed:{lease.run_id}',
            now=now,
            ttl_seconds=60,
        )

        assert skipped is None
        assert executed == []
        assert result == f'executed:{free.id}'
