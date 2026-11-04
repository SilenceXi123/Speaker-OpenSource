from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import threading
from time import monotonic
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.job import Job
from app.services.jobs import ACTIVE_JOB_STATUSES, abort_job
from app.services.storage_cleanup import (
    StorageCleanupReport,
    is_storage_managed_job,
    run_storage_cleanup,
)
from app.services.voice_clone_cleanup import (
    VoiceCloneDeletionTaskDrainResult,
    drain_voice_clone_deletion_tasks,
)
from app.services.voice_retention_cleanup import VoiceRetentionCleanupReport, expire_voice_data


logger = logging.getLogger(__name__)
_maintenance_lock = threading.Lock()
_maintenance_stop_event = threading.Event()
_maintenance_thread: Optional[threading.Thread] = None


@dataclass(frozen=True)
class StorageMaintenanceReport:
    aborted_job_ids: list[str]
    cleanup: StorageCleanupReport
    voice_clone_deletion_tasks: VoiceCloneDeletionTaskDrainResult = (
        VoiceCloneDeletionTaskDrainResult()
    )
    voice_retention: VoiceRetentionCleanupReport = field(default_factory=VoiceRetentionCleanupReport)


def abort_stale_managed_jobs(
    session: Session,
    *,
    now: Optional[datetime] = None,
) -> list[str]:
    reference_now = _normalize_datetime(now or datetime.utcnow())
    cutoff = reference_now - timedelta(
        hours=max(1, int(settings.storage_cleanup_stale_active_hours))
    )
    candidates = list(session.scalars(
        select(Job)
        .where(
            Job.status.in_(ACTIVE_JOB_STATUSES),
            Job.updated_at <= cutoff,
        )
        .order_by(Job.updated_at, Job.id)
    ))
    aborted_job_ids: list[str] = []
    for job in candidates:
        if not is_storage_managed_job(job):
            continue
        if job.render_status in {'queued', 'running'}:
            continue
        abort_job(
            session,
            job.id,
            message=(
                f'任务超过 {max(1, int(settings.storage_cleanup_stale_active_hours))} 小时'
                '没有进度，已由存储保护自动中止'
            ),
        )
        aborted_job_ids.append(job.id)
    return aborted_job_ids


def run_storage_maintenance_once(
    session: Session,
    *,
    now: Optional[datetime] = None,
    include_storage_cleanup: bool = True,
) -> StorageMaintenanceReport:
    reference_now = _normalize_datetime(now or datetime.utcnow())
    voice_retention = expire_voice_data(session, now=reference_now)
    aborted_job_ids = []
    cleanup = StorageCleanupReport(dry_run=False)
    if include_storage_cleanup and settings.storage_cleanup_enabled:
        aborted_job_ids = abort_stale_managed_jobs(session, now=reference_now)
        cleanup = run_storage_cleanup(
            session,
            now=reference_now,
            managed_jobs_only=True,
        )
    voice_clone_deletion_tasks = drain_voice_clone_deletion_tasks(
        session,
        limit=20,
        now=reference_now,
    )
    return StorageMaintenanceReport(
        aborted_job_ids=aborted_job_ids,
        cleanup=cleanup,
        voice_clone_deletion_tasks=voice_clone_deletion_tasks,
        voice_retention=voice_retention,
    )


def start_storage_maintenance_worker(
    *,
    enabled: Optional[bool] = None,
    session_factory: sessionmaker = SessionLocal,
) -> bool:
    should_start = True if enabled is None else bool(enabled)
    if not should_start:
        return False
    with _maintenance_lock:
        global _maintenance_thread
        if _maintenance_thread and _maintenance_thread.is_alive():
            return True
        _maintenance_stop_event.clear()
        _maintenance_thread = threading.Thread(
            target=_run_storage_maintenance_worker,
            args=(session_factory,),
            name='storage-maintenance',
            daemon=True,
        )
        _maintenance_thread.start()
    return True


def stop_storage_maintenance_worker(timeout_seconds: float = 5.0) -> None:
    with _maintenance_lock:
        thread = _maintenance_thread
        _maintenance_stop_event.set()
    if thread and thread.is_alive():
        thread.join(timeout=timeout_seconds)


def _run_storage_maintenance_worker(session_factory: sessionmaker) -> None:
    interval_seconds = max(60, int(settings.storage_cleanup_interval_seconds))
    next_storage_at = 0.0
    while not _maintenance_stop_event.is_set():
        try:
            tick_started_at = monotonic()
            storage_due = tick_started_at >= next_storage_at
            with session_factory() as session:
                report = run_storage_maintenance_once(session, include_storage_cleanup=storage_due)
            if storage_due:
                next_storage_at = tick_started_at + interval_seconds
            if (
                report.aborted_job_ids
                or report.cleanup.deleted_count
                or report.voice_clone_deletion_tasks.attempted_count
                or report.voice_retention.deleted_count
                or report.voice_retention.detached_record_count
            ):
                logger.info(
                    '存储维护完成: aborted_jobs=%s deleted_files=%s '
                    'deleted_bytes=%s clone_deletions=%s clone_failures=%s expired_voice_rows=%s',
                    len(report.aborted_job_ids),
                    report.cleanup.deleted_count,
                    report.cleanup.deleted_bytes,
                    report.voice_clone_deletion_tasks.completed_count,
                    report.voice_clone_deletion_tasks.failed_count,
                    report.voice_retention.deleted_count,
                )
        except Exception as exc:  # pragma: no cover - maintenance must not stop the API
            logger.exception('存储维护失败: %s', exc)
        _maintenance_stop_event.wait(60)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value
