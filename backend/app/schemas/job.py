from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class JobSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    source_url: str
    source_local_path: Optional[str] = None
    source_export_path: Optional[str] = None
    export_frame_rate: str
    project_id: Optional[str] = None
    program_name: Optional[str] = None
    video_source_url: Optional[str] = None
    video_source_local_path: Optional[str] = None
    video_source_export_path: Optional[str] = None
    render_status: Optional[str] = None
    render_progress: Optional[int] = None
    render_message: Optional[str] = None
    processing_progress: Optional[int] = None
    processing_message: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    source_url: str
    source_local_path: Optional[str] = None
    source_export_path: Optional[str] = None
    export_frame_rate: str
    subtitle_strategy: str = 'enhanced'
    project_id: Optional[str] = None
    program_name: Optional[str] = None
    provider_task_id: Optional[str] = None
    provider_task_meta: Optional[list[dict[str, Any]]] = None
    video_source_url: Optional[str] = None
    video_source_local_path: Optional[str] = None
    video_source_export_path: Optional[str] = None
    transcription_url: Optional[str] = None
    render_output_url: Optional[str] = None
    render_status: Optional[str] = None
    render_progress: Optional[int] = None
    render_message: Optional[str] = None
    processing_progress: Optional[int] = None
    processing_message: Optional[str] = None
    processing_metrics: Optional[dict[str, Any]] = None
    pipeline_artifacts: Optional[dict[str, Any]] = None
    script_context: Optional[dict[str, Any]] = None
    script_source_name: Optional[str] = None
    remove_standalone_fillers: bool = False
    error_message: Optional[str] = None
    source_duration_seconds: Optional[float] = None
    subtitle_enhanced: bool = False
    subtitle_enhancement_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class JobDetailResponse(JobResponse):
    speaker_mappings: Optional[dict[str, str]] = None
    lower_third_assets: Optional[list[dict[str, Any]]] = None
    matched_lower_thirds: Optional[dict[str, Any]] = None
    subtitle_backplate_asset: Optional[dict[str, Any]] = None
    normalized_segments: Optional[list[dict[str, Any]]] = None
    raw_payload: Optional[dict[str, Any]] = None


class PipelineArtifactSummary(BaseModel):
    schema_version: Optional[str] = None
    shadow_mode: bool = False
    timeline_revision: Optional[str] = None
    subtitle_cue_count: int = 0
    cue_hash: str = ''
    p0_count: int = 0
    issue_count: int = 0


class PipelineArtifactResponse(BaseModel):
    summary: PipelineArtifactSummary
    artifacts: dict[str, Any]


class SubtitlePipelineAsrProgress(BaseModel):
    completed_chunks: int
    total_chunks: int
    provider_status: Optional[str] = None
    running_seconds: float


class SubtitlePipelineAiProgress(BaseModel):
    completed_windows: int
    total_windows: int
    failed_windows: int
    degraded_windows: int


class SubtitlePipelineValidationProgress(BaseModel):
    phase: Optional[str] = None
    issue_count: int
    p0_count: int


class SubtitlePipelineProgressResponse(BaseModel):
    run_id: str
    project_id: str
    job_id: str
    stage: str
    status: str
    asr: SubtitlePipelineAsrProgress
    ai: SubtitlePipelineAiProgress
    validation: SubtitlePipelineValidationProgress
    warnings: list[str]
    started_at: datetime
    updated_at: datetime
    elapsed_seconds: float
    can_retry_failed_windows: bool
    terminal: bool
