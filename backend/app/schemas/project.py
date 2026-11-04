from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


SubtitleBackplateRole = Literal['with_speaker', 'without_speaker']


class ProjectSubtitleBackplateAsset(BaseModel):
    model_config = ConfigDict(extra='allow')

    asset_id: Optional[str] = None
    file_name: str
    object_key: str
    public_url: Optional[str] = None
    local_path: Optional[str] = None
    export_path: Optional[str] = None
    sha256: Optional[str] = None
    role: Optional[SubtitleBackplateRole] = None
    library_scope: Optional[str] = None


class ProjectSubtitleBackplateAssets(BaseModel):
    schema_version: Literal[1] = 1
    roles: dict[SubtitleBackplateRole, ProjectSubtitleBackplateAsset]


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    owner_name: Optional[str] = Field(default=None, max_length=255)
    default_language: str = Field(default='zh', min_length=1, max_length=32)
    default_frame_rate: str = Field(default='25', pattern='^(25|29\\.97|50)$')
    default_subtitle_profile_id: Optional[str] = Field(default=None, max_length=64)
    default_voiceprint_scope: str = Field(default='project', pattern='^(project|global)$')
    knowledge_domain: str = Field(default='broadcast', pattern='^(broadcast|classical_poetry|general)$')


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    owner_name: Optional[str] = Field(default=None, max_length=255)
    default_language: Optional[str] = Field(default=None, min_length=1, max_length=32)
    default_frame_rate: Optional[str] = Field(default=None, pattern='^(25|29\\.97|50)$')
    default_subtitle_profile_id: Optional[str] = Field(default=None, max_length=64)
    default_voiceprint_scope: Optional[str] = Field(default=None, pattern='^(project|global)$')
    knowledge_domain: Optional[str] = Field(default=None, pattern='^(broadcast|classical_poetry|general)$')


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    name: str
    status: str
    owner_name: Optional[str] = None
    default_language: str
    default_frame_rate: str
    default_subtitle_profile_id: Optional[str] = None
    default_voiceprint_scope: str
    knowledge_domain: str = 'broadcast'
    subtitle_backplate_assets: Optional[ProjectSubtitleBackplateAssets] = None
    legacy_program_key: Optional[str] = None
    job_count: int = 0
    active_job_count: int = 0
    person_count: int = 0
    sample_count: int = 0
    asset_count: int = 0
    created_at: datetime
    updated_at: datetime
