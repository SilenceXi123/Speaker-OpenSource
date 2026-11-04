from __future__ import annotations

from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Person(Base):
    __tablename__ = 'people'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), index=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    program_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    program_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    aliases: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
