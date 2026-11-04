from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CanonicalImportBatch(Base):
    __tablename__ = 'canonical_import_batches'
    __table_args__ = (
        UniqueConstraint(
            'tenant_id',
            'selection_hash',
            name='uq_canonical_import_batches_selection',
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    domain_id: Mapped[str] = mapped_column(
        String(64), default='classical_poetry', index=True
    )
    source_name: Mapped[str] = mapped_column(String(255), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_version: Mapped[str] = mapped_column(String(255), index=True)
    source_license: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    selection_label: Mapped[str] = mapped_column(String(255))
    selection_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default='processing', index=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    passage_count: Mapped[int] = mapped_column(Integer, default=0)
    variant_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
