from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.voiceprint import VoiceprintAutoLearningResponse


class VoiceprintCandidate(BaseModel):
    person_id: str
    person_name: str
    score: float


class SpeakerOption(BaseModel):
    speaker_key: str
    speaker_name: Optional[str] = None
    segment_count: int = 0
    voiceprint_status: Optional[str] = None
    voiceprint_person_name: Optional[str] = None
    voiceprint_score: Optional[float] = None
    voiceprint_candidates: list[VoiceprintCandidate] = Field(default_factory=list)


class SpeakerMappingItem(BaseModel):
    speaker_key: str = Field(min_length=1)
    speaker_name: str = Field(min_length=1)


class SpeakerMappingRequest(BaseModel):
    mappings: list[SpeakerMappingItem]


class SpeakerMappingResponse(BaseModel):
    mappings: dict[str, str]
    speakers: list[SpeakerOption]
    program_name: Optional[str] = None
    memory_suggestions: list[str] = Field(default_factory=list)
    voiceprint_learning: Optional[VoiceprintAutoLearningResponse] = None
