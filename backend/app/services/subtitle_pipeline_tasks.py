from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading
from typing import Any, Callable, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.subtitle_pipeline_run import SubtitlePipelineRun
from app.services.subtitle_pipeline_runs import (
    TERMINAL_RUN_STAGES,
    PipelineRunLease,
    claim_pipeline_run_lease,
)


logger = logging.getLogger(__name__)
_recovery_lock = threading.Lock()
_recovery_thread: Optional[threading.Thread] = None


def claim_and_execute_subtitle_v2_run(
    session: Session,
    run_id: str,
    *,
    owner: str,
    execute: Callable[[Session, PipelineRunLease], Any],
    now: Optional[datetime] = None,
    ttl_seconds: int = 300,
) -> Any:
    lease = claim_pipeline_run_lease(
        session,
        str(run_id),
        owner=owner,
        now=now or datetime.now(timezone.utc),
        ttl_seconds=ttl_seconds,
    )
    if lease is None:
        return None
    return execute(session, lease)


def recover_nonterminal_runs(
    session: Session,
    *,
    enqueue: Callable[[str], None],
    now: Optional[datetime] = None,
) -> list[str]:
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is not None:
        current_time = current_time.astimezone(timezone.utc).replace(tzinfo=None)
    run_ids = list(session.scalars(
        select(SubtitlePipelineRun.id)
        .where(
            SubtitlePipelineRun.pipeline_version == 'speaker-v2',
            SubtitlePipelineRun.stage.not_in(TERMINAL_RUN_STAGES),
            or_(
                SubtitlePipelineRun.lease_expires_at.is_(None),
                SubtitlePipelineRun.lease_expires_at <= current_time,
            ),
        )
        .order_by(SubtitlePipelineRun.created_at, SubtitlePipelineRun.id)
    ))
    for run_id in run_ids:
        enqueue(str(run_id))
    return [str(run_id) for run_id in run_ids]


def start_subtitle_pipeline_recovery_worker(
    *,
    enabled: Optional[bool] = None,
    session_factory: sessionmaker = SessionLocal,
    enqueue: Optional[Callable[[str], None]] = None,
) -> bool:
    should_start = settings.speaker_v2_shadow_enabled if enabled is None else bool(enabled)
    if not should_start:
        return False
    with _recovery_lock:
        global _recovery_thread
        if _recovery_thread and _recovery_thread.is_alive():
            return True
        _recovery_thread = threading.Thread(
            target=_run_recovery_once,
            args=(session_factory, enqueue or _log_recovered_run),
            name='subtitle-v2-recovery',
            daemon=True,
        )
        _recovery_thread.start()
    return True


def stop_subtitle_pipeline_recovery_worker(timeout_seconds: float = 5.0) -> None:
    with _recovery_lock:
        thread = _recovery_thread
    if thread and thread.is_alive():
        thread.join(timeout=timeout_seconds)


def _run_recovery_once(session_factory: sessionmaker, enqueue: Callable[[str], None]) -> None:
    try:
        with session_factory() as session:
            recover_nonterminal_runs(session, enqueue=enqueue)
    except Exception as exc:  # pragma: no cover - startup tracing must not stop the API
        logger.warning('V2 subtitle recovery scan skipped (%s)', type(exc).__name__)


def _log_recovered_run(run_id: str) -> None:
    logger.info('V2 subtitle run is eligible for recovery: %s', run_id)
