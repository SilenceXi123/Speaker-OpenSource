from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Float, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class KnowledgeCorrectionEvidence(Base):
    __tablename__ = 'knowledge_correction_evidence'
    __table_args__ = (
        UniqueConstraint(
            'tenant_id',
            'fingerprint',
            name='uq_knowledge_correction_evidence_fingerprint',
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    project_id: Mapped[str] = mapped_column(String(255), index=True)
    domain_id: Mapped[str] = mapped_column(String(64), default='broadcast', index=True)
    job_id: Mapped[str] = mapped_column(String(255), index=True)
    segment_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    patch_source: Mapped[str] = mapped_column(String(64), index=True)
    source_text: Mapped[str] = mapped_column(String(512))
    corrected_text: Mapped[str] = mapped_column(String(512))
    normalized_source: Mapped[str] = mapped_column(String(512), index=True)
    normalized_corrected: Mapped[str] = mapped_column(String(512), index=True)
    evidence_status: Mapped[str] = mapped_column(String(32), default='active', index=True)
    promotable: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    raw_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    corrected_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
