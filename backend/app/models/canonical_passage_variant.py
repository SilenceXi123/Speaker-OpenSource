from __future__ import annotations

from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CanonicalPassageVariant(Base):
    __tablename__ = 'canonical_passage_variants'
    __table_args__ = (
        UniqueConstraint(
            'passage_id',
            'content_hash',
            name='uq_canonical_passage_variants_content',
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    passage_id: Mapped[str] = mapped_column(String(36), index=True)
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    variant_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    lines_json: Mapped[list[str]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default='active', index=True)
    review_status: Mapped[str] = mapped_column(String(32), default='approved', index=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_priority: Mapped[int] = mapped_column(Integer, default=0)
    import_batch_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, index=True
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
