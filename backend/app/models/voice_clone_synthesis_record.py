from __future__ import annotations

from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class VoiceCloneSynthesisRecord(Base):
    __tablename__ = 'voice_clone_synthesis_records'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    voice_clone_profile_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey('voice_clone_profiles.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    voice_name_snapshot: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider_snapshot: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    target_model_snapshot: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    person_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey('people.id'), nullable=True, index=True)
    program_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    program_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    target_model: Mapped[str] = mapped_column(String(128))
    input_text: Mapped[str] = mapped_column(Text)
    output_audio_local_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_audio_public_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default='processing')
    provider_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
