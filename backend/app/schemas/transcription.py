from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, HttpUrl

from app.schemas.job import JobResponse
from app.schemas.upload import UploadResponse


class SubmitTranscriptionRequest(BaseModel):
    file_url: HttpUrl
    language_hints: Optional[list[str]] = None
    speaker_count: Optional[int] = Field(default=None, ge=2, le=100)
    vocabulary_id: Optional[str] = None
    project_id: Optional[str] = None
    program_name: Optional[str] = None
    export_frame_rate: Optional[str] = Field(default='25', pattern='^(25|29\\.97|50)$')
    subtitle_strategy: Optional[str] = Field(default='enhanced', pattern='^(standard|enhanced|pro)$')
    script_text: Optional[str] = None
    script_source_name: Optional[str] = None
    remove_standalone_fillers: bool = False


class MediaTranscriptionResponse(BaseModel):
    job: JobResponse
    video_upload: Optional[UploadResponse] = None
    audio_upload: UploadResponse
