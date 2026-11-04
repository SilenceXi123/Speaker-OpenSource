from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class JobEvent(Base):
    __tablename__ = 'job_events'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    job_kind: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    program_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    program_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    progress: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
