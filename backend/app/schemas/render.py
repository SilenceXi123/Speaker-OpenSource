from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class LowerThirdAssetResponse(BaseModel):
    speaker_name: str
    file_name: str
    object_key: str
    public_url: Optional[str] = None
    local_path: Optional[str] = None
    export_path: Optional[str] = None
    library_scope: Optional[str] = None


class LowerThirdAssetsResponse(BaseModel):
    assets: list[LowerThirdAssetResponse]


class SubtitleBackplateAssetResponse(BaseModel):
    file_name: str
    object_key: str
    public_url: Optional[str] = None
    local_path: Optional[str] = None
    export_path: Optional[str] = None
    role: Optional[str] = None
    library_scope: Optional[str] = None


class LowerThirdExportPathUpdateRequest(BaseModel):
    object_key: str
    export_path: Optional[str] = None


class SourceExportPathUpdateRequest(BaseModel):
    export_path: Optional[str] = None


class RenderResponse(BaseModel):
    render_status: str
    render_progress: int
    render_message: Optional[str] = None
    render_output_url: Optional[str] = None
    matched_lower_thirds: dict[str, dict]
    missing_speakers: list[str]
