from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SubtitlePipelineRun(Base):
    __tablename__ = 'subtitle_pipeline_runs'
    __table_args__ = (
        UniqueConstraint('job_id', 'run_sequence', name='uq_subtitle_pipeline_runs_job_sequence'),
        UniqueConstraint(
            'submission_idempotency_key',
            name='uq_subtitle_pipeline_runs_submission_key',
        ),
        CheckConstraint(
            "stage IN ('queued', 'asr_running', 'evidence_matching', 'ai_planning', "
            "'cue_validating', 'ready', 'ready_with_warnings', 'failed', 'canceled')",
            name='ck_subtitle_pipeline_runs_stage',
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'ready', 'ready_with_warnings', 'failed', 'canceled')",
            name='ck_subtitle_pipeline_runs_status',
        ),
        CheckConstraint(
            'asr_total_chunks >= 0 AND asr_completed_chunks >= 0 '
            'AND ai_total_windows >= 0 AND ai_completed_windows >= 0 '
            'AND ai_failed_windows >= 0 AND ai_degraded_windows >= 0 '
            'AND validator_issue_count >= 0 AND validator_p0_count >= 0 '
            'AND warning_count >= 0 AND lock_version >= 0',
            name='ck_subtitle_pipeline_runs_nonnegative_counters',
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    run_sequence: Mapped[int] = mapped_column(Integer)
    submission_idempotency_key: Mapped[str] = mapped_column(String(255))
    pipeline_version: Mapped[str] = mapped_column(String(64), default='speaker-v2')
    raw_snapshot_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    stage: Mapped[str] = mapped_column(String(32), default='queued', index=True)
    status: Mapped[str] = mapped_column(String(32), default='queued', index=True)
    artifacts_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    artifact_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    artifact_refs: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    asr_total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    asr_completed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    asr_provider_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ai_total_windows: Mapped[int] = mapped_column(Integer, default=0)
    ai_completed_windows: Mapped[int] = mapped_column(Integer, default=0)
    ai_failed_windows: Mapped[int] = mapped_column(Integer, default=0)
    ai_degraded_windows: Mapped[int] = mapped_column(Integer, default=0)
    validator_phase: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    validator_issue_count: Mapped[int] = mapped_column(Integer, default=0)
    validator_p0_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    lease_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    active_subtitle_version_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    stage_started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    asr_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    asr_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    evidence_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    evidence_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ai_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ai_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    validation_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    validation_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

