from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import uuid
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.decimal_types import ExactDecimal


class PriceCatalogEntry(Base):
    __tablename__ = 'price_catalog'
    __table_args__ = (
        UniqueConstraint(
            'provider',
            'model',
            'operation',
            'meter',
            'effective_from',
            name='uq_price_catalog_identity_effective_from',
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(255))
    operation: Mapped[str] = mapped_column(String(64))
    meter: Mapped[str] = mapped_column(String(64))
    effective_from: Mapped[datetime] = mapped_column(DateTime)
    effective_to: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    billing_unit_size: Mapped[Decimal] = mapped_column(ExactDecimal())
    unit_price_microunits: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8))
    source_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
