from __future__ import annotations

from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class LexiconOccurrence(Base):
    __tablename__ = 'lexicon_occurrences'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    term_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    candidate_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    import_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalized_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_start_char: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_end_char: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    normalized_start_char: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    normalized_end_char: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
