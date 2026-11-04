from __future__ import annotations

from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class VoiceCloneProfile(Base):
    __tablename__ = 'voice_clone_profiles'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    person_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey('people.id'), nullable=True, index=True)
    source_sample_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey('voiceprint_samples.id'), nullable=True, index=True)
    source_clone_sample_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey('voice_clone_samples.id'), nullable=True, index=True)
    source_audio_local_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    program_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    program_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), default='cosyvoice')
    provider_model_family: Mapped[str] = mapped_column(String(64), default='cosyvoice')
    target_model: Mapped[str] = mapped_column(String(128))
    voice_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    voice_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default='creating')
    provider_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    identity_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    preview_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    preview_audio_local_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    usage_program_keys: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
