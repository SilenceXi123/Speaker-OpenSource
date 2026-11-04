from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.subtitle_pipeline_run import SubtitlePipelineRun


TERMINAL_RUN_STAGES = frozenset({'ready', 'ready_with_warnings', 'failed', 'canceled'})
RUN_STAGE_TRANSITIONS = {
    'queued': frozenset({'asr_running', 'failed', 'canceled'}),
    'asr_running': frozenset({'evidence_matching', 'failed', 'canceled'}),
    'evidence_matching': frozenset({'ai_planning', 'failed', 'canceled'}),
    'ai_planning': frozenset({'cue_validating', 'failed', 'canceled'}),
    'cue_validating': frozenset({'ready', 'ready_with_warnings', 'failed', 'canceled'}),
    'ready': frozenset(),
    'ready_with_warnings': frozenset(),
    'failed': frozenset(),
    'canceled': frozenset(),
}
RUN_STAGE_STATUSES = {
    'queued': 'queued',
    'asr_running': 'running',
    'evidence_matching': 'running',
    'ai_planning': 'running',
    'cue_validating': 'running',
    'ready': 'ready',
    'ready_with_warnings': 'ready_with_warnings',
    'failed': 'failed',
    'canceled': 'canceled',
}
METRIC_FIELDS = frozenset({
    'asr_total_chunks',
    'asr_completed_chunks',
    'asr_provider_status',
    'ai_total_windows',
    'ai_completed_windows',
    'ai_failed_windows',
    'ai_degraded_windows',
    'validator_phase',
    'validator_issue_count',
    'validator_p0_count',
    'warnings',
    'error_code',
    'error_summary',
    'active_subtitle_version_id',
})
COUNTER_FIELDS = frozenset({
    'asr_total_chunks',
    'asr_completed_chunks',
    'ai_total_windows',
    'ai_completed_windows',
    'ai_failed_windows',
    'ai_degraded_windows',
    'validator_issue_count',
    'validator_p0_count',
})


class SubtitlePipelineLeaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class PipelineRunLease:
    run_id: str
    owner: str
    lock_version: int
    expires_at: datetime

    @property
    def fencing_token(self) -> str:
        return f'{self.run_id}:{self.lock_version}:{self.owner}'


def build_job_finalize_lease_owner(job_id: str, *, process_id: Optional[int] = None) -> str:
    normalized_job_id = str(job_id or '').strip()
    if not normalized_job_id:
        raise ValueError('job id is required')
    pid = os.getpid() if process_id is None else int(process_id)
    if pid <= 0:
        raise ValueError('process id must be positive')
    return f'job-finalize:{normalized_job_id}:pid:{pid}'


def claim_pipeline_run_lease(
    session: Session,
    run_id: str,
    *,
    owner: str,
    now: datetime,
    ttl_seconds: int,
) -> Optional[PipelineRunLease]:
    normalized_owner = str(owner or '').strip()
    if not normalized_owner:
        raise ValueError('lease owner is required')
    if int(ttl_seconds) <= 0:
        raise ValueError('ttl_seconds must be positive')
    current_time = _utc_naive(now)
    snapshot = session.get(SubtitlePipelineRun, str(run_id))
    if snapshot is None:
        raise LookupError('subtitle pipeline run not found')
    if snapshot.stage in TERMINAL_RUN_STAGES:
        return None

    expected_version = int(snapshot.lock_version or 0)
    expires_at = current_time + timedelta(seconds=int(ttl_seconds))
    result = session.execute(
        update(SubtitlePipelineRun)
        .where(
            SubtitlePipelineRun.id == str(run_id),
            SubtitlePipelineRun.lock_version == expected_version,
            SubtitlePipelineRun.stage.not_in(TERMINAL_RUN_STAGES),
            or_(
                SubtitlePipelineRun.lease_expires_at.is_(None),
                SubtitlePipelineRun.lease_expires_at <= current_time,
            ),
        )
        .values(
            lease_owner=normalized_owner,
            lease_expires_at=expires_at,
            lock_version=SubtitlePipelineRun.lock_version + 1,
            updated_at=current_time,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    return PipelineRunLease(
        run_id=str(run_id),
        owner=normalized_owner,
        lock_version=expected_version + 1,
        expires_at=expires_at,
    )


def renew_pipeline_run_lease(
    session: Session,
    lease: PipelineRunLease,
    *,
    now: datetime,
    ttl_seconds: int,
) -> PipelineRunLease:
    if int(ttl_seconds) <= 0:
        raise ValueError('ttl_seconds must be positive')
    current_time = _utc_naive(now)
    expires_at = current_time + timedelta(seconds=int(ttl_seconds))
    result = session.execute(
        update(SubtitlePipelineRun)
        .where(
            SubtitlePipelineRun.id == lease.run_id,
            SubtitlePipelineRun.lease_owner == lease.owner,
            SubtitlePipelineRun.lock_version == lease.lock_version,
            SubtitlePipelineRun.lease_expires_at > current_time,
        )
        .values(
            lease_expires_at=expires_at,
            lock_version=SubtitlePipelineRun.lock_version + 1,
            updated_at=current_time,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        raise SubtitlePipelineLeaseError('pipeline run lease is stale or expired')
    session.commit()
    return PipelineRunLease(
        run_id=lease.run_id,
        owner=lease.owner,
        lock_version=lease.lock_version + 1,
        expires_at=expires_at,
    )


def release_pipeline_run_lease(
    session: Session,
    lease: PipelineRunLease,
    *,
    now: Optional[datetime] = None,
) -> None:
    current_time = _utc_naive(now or datetime.now(timezone.utc))
    result = session.execute(
        update(SubtitlePipelineRun)
        .where(
            SubtitlePipelineRun.id == lease.run_id,
            SubtitlePipelineRun.lease_owner == lease.owner,
            SubtitlePipelineRun.lock_version == lease.lock_version,
        )
        .values(
            lease_owner=None,
            lease_expires_at=None,
            lock_version=SubtitlePipelineRun.lock_version + 1,
            updated_at=current_time,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        raise SubtitlePipelineLeaseError('pipeline run lease is stale')
    session.commit()


def clear_pipeline_run_lease_for_owner(
    session: Session,
    run_id: str,
    *,
    owner: str,
    now: Optional[datetime] = None,
) -> bool:
    """Fence and clear an observed lease after its worker is known to be gone."""
    normalized_owner = str(owner or '').strip()
    if not normalized_owner:
        raise ValueError('lease owner is required')
    snapshot = session.get(SubtitlePipelineRun, str(run_id))
    if (
        snapshot is None
        or snapshot.stage in TERMINAL_RUN_STAGES
        or snapshot.lease_owner != normalized_owner
    ):
        return False
    expected_version = int(snapshot.lock_version or 0)
    current_time = _utc_naive(now or datetime.now(timezone.utc))
    result = session.execute(
        update(SubtitlePipelineRun)
        .where(
            SubtitlePipelineRun.id == str(run_id),
            SubtitlePipelineRun.stage.not_in(TERMINAL_RUN_STAGES),
            SubtitlePipelineRun.lease_owner == normalized_owner,
            SubtitlePipelineRun.lock_version == expected_version,
        )
        .values(
            lease_owner=None,
            lease_expires_at=None,
            lock_version=SubtitlePipelineRun.lock_version + 1,
            updated_at=current_time,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        return False
    session.commit()
    return True


def assert_pipeline_run_lease(
    session: Session,
    lease: PipelineRunLease,
    *,
    now: Optional[datetime] = None,
    project_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> SubtitlePipelineRun:
    current_time = _utc_naive(now or datetime.now(timezone.utc))
    session.expire_all()
    run = session.get(SubtitlePipelineRun, lease.run_id)
    if (
        run is None
        or run.stage in TERMINAL_RUN_STAGES
        or run.lease_owner != lease.owner
        or int(run.lock_version or 0) != lease.lock_version
        or run.lease_expires_at is None
        or run.lease_expires_at <= current_time
        or (project_id is not None and run.project_id != str(project_id))
        or (job_id is not None and run.job_id != str(job_id))
    ):
        raise SubtitlePipelineLeaseError('pipeline run lease is stale or expired')
    return run


def transition_pipeline_run_with_lease(
    session: Session,
    lease: PipelineRunLease,
    next_stage: str,
    *,
    now: Optional[datetime] = None,
    error_code: Optional[str] = None,
    error_summary: Optional[str] = None,
) -> SubtitlePipelineRun:
    current_time = _utc_naive(now or datetime.now(timezone.utc))
    run = assert_pipeline_run_lease(session, lease, now=current_time)
    normalized_stage = str(next_stage or '').strip()
    if normalized_stage not in RUN_STAGE_TRANSITIONS.get(run.stage, frozenset()):
        raise RuntimeError(f'不允许的状态转换: {run.stage} -> {normalized_stage}')

    values: dict[str, Any] = {
        'stage': normalized_stage,
        'status': RUN_STAGE_STATUSES[normalized_stage],
        'stage_started_at': current_time,
        'updated_at': current_time,
        'error_code': str(error_code).strip() if error_code else None,
        'error_summary': str(error_summary).strip()[:512] if error_summary else None,
    }
    closing_field = _stage_completed_field(run.stage)
    if closing_field and getattr(run, closing_field) is None:
        values[closing_field] = current_time
    opening_field = _stage_started_field(normalized_stage)
    if opening_field and getattr(run, opening_field) is None:
        values[opening_field] = current_time
    if normalized_stage in TERMINAL_RUN_STAGES:
        values.update({
            'completed_at': current_time,
            'lease_owner': None,
            'lease_expires_at': None,
            'lock_version': SubtitlePipelineRun.lock_version + 1,
        })

    result = session.execute(
        update(SubtitlePipelineRun)
        .where(
            SubtitlePipelineRun.id == lease.run_id,
            SubtitlePipelineRun.stage == run.stage,
            SubtitlePipelineRun.lease_owner == lease.owner,
            SubtitlePipelineRun.lock_version == lease.lock_version,
            SubtitlePipelineRun.lease_expires_at > current_time,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        raise SubtitlePipelineLeaseError('pipeline run lease is stale or expired')
    session.commit()
    session.expire_all()
    advanced = session.get(SubtitlePipelineRun, lease.run_id)
    if advanced is None:
        raise LookupError('subtitle pipeline run not found')
    return advanced


def create_pipeline_run_before_asr(
    session: Session,
    *,
    job: Job,
    submission_idempotency_key: str,
    pipeline_version: str = 'speaker-v2',
) -> SubtitlePipelineRun:
    project_id = str(job.project_id or '').strip()
    key = str(submission_idempotency_key or '').strip()
    if not project_id:
        raise ValueError('V2 subtitle run requires a project job')
    if not key:
        raise ValueError('submission_idempotency_key is required')

    existing = session.scalar(
        select(SubtitlePipelineRun).where(
            SubtitlePipelineRun.submission_idempotency_key == key,
        )
    )
    if existing is not None:
        if existing.job_id != job.id:
            raise RuntimeError('submission idempotency key belongs to another job')
        return existing

    last_sequence = session.scalar(
        select(func.max(SubtitlePipelineRun.run_sequence)).where(
            SubtitlePipelineRun.job_id == job.id,
        )
    )
    run = SubtitlePipelineRun(
        project_id=project_id,
        job_id=job.id,
        run_sequence=int(last_sequence or 0) + 1,
        submission_idempotency_key=key,
        pipeline_version=str(pipeline_version or 'speaker-v2').strip() or 'speaker-v2',
        stage='queued',
        status='queued',
        artifacts={'schema_version': 1},
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def get_pipeline_run(session: Session, run_id: str) -> Optional[SubtitlePipelineRun]:
    return session.get(SubtitlePipelineRun, str(run_id))


def get_latest_pipeline_run(session: Session, job_id: str) -> Optional[SubtitlePipelineRun]:
    return session.scalar(
        select(SubtitlePipelineRun)
        .where(SubtitlePipelineRun.job_id == str(job_id))
        .order_by(SubtitlePipelineRun.run_sequence.desc())
        .limit(1)
    )


def bind_raw_snapshot_hash(
    session: Session,
    *,
    run_id: str,
    raw_snapshot_hash: str,
    expected_lock_version: int,
) -> SubtitlePipelineRun:
    snapshot_hash = str(raw_snapshot_hash or '').strip()
    if not snapshot_hash:
        raise ValueError('raw_snapshot_hash is required')
    now = datetime.utcnow()
    result = session.execute(
        update(SubtitlePipelineRun)
        .where(
            SubtitlePipelineRun.id == str(run_id),
            SubtitlePipelineRun.raw_snapshot_hash.is_(None),
            SubtitlePipelineRun.lock_version == int(expected_lock_version),
        )
        .values(
            raw_snapshot_hash=snapshot_hash,
            lock_version=SubtitlePipelineRun.lock_version + 1,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        raise RuntimeError('raw snapshot CAS binding failed')
    session.commit()
    run = get_pipeline_run(session, run_id)
    if run is None:
        raise LookupError('subtitle pipeline run not found')
    session.refresh(run)
    return run


def transition_pipeline_run(
    session: Session,
    run_id: str,
    next_stage: str,
    *,
    error_code: Optional[str] = None,
    error_summary: Optional[str] = None,
) -> SubtitlePipelineRun:
    run = get_pipeline_run(session, run_id)
    if run is None:
        raise LookupError('subtitle pipeline run not found')
    normalized_stage = str(next_stage or '').strip()
    if normalized_stage not in RUN_STAGE_TRANSITIONS.get(run.stage, frozenset()):
        raise RuntimeError(f'不允许的状态转换: {run.stage} -> {normalized_stage}')

    now = datetime.utcnow()
    _close_stage_timer(run, now)
    run.stage = normalized_stage
    run.status = RUN_STAGE_STATUSES[normalized_stage]
    run.stage_started_at = now
    run.updated_at = now
    run.error_code = str(error_code).strip() if error_code else None
    run.error_summary = str(error_summary).strip()[:512] if error_summary else None
    _open_stage_timer(run, normalized_stage, now)
    if normalized_stage in TERMINAL_RUN_STAGES:
        run.completed_at = now
    session.commit()
    session.refresh(run)
    return run


def update_pipeline_run_metrics(
    session: Session,
    run_id: str,
    **metrics: Any,
) -> SubtitlePipelineRun:
    run = get_pipeline_run(session, run_id)
    if run is None:
        raise LookupError('subtitle pipeline run not found')
    normalized_metrics = _normalize_metrics(run, metrics)
    for name, value in normalized_metrics.items():
        setattr(run, name, value)
    if 'warnings' in normalized_metrics:
        run.warning_count = len(normalized_metrics['warnings'])
    run.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(run)
    return run


def set_pipeline_run_artifacts(
    session: Session,
    run_id: str,
    *,
    artifacts: dict[str, Any],
    artifact_hash: Optional[str] = None,
    artifact_refs: Optional[dict[str, Any]] = None,
) -> SubtitlePipelineRun:
    if not isinstance(artifacts, dict) or artifacts.get('schema_version') != 1:
        raise ValueError('pipeline run artifacts require schema_version=1')
    if artifact_refs is not None and not isinstance(artifact_refs, dict):
        raise ValueError('artifact_refs must be an object')
    run = get_pipeline_run(session, run_id)
    if run is None:
        raise LookupError('subtitle pipeline run not found')
    run.artifacts_schema_version = 1
    run.artifacts = artifacts
    run.artifact_hash = str(artifact_hash).strip() if artifact_hash else None
    run.artifact_refs = artifact_refs
    run.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(run)
    return run


def checkpoint_pipeline_run_with_lease(
    session: Session,
    lease: PipelineRunLease,
    *,
    artifacts: dict[str, Any],
    artifact_hash: Optional[str] = None,
    artifact_refs: Optional[dict[str, Any]] = None,
    metrics: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> SubtitlePipelineRun:
    if not isinstance(artifacts, dict) or artifacts.get('schema_version') != 1:
        raise ValueError('pipeline run artifacts require schema_version=1')
    if artifact_refs is not None and not isinstance(artifact_refs, dict):
        raise ValueError('artifact_refs must be an object')
    current_time = _utc_naive(now or datetime.now(timezone.utc))
    run = assert_pipeline_run_lease(session, lease, now=current_time)
    normalized_metrics = _normalize_metrics(run, dict(metrics or {}))

    values: dict[str, Any] = {
        'artifacts_schema_version': 1,
        'artifacts': artifacts,
        'artifact_hash': str(artifact_hash).strip() if artifact_hash else None,
        'artifact_refs': artifact_refs,
        'updated_at': current_time,
        **normalized_metrics,
    }
    if 'warnings' in normalized_metrics:
        values['warning_count'] = len(normalized_metrics['warnings'])
    result = session.execute(
        update(SubtitlePipelineRun)
        .where(
            SubtitlePipelineRun.id == lease.run_id,
            SubtitlePipelineRun.lease_owner == lease.owner,
            SubtitlePipelineRun.lock_version == lease.lock_version,
            SubtitlePipelineRun.lease_expires_at > current_time,
            SubtitlePipelineRun.stage.not_in(TERMINAL_RUN_STAGES),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        raise SubtitlePipelineLeaseError('pipeline run lease is stale or expired')
    session.commit()
    session.expire_all()
    saved = session.get(SubtitlePipelineRun, lease.run_id)
    if saved is None:
        raise LookupError('subtitle pipeline run not found')
    return saved


def cancel_active_pipeline_runs(
    session: Session,
    job_id: str,
    *,
    commit: bool = True,
) -> int:
    runs = list(session.scalars(
        select(SubtitlePipelineRun).where(
            SubtitlePipelineRun.job_id == str(job_id),
            SubtitlePipelineRun.stage.not_in(TERMINAL_RUN_STAGES),
        )
    ))
    now = datetime.utcnow()
    for run in runs:
        _close_stage_timer(run, now)
        run.stage = 'canceled'
        run.status = 'canceled'
        run.stage_started_at = now
        run.completed_at = now
        run.updated_at = now
        run.lease_owner = None
        run.lease_expires_at = None
    if commit:
        session.commit()
    return len(runs)


def build_pipeline_progress(run: SubtitlePipelineRun, *, now: Optional[datetime] = None) -> dict[str, Any]:
    current_time = now or datetime.utcnow()
    elapsed_seconds = max(0.0, (current_time - run.started_at).total_seconds())
    asr_running_seconds = 0.0
    if run.asr_started_at is not None:
        asr_end = run.asr_completed_at or current_time
        asr_running_seconds = max(0.0, (asr_end - run.asr_started_at).total_seconds())
    terminal = run.stage in TERMINAL_RUN_STAGES
    return {
        'run_id': run.id,
        'project_id': run.project_id,
        'job_id': run.job_id,
        'stage': run.stage,
        'status': run.status,
        'asr': {
            'completed_chunks': run.asr_completed_chunks,
            'total_chunks': run.asr_total_chunks,
            'provider_status': run.asr_provider_status,
            'running_seconds': round(asr_running_seconds, 3),
        },
        'ai': {
            'completed_windows': run.ai_completed_windows,
            'total_windows': run.ai_total_windows,
            'failed_windows': run.ai_failed_windows,
            'degraded_windows': run.ai_degraded_windows,
        },
        'validation': {
            'phase': run.validator_phase,
            'issue_count': run.validator_issue_count,
            'p0_count': run.validator_p0_count,
        },
        'warnings': list(run.warnings or []),
        'started_at': run.started_at,
        'updated_at': run.updated_at,
        'elapsed_seconds': round(elapsed_seconds, 3),
        'can_retry_failed_windows': bool(run.ai_failed_windows and not terminal),
        'terminal': terminal,
    }


def _validate_metric_totals(run: SubtitlePipelineRun, metrics: dict[str, Any]) -> None:
    asr_total = metrics.get('asr_total_chunks', run.asr_total_chunks)
    asr_completed = metrics.get('asr_completed_chunks', run.asr_completed_chunks)
    ai_total = metrics.get('ai_total_windows', run.ai_total_windows)
    ai_completed = metrics.get('ai_completed_windows', run.ai_completed_windows)
    ai_failed = metrics.get('ai_failed_windows', run.ai_failed_windows)
    ai_degraded = metrics.get('ai_degraded_windows', run.ai_degraded_windows)
    if asr_completed > asr_total:
        raise ValueError('asr completed chunks exceed total chunks')
    if ai_completed + ai_failed > ai_total:
        raise ValueError('AI accounted windows exceed total windows')
    if ai_degraded > ai_completed:
        raise ValueError('AI degraded windows exceed completed windows')


def _normalize_metrics(
    run: SubtitlePipelineRun,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    unknown = sorted(set(metrics) - METRIC_FIELDS)
    if unknown:
        raise ValueError(f'unsupported pipeline run metrics: {", ".join(unknown)}')
    normalized = {}
    for name, value in metrics.items():
        if name in COUNTER_FIELDS:
            value = int(value)
            if value < 0:
                raise ValueError(f'{name} must be nonnegative')
        if name == 'warnings':
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError('warnings must be a string list')
        normalized[name] = value
    _validate_metric_totals(run, normalized)
    return normalized


def _open_stage_timer(run: SubtitlePipelineRun, stage: str, now: datetime) -> None:
    field = _stage_started_field(stage)
    if field and getattr(run, field) is None:
        setattr(run, field, now)


def _close_stage_timer(run: SubtitlePipelineRun, now: datetime) -> None:
    field = _stage_completed_field(run.stage)
    if field and getattr(run, field) is None:
        setattr(run, field, now)


def _stage_started_field(stage: str) -> Optional[str]:
    return {
        'asr_running': 'asr_started_at',
        'evidence_matching': 'evidence_started_at',
        'ai_planning': 'ai_started_at',
        'cue_validating': 'validation_started_at',
    }.get(stage)


def _stage_completed_field(stage: str) -> Optional[str]:
    return {
        'asr_running': 'asr_completed_at',
        'evidence_matching': 'evidence_completed_at',
        'ai_planning': 'ai_completed_at',
        'cue_validating': 'validation_completed_at',
    }.get(stage)


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
