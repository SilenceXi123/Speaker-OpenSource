from __future__ import annotations

from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class VoiceCloneIdentityReview(Base):
    __tablename__ = 'voice_clone_identity_reviews'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_identity_id: Mapped[str] = mapped_column(String(36), ForeignKey('voice_clone_identities.id'), index=True)
    sample_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey('voice_clone_samples.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    submitted_name: Mapped[str] = mapped_column(String(255))
    match_score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default='pending', index=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    resolution_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
