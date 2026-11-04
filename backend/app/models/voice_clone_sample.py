from __future__ import annotations

from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import BigInteger, DateTime, JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class VoiceCloneSample(Base):
    __tablename__ = 'voice_clone_samples'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    display_name: Mapped[str] = mapped_column(String(255), index=True)
    name_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    program_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    program_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    audio_local_path: Mapped[str] = mapped_column(Text)
    audio_public_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sample_rate_hz: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    channel_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    clone_readiness_status: Mapped[str] = mapped_column(String(32), default='unknown')
    clone_readiness_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    identity_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    embedding: Mapped[Optional[list[float]]] = mapped_column(JSON, nullable=True)
    embedding_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    embedding_provider_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    embedding_model_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    embedding_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
