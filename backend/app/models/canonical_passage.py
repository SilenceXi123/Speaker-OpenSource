from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CanonicalPassage(Base):
    __tablename__ = 'canonical_passages'
    __table_args__ = (
        UniqueConstraint(
            'tenant_id',
            'domain_id',
            'content_hash',
            name='uq_canonical_passages_content',
        ),
    )
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    domain_id: Mapped[str] = mapped_column(String(64), default='classical_poetry', index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    author: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    dynasty: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    genre: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    tune_pattern: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    canonical_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    lines_json: Mapped[list[str]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default='active', index=True)
    review_status: Mapped[str] = mapped_column(String(32), default='approved', index=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    import_batch_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, index=True
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
