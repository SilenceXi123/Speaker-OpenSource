from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.provider_cost import ProviderCostSummary


class AdminLoginResponse(BaseModel):
    username: str
    authenticated: bool = True


class ProviderCallCostResponse(BaseModel):
    id: str
    project_id: str
    job_id: Optional[str] = None
    run_id: Optional[str] = None
    provider: str
    model: str
    operation: str
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    prompt_cache_hit_tokens: Optional[int] = None
    prompt_cache_miss_tokens: Optional[int] = None
    audio_seconds: Decimal = Decimal('0')
    event_count: int = 0
    measured_event_count: int = 0
    estimated_event_count: int = 0
    unmetered_event_count: int = 0
    catalog_estimated_amounts_by_currency: dict[str, int] = Field(default_factory=dict)
    actual_amounts_by_currency: dict[str, int] = Field(default_factory=dict)
    actual_minus_catalog_by_currency: dict[str, int] = Field(default_factory=dict)
    usage_payload: None = None


class ProviderTaskCostResponse(BaseModel):
    project_id: str
    project_name: str
    job_id: Optional[str] = None
    call_count: int = 0
    token_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    audio_seconds: Decimal = Decimal('0')
    event_count: int = 0
    measured_event_count: int = 0
    estimated_event_count: int = 0
    unmetered_event_count: int = 0
    catalog_estimated_amounts_by_currency: dict[str, int] = Field(default_factory=dict)
    actual_amounts_by_currency: dict[str, int] = Field(default_factory=dict)
    actual_minus_catalog_by_currency: dict[str, int] = Field(default_factory=dict)
    calls: list[ProviderCallCostResponse] = Field(default_factory=list)


class ProviderProjectTokenResponse(BaseModel):
    project_id: str
    project_name: str
    call_count: int = 0
    token_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    audio_seconds: Decimal = Decimal('0')
    catalog_estimated_amounts_by_currency: dict[str, int] = Field(default_factory=dict)


class ProviderDailyTokenResponse(BaseModel):
    usage_date: str
    call_count: int = 0
    token_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    audio_seconds: Decimal = Decimal('0')


class AdminProviderCostReportResponse(BaseModel):
    summary: ProviderCostSummary
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    token_call_count: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    audio_seconds: Decimal = Decimal('0')
    catalog_estimated_amounts_by_currency: dict[str, int] = Field(default_factory=dict)
    actual_amounts_by_currency: dict[str, int] = Field(default_factory=dict)
    actual_minus_catalog_by_currency: dict[str, int] = Field(default_factory=dict)
    projects: list[ProviderProjectTokenResponse] = Field(default_factory=list)
    daily: list[ProviderDailyTokenResponse] = Field(default_factory=list)
    tasks: list[ProviderTaskCostResponse] = Field(default_factory=list)


class ProviderCostReadinessResponse(BaseModel):
    status: str
    ready_for_recording: bool = False
    enabled: bool = False
    schema_ready: bool = False
    catalog_ready: bool = False
    actual_bill_connected: bool = False
    configured_models: dict[str, str] = Field(default_factory=dict)
    expected_price_count: int = 0
    matching_price_count: int = 0
    missing_prices: list[str] = Field(default_factory=list)
    conflicting_prices: list[str] = Field(default_factory=list)
    provider_call_count: int = 0
    cost_event_count: int = 0
    price_catalog_entry_count: int = 0
    token_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class PriceCatalogCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=255)
    operation: str = Field(min_length=1, max_length=64)
    meter: str = Field(min_length=1, max_length=64)
    effective_from: datetime
    effective_to: Optional[datetime] = None
    billing_unit_size: Decimal = Field(gt=0)
    unit_price_microunits: int = Field(ge=0)
    currency: str = Field(default='CNY', min_length=3, max_length=8)
    source_note: Optional[str] = None

    @model_validator(mode='after')
    def validate_effective_interval(self):
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError('effective_to must be later than effective_from')
        self.currency = self.currency.upper()
        return self


class PriceCatalogEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    model: str
    operation: str
    meter: str
    effective_from: datetime
    effective_to: Optional[datetime] = None
    billing_unit_size: Decimal
    unit_price_microunits: int
    currency: str
    source_note: Optional[str] = None


class JobEventResponse(BaseModel):
    id: str
    job_id: str
    job_kind: Optional[str] = None
    program_key: Optional[str] = None
    program_name: Optional[str] = None
    event_type: str
    stage: Optional[str] = None
    status: Optional[str] = None
    progress: Optional[int] = None
    message: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    created_at: datetime


class JobEventListResponse(BaseModel):
    job_id: str
    total: int = 0
    events: list[JobEventResponse] = Field(default_factory=list)


class StageTwoSmokeCheckResponse(BaseModel):
    key: str
    status: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class StageTwoSmokeReportResponse(BaseModel):
    job_id: str
    job_kind: str
    job_status: str
    overall_status: str
    summary: dict[str, int] = Field(default_factory=dict)
    checks: list[StageTwoSmokeCheckResponse] = Field(default_factory=list)


class StorageCleanupItemResponse(BaseModel):
    path: str
    bytes: int = 0
    reason: str
    job_id: Optional[str] = None
    field: Optional[str] = None


class StorageCleanupReportResponse(BaseModel):
    dry_run: bool = True
    total_bytes: int = 0
    deleted_bytes: int = 0
    item_count: int = 0
    deleted_count: int = 0
    items: list[StorageCleanupItemResponse] = Field(default_factory=list)
