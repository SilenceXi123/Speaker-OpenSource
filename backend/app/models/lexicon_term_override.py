from __future__ import annotations

from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.lexicon_term import TERM_SCOPE_TYPES, _sql_values

OVERRIDE_ACTIONS = {'disable', 'force_hard', 'force_strong', 'force_soft'}


class LexiconTermOverride(Base):
    __tablename__ = 'lexicon_term_overrides'
    __table_args__ = (
        CheckConstraint(f"scope_type IN ({_sql_values(TERM_SCOPE_TYPES)})", name='ck_lexicon_term_overrides_scope_type'),
        CheckConstraint(f"action IN ({_sql_values(OVERRIDE_ACTIONS)})", name='ck_lexicon_term_overrides_action'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    term_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    normalized_term: Mapped[str] = mapped_column(String(512), index=True)
    scope_type: Mapped[str] = mapped_column(String(32), index=True)
    scope_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
