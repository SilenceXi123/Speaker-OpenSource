from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.voiceprint import VoiceprintSampleResponse


class VoiceCloneProviderOption(BaseModel):
    provider: str
    label: str
    target_model: str
    sample_requirements: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class VoiceCloneModelCapabilities(BaseModel):
    instruction: bool


class VoiceCloneModelConstraints(BaseModel):
    sample_min_seconds: int
    sample_max_seconds: int = 60
    sample_recommended_min_seconds: int = 10
    sample_recommended_max_seconds: int = 20
    sample_min_rate_hz: int
    sample_max_bytes: int = 10 * 1024 * 1024
    text_max_characters: int = 500
    instruction_max_weight: int
    instruction_provider_max_weight: int
    instruction_han_weight: int


class VoiceCloneModelOption(BaseModel):
    id: str
    provider: str
    model: str
    label: str
    recommended: bool = False
    available: bool
    unavailable_reason: Optional[str] = None
    capabilities: VoiceCloneModelCapabilities
    constraints: VoiceCloneModelConstraints


class VoiceCloneProviderStatusResponse(BaseModel):
    discard_voice_after_download: bool = True
    discard_outputs_after_download: bool = True
    enabled: bool
    provider: str
    region: str
    target_model: str
    available: bool
    message: str
    providers: list[VoiceCloneProviderOption] = Field(default_factory=list)
    models: list[VoiceCloneModelOption] = Field(default_factory=list)


class VoiceCloneProfileCreateRequest(BaseModel):
    project_id: Optional[str] = None
    person_id: Optional[str] = None
    sample_id: Optional[str] = None
    clone_sample_id: Optional[str] = None
    program_name: Optional[str] = None
    voice_name: str = Field(min_length=1, max_length=255)
    provider: Optional[str] = None
    target_model: Optional[str] = None
    language_hints: list[str] = Field(default_factory=list)


class VoiceCloneSampleResponse(BaseModel):
    expires_at: Optional[datetime] = None
    id: str
    display_name: str
    project_id: Optional[str] = None
    program_key: Optional[str] = None
    program_name: Optional[str] = None
    original_filename: str
    content_sha256: Optional[str] = None
    file_size_bytes: Optional[int] = None
    duration_ms: Optional[int] = None
    sample_rate_hz: Optional[int] = None
    channel_count: Optional[int] = None
    clone_readiness_status: str = 'unknown'
    clone_readiness_message: Optional[str] = None
    audio_public_url: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None


class VoiceCloneSampleSaveResponse(VoiceCloneSampleResponse):
    reused_existing: bool = False
    identity_id: str
    identity_display_name: str
    identity_aliases: list[str] = Field(default_factory=list)
    match_type: str
    match_score: Optional[float] = None
    sample_slots_remaining: int
    message: Optional[str] = None


class VoiceCloneProfileResponse(BaseModel):
    expires_at: Optional[datetime] = None
    id: str
    identity_id: Optional[str] = None
    project_id: Optional[str] = None
    person_id: Optional[str] = None
    source_sample_id: Optional[str] = None
    source_clone_sample_id: Optional[str] = None
    source_audio_local_path: Optional[str] = None
    program_key: Optional[str] = None
    program_name: Optional[str] = None
    provider: str
    provider_model_family: str
    target_model: str
    voice_id: Optional[str] = None
    voice_name: str
    status: str
    provider_message: Optional[str] = None
    preview_text: Optional[str] = None
    preview_audio_local_path: Optional[str] = None
    usage_program_keys: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    last_used_at: Optional[datetime] = None
    source_sample: Optional[VoiceprintSampleResponse] = None
    source_clone_sample: Optional[VoiceCloneSampleResponse] = None


class VoiceCloneProfileCreateResponse(VoiceCloneProfileResponse):
    reused_existing: bool = False
    message: Optional[str] = None


class VoiceCloneLibraryProfileResponse(BaseModel):
    expires_at: Optional[datetime] = None
    id: str
    identity_id: Optional[str] = None
    identity_display_name: Optional[str] = None
    identity_aliases: list[str] = Field(default_factory=list)
    voice_name: str
    provider: str
    target_model: str
    status: str
    voice_id: Optional[str] = None
    source_sample_id: Optional[str] = None
    source_clone_sample_id: Optional[str] = None
    source_display_name: Optional[str] = None
    selectable: bool = False
    project_scope: str = 'shared'
    project_id: Optional[str] = None
    source_project_name: Optional[str] = None
    source_original_filename: Optional[str] = None
    source_sample_ready: bool = False
    created_at: datetime
    updated_at: datetime
    last_used_at: Optional[datetime] = None


class VoiceCloneLibraryGroupResponse(BaseModel):
    expires_at: Optional[datetime] = None
    group_key: str
    identity_id: Optional[str] = None
    identity_display_name: Optional[str] = None
    identity_aliases: list[str] = Field(default_factory=list)
    display_name: str
    version_count: int
    health_status: str
    recommended_profile: Optional[VoiceCloneLibraryProfileResponse] = None


class VoiceCloneLibraryPageResponse(BaseModel):
    groups: list[VoiceCloneLibraryGroupResponse] = Field(default_factory=list)
    next_cursor: Optional[str] = None
    total: int = 0
    available_group_count: int = 0
    exception_group_count: int = 0
    reusable_sample_count: int = 0


class VoiceCloneSamplePageItemResponse(VoiceCloneSampleResponse):
    in_use: bool = False
    profile_count: int = 0


class VoiceCloneSamplePageResponse(BaseModel):
    items: list[VoiceCloneSamplePageItemResponse] = Field(default_factory=list)
    next_cursor: Optional[str] = None
    total: int = 0


class VoiceClonePreviewRequest(BaseModel):
    project_id: Optional[str] = None
    text: str = Field(min_length=1, max_length=500)
    instruction: Optional[str] = Field(default=None, max_length=1600)


class VoiceCloneSynthesisRecordResponse(BaseModel):
    expires_at: Optional[datetime] = None
    id: str
    project_id: Optional[str] = None
    voice_clone_profile_id: Optional[str] = None
    voice_name_snapshot: Optional[str] = None
    provider_snapshot: Optional[str] = None
    target_model_snapshot: Optional[str] = None
    person_id: Optional[str] = None
    program_key: Optional[str] = None
    program_name: Optional[str] = None
    target_model: str
    input_text: str
    output_audio_local_path: Optional[str] = None
    output_audio_public_url: Optional[str] = None
    status: str
    provider_message: Optional[str] = None
    created_at: datetime


class VoiceCloneDeleteResponse(BaseModel):
    id: str
    deleted: bool = True
