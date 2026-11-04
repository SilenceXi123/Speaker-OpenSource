from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.job_event import JobEvent


MAX_EVENT_MESSAGE_LENGTH = 1000
logger = logging.getLogger(__name__)


def append_job_event(
    session: Session,
    *,
    job: Job,
    event_type: str,
    stage: Optional[str] = None,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    message: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> Optional[JobEvent]:
    event = JobEvent(
        job_id=str(job.id),
        job_kind=str(getattr(job, 'kind', '') or '') or None,
        program_key=str(getattr(job, 'program_key', '') or '') or None,
        program_name=str(getattr(job, 'program_name', '') or '') or None,
        event_type=_clean_event_field(event_type, fallback='unknown'),
        stage=_clean_event_field(stage),
        status=_clean_event_field(status or getattr(job, 'status', None)),
        progress=_normalize_progress(progress),
        message=_truncate_message(message),
        details=_normalize_details(details),
    )
    event_session: Optional[Session] = None
    try:
        bind = session.get_bind()
        isolated_bind = getattr(bind, 'engine', bind)
        event_session = Session(bind=isolated_bind)
        event_session.add(event)
        event_session.commit()
        event_session.refresh(event)
        event_session.expunge(event)
        return event
    except Exception as error:
        if event_session is not None:
            with suppress(Exception):
                event_session.rollback()
        logger.warning(
            'job event write failed job_id=%s event_type=%s error=%s',
            event.job_id,
            event.event_type,
            type(error).__name__,
        )
        return None
    finally:
        if event_session is not None:
            with suppress(Exception):
                event_session.close()


def list_job_events(session: Session, job_id: str, *, limit: int = 200) -> list[JobEvent]:
    safe_limit = max(1, min(int(limit or 200), 1000))
    latest_events = list(
        session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == str(job_id))
            .order_by(JobEvent.created_at.desc(), JobEvent.id.desc())
            .limit(safe_limit)
        )
    )
    return sorted(latest_events, key=lambda event: (event.created_at, event.id))


def count_job_events(session: Session, job_id: str) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(JobEvent).where(JobEvent.job_id == str(job_id))
        )
        or 0
    )


def _clean_event_field(value: Optional[str], *, fallback: Optional[str] = None) -> Optional[str]:
    cleaned = str(value or '').strip()
    return cleaned or fallback


def _normalize_progress(progress: Optional[int]) -> Optional[int]:
    if progress is None:
        return None
    try:
        value = int(progress)
    except (TypeError, ValueError):
        return None
    return max(0, min(value, 100))


def _truncate_message(message: Optional[str]) -> Optional[str]:
    cleaned = str(message or '').strip()
    if not cleaned:
        return None
    return cleaned[:MAX_EVENT_MESSAGE_LENGTH]


def _normalize_details(details: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not isinstance(details, dict):
        return None
    normalized: dict[str, Any] = {}
    for key, value in details.items():
        if value is None:
            continue
        normalized[str(key)] = value
    return normalized or None
