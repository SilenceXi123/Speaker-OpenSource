from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PersonCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    project_id: Optional[str] = Field(default=None, max_length=36)
    program_name: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class ProgramSummaryResponse(BaseModel):
    program_key: str
    program_name: str
    person_count: int = 0
    sample_count: int = 0
    job_count: int = 0


class ProgramCreateRequest(BaseModel):
    program_name: str = Field(min_length=1, max_length=255)


class ProgramDeleteResponse(BaseModel):
    program_key: str
    program_name: str
    deleted: bool = True


class VoiceprintSampleResponse(BaseModel):
    id: str
    original_filename: str
    duration_ms: Optional[int] = None
    sample_rate_hz: Optional[int] = None
    channel_count: Optional[int] = None
    quality_score: Optional[float] = None
    confidence_score: Optional[float] = None
    usage_types: list[str] = Field(default_factory=list)
    embedding_status: str
    provider_name: Optional[str] = None
    provider_model_id: Optional[str] = None
    provider_message: Optional[str] = None
    clone_readiness_status: str = 'unknown'
    clone_readiness_message: Optional[str] = None
    audio_public_url: Optional[str] = None
    clone_audio_local_path: Optional[str] = None
    clone_audio_public_url: Optional[str] = None
    clone_sample_rate_hz: Optional[int] = None
    clone_channel_count: Optional[int] = None
    waveform_ready: bool = False
    source_type: str = 'manual_upload'
    review_status: str = 'confirmed'
    source_job_id: Optional[str] = None
    source_speaker_key: Optional[str] = None
    source_start_ms: Optional[int] = None
    source_end_ms: Optional[int] = None
    duplicate_of_sample_id: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None


class VoiceprintAutoLearningResponse(BaseModel):
    created_count: int = 0
    skipped_count: int = 0
    duplicate_count: int = 0
    auto_confirmed_count: int = 0
    global_promoted_count: int = 0
    pruned_count: int = 0
    conflict_count: int = 0
    error_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    candidate_sample_ids: list[str] = Field(default_factory=list)


class PersonResponse(BaseModel):
    id: str
    name: str
    project_id: Optional[str] = None
    program_name: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    notes: Optional[str] = None
    sample_count: int = 0
    ready_sample_count: int = 0
    created_at: datetime
    updated_at: datetime
    samples: list[VoiceprintSampleResponse] = Field(default_factory=list)


class VoiceprintProviderStatusResponse(BaseModel):
    provider: str
    model_id: str
    available: bool
    message: str
