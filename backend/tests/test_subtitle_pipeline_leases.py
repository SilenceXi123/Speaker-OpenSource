from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.subtitle_pipeline_run import SubtitlePipelineRun
from app.services.subtitle_pipeline_runs import (
    SubtitlePipelineLeaseError,
    build_job_finalize_lease_owner,
    claim_pipeline_run_lease,
    clear_pipeline_run_lease_for_owner,
    release_pipeline_run_lease,
    renew_pipeline_run_lease,
    transition_pipeline_run_with_lease,
)


def _factory(tmp_path):
    engine = create_engine(
        f'sqlite:///{tmp_path / "pipeline-leases.db"}',
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


def test_pipeline_run_lease_is_single_owner_and_stale_owner_cannot_finish(tmp_path) -> None:
    factory = _factory(tmp_path)
    with factory() as creator:
        run_id = _run(creator).id

    now = datetime(2026, 7, 16, 8, 0, 0)
    with factory() as first_session, factory() as second_session:
        first = claim_pipeline_run_lease(
            first_session,
            run_id,
            owner='worker-a',
            now=now,
            ttl_seconds=30,
        )
        competing = claim_pipeline_run_lease(
            second_session,
            run_id,
            owner='worker-b',
            now=now,
            ttl_seconds=30,
        )

        assert first is not None
        assert competing is None

        with pytest.raises(SubtitlePipelineLeaseError):
            renew_pipeline_run_lease(
                second_session,
                replace(first, owner='worker-b'),
                now=now + timedelta(seconds=5),
                ttl_seconds=30,
            )

        renewed = renew_pipeline_run_lease(
            first_session,
            first,
            now=now + timedelta(seconds=5),
            ttl_seconds=30,
        )
        takeover = claim_pipeline_run_lease(
            second_session,
            run_id,
            owner='worker-b',
            now=now + timedelta(seconds=36),
            ttl_seconds=30,
        )

        assert takeover is not None
        assert takeover.lock_version > renewed.lock_version

        with pytest.raises(SubtitlePipelineLeaseError):
            transition_pipeline_run_with_lease(
                first_session,
                renewed,
                'asr_running',
                now=now + timedelta(seconds=37),
            )

        advanced = transition_pipeline_run_with_lease(
            second_session,
            takeover,
            'asr_running',
            now=now + timedelta(seconds=37),
        )
        assert advanced.stage == 'asr_running'

        release_pipeline_run_lease(
            second_session,
            takeover,
            now=now + timedelta(seconds=38),
        )
        second_session.expire_all()
        released = second_session.get(SubtitlePipelineRun, run_id)
        assert released.lease_owner is None
        assert released.lease_expires_at is None
        assert released.lock_version == takeover.lock_version + 1


def test_orphaned_lease_clear_fences_the_observed_owner(tmp_path) -> None:
    factory = _factory(tmp_path)
    now = datetime(2026, 7, 16, 8, 0, 0)
    with factory() as session:
        run_id = _run(session).id
        owner = build_job_finalize_lease_owner('job-1', process_id=12345)
        lease = claim_pipeline_run_lease(
            session,
            run_id,
            owner=owner,
            now=now,
            ttl_seconds=3600,
        )

        assert lease is not None
        assert clear_pipeline_run_lease_for_owner(
            session,
            run_id,
            owner=owner,
            now=now + timedelta(seconds=5),
        ) is True
        session.expire_all()
        cleared = session.get(SubtitlePipelineRun, run_id)
        assert cleared.lease_owner is None
        assert cleared.lease_expires_at is None
        assert cleared.lock_version == lease.lock_version + 1

        with pytest.raises(SubtitlePipelineLeaseError):
            transition_pipeline_run_with_lease(
                session,
                lease,
                'asr_running',
                now=now + timedelta(seconds=6),
            )


def test_orphaned_lease_clear_requires_exact_owner(tmp_path) -> None:
    factory = _factory(tmp_path)
    now = datetime(2026, 7, 16, 8, 0, 0)
    with factory() as session:
        run_id = _run(session).id
        lease = claim_pipeline_run_lease(
            session,
            run_id,
            owner='worker-a',
            now=now,
            ttl_seconds=30,
        )

        assert lease is not None
        assert clear_pipeline_run_lease_for_owner(
            session,
            run_id,
            owner='worker-b',
            now=now + timedelta(seconds=5),
        ) is False
        session.expire_all()
        current = session.get(SubtitlePipelineRun, run_id)
        assert current.lease_owner == 'worker-a'
        assert current.lock_version == lease.lock_version
