from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.voiceprint import VoiceprintAutoLearningResponse


SubtitleVariant = Literal['full', 'clean']
SubtitleFormat = Literal['srt', 'vtt']


class SubtitleCue(BaseModel):
    index: int
    start_ms: int
    end_ms: int
    speaker_key: str
    speaker_name: str
    full_text: str
    clean_text: str


class SubtitlePreviewResponse(BaseModel):
    variant: SubtitleVariant
    format: SubtitleFormat
    content: str
    cues: list[SubtitleCue]
    cue_hash: Optional[str] = None
    voiceprint_learning: Optional[VoiceprintAutoLearningResponse] = None


class SubtitleTextBatchDeleteResponse(SubtitlePreviewResponse):
    deleted_count: int = 0


class SubtitleTextBatchApplyResponse(SubtitlePreviewResponse):
    updated_count: int = 0


class SubtitleSpeakerOverrideUpdate(BaseModel):
    index: int = Field(ge=1)
    speaker_name: str = Field(min_length=1, max_length=255)


class SubtitleSpeakerOverrideRequest(BaseModel):
    cues: list[SubtitleSpeakerOverrideUpdate] = Field(default_factory=list)


class SubtitleTextOverrideUpdate(BaseModel):
    index: int = Field(ge=1)
    clean_text: Optional[str] = Field(default=None, max_length=500)
    delete: bool = False


class SubtitleTextOverrideRequest(BaseModel):
    cues: list[SubtitleTextOverrideUpdate] = Field(default_factory=list)
