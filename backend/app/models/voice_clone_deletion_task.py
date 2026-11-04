from __future__ import annotations

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class VoiceCloneDeletionTask(Base):
    __tablename__ = 'voice_clone_deletion_tasks'

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    resource_key: Mapped[str] = mapped_column(String(1024), unique=True)
    provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    target_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    voice_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    oss_object_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    local_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
