from __future__ import annotations

from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SpeakerMemory(Base):
    __tablename__ = 'speaker_memories'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    program_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    program_name: Mapped[str] = mapped_column(String(255))
    speaker_names: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    lower_third_assets: Mapped[Optional[list[dict]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
