from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class LexiconTermResponse(BaseModel):
    id: str
    scope_type: str
    scope_id: Optional[str] = None
    normalized_term: str
    display_text: str
    term_type: str
    status: str
    protection_level: str
    review_status: str
    occurrence_count: int = 0
    metadata_json: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class LexiconCandidateResponse(BaseModel):
    id: str
    project_id: str
    normalized_term: str
    display_text: str
    term_type: str
    status: str
    suggested_protection_level: str
    extraction_method: str
    confidence: Optional[float] = None
    source_text_hash: Optional[str] = None
    context: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class LexiconTermCreateRequest(BaseModel):
    project_id: str
    display_text: str
    term_type: str = "term"
    protection_level: str = "strong"
    source_type: str = "manual"


class LexiconCandidateReviewRequest(BaseModel):
    protection_level: str = "strong"
    reviewed_by: str = "client"
    reason: str = ""


class LexiconImportRequest(BaseModel):
    project_id: str
    source_name: str = "粘贴文本"
    text: str = Field(default="", max_length=80000)
