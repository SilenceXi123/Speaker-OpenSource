from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ClientUpdatePlatformEntry(BaseModel):
    file: str
    signature: str
    sha256: Optional[str] = None
    size_bytes: Optional[int] = Field(default=None, ge=0)


class ClientUpdateVersionEntry(BaseModel):
    version: str
    pub_date: str
    notes: str = ''
    mandatory: bool = False
    minimum_supported_version: Optional[str] = None
    platforms: dict[str, ClientUpdatePlatformEntry] = Field(default_factory=dict)


class ClientUpdateChannelEntry(BaseModel):
    latest: Optional[str] = None
    versions: dict[str, ClientUpdateVersionEntry] = Field(default_factory=dict)


class ClientUpdateManifest(BaseModel):
    channels: dict[str, ClientUpdateChannelEntry] = Field(default_factory=dict)


class TauriUpdatePlatformResponse(BaseModel):
    url: str
    signature: str


class TauriUpdateResponse(BaseModel):
    version: str
    notes: str = ''
    pub_date: str
    platforms: dict[str, TauriUpdatePlatformResponse]


class ClientUpdateReleaseSummary(BaseModel):
    channel: str
    latest: Optional[str] = None
    version_count: int = 0
    updated_at: Optional[datetime] = None


class ClientUpdateAdminRegisterRequest(BaseModel):
    channel: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    target: str = Field(default='darwin', min_length=1, max_length=64)
    arch: str = Field(default='universal', min_length=1, max_length=64)
    package_file: str = Field(min_length=1)
    signature: str = Field(min_length=1)
    sha256: Optional[str] = None
    size_bytes: Optional[int] = Field(default=None, ge=0)
    pub_date: str
    notes: str = ''
    mandatory: bool = False
    minimum_supported_version: Optional[str] = None
    mark_latest: bool = True


class ClientUpdateEventRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=64)
    current_version: Optional[str] = None
    next_version: Optional[str] = None
    channel: Optional[str] = None
    message: Optional[str] = None
