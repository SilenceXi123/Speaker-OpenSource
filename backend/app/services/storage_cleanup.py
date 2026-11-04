from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import select, union_all
from sqlalchemy.orm import Session, load_only

from app.core.config import settings
from app.models.job import Job


FINISHED_SOURCE_RETENTION_STATUSES = {'completed', 'completed_with_warnings'}
FAILED_SOURCE_RETENTION_STATUSES = {'failed', 'aborted'}


@dataclass
class StorageCleanupItem:
    path: str
    bytes: int
    reason: str
    job_id: Optional[str] = None
    field: Optional[str] = None


@dataclass
class StorageCleanupReport:
    dry_run: bool
    total_bytes: int = 0
    deleted_bytes: int = 0
    item_count: int = 0
    deleted_count: int = 0
    items: list[StorageCleanupItem] = field(default_factory=list)


def build_storage_cleanup_plan(
    session: Session,
    now: Optional[datetime] = None,
    *,
    include_job_media: bool = True,
    managed_jobs_only: bool = False,
) -> StorageCleanupReport:
    reference_now = _normalize_now(now)
    items: list[StorageCleanupItem] = []
    items.extend(_collect_expired_tmp_files(session, reference_now))
    if include_job_media:
        items.extend(
            _collect_expired_job_media(
                session,
                reference_now,
                managed_jobs_only=managed_jobs_only,
            )
        )
    total_bytes = sum(item.bytes for item in items)
    return StorageCleanupReport(
        dry_run=True,
        total_bytes=total_bytes,
        item_count=len(items),
        items=items,
    )


def run_storage_cleanup(
    session: Session,
    now: Optional[datetime] = None,
    *,
    include_job_media: bool = True,
    managed_jobs_only: bool = False,
) -> StorageCleanupReport:
    plan = build_storage_cleanup_plan(
        session,
        now=now,
        include_job_media=include_job_media,
        managed_jobs_only=managed_jobs_only,
    )
    deleted_bytes = 0
    deleted_count = 0
    cleared_job_fields: dict[tuple[str, str], None] = {}
    for item in plan.items:
        if item.job_id and item.field:
            cleared_job_fields[(item.job_id, item.field)] = None
        target = Path(item.path)
        if not target.exists() or not target.is_file():
            continue
        size = target.stat().st_size
        target.unlink()
        deleted_bytes += size
        deleted_count += 1

    for job_id, field_name in cleared_job_fields:
        job = session.get(Job, job_id, options=[load_only(Job.id, Job.source_local_path, Job.video_source_local_path)])
        if job:
            setattr(job, field_name, None)
    if cleared_job_fields:
        session.commit()

    return StorageCleanupReport(
        dry_run=False,
        total_bytes=plan.total_bytes,
        deleted_bytes=deleted_bytes,
        item_count=plan.item_count,
        deleted_count=deleted_count,
        items=plan.items,
    )


def _collect_expired_tmp_files(session: Session, now: datetime) -> list[StorageCleanupItem]:
    from app.models.voice_clone_deletion_task import VoiceCloneDeletionTask
    from app.models.voice_clone_profile import VoiceCloneProfile
    from app.models.voice_clone_sample import VoiceCloneSample
    from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
    from app.models.voiceprint_sample import VoiceprintSample
    from app.services.voice_clone_cleanup import _local_path_identity

    # Voice files follow record creation time, not the general temporary-file
    # mtime policy. Queued files must also pass the queue's ownership guards.
    columns = (
        Job.source_local_path, Job.source_export_path,
        Job.video_source_local_path, Job.video_source_export_path,
        VoiceprintSample.audio_local_path, VoiceprintSample.clone_audio_local_path,
        VoiceCloneSample.audio_local_path, VoiceCloneProfile.source_audio_local_path,
        VoiceCloneProfile.preview_audio_local_path,
        VoiceCloneSynthesisRecord.output_audio_local_path,
        VoiceCloneDeletionTask.local_path,
    )
    protected_paths = {
        _local_path_identity(value)
        for value in session.scalars(union_all(*(select(column) for column in columns)))
        if value
    }
    cutoff = now - timedelta(days=max(0, settings.storage_cleanup_tmp_days))
    roots = [settings.upload_dir_path, settings.audio_dir_path, settings.render_dir_path]
    items: list[StorageCleanupItem] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob('*'):
            if not path.is_file():
                continue
            if _local_path_identity(path) in protected_paths:
                continue
            modified_at = datetime.fromtimestamp(path.stat().st_mtime)
            if modified_at > cutoff:
                continue
            items.append(StorageCleanupItem(path=str(path), bytes=path.stat().st_size, reason='expired_tmp'))
    return items


def _collect_expired_job_media(
    session: Session,
    now: datetime,
    *,
    managed_jobs_only: bool = False,
) -> list[StorageCleanupItem]:
    media_root = settings.media_library_dir_path
    items: list[StorageCleanupItem] = []
    jobs = session.scalars(
        select(Job)
        .options(load_only(Job.id, Job.status, Job.created_at, Job.processing_metrics,
                           Job.source_local_path, Job.video_source_local_path))
        .where(Job.status.in_(FINISHED_SOURCE_RETENTION_STATUSES | FAILED_SOURCE_RETENTION_STATUSES))
        .execution_options(yield_per=50)
    )
    for job in jobs:
        if managed_jobs_only and not is_storage_managed_job(job):
            continue
        if not _job_is_past_retention(job, now):
            continue
        for field_name in ('source_local_path', 'video_source_local_path'):
            path_value = str(getattr(job, field_name) or '').strip()
            if not path_value:
                continue
            path = _safe_file_in_root(Path(path_value), media_root)
            if not path:
                continue
            items.append(
                StorageCleanupItem(
                    path=str(path),
                    bytes=path.stat().st_size,
                    reason='expired_job_media',
                    job_id=job.id,
                    field=field_name,
                )
            )
    return items


def is_storage_managed_job(job: Job) -> bool:
    metrics = job.processing_metrics if isinstance(job.processing_metrics, dict) else {}
    return int(metrics.get('storage_policy_version') or 0) >= 2


def _job_is_past_retention(job: Job, now: datetime) -> bool:
    created_at = _normalize_datetime(job.created_at)
    if not created_at:
        return False
    if job.status in FINISHED_SOURCE_RETENTION_STATUSES:
        days = max(0, settings.storage_cleanup_completed_source_days)
    elif job.status in FAILED_SOURCE_RETENTION_STATUSES:
        days = max(0, settings.storage_cleanup_failed_source_days)
    else:
        return False
    return created_at <= now - timedelta(days=days)


def _safe_file_in_root(path: Path, root: Path) -> Optional[Path]:
    if not path.exists() or not path.is_file():
        return None
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved_path


def _normalize_now(now: Optional[datetime]) -> datetime:
    return _normalize_datetime(now) or datetime.utcnow()


def _normalize_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value
