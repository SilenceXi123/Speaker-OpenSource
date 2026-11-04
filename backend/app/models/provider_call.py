from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ProviderCall(Base):
    __tablename__ = 'provider_calls'
    __table_args__ = (
        UniqueConstraint('idempotency_key', 'attempt', name='uq_provider_calls_idempotency_attempt'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    window_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(255))
    operation: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default='pending')
    provider_request_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prompt_cache_hit_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prompt_cache_miss_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    usage_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    claim_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    claim_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
