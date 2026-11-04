from __future__ import annotations

from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.lexicon_term import TERM_STATUSES, _sql_values


class LexiconTermAlias(Base):
    __tablename__ = 'lexicon_term_aliases'
    __table_args__ = (
        CheckConstraint(f"status IN ({_sql_values(TERM_STATUSES)})", name='ck_lexicon_term_aliases_status'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    term_id: Mapped[str] = mapped_column(String(36), index=True)
    alias_text: Mapped[str] = mapped_column(String(512))
    normalized_alias: Mapped[str] = mapped_column(String(512), index=True)
    status: Mapped[str] = mapped_column(String(32), default='active', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
