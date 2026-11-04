from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import CheckConstraint, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.lexicon_term import PROTECTION_LEVELS, _sql_values

CANDIDATE_STATUSES = {'candidate', 'suggested_active', 'rejected', 'promoted', 'deprecated'}


class LexiconCandidate(Base):
    __tablename__ = 'lexicon_candidates'
    __table_args__ = (
        CheckConstraint(f"status IN ({_sql_values(CANDIDATE_STATUSES)})", name='ck_lexicon_candidates_status'),
        CheckConstraint(
            f"suggested_protection_level IN ({_sql_values(PROTECTION_LEVELS)})",
            name='ck_lexicon_candidates_suggested_protection_level',
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    project_id: Mapped[str] = mapped_column(String(255), index=True)
    normalized_term: Mapped[str] = mapped_column(String(512), index=True)
    display_text: Mapped[str] = mapped_column(String(512))
    term_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default='candidate', index=True)
    suggested_protection_level: Mapped[str] = mapped_column(String(16), default='soft')
    extraction_method: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_text_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    raw_start_char: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_end_char: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    normalized_start_char: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    normalized_end_char: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    promoted_term_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    review_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
