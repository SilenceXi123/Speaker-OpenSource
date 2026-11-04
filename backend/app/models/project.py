from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Project(Base):
    __tablename__ = 'projects'
    __table_args__ = (
        UniqueConstraint('tenant_id', 'name_key', name='uq_projects_tenant_name_key'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), default='default', index=True)
    name: Mapped[str] = mapped_column(String(255))
    name_key: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(16), default='active', index=True)
    owner_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    default_language: Mapped[str] = mapped_column(String(32), default='zh')
    default_frame_rate: Mapped[str] = mapped_column(String(8), default='25')
    default_subtitle_profile_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    default_voiceprint_scope: Mapped[str] = mapped_column(String(32), default='project')
    knowledge_domain: Mapped[str] = mapped_column(String(64), default='broadcast', index=True)
    subtitle_backplate_assets: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    legacy_program_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
