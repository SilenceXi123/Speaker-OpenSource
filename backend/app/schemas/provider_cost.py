from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


PipelineScope = Literal['project_asset', 'subtitle_legacy', 'subtitle_v2']


class ProviderCallContext(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    project_id: str = Field(min_length=1, max_length=36)
    job_id: Optional[str] = Field(default=None, max_length=36)
    run_id: Optional[str] = Field(default=None, max_length=36)
    window_id: Optional[str] = Field(default=None, max_length=36)
    pipeline_scope: PipelineScope = 'project_asset'

    @model_validator(mode='after')
    def validate_scope_identity(self):
        if self.pipeline_scope in {'subtitle_legacy', 'subtitle_v2'} and not self.job_id:
            raise ValueError('job_id is required for subtitle provider calls')
        if self.pipeline_scope == 'subtitle_v2' and not self.run_id:
            raise ValueError('run_id is required for subtitle_v2 provider calls')
        return self


class ProviderMeterObservation(BaseModel):
    meter: str = Field(min_length=1, max_length=64)
    quantity: Optional[Decimal] = Field(default=None, ge=0)
    unit: str = Field(min_length=1, max_length=32)
    cost_basis: str = Field(default='list_price', min_length=1, max_length=32)


class ProviderCallObservation(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    attempt: int = Field(default=1, ge=1)
    status: Literal['completed', 'failed'] = 'completed'
    request_id: Optional[str] = Field(default=None, max_length=255)
    usage: Optional[dict[str, Any]] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_code: Optional[str] = Field(default=None, max_length=64)
    error_summary: Optional[str] = None
    meters: list[ProviderMeterObservation] = Field(default_factory=list)
    value: Any = None


class ProviderCallClaim(BaseModel):
    call_id: str
    owner: str
    lock_version: int
    expires_at: datetime


class ProviderCostBreakdown(BaseModel):
    provider: str
    model: str
    call_count: int
    event_count: int
    measured_event_count: int
    estimated_event_count: int
    unmetered_event_count: int
    total_amount_microunits: Optional[int]
    amounts_by_currency: dict[str, int] = Field(default_factory=dict)
    currencies: list[str] = Field(default_factory=list)


class ProviderCostSummary(BaseModel):
    call_count: int
    event_count: int
    measured_event_count: int
    estimated_event_count: int
    unmetered_event_count: int
    total_amount_microunits: Optional[int]
    amounts_by_currency: dict[str, int] = Field(default_factory=dict)
    currencies: list[str] = Field(default_factory=list)
    breakdowns: list[ProviderCostBreakdown] = Field(default_factory=list)
