from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Job(Base):
    __tablename__ = 'jobs'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[str] = mapped_column(String(32), default='transcription')
    status: Mapped[str] = mapped_column(String(32), default='pending')
    source_url: Mapped[str] = mapped_column(Text)
    source_local_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_export_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_media_kind: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    export_frame_rate: Mapped[str] = mapped_column(String(8), default='25')
    subtitle_strategy: Mapped[str] = mapped_column(String(16), default='enhanced')
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    program_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    program_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider_task_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    provider_request_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    provider_task_meta: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    video_source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    video_source_local_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    video_source_export_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transcription_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    render_output_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    render_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default='idle')
    render_progress: Mapped[Optional[int]] = mapped_column(nullable=True, default=0)
    render_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processing_progress: Mapped[Optional[int]] = mapped_column(nullable=True, default=0)
    processing_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processing_metrics: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    pipeline_artifacts: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    script_context: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    script_source_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    remove_standalone_fillers: Mapped[bool] = mapped_column(default=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    normalized_segments: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    subtitle_enhanced: Mapped[bool] = mapped_column(default=False)
    subtitle_enhancement_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    speaker_mappings: Mapped[Optional[dict[str, str]]] = mapped_column(JSON, nullable=True)
    lower_third_assets: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    matched_lower_thirds: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    subtitle_backplate_asset: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
