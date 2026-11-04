from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import uuid
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.decimal_types import ExactDecimal


class CostEvent(Base):
    __tablename__ = 'cost_events'
    __table_args__ = (
        UniqueConstraint(
            'provider_call_id',
            'meter',
            'cost_basis',
            name='uq_cost_events_call_meter_basis',
        ),
        CheckConstraint(
            "measurement_status IN ('measured', 'estimated', 'unmetered')",
            name='ck_cost_events_measurement_status',
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_call_id: Mapped[str] = mapped_column(String(36))
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    window_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    meter: Mapped[str] = mapped_column(String(64))
    cost_basis: Mapped[str] = mapped_column(String(32))
    measurement_status: Mapped[str] = mapped_column(String(16), default='measured')
    quantity: Mapped[Decimal] = mapped_column(ExactDecimal())
    unit: Mapped[str] = mapped_column(String(32))
    price_catalog_entry_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    billing_unit_size: Mapped[Optional[Decimal]] = mapped_column(ExactDecimal(), nullable=True)
    unit_price_microunits: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    amount_microunits: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(8))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
