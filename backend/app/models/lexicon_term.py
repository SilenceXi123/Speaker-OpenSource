from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import CheckConstraint, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

TERM_SCOPE_TYPES = {'job', 'project', 'domain', 'global', 'builtin'}
TERM_STATUSES = {'active', 'disabled', 'deprecated', 'pending_review'}
PROTECTION_LEVELS = {'hard', 'strong', 'soft'}


def _sql_values(values: set[str]) -> str:
    return ', '.join(repr(value) for value in sorted(values))


class LexiconTerm(Base):
    __tablename__ = 'lexicon_terms'
    __table_args__ = (
        CheckConstraint(f"scope_type IN ({_sql_values(TERM_SCOPE_TYPES)})", name='ck_lexicon_terms_scope_type'),
        CheckConstraint(f"status IN ({_sql_values(TERM_STATUSES)})", name='ck_lexicon_terms_status'),
        CheckConstraint(f"protection_level IN ({_sql_values(PROTECTION_LEVELS)})", name='ck_lexicon_terms_protection_level'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    scope_type: Mapped[str] = mapped_column(String(32), index=True)
    scope_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    normalized_term: Mapped[str] = mapped_column(String(512), index=True)
    display_text: Mapped[str] = mapped_column(String(512))
    term_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default='pending_review', index=True)
    protection_level: Mapped[str] = mapped_column(String(16), default='soft')
    review_status: Mapped[str] = mapped_column(String(32), default='pending')
    source_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
