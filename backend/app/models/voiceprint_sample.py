from __future__ import annotations

from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class VoiceprintSample(Base):
    __tablename__ = 'voiceprint_samples'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id: Mapped[str] = mapped_column(String(36), ForeignKey('people.id'), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    audio_local_path: Mapped[str] = mapped_column(Text)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sample_rate_hz: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    channel_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    usage_types: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    embedding: Mapped[Optional[list[float]]] = mapped_column(JSON, nullable=True)
    embedding_status: Mapped[str] = mapped_column(String(32), default='pending_provider')
    provider_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    provider_model_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    clone_readiness_status: Mapped[str] = mapped_column(String(32), default='unknown')
    clone_readiness_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    audio_public_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    clone_audio_local_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    clone_audio_public_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    clone_sample_rate_hz: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    clone_channel_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    waveform_ready: Mapped[bool] = mapped_column(default=False)
    source_type: Mapped[str] = mapped_column(String(32), default='manual_upload')
    review_status: Mapped[str] = mapped_column(String(32), default='confirmed')
    source_job_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    source_speaker_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_start_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_end_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duplicate_of_sample_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
