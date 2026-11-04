from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
import mimetypes
from pathlib import Path
import re
import httpx
import time
from typing import Any, Literal, Optional
from uuid import UUID, uuid4
import shutil
import zipfile
from urllib.parse import quote, urlparse
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.schemas.job import JobDetailResponse, JobResponse, JobSummaryResponse, PipelineArtifactResponse, PipelineArtifactSummary
from app.schemas.provider_cost import ProviderCallContext
from app.schemas.render import LowerThirdAssetsResponse, LowerThirdAssetResponse, LowerThirdExportPathUpdateRequest, RenderResponse, SourceExportPathUpdateRequest, SubtitleBackplateAssetResponse
from app.schemas.speaker import SpeakerMappingRequest, SpeakerMappingResponse
from app.schemas.subtitle import SubtitleFormat, SubtitlePreviewResponse, SubtitleSpeakerOverrideRequest, SubtitleTextBatchApplyResponse, SubtitleTextBatchDeleteResponse, SubtitleTextOverrideRequest, SubtitleVariant
from app.schemas.transcription import MediaTranscriptionResponse, SubmitTranscriptionRequest
from app.schemas.voiceprint import VoiceprintAutoLearningResponse
from app.services.local_files import copy_local_file_to_library, remove_local_file, save_upload_file_locally
from app.services.lexicon_imports import build_project_script_context, save_project_script_evidence
from app.services.lower_thirds import build_lower_third_asset, match_lower_thirds
from app.services.lyrics_alignment import attach_lyrics_reference, build_lyrics_reference
from app.services.media import ASR_RECORDED_FILE_MAX_BYTES, RecognitionAudioChunk, ensure_diarization_friendly_duration, extract_recognition_audio, get_media_duration_seconds, prepare_preview_audio, prepare_recognition_audio, split_recognition_audio, validate_recognition_source_constraints
from app.services.jobs import InactiveProjectError, abort_job, append_lower_third_assets, create_job, delete_job, find_active_job_by_provider_task_id, get_job, initialize_job_v2_pipeline_run, list_job_summaries, select_subtitle_backplate_asset, sync_project_subtitle_backplate_asset, update_job_failure, update_job_normalized_segments, update_job_processing, update_job_provider, update_job_script_context as update_job_script_context_data, update_job_source_export_path, update_job_status, update_job_v2_asr_progress, update_job_video_source, update_lower_third_export_path, update_render_state, update_speaker_mappings, update_subtitle_backplate_asset
from app.services.fun_asr import FunAsrClient, FunAsrError
from app.services.oss_storage import delete_oss_object, upload_local_file_to_oss
from app.services.premiere_xml import build_premiere_xml, build_visual_aligned_subtitle_cues
from app.services.pipeline_artifact_schema import PipelineArtifactSchemaError, summarize_pipeline_artifacts
from app.services.pipeline_artifacts import build_cue_hash
from app.services.projects import ensure_legacy_project, get_project
from app.services.render_tasks import start_render_job
from app.services.speaker_memory import build_program_key, build_program_memory_suggestions, derive_program_name, get_program_speaker_memory, list_program_lower_third_assets, upsert_program_lower_third_assets, upsert_program_speaker_memory
from app.services.speakers import build_speaker_options
from app.services.script_context import extract_script_context, extract_text_from_script_bytes, merge_script_contexts
from app.services.subtitle_strategy import DEFAULT_SUBTITLE_STRATEGY, normalize_subtitle_strategy
from app.services.subtitle_cue_validator import validate_subtitle_cues
from app.services.subtitle_length_profiles import job_subtitle_length_profile
from app.services.subtitle_protected_terms import normalize_runtime_lexicon_bundle
from app.services.subtitles import apply_subtitle_speaker_overrides, apply_subtitle_text_overrides, build_script_text_correction_overrides, build_standalone_filler_delete_overrides, build_subtitle_cues, build_subtitle_length_profile, collect_subtitle_protected_terms, render_subtitle_content
from app.services.voiceprint_matching import build_voiceprint_match_lookup, match_job_voiceprints
from app.services.voiceprint_auto_learning import learn_voiceprint_candidates_for_job
from app.services.transcription_tasks import is_finalize_transcription_job_running, start_finalize_transcription_job
from app.services.user_edit_patches import build_segment_edit_patch


LEXICON_TENANT_ID = 'default'

router = APIRouter()


@router.get('/jobs', response_model=list[JobResponse])
def get_jobs() -> list[JobResponse]:
    session = SessionLocal()
    try:
        return [JobResponse.model_validate(job) for job in list_job_summaries(session)]
    finally:
        session.close()


@router.get('/jobs/summaries', response_model=list[JobSummaryResponse])
def get_job_summaries() -> list[JobSummaryResponse]:
    session = SessionLocal()
    try:
        return [
            JobSummaryResponse.model_validate(summary)
            for summary in list_job_summaries(session)
        ]
    finally:
        session.close()


@router.post('/jobs', response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def submit_transcription_job(payload: SubmitTranscriptionRequest) -> JobResponse:
    session = SessionLocal()
    client = FunAsrClient()
    file_url = str(payload.file_url)
    try:
        project_id, program_name, program_key = _resolve_locked_project_context(
            session,
            project_id=payload.project_id,
            requested_program_name=payload.program_name,
            fallback_name=file_url,
        )
        job = create_job(
            session=session,
            source_url=file_url,
            export_frame_rate=_normalize_export_frame_rate(payload.export_frame_rate),
            subtitle_strategy=_normalize_subtitle_strategy(payload.subtitle_strategy),
            project_id=project_id,
            program_key=program_key,
            program_name=program_name or None,
            script_context=extract_script_context(payload.script_text, payload.script_source_name) if payload.script_text else None,
            script_source_name=payload.script_source_name,
            remove_standalone_fillers=payload.remove_standalone_fillers,
        )
        provider_result = client.submit_transcription(
            file_url=file_url,
            language_hints=payload.language_hints,
            speaker_count=payload.speaker_count,
            vocabulary_id=payload.vocabulary_id,
            provider_context=_fun_asr_provider_context(job),
            observation_key=f'fun-asr:{job.id}:0',
            audio_seconds=job.source_duration_seconds,
        )
        provider_task_meta = [
            _build_provider_task_entry(
                task_id=provider_result['task_id'],
                request_id=provider_result.get('request_id'),
                file_url=file_url,
                offset_ms=0,
                duration_seconds=None,
                chunk_index=0,
                speaker_offset=0,
                language_hints=payload.language_hints,
                speaker_count=payload.speaker_count,
                vocabulary_id=payload.vocabulary_id,
            )
        ]
        job = update_job_provider(
            session=session,
            job_id=job.id,
            provider_task_id=provider_result['task_id'],
            provider_request_id=provider_result.get('request_id'),
            status='pending',
            provider_task_meta=provider_task_meta,
        )
        initialize_job_v2_pipeline_run(
            session,
            job,
            asr_total_chunks=len(provider_task_meta),
        )
        return JobResponse.model_validate(job)
    except FunAsrError as exc:
        if 'job' in locals():
            update_job_failure(session=session, job_id=job.id, error_message=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InactiveProjectError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        session.close()


@router.post('/video-jobs', response_model=MediaTranscriptionResponse, status_code=status.HTTP_201_CREATED)
@router.post('/media-jobs', response_model=MediaTranscriptionResponse, status_code=status.HTTP_201_CREATED)
async def submit_media_transcription_job(
    file: Optional[UploadFile] = File(default=None),
    language_hints: Optional[str] = Form(default=None),
    speaker_count: Optional[int] = Form(default=None),
    vocabulary_id: Optional[str] = Form(default=None),
    project_id: Optional[str] = Form(default=None),
    program_name: Optional[str] = Form(default=None),
    client_source_path: Optional[str] = Form(default=None),
    job_kind: Optional[str] = Form(default='transcription'),
    export_frame_rate: Optional[str] = Form(default='25'),
    subtitle_strategy: Optional[str] = Form(default=DEFAULT_SUBTITLE_STRATEGY),
    script_file: Optional[UploadFile] = File(default=None),
    script_files: Optional[list[UploadFile]] = File(default=None),
    script_text: Optional[str] = Form(default=None),
    use_project_scripts: bool = Form(default=True),
    lyrics_correction_mode: bool = Form(default=False),
    remove_standalone_fillers: bool = Form(default=False),
    subtitle_length_mode: str = Form(default='smart'),
    subtitle_max_chars: Optional[int] = Form(default=None),
    chunk_files: Optional[list[UploadFile]] = File(default=None),
    chunk_manifest: Optional[str] = Form(default=None),
) -> MediaTranscriptionResponse:
    session = SessionLocal()
    client = FunAsrClient()
    local_media_path = None
    local_audio_path = None
    local_source_library_path = None
    local_video_library_path = None
    generated_chunk_paths: list[Path] = []
    uploaded_object_keys: list[str] = []
    try:
        # 直接函数调用（测试）时 FastAPI 默认值是 Form/File 实例而非 None，统一归一化
        if not isinstance(chunk_files, list):
            chunk_files = None
        if not isinstance(chunk_manifest, str):
            chunk_manifest = None
        locked_project_id, locked_program_name, locked_program_key = _resolve_locked_project_context(
            session,
            project_id=project_id,
            requested_program_name=program_name,
            fallback_name=file.filename if file is not None else '分片音频',
        )
        oss_context = ProviderCallContext(
            project_id=locked_project_id,
            pipeline_scope='project_asset',
        )
        chunk_entries = _parse_chunk_manifest(chunk_manifest, chunk_files) if (chunk_files or chunk_manifest) else None
        is_chunk_batch = chunk_entries is not None
        if not is_chunk_batch:
            if file is None:
                raise HTTPException(status_code=400, detail='缺少音频文件：需要 file 或 chunk_files + chunk_manifest')
            local_media_path = await save_upload_file_locally(file, max_bytes=ASR_RECORDED_FILE_MAX_BYTES)
            media_kind = _detect_media_kind(file)
            validate_recognition_source_constraints(local_media_path)
        else:
            media_kind = 'audio'

        video_upload = None
        source_upload = None
        recognition_upload = None
        recognition_chunks = []
        if is_chunk_batch:
            recognition_chunks = await _save_chunk_files(chunk_files, chunk_entries, generated_chunk_paths)
            recognition_upload = upload_local_file_to_oss(
                file_path=recognition_chunks[0].path,
                prefix=settings.oss_audio_prefix,
                content_type='audio/wav',
                provider_context=oss_context,
                observation_key=f'oss:put:recognition-chunk:{recognition_chunks[0].path.stem}:0',
            )
        elif media_kind == 'video':
            local_video_library_path = copy_local_file_to_library(
                source_path=local_media_path,
                target_dir=settings.media_library_dir_path,
                original_name=file.filename,
            )
            local_audio_path = extract_recognition_audio(local_media_path)
            recognition_chunks = split_recognition_audio(local_audio_path)
            generated_chunk_paths = [chunk.path for chunk in recognition_chunks if chunk.path != local_audio_path]
            local_source_library_path = copy_local_file_to_library(
                source_path=local_audio_path,
                target_dir=settings.media_library_dir_path,
                original_name=f'{Path(file.filename or local_audio_path.name).stem}.wav',
            )
            video_upload = upload_local_file_to_oss(
                file_path=local_media_path,
                prefix=settings.oss_upload_prefix,
                content_type=file.content_type or 'video/mp4',
                provider_context=oss_context,
                observation_key=f'oss:put:video-source:{local_media_path.name}',
            )
            uploaded_object_keys.append(video_upload.object_key)
            recognition_upload = upload_local_file_to_oss(
                file_path=local_audio_path,
                prefix=settings.oss_audio_prefix,
                content_type='audio/wav',
                provider_context=oss_context,
                observation_key=f'oss:put:recognition-audio:{local_audio_path.name}',
            )
            uploaded_object_keys.append(recognition_upload.object_key)
        else:
            local_source_library_path = copy_local_file_to_library(
                source_path=local_media_path,
                target_dir=settings.media_library_dir_path,
                original_name=file.filename,
            )
            source_upload = upload_local_file_to_oss(
                file_path=local_media_path,
                prefix=settings.oss_audio_prefix,
                content_type=file.content_type or 'audio/mpeg',
                provider_context=oss_context,
                observation_key=f'oss:put:audio-source:{local_media_path.name}',
            )
            uploaded_object_keys.append(source_upload.object_key)
            local_audio_path = prepare_recognition_audio(local_media_path, media_kind='audio')
            recognition_chunks = split_recognition_audio(local_audio_path)
            generated_chunk_paths = [chunk.path for chunk in recognition_chunks if chunk.path != local_audio_path]
            recognition_upload = upload_local_file_to_oss(
                file_path=local_audio_path,
                prefix=settings.oss_audio_prefix,
                content_type='audio/wav',
                provider_context=oss_context,
                observation_key=f'oss:put:recognition-audio:{local_audio_path.name}',
            )
            uploaded_object_keys.append(recognition_upload.object_key)

        if local_audio_path:
            measured_duration_seconds = get_media_duration_seconds(local_audio_path)
        elif is_chunk_batch:
            measured_duration_seconds = max(
                (chunk.offset_ms + chunk.duration_seconds * 1000) / 1000 for chunk in recognition_chunks
            )
        else:
            measured_duration_seconds = None
        parsed_language_hints = _parse_language_hints(language_hints)
        script_context, script_source_name, script_processing_metrics, script_sources = await _resolve_script_context_inputs(
            script_file=script_file,
            script_files=script_files,
            script_text=script_text,
            lyrics_correction_mode=lyrics_correction_mode,
        )
        normalized_job_kind = _normalize_media_job_kind(job_kind)
        processing_metrics = dict(script_processing_metrics or {})
        if normalized_job_kind != 'transcript':
            processing_metrics['subtitle_length_profile'] = build_subtitle_length_profile(
                subtitle_length_mode,
                subtitle_max_chars,
            )
        if locked_project_id:
            session.rollback()
            locked_project_id, locked_program_name, locked_program_key = _resolve_locked_project_context(
                session,
                project_id=locked_project_id,
                requested_program_name=locked_program_name,
                fallback_name=file.filename if file is not None else '分片音频',
            )
            for source in script_sources:
                save_project_script_evidence(
                    session,
                    tenant_id=LEXICON_TENANT_ID,
                    project_id=locked_project_id,
                    source_name=source['source_name'],
                    text=source['text'],
                    script_context=source.get('script_context'),
                )
            if use_project_scripts:
                current_script_context = script_context
                project_script_context = build_project_script_context(
                    session,
                    tenant_id=LEXICON_TENANT_ID,
                    project_id=locked_project_id,
                )
                script_context = merge_script_contexts(
                    current_script_context,
                    project_script_context,
                )
                if current_script_context and project_script_context:
                    script_processing_metrics['script_context_current_source_precedence'] = 1
                script_source_name = str((script_context or {}).get('source_name') or '').strip() or None
                script_processing_metrics['script_source_count'] = int(
                    ((script_context or {}).get('summary') or {}).get('source_count') or 0
                )
        if script_context:
            script_context = {**script_context, 'lyrics_correction_mode': bool(lyrics_correction_mode)}
        script_processing_metrics['lyrics_correction_enabled'] = 1 if lyrics_correction_mode else 0
        script_processing_metrics['lyrics_reference_count'] = len(
            (script_context or {}).get('lyrics_references') or []
        )
        processing_metrics.update(script_processing_metrics)
        job = create_job(
            session=session,
            source_url=(source_upload or recognition_upload).public_url,
            kind=normalized_job_kind,
            source_local_path=str(local_source_library_path) if local_source_library_path else None,
            source_export_path=_normalize_optional_export_path(client_source_path),
            source_media_kind=media_kind,
            export_frame_rate=_normalize_export_frame_rate(export_frame_rate),
            subtitle_strategy=_normalize_subtitle_strategy(subtitle_strategy),
            project_id=locked_project_id,
            program_key=locked_program_key,
            program_name=locked_program_name or None,
            source_duration_seconds=measured_duration_seconds,
            processing_metrics=processing_metrics or None,
            script_context=script_context,
            script_source_name=script_source_name,
            remove_standalone_fillers=remove_standalone_fillers,
        )
        if video_upload:
            job = update_job_video_source(
                session=session,
                job_id=job.id,
                video_source_url=video_upload.public_url,
                video_source_local_path=str(local_video_library_path) if local_video_library_path else None,
                video_source_export_path=_normalize_optional_export_path(client_source_path),
            )
        provider_task_meta = []
        provider_context = _fun_asr_provider_context(job)
        vocabulary = _parse_vocabulary_id(vocabulary_id)
        for chunk in recognition_chunks:
            chunk_upload = recognition_upload
            if chunk.path != local_audio_path and not (is_chunk_batch and chunk.index == 0):
                chunk_upload = upload_local_file_to_oss(
                    file_path=chunk.path,
                    prefix=settings.oss_audio_prefix,
                    content_type='audio/wav',
                    provider_context=provider_context,
                    observation_key=f'oss:put:recognition-chunk:{job.id}:{chunk.index}',
                )
            provider_result = client.submit_transcription(
                file_url=chunk_upload.public_url,
                language_hints=parsed_language_hints,
                speaker_count=speaker_count,
                vocabulary_id=vocabulary,
                provider_context=provider_context,
                observation_key=f'fun-asr:{job.id}:{chunk.index}',
                audio_seconds=chunk.duration_seconds,
            )
            provider_task_meta.append(
                _build_provider_task_entry(
                    task_id=provider_result['task_id'],
                    request_id=provider_result.get('request_id'),
                    file_url=chunk_upload.public_url,
                    offset_ms=chunk.offset_ms,
                    duration_seconds=chunk.duration_seconds,
                    chunk_index=chunk.index,
                    speaker_offset=chunk.index * 1000,
                    language_hints=parsed_language_hints,
                    speaker_count=speaker_count,
                    vocabulary_id=vocabulary,
                )
            )
        first_provider = provider_task_meta[0]
        job = update_job_provider(
            session=session,
            job_id=job.id,
            provider_task_id=first_provider['task_id'],
            provider_request_id=first_provider.get('request_id'),
            status='pending',
            provider_task_meta=provider_task_meta,
        )
        initialize_job_v2_pipeline_run(
            session,
            job,
            asr_total_chunks=len(provider_task_meta),
        )
        return MediaTranscriptionResponse(
            job=JobResponse.model_validate(job),
            video_upload=video_upload,
            audio_upload=source_upload or recognition_upload,
        )
    except FunAsrError as exc:
        if 'job' in locals():
            update_job_failure(session=session, job_id=job.id, error_message=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InactiveProjectError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        session.close()
        if 'job' not in locals():
            remove_local_file(local_source_library_path)
            remove_local_file(local_video_library_path)
            for object_key in uploaded_object_keys:
                try:
                    delete_oss_object(object_key)
                except Exception:
                    pass
        remove_local_file(local_media_path)
        remove_local_file(local_audio_path)
        for chunk_path in generated_chunk_paths:
            remove_local_file(chunk_path)


def _parse_chunk_manifest(raw_manifest: Optional[str], chunk_files: Optional[list[UploadFile]]) -> Optional[list[dict[str, Any]]]:
    if raw_manifest is None:
        if chunk_files:
            raise HTTPException(status_code=400, detail='chunk_files 必须与 chunk_manifest 一起提供')
        return None
    if not chunk_files:
        raise HTTPException(status_code=400, detail='chunk_manifest 必须与 chunk_files 一起提供')
    try:
        entries = json.loads(raw_manifest)
    except ValueError:
        raise HTTPException(status_code=400, detail='chunk_manifest 必须是合法 JSON 数组')
    if not isinstance(entries, list) or not entries:
        raise HTTPException(status_code=400, detail='chunk_manifest 必须是非空数组')
    if len(entries) != len(chunk_files):
        raise HTTPException(status_code=400, detail='chunk_manifest 数量与 chunk_files 不一致')
    names = {Path(upload.filename or '').name for upload in chunk_files}
    parsed: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get('file'):
            raise HTTPException(status_code=400, detail='chunk_manifest 每项必须包含 file 文件名')
        filename = str(entry['file'])
        if filename not in names:
            raise HTTPException(status_code=400, detail=f'chunk_manifest 引用了未上传的文件：{filename}')
        try:
            offset_ms = int(entry['offset_ms'])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f'chunk_manifest 的 offset_ms 必须是非负整数：{filename}')
        if offset_ms < 0:
            raise HTTPException(status_code=400, detail=f'chunk_manifest 的 offset_ms 必须是非负整数：{filename}')
        parsed.append({'filename': filename, 'offset_ms': offset_ms})
    parsed.sort(key=lambda item: item['offset_ms'])
    return parsed


async def _save_chunk_files(
    chunk_files: list[UploadFile],
    chunk_entries: list[dict[str, Any]],
    saved_paths: list[Path],
) -> list[RecognitionAudioChunk]:
    by_name = {Path(upload.filename or '').name: upload for upload in chunk_files}
    chunks: list[RecognitionAudioChunk] = []
    for index, entry in enumerate(chunk_entries):
        chunk_path = await save_upload_file_locally(
            by_name[entry['filename']],
            max_bytes=ASR_RECORDED_FILE_MAX_BYTES,
        )
        saved_paths.append(chunk_path)
        ensure_diarization_friendly_duration(chunk_path)
        duration_seconds = get_media_duration_seconds(chunk_path) or 0.0
        if duration_seconds <= 0:
            raise HTTPException(status_code=400, detail=f'分片无法读取时长：{chunk_path.name}')
        chunks.append(
            RecognitionAudioChunk(
                index=index,
                path=chunk_path,
                offset_ms=entry['offset_ms'],
                duration_seconds=duration_seconds,
            )
        )
    return chunks


def _resolve_locked_project_context(
    session: Session,
    *,
    project_id: Optional[str],
    requested_program_name: Optional[str],
    fallback_name: Optional[str],
) -> tuple[Optional[str], str, str]:
    normalized_project_id = str(project_id or '').strip()
    if normalized_project_id:
        project = get_project(session, normalized_project_id)
        if not project:
            raise HTTPException(status_code=404, detail='项目不存在，请返回项目列表重新选择')
        if project.status != 'active':
            raise HTTPException(status_code=409, detail='项目已归档，不能继续创建任务')
        return (
            project.id,
            project.name,
            project.legacy_program_key or build_program_key(project.name),
        )
    derived_program_name = derive_program_name(requested_program_name, fallback_name)
    project = ensure_legacy_project(session, derived_program_name)
    if project.status != 'active':
        raise HTTPException(status_code=409, detail='项目已归档，不能继续创建任务')
    return (
        project.id,
        project.name,
        project.legacy_program_key or build_program_key(project.name),
    )


@router.get('/jobs/{job_id}', response_model=JobDetailResponse)
def get_transcription_job(job_id: UUID) -> JobDetailResponse:
    session = SessionLocal()
    try:
        job = get_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail='任务不存在')
        return JobDetailResponse.model_validate(job)
    finally:
        session.close()


@router.get('/jobs/{job_id}/pipeline-artifacts', response_model=PipelineArtifactResponse)
def get_job_pipeline_artifacts(job_id: UUID) -> PipelineArtifactResponse:
    session = SessionLocal()
    try:
        job = get_job(session, str(job_id))
        if not job:
            raise HTTPException(status_code=404, detail='任务不存在')
        artifacts = dict(job.pipeline_artifacts or {})
        try:
            summary = summarize_pipeline_artifacts(artifacts)
        except PipelineArtifactSchemaError as exc:
            raise HTTPException(status_code=409, detail=f'任务缺少有效的 pipeline artifacts：{exc}') from exc
        return PipelineArtifactResponse(
            summary=PipelineArtifactSummary(**summary),
            artifacts=artifacts,
        )
    finally:
        session.close()


@router.post('/jobs/{job_id}/script-context', response_model=JobDetailResponse)
async def update_job_script_context(
    job_id: UUID,
    script_file: Optional[UploadFile] = File(default=None),
    script_files: Optional[list[UploadFile]] = File(default=None),
    script_text: Optional[str] = Form(default=None),
    lyrics_correction_mode: bool = Form(default=False),
) -> JobDetailResponse:
    session = SessionLocal()
    try:
        job = get_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail='任务不存在')
        script_context, script_source_name, script_processing_metrics, script_sources = await _resolve_script_context_inputs(
            script_file=script_file,
            script_files=script_files,
            script_text=script_text,
            lyrics_correction_mode=lyrics_correction_mode,
        )
        if not script_context:
            detail = script_processing_metrics.get('script_context_error') or '请上传或粘贴可识别的台本/唱词内容'
            raise HTTPException(status_code=400, detail=detail)
        if job.project_id:
            for source in script_sources:
                save_project_script_evidence(
                    session,
                    tenant_id=LEXICON_TENANT_ID,
                    project_id=str(job.project_id),
                    source_name=source['source_name'],
                    text=source['text'],
                    script_context=source.get('script_context'),
                )
        script_context = merge_script_contexts(script_context, job.script_context)
        if script_context:
            script_context = {**script_context, 'lyrics_correction_mode': bool(lyrics_correction_mode)}
        script_source_name = str((script_context or {}).get('source_name') or script_source_name or '').strip() or None
        job = update_job_script_context_data(
            session=session,
            job_id=job.id,
            script_context=script_context,
            script_source_name=script_source_name,
            processing_metrics=script_processing_metrics or None,
        )
        return JobDetailResponse.model_validate(job)
    finally:
        session.close()


@router.delete('/jobs/{job_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_transcription_job(job_id: UUID) -> Response:
    session = SessionLocal()
    try:
        try:
            delete_job(session, str(job_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail='任务不存在') from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()


@router.post('/jobs/{job_id}/abort', response_model=JobDetailResponse)
def abort_transcription_job(job_id: UUID) -> JobDetailResponse:
    session = SessionLocal()
    client = FunAsrClient()
    try:
        job = get_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail='任务不存在')

        if job.status in {'finalizing', 'subtitle_enhancing'}:
            job = abort_job(session, str(job_id), message='任务已中止。当前已进入本地字幕整理阶段，已停止继续入库结果')
            return JobDetailResponse.model_validate(job)

        provider_task_meta = _resolve_provider_task_meta(job)
        cancelled_pending_count = 0
        running_or_finished_count = 0
        for entry in provider_task_meta:
            task_id = str(entry.get('task_id') or '').strip()
            if not task_id:
                continue
            try:
                provider_output = client.query_task(task_id)
                task_status = str(provider_output.get('output', {}).get('task_status') or '').upper()
                if task_status == 'PENDING':
                    client.cancel_task(task_id)
                    cancelled_pending_count += 1
                else:
                    running_or_finished_count += 1
            except FunAsrError:
                running_or_finished_count += 1

        message = _build_abort_message(cancelled_pending_count, running_or_finished_count)
        job = abort_job(session, str(job_id), message=message)
        return JobDetailResponse.model_validate(job)
    except FunAsrError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail='任务不存在') from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        session.close()


@router.get('/jobs/{job_id}/source-media')
def get_job_source_media(job_id: UUID):
    session = SessionLocal()
    try:
        job = get_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail='任务不存在')

        source_path = job.video_source_local_path or job.source_local_path
        source_url = job.video_source_url or job.source_url
        if source_path:
            local_file = Path(source_path)
            if not local_file.exists():
                raise HTTPException(status_code=404, detail='媒体文件不存在')
            if not job.video_source_local_path and (job.source_media_kind or '').lower() == 'audio':
                preview_file = prepare_preview_audio(local_file, cache_key=f'job-{job.id}', media_kind='audio')
                return FileResponse(path=preview_file, media_type='audio/wav')
            media_type = mimetypes.guess_type(local_file.name)[0] or 'application/octet-stream'
            return FileResponse(path=local_file, media_type=media_type)
        if source_url:
            return RedirectResponse(url=source_url)
        raise HTTPException(status_code=400, detail='当前任务缺少可试听媒体')
    finally:
        session.close()


@router.get('/jobs/{job_id}/lower-thirds/{object_key}/file')
def get_job_lower_third_file(job_id: UUID, object_key: str):
    session = SessionLocal()
    try:
        job = get_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail='任务不存在')
        asset = next((item for item in (job.lower_third_assets or []) if item.get('object_key') == object_key), None)
        if not asset:
            raise HTTPException(status_code=404, detail='人名条不存在')

        local_path = str(asset.get('local_path') or '').strip()
        if local_path:
            local_file = Path(local_path)
            if not local_file.exists():
                raise HTTPException(status_code=404, detail='人名条文件不存在')
            media_type = mimetypes.guess_type(local_file.name)[0] or 'application/octet-stream'
            return FileResponse(path=local_file, media_type=media_type)

        public_url = str(asset.get('public_url') or '').strip()
        if public_url:
            content, media_type = _fetch_remote_asset_bytes(public_url, asset.get('file_name'))
            return Response(content=content, media_type=media_type)
        raise HTTPException(status_code=404, detail='人名条文件不存在')
    finally:
        session.close()


@router.get('/jobs/{job_id}/subtitle-backplate/file')
def get_job_subtitle_backplate_file(job_id: UUID, role: Optional[str] = Query(default=None)):
    session = SessionLocal()
    try:
        job = get_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail='任务不存在')
        asset = _select_subtitle_backplate_asset_for_role(
            job.subtitle_backplate_asset or None,
            _normalize_subtitle_backplate_role(role),
        )
        if not asset:
            raise HTTPException(status_code=404, detail='唱词条底图不存在')

        local_path = str(asset.get('local_path') or '').strip()
        if local_path:
            local_file = Path(local_path)
            if not local_file.exists():
                raise HTTPException(status_code=404, detail='唱词条底图文件不存在')
            media_type = mimetypes.guess_type(local_file.name)[0] or 'application/octet-stream'
            return FileResponse(path=local_file, media_type=media_type)

        public_url = str(asset.get('public_url') or '').strip()
        if public_url:
            content, media_type = _fetch_remote_asset_bytes(public_url, asset.get('file_name'))
            return Response(content=content, media_type=media_type)
        raise HTTPException(status_code=404, detail='唱词条底图文件不存在')
    finally:
        session.close()


@router.get('/jobs/{job_id}/speakers', response_model=SpeakerMappingResponse)
def get_job_speakers(job_id: UUID) -> SpeakerMappingResponse:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        resolved_program_name = _resolve_job_program_name(job)
        mappings = dict(job.speaker_mappings or {})
        voiceprint_matches = build_voiceprint_match_lookup(session, job.id)
        speakers = build_speaker_options(job.normalized_segments or [], mappings, voiceprint_matches)
        memory = get_program_speaker_memory(
            session,
            job.program_key or resolved_program_name,
            project_id=job.project_id,
        )
        return SpeakerMappingResponse(
            mappings=mappings,
            speakers=speakers,
            program_name=resolved_program_name,
            memory_suggestions=build_program_memory_suggestions(memory, mappings),
        )
    finally:
        session.close()


@router.put('/jobs/{job_id}/speakers', response_model=SpeakerMappingResponse)
def put_job_speakers(
    job_id: UUID,
    payload: SpeakerMappingRequest,
    learn_voiceprints: bool = Query(default=False),
) -> SpeakerMappingResponse:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        resolved_program_name = _resolve_job_program_name(job)
        if resolved_program_name and not job.program_name:
            job.program_name = resolved_program_name
            job.program_key = build_program_key(resolved_program_name)
            session.commit()
            session.refresh(job)
        mappings = {item.speaker_key: item.speaker_name.strip() for item in payload.mappings}
        job = update_speaker_mappings(session, job.id, mappings)
        memory = upsert_program_speaker_memory(
            session,
            resolved_program_name,
            (job.speaker_mappings or {}).values(),
            project_id=job.project_id,
        )
        voiceprint_learning = _learn_voiceprint_candidates_if_requested(session, job, learn_voiceprints)
        voiceprint_matches = build_voiceprint_match_lookup(session, job.id)
        speakers = build_speaker_options(job.normalized_segments or [], job.speaker_mappings or {}, voiceprint_matches)
        return SpeakerMappingResponse(
            mappings=job.speaker_mappings or {},
            speakers=speakers,
            program_name=resolved_program_name,
            memory_suggestions=build_program_memory_suggestions(memory, job.speaker_mappings or {}),
            voiceprint_learning=voiceprint_learning,
        )
    finally:
        session.close()


@router.post('/jobs/{job_id}/lower-thirds', response_model=LowerThirdAssetsResponse)
async def upload_job_lower_thirds(
    job_id: UUID,
    files: list[UploadFile] = File(...),
    client_paths: Optional[str] = Form(default=None),
) -> LowerThirdAssetsResponse:
    session = SessionLocal()
    local_paths = []
    try:
        job = _require_completed_job(session, job_id)
        assets = []
        resolved_client_paths = _parse_client_paths_payload(client_paths)
        program_name = _resolve_job_program_name(job)
        for index, file in enumerate(files):
            local_path = await save_upload_file_locally(file)
            local_paths.append(local_path)
            library_path = copy_local_file_to_library(
                source_path=local_path,
                target_dir=settings.lower_third_library_dir_path,
                original_name=file.filename,
            )
            asset = build_lower_third_asset(
                file.filename or local_path.name,
                library_path.name,
                public_url=None,
                local_path=str(library_path),
                export_path=_resolve_client_export_path(
                    resolved_client_paths,
                    index,
                    file.filename or local_path.name,
                ),
            )
            if program_name:
                asset['library_scope'] = 'program'
            assets.append(asset)
        job = append_lower_third_assets(session, job.id, assets)
        if program_name:
            upsert_program_lower_third_assets(
                session,
                program_name,
                assets,
                project_id=job.project_id,
            )
        return LowerThirdAssetsResponse(
            assets=[LowerThirdAssetResponse.model_validate(asset) for asset in (job.lower_third_assets or [])]
        )
    finally:
        session.close()
        for path in local_paths:
            remove_local_file(path)


@router.post('/jobs/{job_id}/lower-thirds/from-project-library', response_model=LowerThirdAssetsResponse)
def load_job_lower_thirds_from_project_library(job_id: UUID) -> LowerThirdAssetsResponse:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        program_name = _resolve_job_program_name(job)
        assets = list_program_lower_third_assets(
            session,
            program_name,
            project_id=job.project_id,
        )
        if not assets:
            raise HTTPException(status_code=404, detail='当前项目还没有保存过人名条')
        job = append_lower_third_assets(session, job.id, assets)
        return LowerThirdAssetsResponse(
            assets=[LowerThirdAssetResponse.model_validate(asset) for asset in (job.lower_third_assets or [])]
        )
    finally:
        session.close()


@router.put('/jobs/{job_id}/lower-thirds/export-path', response_model=LowerThirdAssetsResponse)
def put_job_lower_third_export_path(job_id: UUID, payload: LowerThirdExportPathUpdateRequest) -> LowerThirdAssetsResponse:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        try:
            job = update_lower_third_export_path(session, job.id, payload.object_key, payload.export_path)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return LowerThirdAssetsResponse(
            assets=[LowerThirdAssetResponse.model_validate(asset) for asset in (job.lower_third_assets or [])]
        )
    finally:
        session.close()


@router.post('/jobs/{job_id}/subtitle-backplate', response_model=SubtitleBackplateAssetResponse)
async def upload_job_subtitle_backplate(
    job_id: UUID,
    file: UploadFile = File(...),
    client_path: Optional[str] = Form(default=None),
    role: Optional[str] = Form(default=None),
) -> SubtitleBackplateAssetResponse:
    session = SessionLocal()
    local_path = None
    try:
        job = _require_completed_job(session, job_id)
        local_path = await save_upload_file_locally(file)
        target_dir = settings.lower_third_library_dir_path
        if job.project_id:
            safe_project_id = re.sub(r'[^A-Za-z0-9_-]', '_', str(job.project_id))
            target_dir = target_dir / 'projects' / safe_project_id / 'subtitle-backplates'
        library_path = copy_local_file_to_library(
            source_path=local_path,
            target_dir=target_dir,
            original_name=file.filename,
        )
        asset = {
            'asset_id': str(uuid4()),
            'file_name': Path(file.filename or local_path.name).name,
            'object_key': _subtitle_backplate_object_key(library_path),
            'local_path': str(library_path),
            'sha256': hashlib.sha256(library_path.read_bytes()).hexdigest(),
        }
        export_path = _normalize_optional_export_path(client_path)
        if export_path:
            asset['export_path'] = export_path
        normalized_role = _normalize_subtitle_backplate_role(role)
        if normalized_role:
            asset['role'] = normalized_role
        if job.project_id:
            asset['library_scope'] = 'project'
            _, job = sync_project_subtitle_backplate_asset(
                session,
                project_id=job.project_id,
                job_id=job.id,
                role=normalized_role,
                asset=asset,
            )
        else:
            job = update_subtitle_backplate_asset(session, job.id, asset, role=normalized_role)
        stored_asset = _select_subtitle_backplate_asset_for_role(job.subtitle_backplate_asset or asset, normalized_role)
        return SubtitleBackplateAssetResponse.model_validate(stored_asset or asset)
    finally:
        session.close()
        remove_local_file(local_path)


@router.delete('/jobs/{job_id}/subtitle-backplate', status_code=status.HTTP_204_NO_CONTENT)
def delete_job_subtitle_backplate(job_id: UUID, role: Optional[str] = Query(default=None)) -> Response:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        update_subtitle_backplate_asset(session, job.id, None, role=_normalize_subtitle_backplate_role(role))
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()


@router.put('/jobs/{job_id}/source-export-path', response_model=JobDetailResponse)
def put_job_source_export_path(job_id: UUID, payload: SourceExportPathUpdateRequest) -> JobDetailResponse:
    session = SessionLocal()
    try:
        job = get_job(session, str(job_id))
        if not job:
            raise HTTPException(status_code=404, detail='任务不存在')
        job = update_job_source_export_path(session, job.id, payload.export_path)
        return JobDetailResponse.model_validate(job)
    finally:
        session.close()


@router.get('/jobs/{job_id}/subtitles/preview', response_model=SubtitlePreviewResponse)
def preview_job_subtitles(
    job_id: UUID,
    subtitle_format: SubtitleFormat = Query(default='srt', alias='format'),
    variant: SubtitleVariant = Query(default='full'),
) -> SubtitlePreviewResponse:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        cues = _build_job_subtitle_cues(job)
        content = render_subtitle_content(cues, subtitle_format=subtitle_format, variant=variant)
        return SubtitlePreviewResponse(
            variant=variant,
            format=subtitle_format,
            content=content,
            cues=cues,
            cue_hash=build_cue_hash(cues),
        )
    finally:
        session.close()


@router.put('/jobs/{job_id}/subtitles/speakers', response_model=SubtitlePreviewResponse)
def put_job_subtitle_speakers(
    job_id: UUID,
    payload: SubtitleSpeakerOverrideRequest,
    learn_voiceprints: bool = Query(default=False),
) -> SubtitlePreviewResponse:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        before_segments = list(job.normalized_segments or [])
        runtime_lexicon_bundle = _job_runtime_lexicon_bundle_snapshot(job)
        profile = job_subtitle_length_profile(job)
        updated_segments = apply_subtitle_speaker_overrides(
            normalized_segments=before_segments,
            overrides=[item.model_dump() for item in payload.cues],
            speaker_mappings=job.speaker_mappings or {},
            script_context=job.script_context,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            subtitle_length_mode=profile['mode'],
            subtitle_max_chars=profile['max_chars'],
        )
        edit_patch = build_segment_edit_patch(before_segments, updated_segments, source='subtitle_speaker_override')
        job = update_job_normalized_segments(session, job.id, updated_segments, edit_patch=edit_patch)
        voiceprint_learning = _learn_voiceprint_candidates_if_requested(session, job, learn_voiceprints)
        cues = _build_job_subtitle_cues(job)
        content = render_subtitle_content(cues, subtitle_format='srt', variant='full')
        return SubtitlePreviewResponse(
            variant='full',
            format='srt',
            content=content,
            cues=cues,
            voiceprint_learning=voiceprint_learning,
        )
    finally:
        session.close()


@router.put('/jobs/{job_id}/subtitles/text', response_model=SubtitlePreviewResponse)
def put_job_subtitle_text(job_id: UUID, payload: SubtitleTextOverrideRequest) -> SubtitlePreviewResponse:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        before_segments = list(job.normalized_segments or [])
        runtime_lexicon_bundle = _job_runtime_lexicon_bundle_snapshot(job)
        profile = job_subtitle_length_profile(job)
        updated_segments = apply_subtitle_text_overrides(
            normalized_segments=before_segments,
            overrides=[item.model_dump() for item in payload.cues],
            speaker_mappings=job.speaker_mappings or {},
            script_context=job.script_context,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            subtitle_length_mode=profile['mode'],
            subtitle_max_chars=profile['max_chars'],
        )
        edit_patch = build_segment_edit_patch(before_segments, updated_segments, source='subtitle_text_override')
        job = update_job_normalized_segments(session, job.id, updated_segments, edit_patch=edit_patch)
        cues = _build_job_subtitle_cues(job)
        content = render_subtitle_content(cues, subtitle_format='srt', variant='full')
        return SubtitlePreviewResponse(variant='full', format='srt', content=content, cues=cues)
    finally:
        session.close()


@router.post('/jobs/{job_id}/subtitles/text/delete-fillers', response_model=SubtitleTextBatchDeleteResponse)
def delete_job_subtitle_fillers(job_id: UUID) -> SubtitleTextBatchDeleteResponse:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        before_segments = list(job.normalized_segments or [])
        runtime_lexicon_bundle = _job_runtime_lexicon_bundle_snapshot(job)
        profile = job_subtitle_length_profile(job)
        overrides = build_standalone_filler_delete_overrides(
            normalized_segments=before_segments,
            speaker_mappings=job.speaker_mappings or {},
            script_context=job.script_context,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            subtitle_length_mode=profile['mode'],
            subtitle_max_chars=profile['max_chars'],
        )
        if overrides:
            updated_segments = apply_subtitle_text_overrides(
                normalized_segments=before_segments,
                overrides=overrides,
                speaker_mappings=job.speaker_mappings or {},
                script_context=job.script_context,
                runtime_lexicon_bundle=runtime_lexicon_bundle,
                subtitle_length_mode=profile['mode'],
                subtitle_max_chars=profile['max_chars'],
            )
            edit_patch = build_segment_edit_patch(before_segments, updated_segments, source='standalone_filler_delete')
            job = update_job_normalized_segments(session, job.id, updated_segments, edit_patch=edit_patch)
        cues = _build_job_subtitle_cues(job)
        content = render_subtitle_content(cues, subtitle_format='srt', variant='full')
        return SubtitleTextBatchDeleteResponse(
            variant='full',
            format='srt',
            content=content,
            cues=cues,
            deleted_count=len(overrides),
        )
    finally:
        session.close()


@router.post('/jobs/{job_id}/subtitles/text/apply-script-context', response_model=SubtitleTextBatchApplyResponse)
def apply_job_script_text_corrections(job_id: UUID) -> SubtitleTextBatchApplyResponse:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        if not job.script_context:
            raise HTTPException(status_code=400, detail='请先在校对页上传或粘贴台本')
        before_segments = list(job.normalized_segments or [])
        runtime_lexicon_bundle = _job_runtime_lexicon_bundle_snapshot(job)
        profile = job_subtitle_length_profile(job)
        overrides = []
        if job.subtitle_strategy != 'evidence':
            overrides = build_script_text_correction_overrides(
                normalized_segments=before_segments,
                script_context=job.script_context or {},
                speaker_mappings=job.speaker_mappings or {},
                runtime_lexicon_bundle=runtime_lexicon_bundle,
                subtitle_length_mode=profile['mode'],
                subtitle_max_chars=profile['max_chars'],
            )
        if overrides:
            updated_segments = apply_subtitle_text_overrides(
                normalized_segments=before_segments,
                overrides=overrides,
                speaker_mappings=job.speaker_mappings or {},
                script_context=job.script_context,
                runtime_lexicon_bundle=runtime_lexicon_bundle,
                subtitle_length_mode=profile['mode'],
                subtitle_max_chars=profile['max_chars'],
            )
            edit_patch = build_segment_edit_patch(before_segments, updated_segments, source='script_text_correction')
            job = update_job_normalized_segments(session, job.id, updated_segments, edit_patch=edit_patch)
        cues = _build_job_subtitle_cues(job)
        content = render_subtitle_content(cues, subtitle_format='srt', variant='full')
        return SubtitleTextBatchApplyResponse(
            variant='full',
            format='srt',
            content=content,
            cues=cues,
            updated_count=len(overrides),
        )
    finally:
        session.close()


@router.post('/jobs/{job_id}/render', response_model=RenderResponse)
def render_job_video(job_id: UUID) -> RenderResponse:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        if not job.video_source_url:
            raise HTTPException(status_code=400, detail='当前任务缺少原视频地址')
        if not job.lower_third_assets:
            raise HTTPException(status_code=400, detail='当前任务还没有上传人名条图片')
        cues = _build_job_subtitle_cues(job)
        _assert_exportable_subtitle_cues(job, cues)
        speaker_names = sorted({cue.speaker_name for cue in cues if cue.speaker_name})
        matched_assets, missing_speakers = match_lower_thirds(speaker_names, job.lower_third_assets or [])
        if job.render_status == 'running':
            return RenderResponse(
                render_status=job.render_status or 'running',
                render_progress=job.render_progress or 0,
                render_message=job.render_message,
                render_output_url=job.render_output_url,
                matched_lower_thirds=job.matched_lower_thirds or matched_assets,
                missing_speakers=missing_speakers,
            )
        job = update_render_state(session, job.id, 'queued', 0, '渲染任务已进入队列')
        start_render_job(job.id)
        return RenderResponse(
            render_status=job.render_status or 'queued',
            render_progress=job.render_progress or 0,
            render_message=job.render_message,
            render_output_url=job.render_output_url,
            matched_lower_thirds=job.matched_lower_thirds or matched_assets,
            missing_speakers=missing_speakers,
        )
    finally:
        session.close()


@router.get('/jobs/{job_id}/render-status', response_model=RenderResponse)
def get_render_job_status(job_id: UUID) -> RenderResponse:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        cues = _build_job_subtitle_cues(job)
        speaker_names = sorted({cue.speaker_name for cue in cues if cue.speaker_name})
        matched_assets, missing_speakers = match_lower_thirds(speaker_names, job.lower_third_assets or [])
        return RenderResponse(
            render_status=job.render_status or 'idle',
            render_progress=job.render_progress or 0,
            render_message=job.render_message,
            render_output_url=job.render_output_url,
            matched_lower_thirds=job.matched_lower_thirds or matched_assets,
            missing_speakers=missing_speakers,
        )
    finally:
        session.close()


@router.get('/jobs/{job_id}/subtitles/download')
def download_job_subtitles(
    job_id: UUID,
    subtitle_format: SubtitleFormat = Query(default='srt', alias='format'),
    variant: SubtitleVariant = Query(default='full'),
) -> Response:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        export_frame_rate = _resolve_job_export_frame_rate(job, None)
        cues = _build_job_subtitle_cues(job, export_frame_rate=export_frame_rate)
        export_cues = build_visual_aligned_subtitle_cues(cues, frame_rate=export_frame_rate)
        _assert_exportable_subtitle_cues(job, export_cues)
        content = render_subtitle_content(export_cues, subtitle_format=subtitle_format, variant=variant)
        output_stem = _resolve_job_output_stem(job)
        filename = f'{output_stem}-{variant}.{subtitle_format}'
        media_type = 'text/vtt; charset=utf-8' if subtitle_format == 'vtt' else 'application/x-subrip; charset=utf-8'
        headers = _build_attachment_headers(filename)
        headers['X-Speaker-Cue-Hash'] = build_cue_hash(cues)
        return Response(content=content, media_type=media_type, headers=headers)
    finally:
        session.close()


@router.get('/jobs/{job_id}/transcript/download')
def download_job_transcript(
    job_id: UUID,
    transcript_format: Literal['txt', 'srt', 'docx'] = Query(default='txt', alias='format'),
) -> Response:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        lines = _build_transcript_lines(job)
        output_stem = _resolve_job_output_stem(job)
        filename = f'{output_stem}-transcript.{transcript_format}'
        headers = _build_attachment_headers(filename)
        if transcript_format == 'docx':
            return Response(
                content=_build_transcript_docx(lines),
                media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                headers=headers,
            )
        if transcript_format == 'srt':
            return Response(
                content=_render_transcript_srt(lines),
                media_type='application/x-subrip; charset=utf-8',
                headers=headers,
            )
        return Response(
            content=_render_transcript_txt(lines),
            media_type='text/plain; charset=utf-8',
            headers=headers,
        )
    finally:
        session.close()


@router.get('/jobs/{job_id}/premiere-xml/download')
def download_job_premiere_xml(
    job_id: UUID,
    frame_rate: Optional[Literal['25', '29.97', '50']] = Query(default=None),
    subtitle_mode: Literal['sidecar', 'text_track', 'graphicandtype'] = Query(default='sidecar'),
) -> Response:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        export_frame_rate = _resolve_job_export_frame_rate(job, frame_rate)
        cues = _build_job_subtitle_cues(job, export_frame_rate=export_frame_rate)
        _assert_exportable_subtitle_cues(job, cues)
        content = _build_job_premiere_xml_content(job, cues, frame_rate=export_frame_rate, subtitle_mode=subtitle_mode)
        filename = f'{_resolve_job_output_stem(job)}-premiere.xml'
        headers = _build_attachment_headers(filename)
        headers['X-Speaker-Cue-Hash'] = build_cue_hash(cues)
        return Response(content=content, media_type='application/xml; charset=utf-8', headers=headers)
    finally:
        session.close()


@router.get('/jobs/{job_id}/premiere-package/download')
def download_job_premiere_package(job_id: UUID, frame_rate: Optional[Literal['25', '29.97', '50']] = Query(default=None)) -> Response:
    session = SessionLocal()
    try:
        job = _require_completed_job(session, job_id)
        export_frame_rate = _resolve_job_export_frame_rate(job, frame_rate)
        cues = _build_job_subtitle_cues(job, export_frame_rate=export_frame_rate)
        _assert_exportable_subtitle_cues(job, cues)
        xml_content = _build_job_premiere_xml_content(job, cues, frame_rate=export_frame_rate)
        srt_cues = build_visual_aligned_subtitle_cues(cues, frame_rate=export_frame_rate)
        _assert_exportable_subtitle_cues(job, srt_cues)
        full_srt_content = render_subtitle_content(srt_cues, subtitle_format='srt', variant='full')
        clean_srt_content = render_subtitle_content(srt_cues, subtitle_format='srt', variant='clean')
        archive = _build_premiere_package_archive(
            job_id=str(job.id),
            output_stem=_resolve_job_output_stem(job),
            xml_content=xml_content,
            clean_srt_content=clean_srt_content,
            full_srt_content=full_srt_content,
            frame_rate=export_frame_rate,
        )
        filename = f'{_resolve_job_output_stem(job)}-premiere-package.zip'
        headers = _build_attachment_headers(filename)
        return Response(content=archive, media_type='application/zip', headers=headers)
    finally:
        session.close()


def _parse_language_hints(raw_value: Optional[str]) -> Optional[list[str]]:
    if not raw_value:
        return None
    hints = [item.strip() for item in raw_value.split(',') if item.strip()]
    if not hints:
        return None
    return [hints[0]]


def _parse_vocabulary_id(raw_value: Optional[str]) -> Optional[str]:
    if not raw_value:
        return None
    value = raw_value.strip()
    return value or None


async def _resolve_script_context_inputs(
    *,
    script_file: Optional[UploadFile] = None,
    script_files: Optional[list[UploadFile]] = None,
    script_text: Optional[str] = None,
    lyrics_correction_mode: bool = False,
) -> tuple[Optional[dict[str, Any]], Optional[str], dict[str, Any], list[dict[str, Any]]]:
    contexts: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}

    files = ([script_file] if getattr(script_file, 'filename', None) else []) + (
        script_files if isinstance(script_files, list) else []
    )
    for uploaded_file in files:
        if not getattr(uploaded_file, 'filename', None):
            continue
        source_name = str(uploaded_file.filename)
        try:
            payload = await uploaded_file.read()
            if payload:
                text = extract_text_from_script_bytes(payload, uploaded_file.filename)
                source_context = extract_script_context(text, source_name=source_name)
                if lyrics_correction_mode:
                    source_context = attach_lyrics_reference(
                        source_context,
                        build_lyrics_reference(
                            source_name=source_name,
                            extracted_text=text,
                            raw_payload=payload,
                        ),
                    )
                contexts.append(source_context)
                sources.append({
                    'source_name': source_name,
                    'text': text,
                    'script_context': source_context,
                })
        except Exception as exc:
            metrics['script_context_error'] = f'台本读取失败：{exc}'

    pasted_text = str(script_text or '').strip()
    if pasted_text:
        pasted_context = extract_script_context(pasted_text, source_name='粘贴台本')
        if lyrics_correction_mode:
            pasted_context = attach_lyrics_reference(
                pasted_context,
                build_lyrics_reference(
                    source_name='粘贴歌词',
                    extracted_text=pasted_text,
                ),
            )
        contexts.append(pasted_context)
        sources.append({
            'source_name': '粘贴台本',
            'text': pasted_text,
            'script_context': pasted_context,
        })

    context = merge_script_contexts(*contexts)
    if context:
        context = {**context, 'lyrics_correction_mode': bool(lyrics_correction_mode)}
    source_name = str((context or {}).get('source_name') or '').strip() or None
    if not context:
        return None, source_name, metrics, sources

    try:
        metrics['script_source_count'] = int((context.get('summary') or {}).get('source_count') or 0)
        return context, source_name, metrics, sources
    except Exception as exc:
        metrics['script_context_error'] = f'台本关键词提取失败：{exc}'
        return None, source_name, metrics, sources


def _build_job_subtitle_cues(job, export_frame_rate: Optional[str] = None):
    runtime_lexicon_bundle = _job_runtime_lexicon_bundle_snapshot(job)
    profile = job_subtitle_length_profile(job)
    return build_subtitle_cues(
        job.normalized_segments or [],
        job.speaker_mappings or {},
        export_frame_rate=export_frame_rate or getattr(job, 'export_frame_rate', None),
        remove_standalone_fillers=bool(getattr(job, 'remove_standalone_fillers', False)),
        script_context=getattr(job, 'script_context', None),
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        subtitle_length_mode=profile['mode'],
        subtitle_max_chars=profile['max_chars'],
    )


def _assert_exportable_subtitle_cues(job, cues: list) -> None:
    runtime_lexicon_bundle = _job_runtime_lexicon_bundle_snapshot(job)
    profile = job_subtitle_length_profile(job)
    protected_terms = collect_subtitle_protected_terms(
        getattr(job, 'script_context', None),
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    report = validate_subtitle_cues(
        cues or [],
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        max_cjk_chars=int(profile['max_chars']),
    )
    p0_issues = [issue for issue in report.issues if issue.severity == 'P0']
    normalized_segments = getattr(job, 'normalized_segments', None) or []
    if normalized_segments and all(segment.get('v2FinalCue') is True for segment in normalized_segments):
        p0_issues = [
            issue for issue in p0_issues
            if issue.code in {'invalid_timing', 'overlapping_time'}
        ]
    if not p0_issues:
        return
    details = []
    for issue in p0_issues[:5]:
        location = ''
        if issue.index is not None:
            location = f'第 {issue.index} 条'
        elif issue.left_index is not None and issue.right_index is not None:
            location = f'第 {issue.left_index}-{issue.right_index} 条'
        term = f'：{issue.term}' if issue.term else ''
        details.append(f'{location}{issue.message}{term}'.strip())
    detail_text = '；'.join(details) if details else '字幕存在 P0 级错误'
    raise HTTPException(
        status_code=409,
        detail=f'当前字幕存在 P0 级错误，已禁止导出。请先在校对页修复：{detail_text}',
    )


def _job_runtime_lexicon_bundle_snapshot(job) -> dict[str, Any]:
    artifacts = getattr(job, 'pipeline_artifacts', None)
    if isinstance(artifacts, dict):
        return normalize_runtime_lexicon_bundle(artifacts.get('runtime_lexicon_bundle_snapshot'))
    return normalize_runtime_lexicon_bundle(None)


def _learn_voiceprint_candidates_if_requested(
    session: Session,
    job,
    learn_voiceprints: bool,
) -> VoiceprintAutoLearningResponse | None:
    if not learn_voiceprints:
        return None
    try:
        result = learn_voiceprint_candidates_for_job(session, job)
    except Exception as error:
        return VoiceprintAutoLearningResponse(error_count=1, warnings=[str(error)])
    return VoiceprintAutoLearningResponse(
        created_count=int(getattr(result, 'created_count', 0) or 0),
        skipped_count=int(getattr(result, 'skipped_count', 0) or 0),
        duplicate_count=int(getattr(result, 'duplicate_count', 0) or 0),
        auto_confirmed_count=int(getattr(result, 'auto_confirmed_count', 0) or 0),
        global_promoted_count=int(getattr(result, 'global_promoted_count', 0) or 0),
        pruned_count=int(getattr(result, 'pruned_count', 0) or 0),
        conflict_count=int(getattr(result, 'conflict_count', 0) or 0),
        error_count=int(getattr(result, 'error_count', 0) or 0),
        warnings=list(getattr(result, 'warnings', []) or []),
        candidate_sample_ids=list(getattr(result, 'candidate_sample_ids', []) or []),
    )


def _normalize_export_frame_rate(raw_value: Optional[str]) -> str:
    normalized = str(raw_value or '').strip() or '25'
    if normalized not in {'25', '29.97', '50'}:
        raise HTTPException(status_code=400, detail='仅支持 25 fps、29.97 fps 或 50 fps')
    return normalized


def _normalize_subtitle_strategy(raw_value: Optional[str]) -> str:
    normalized = normalize_subtitle_strategy(raw_value)
    if normalized not in {'standard', 'enhanced', 'pro', 'evidence'}:
        raise HTTPException(status_code=400, detail='仅支持 standard、enhanced、pro 或 evidence')
    return normalized


def _normalize_media_job_kind(raw_value: Optional[str]) -> str:
    normalized = str(raw_value or 'transcription').strip().lower()
    return normalized if normalized in {'transcription', 'transcript'} else 'transcription'


def _detect_media_kind(file: UploadFile) -> str:
    content_type = (file.content_type or '').lower()
    suffix = Path(file.filename or '').suffix.lower()
    if content_type.startswith('audio/') or suffix in {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'}:
        return 'audio'
    if content_type.startswith('video/') or suffix in {'.mp4', '.mov', '.mkv', '.avi', '.m4v', '.webm'}:
        return 'video'
    raise HTTPException(status_code=400, detail='仅支持上传音频或视频文件')


def _require_completed_job(session: Session, job_id: UUID):
    job = get_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail='任务不存在')
    if not job.normalized_segments:
        raise HTTPException(status_code=400, detail='任务还没有可导出的转写结果')
    return job


def _build_job_premiere_xml_content(
    job,
    cues: list,
    frame_rate: str = '25',
    subtitle_mode: Literal['sidecar', 'text_track', 'graphicandtype'] = 'sidecar',
) -> str:
    source_path = job.video_source_export_path or job.video_source_local_path or job.source_export_path or job.source_local_path
    source_url = job.video_source_url or job.source_url
    if not source_path and not source_url:
        raise HTTPException(status_code=400, detail='当前任务缺少可导出的媒体地址')
    speaker_names = sorted({cue.speaker_name for cue in cues if cue.speaker_name})
    matched_assets, _missing_speakers = match_lower_thirds(speaker_names, job.lower_third_assets or [])
    source_media_kind = 'video' if (job.video_source_export_path or job.video_source_local_path or job.video_source_url or job.source_media_kind == 'video') else 'audio'
    return build_premiere_xml(
        job_id=job.id,
        source_path=source_path or source_url,
        cues=cues,
        matched_assets=matched_assets,
        source_media_kind=source_media_kind,
        frame_rate=frame_rate,
        subtitle_mode=subtitle_mode,
        subtitle_backplate_asset=getattr(job, 'subtitle_backplate_asset', None),
    )


def _normalize_subtitle_backplate_role(role: Optional[str]) -> Optional[str]:
    normalized = str(role or '').strip()
    if not normalized:
        return None
    aliases = {
        'with_speaker': 'with_speaker',
        'with_name': 'with_speaker',
        'speaker': 'with_speaker',
        'without_speaker': 'without_speaker',
        'without_name': 'without_speaker',
        'no_speaker': 'without_speaker',
    }
    mapped = aliases.get(normalized)
    if not mapped:
        raise HTTPException(status_code=400, detail='唱词条底图角色只能是 with_speaker 或 without_speaker')
    return mapped


def _select_subtitle_backplate_asset_for_role(config: Optional[dict], role: Optional[str]) -> Optional[dict]:
    return select_subtitle_backplate_asset(config, role)


def _subtitle_backplate_object_key(library_path: Path) -> str:
    try:
        return library_path.resolve().relative_to(
            settings.lower_third_library_dir_path.resolve()
        ).as_posix()
    except ValueError:
        return library_path.name


def _resolve_job_program_name(job) -> str:
    if job.program_name:
        return job.program_name

    for candidate in [job.source_export_path, job.video_source_export_path, job.source_local_path, job.video_source_local_path]:
        if candidate:
            derived = derive_program_name(None, Path(candidate).name)
            if derived:
                return derived

    return derive_program_name(None, job.video_source_url or job.source_url)


def _resolve_job_output_stem(job) -> str:
    candidates = [
        job.video_source_export_path,
        job.source_export_path,
        job.video_source_local_path,
        job.source_local_path,
        job.video_source_url,
        job.source_url,
        job.program_name,
        job.id,
    ]
    for candidate in candidates:
        stem = _path_or_url_stem(candidate)
        normalized = _sanitize_download_stem(stem)
        if normalized:
            return normalized
    return 'speaker-output'


def _path_or_url_stem(value: Optional[str]) -> str:
    raw_value = str(value or '').strip()
    if not raw_value:
        return ''
    parsed = urlparse(raw_value)
    path_value = parsed.path if parsed.scheme else raw_value
    return Path(path_value).stem or raw_value


def _sanitize_download_stem(value: str) -> str:
    normalized = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', str(value or ''))
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized[:64]


def _build_attachment_headers(filename: str) -> dict[str, str]:
    safe_filename = filename.strip() or 'speaker-output'
    suffix = ''.join(Path(safe_filename).suffixes)
    ascii_stem = re.sub(r'[^A-Za-z0-9._-]+', '-', Path(safe_filename).stem).strip('-') or 'speaker-output'
    ascii_fallback = f'{ascii_stem[:48]}{suffix}'
    return {
        'Content-Disposition': (
            f'attachment; filename="{ascii_fallback}"; '
            f"filename*=UTF-8''{quote(safe_filename)}"
        )
    }


def _build_transcript_lines(job) -> list[dict[str, int | str]]:
    speaker_mappings = dict(getattr(job, 'speaker_mappings', None) or {})
    lines: list[dict[str, int | str]] = []
    for segment in getattr(job, 'normalized_segments', None) or []:
        text = str(segment.get('text') or '').strip()
        if not text:
            continue
        speaker_key = str(segment.get('speakerKey') or 'speaker_0')
        speaker_name = speaker_mappings.get(speaker_key, speaker_key)
        start_ms = _coerce_ms(segment.get('startMs'))
        end_ms = _coerce_ms(segment.get('endMs'))
        if end_ms <= start_ms:
            end_ms = start_ms + 1000
        lines.append(
            {
                'index': len(lines) + 1,
                'start_ms': start_ms,
                'end_ms': end_ms,
                'speaker_name': speaker_name,
                'text': text,
            }
        )
    return lines


def _render_transcript_txt(lines: list[dict[str, int | str]]) -> str:
    return '\n'.join(
        f"[{_format_transcript_clock(int(line['start_ms']))}] {line['speaker_name']}：{line['text']}"
        for line in lines
    ) + ('\n' if lines else '')


def _render_transcript_srt(lines: list[dict[str, int | str]]) -> str:
    blocks = []
    for line in lines:
        blocks.append(
            '\n'.join(
                [
                    str(line['index']),
                    f"{_format_srt_timestamp(int(line['start_ms']))} --> {_format_srt_timestamp(int(line['end_ms']))}",
                    f"{line['speaker_name']}：{line['text']}",
                ]
            )
        )
    return '\n\n'.join(blocks) + ('\n' if blocks else '')


def _build_transcript_docx(lines: list[dict[str, int | str]]) -> bytes:
    paragraph_xml = ''.join(
        '<w:p><w:r><w:t>'
        + xml_escape(f"[{_format_transcript_clock(int(line['start_ms']))}] {line['speaker_name']}：{line['text']}")
        + '</w:t></w:r></w:p>'
        for line in lines
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{paragraph_xml}<w:sectPr/></w:body>'
        '</w:document>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '</Relationships>'
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types_xml)
        archive.writestr('_rels/.rels', rels_xml)
        archive.writestr('word/document.xml', document_xml)
    return buffer.getvalue()


def _coerce_ms(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _format_transcript_clock(ms: int) -> str:
    total_seconds = max(0, int(ms)) // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def _format_srt_timestamp(ms: int) -> str:
    total_ms = max(0, int(ms))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    seconds = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}'


def _build_premiere_package_archive(
    job_id: str,
    xml_content: str,
    clean_srt_content: str,
    full_srt_content: str,
    frame_rate: str,
    output_stem: Optional[str] = None,
) -> bytes:
    guide = (
        'Premiere 工程包使用说明\n'
        f'1. 先在 Premiere Pro 中导入同目录下的 XML 文件，载入原媒体和已匹配的人名条 PNG 轨。当前导出帧率：{frame_rate} fps。\n'
        '2. 如果你需要 Premiere 原生字幕轨，请再导入 clean.srt；Premiere 会按 SRT 创建标准字幕轨。\n'
        '3. 如果你想保留说话人姓名前缀，也可以改用 full.srt。\n'
        '4. 如果后续需要图形字幕，请先导入 SRT 生成字幕轨，再在 Premiere 内执行升级为图形字幕。\n'
    )
    buffer = BytesIO()
    safe_stem = _sanitize_download_stem(output_stem) or job_id
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f'{safe_stem}/{safe_stem}-premiere.xml', xml_content)
        archive.writestr(f'{safe_stem}/{safe_stem}-clean.srt', clean_srt_content)
        archive.writestr(f'{safe_stem}/{safe_stem}-full.srt', full_srt_content)
        archive.writestr(f'{safe_stem}/README.txt', guide)
    return buffer.getvalue()


def _normalize_optional_export_path(value: Optional[str]) -> Optional[str]:
    normalized = str(value or '').strip()
    return normalized or None


def _resolve_job_export_frame_rate(job, requested_frame_rate: Optional[str]) -> str:
    locked_frame_rate = str(getattr(job, 'export_frame_rate', '') or '').strip()
    if locked_frame_rate in {'25', '29.97', '50'}:
        return locked_frame_rate
    return _normalize_export_frame_rate(requested_frame_rate)


def _fetch_remote_asset_bytes(url: str, file_name: Optional[str] = None) -> tuple[bytes, str]:
    try:
        response = httpx.get(url, timeout=60)
    except Exception as exc:
        raise HTTPException(status_code=502, detail='读取远端人名条失败') from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail='读取远端人名条失败')
    media_type = (response.headers.get('content-type') or '').split(';', 1)[0].strip()
    if not media_type:
        media_type = mimetypes.guess_type(str(file_name or url))[0] or 'application/octet-stream'
    return response.content, media_type


def _parse_client_paths_payload(raw_value: Optional[str]) -> list[dict[str, Optional[str]]]:
    value = str(raw_value or '').strip()
    if not value:
        return []
    try:
        payload = json.loads(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail='客户端路径数据格式无效') from exc
    if not isinstance(payload, list):
        raise HTTPException(status_code=400, detail='客户端路径数据格式无效')
    items: list[dict[str, Optional[str]]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get('file_name') or '').strip()
        export_path = _normalize_optional_export_path(item.get('path'))
        items.append({'file_name': file_name or None, 'path': export_path})
    return items


def _resolve_client_export_path(
    client_paths: list[dict[str, Optional[str]]],
    index: int,
    file_name: str,
) -> Optional[str]:
    if 0 <= index < len(client_paths):
        export_path = client_paths[index].get('path')
        if export_path:
            return export_path

    normalized_name = str(file_name or '').strip()
    if not normalized_name:
        return None
    for item in client_paths:
        if item.get('file_name') == normalized_name and item.get('path'):
            return item['path']
    return None


def _build_provider_task_entry(
    task_id: str,
    request_id: Optional[str],
    file_url: str,
    offset_ms: int,
    duration_seconds: Optional[float],
    chunk_index: int,
    speaker_offset: int,
    language_hints: Optional[list[str]] = None,
    speaker_count: Optional[int] = None,
    vocabulary_id: Optional[str] = None,
) -> dict[str, object]:
    entry: dict[str, object] = {
        'task_id': task_id,
        'request_id': request_id,
        'file_url': file_url,
        'offset_ms': int(offset_ms),
        'duration_seconds': duration_seconds,
        'chunk_index': chunk_index,
        'speaker_offset': speaker_offset,
    }
    if language_hints:
        entry['language_hints'] = list(language_hints)
    if speaker_count:
        entry['speaker_count'] = speaker_count
    if vocabulary_id:
        entry['vocabulary_id'] = vocabulary_id
    return entry


def _resolve_provider_task_meta(job) -> list[dict]:
    if job.provider_task_meta:
        return [dict(item) for item in (job.provider_task_meta or []) if isinstance(item, dict)]
    task_id = str(getattr(job, 'provider_task_id', '') or '').strip()
    if not task_id:
        return []
    return [
        _build_provider_task_entry(
            task_id=task_id,
            request_id=getattr(job, 'provider_request_id', None),
            file_url=str(getattr(job, 'source_url', '') or ''),
            offset_ms=0,
            duration_seconds=getattr(job, 'source_duration_seconds', None),
            chunk_index=0,
            speaker_offset=0,
        )
    ]


@router.post('/providers/dashscope/callback')
def receive_dashscope_async_finish_event(payload: dict[str, Any]) -> dict[str, Any]:
    event_data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    task_id = str(event_data.get('task_id') or '').strip()
    task_status = str(event_data.get('task_status') or '').upper().strip()
    event_type = str(payload.get('type') or '').strip()
    event_source = str(payload.get('source') or '').strip()
    if event_source != 'acs.dashscope' or event_type != 'dashscope:System:AsyncTaskFinish' or not task_id:
        return {
            'accepted': False,
            'ignored': True,
            'reason': 'unsupported event payload',
        }

    session = SessionLocal()
    client = FunAsrClient()
    try:
        job = find_active_job_by_provider_task_id(session, task_id)
        if not job:
            return {
                'accepted': True,
                'ignored': True,
                'task_id': task_id,
                'reason': 'job not found',
            }
        result = _handle_dashscope_finish_event(
            session=session,
            client=client,
            job_id=job.id,
            task_id=task_id,
            task_status=task_status,
            event_data=event_data,
        )
        return {
            'accepted': True,
            **result,
        }
    except FunAsrError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        session.close()


@router.post('/jobs/{job_id}/sync', response_model=JobDetailResponse)
def sync_transcription_job(job_id: UUID) -> JobDetailResponse:
    session = SessionLocal()
    try:
        job = get_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail='任务不存在')
        if isinstance(job.raw_payload, dict) and job.raw_payload.get('_apple_speech'):
            if job.status not in {'completed', 'completed_with_warnings', 'aborted', 'failed'}:
                from app.services import transcription_tasks
                transcription_tasks.start_finalize_transcription_job(job.id, [])
            return JobDetailResponse.model_validate(job)
        client = FunAsrClient()
        provider_task_meta = _resolve_provider_task_meta(job)
        if not provider_task_meta:
            raise HTTPException(status_code=400, detail='任务缺少百炼 task_id')

        if job.status in {'completed', 'completed_with_warnings', 'aborted'}:
            return JobDetailResponse.model_validate(job)

        if job.status in {'finalizing', 'subtitle_enhancing'} and is_finalize_transcription_job_running(job.id):
            return JobDetailResponse.model_validate(job)

        succeeded_entries: list[dict] = []
        total_parts = len(provider_task_meta)
        provider_outputs = _query_provider_tasks(client, provider_task_meta)
        update_job_v2_asr_progress(
            session,
            job,
            [
                str(output.get('output', {}).get('task_status') or '')
                for output in provider_outputs
            ],
        )
        for index, (entry, provider_output) in enumerate(zip(provider_task_meta, provider_outputs)):
            task_status = str(provider_output.get('output', {}).get('task_status') or '').upper()

            if task_status in {'PENDING', 'RUNNING'}:
                job = update_job_processing(
                    session=session,
                    job_id=job.id,
                    status='processing' if task_status == 'PENDING' else 'running',
                    progress=_estimate_provider_poll_progress(job, index, total_parts, task_status),
                    message=f'云端转写仍在进行中，当前检查到第 {index + 1}/{total_parts} 段为 {task_status}',
                )
                return JobDetailResponse.model_validate(job)

            try:
                succeeded_entries.append(
                    _resolve_succeeded_provider_entry(
                        client=client,
                        entry=entry,
                        provider_output=provider_output,
                    )
                )
            except RuntimeError as exc:
                retry_entry = _retry_provider_task_if_transient(
                    client=client,
                    entry=entry,
                    provider_output=provider_output,
                    provider_context=_fun_asr_provider_context(job),
                )
                if retry_entry:
                    provider_task_meta[index] = retry_entry
                    job = _persist_provider_task_retry(session, job.id, provider_task_meta, index)
                    job = update_job_processing(
                        session=session,
                        job_id=job.id,
                        status='processing',
                        progress=_estimate_provider_poll_progress(job, index, total_parts, 'PENDING'),
                        message=_build_provider_retry_message(retry_entry, index, total_parts),
                    )
                    return JobDetailResponse.model_validate(job)
                provider_task_meta[index] = _mark_provider_entry_failure(entry, provider_output, str(exc))
                _persist_job_provider_task_meta(session, job.id, provider_task_meta)
                job = update_job_failure(session, job.id, str(exc))
                return JobDetailResponse.model_validate(job)

        job = _start_finalize_if_all_provider_tasks_ready(
            session=session,
            job=job,
            provider_task_meta=succeeded_entries,
        )
        return JobDetailResponse.model_validate(job)
    except FunAsrError as exc:
        job = update_job_failure(session, job_id, str(exc))
        return JobDetailResponse.model_validate(job)
    finally:
        session.close()


def _build_abort_message(cancelled_pending_count: int, running_or_finished_count: int) -> str:
    if cancelled_pending_count and not running_or_finished_count:
        return f'任务已中止，已取消 {cancelled_pending_count} 个仍在排队中的百炼分段任务'
    if cancelled_pending_count and running_or_finished_count:
        return (
            f'任务已中止，已取消 {cancelled_pending_count} 个排队中的百炼分段任务；'
            f'其余 {running_or_finished_count} 个分段已开始或已结束，本地将不再继续整理结果'
        )
    return '任务已中止。当前云端分段已开始或已结束，本地将不再继续整理结果'


def _handle_dashscope_finish_event(
    session: Session,
    client: FunAsrClient,
    job_id: str,
    task_id: str,
    task_status: str,
    event_data: dict[str, Any],
) -> dict[str, Any]:
    job = get_job(session, job_id)
    if not job:
        return {'ignored': True, 'task_id': task_id, 'reason': 'job not found'}
    if job.status in {'completed', 'failed', 'aborted'}:
        return {'ignored': True, 'job_id': job.id, 'task_id': task_id, 'reason': f'job already {job.status}'}

    provider_task_meta = _resolve_provider_task_meta(job)
    matched_index = next((
        index for index, entry in enumerate(provider_task_meta)
        if str(entry.get('task_id') or '').strip() == task_id
    ), -1)
    if matched_index < 0:
        return {'ignored': True, 'job_id': job.id, 'task_id': task_id, 'reason': 'task id not bound to job'}

    updated_entry = dict(provider_task_meta[matched_index])
    updated_entry['callback_task_status'] = task_status
    updated_entry['callback_request_id'] = event_data.get('request_id')
    updated_entry['callback_start_time'] = event_data.get('start_time')
    updated_entry['callback_end_time'] = event_data.get('end_time')

    if task_status == 'SUCCEEDED':
        updated_entry = _resolve_succeeded_provider_entry(client=client, entry=updated_entry)
        provider_task_meta[matched_index] = updated_entry
        job = _persist_job_provider_task_meta(session, job.id, provider_task_meta)
        succeeded_count = sum(1 for item in provider_task_meta if str(item.get('transcription_url') or '').strip())
        if succeeded_count == len(provider_task_meta):
            job = _start_finalize_if_all_provider_tasks_ready(
                session=session,
                job=job,
                provider_task_meta=provider_task_meta,
            )
            return {
                'job_id': job.id,
                'task_id': task_id,
                'task_status': task_status,
                'started_finalize': True,
            }

        job = update_job_processing(
            session=session,
            job_id=job.id,
            status='running',
            progress=min(60, int((succeeded_count / max(len(provider_task_meta), 1)) * 60) + 8),
            message=f'已收到百炼完成通知，当前 {succeeded_count}/{len(provider_task_meta)} 段已完成',
        )
        return {
            'job_id': job.id,
            'task_id': task_id,
            'task_status': task_status,
            'started_finalize': False,
        }

    provider_task_meta[matched_index] = updated_entry
    _persist_job_provider_task_meta(session, job.id, provider_task_meta)
    if task_status in {'FAILED', 'CANCELED', 'UNKNOWN'}:
        failure_payload = {
            'request_id': event_data.get('request_id'),
            'output': {
                **event_data,
                'task_id': task_id,
                'task_status': task_status,
            },
        }
        try:
            failure_payload = client.query_task(task_id)
        except FunAsrError:
            pass
        failure_message = _build_provider_failure_message(failure_payload, updated_entry)
        retry_entry = _retry_provider_task_if_transient(
            client=client,
            entry=updated_entry,
            provider_output=failure_payload,
            provider_context=_fun_asr_provider_context(job),
        )
        if retry_entry:
            provider_task_meta[matched_index] = retry_entry
            job = _persist_provider_task_retry(session, job.id, provider_task_meta, matched_index)
            job = update_job_processing(
                session=session,
                job_id=job.id,
                status='processing',
                progress=_estimate_provider_poll_progress(job, matched_index, len(provider_task_meta), 'PENDING'),
                message=_build_provider_retry_message(retry_entry, matched_index, len(provider_task_meta)),
            )
            return {
                'job_id': job.id,
                'task_id': task_id,
                'task_status': task_status,
                'started_finalize': False,
                'retried': True,
                'failed': False,
            }
        provider_task_meta[matched_index] = _mark_provider_entry_failure(updated_entry, failure_payload, failure_message)
        _persist_job_provider_task_meta(session, job.id, provider_task_meta)
        job = update_job_failure(session, job.id, failure_message)
        return {
            'job_id': job.id,
            'task_id': task_id,
            'task_status': task_status,
            'started_finalize': False,
            'failed': True,
        }

    return {
        'job_id': job.id,
        'task_id': task_id,
        'task_status': task_status,
        'started_finalize': False,
        'ignored': True,
    }


def _resolve_succeeded_provider_entry(
    client: FunAsrClient,
    entry: dict[str, Any],
    provider_output: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    output = provider_output or client.query_task(str(entry.get('task_id') or ''))
    task_status = str(output.get('output', {}).get('task_status') or '').upper()
    if task_status != 'SUCCEEDED':
        raise RuntimeError(_build_provider_failure_message(output, entry))

    results = output['output'].get('results') or []
    if not results:
        raise RuntimeError('百炼未返回结果列表')

    result = results[0]
    if result.get('subtask_status') != 'SUCCEEDED':
        raise RuntimeError(_build_provider_failure_message(output, entry))

    transcription_url = str(result.get('transcription_url') or '').strip()
    if not transcription_url:
        raise RuntimeError('缺少 transcription_url')

    updated_entry = dict(entry)
    updated_entry['transcription_url'] = transcription_url
    updated_entry['provider_task_status'] = task_status
    return updated_entry


def _estimate_provider_poll_progress(
    job,
    index: int,
    total_parts: int,
    task_status: str,
    now_seconds: Optional[float] = None,
) -> int:
    safe_total = max(int(total_parts or 0), 1)
    safe_index = max(0, min(int(index or 0), safe_total - 1))
    base_progress = min(60, int((safe_index / safe_total) * 52) + 8)
    if str(task_status or '').upper() != 'RUNNING':
        return base_progress

    created_at = getattr(job, 'created_at', None)
    timestamp = getattr(created_at, 'timestamp', None)
    if not callable(timestamp):
        return base_progress
    try:
        elapsed_seconds = max(0.0, float((now_seconds if now_seconds is not None else time.time()) - timestamp()))
    except Exception:
        return base_progress
    elapsed_bonus = min(52, int(elapsed_seconds / 18))
    return max(base_progress, min(60, 8 + elapsed_bonus))


def _build_provider_failure_message(provider_output: dict[str, Any], entry: Optional[dict[str, Any]] = None) -> str:
    output = provider_output.get('output') if isinstance(provider_output.get('output'), dict) else {}
    entry = entry or {}
    task_status = str(output.get('task_status') or provider_output.get('task_status') or 'UNKNOWN').upper()
    parts = [f'百炼任务状态异常: {task_status}']

    task_id = str(output.get('task_id') or provider_output.get('task_id') or entry.get('task_id') or '').strip()
    request_id = str(provider_output.get('request_id') or output.get('request_id') or entry.get('request_id') or '').strip()
    code = str(output.get('code') or provider_output.get('code') or '').strip()
    message = str(output.get('message') or provider_output.get('message') or '').strip()
    chunk_index = entry.get('chunk_index')
    file_url = str(entry.get('file_url') or '').strip()
    file_name = Path(urlparse(file_url).path).name if file_url else ''

    if task_id:
        parts.append(f'task_id={task_id}')
    if request_id:
        parts.append(f'request_id={request_id}')
    if chunk_index is not None:
        parts.append(f'分段={int(chunk_index) + 1}')
    if file_name:
        parts.append(f'文件={file_name}')
    if code:
        parts.append(f'code={code}')
    if message:
        parts.append(f'message={message}')

    for result in output.get('results') or []:
        if not isinstance(result, dict):
            continue
        sub_status = str(result.get('subtask_status') or '').strip()
        sub_code = str(result.get('code') or '').strip()
        sub_message = str(result.get('message') or '').strip()
        detail = ' '.join(item for item in [sub_status, sub_code, sub_message] if item)
        if detail:
            parts.append(f'子任务={detail}')

    return '；'.join(parts)[:1200]


def _mark_provider_entry_failure(entry: dict[str, Any], provider_output: dict[str, Any], failure_message: str) -> dict[str, Any]:
    output = provider_output.get('output') if isinstance(provider_output.get('output'), dict) else {}
    updated_entry = dict(entry)
    updated_entry['provider_task_status'] = str(output.get('task_status') or '').upper() or None
    updated_entry['provider_failure_code'] = output.get('code') or provider_output.get('code')
    updated_entry['provider_failure_message'] = failure_message
    updated_entry['provider_request_id'] = provider_output.get('request_id') or output.get('request_id') or entry.get('request_id')
    return updated_entry


_TRANSIENT_PROVIDER_FAILURE_CODES = {
    'INSTANCE_POOL_EXHAUSTED',
    'RESOURCE_EXHAUSTED',
    'SERVICE_UNAVAILABLE',
    'TOO_MANY_REQUESTS',
    'THROTTLING',
    'THROTTLED',
    'RATE_LIMIT_EXCEEDED',
}


def _retry_provider_task_if_transient(
    client: FunAsrClient,
    entry: dict[str, Any],
    provider_output: dict[str, Any],
    provider_context: Optional[ProviderCallContext] = None,
) -> Optional[dict[str, Any]]:
    if not _is_transient_provider_failure(provider_output):
        return None
    retry_limit = max(0, int(settings.dashscope_asr_transient_retry_limit or 0))
    retry_count = max(0, int(entry.get('provider_retry_count') or 0))
    if retry_count >= retry_limit:
        return None

    file_url = str(entry.get('file_url') or '').strip()
    if not file_url:
        return None

    language_hints = entry.get('language_hints')
    if not isinstance(language_hints, list):
        language_hints = None
    speaker_count = _safe_optional_int(entry.get('speaker_count'))
    vocabulary_id = str(entry.get('vocabulary_id') or '').strip() or None

    next_retry_count = retry_count + 1
    submit_kwargs: dict[str, Any] = {}
    if provider_context is not None:
        submit_kwargs = {
            'provider_context': provider_context,
            'observation_key': f"fun-asr:{provider_context.job_id}:{int(entry.get('chunk_index') or 0)}",
            'attempt': next_retry_count + 1,
            'audio_seconds': entry.get('duration_seconds'),
        }
    retry_result = client.submit_transcription(
        file_url=file_url,
        language_hints=language_hints,
        speaker_count=speaker_count,
        vocabulary_id=vocabulary_id,
        **submit_kwargs,
    )
    old_task_id = str(entry.get('task_id') or '').strip()
    previous_task_ids = [
        str(item)
        for item in (entry.get('previous_task_ids') or [])
        if str(item or '').strip()
    ]
    if old_task_id and old_task_id not in previous_task_ids:
        previous_task_ids.append(old_task_id)

    updated_entry = dict(entry)
    updated_entry['task_id'] = retry_result['task_id']
    updated_entry['request_id'] = retry_result.get('request_id')
    updated_entry['provider_retry_count'] = next_retry_count
    updated_entry['provider_retry_reason'] = _build_provider_failure_message(provider_output, entry)
    updated_entry['previous_task_ids'] = previous_task_ids[-5:]
    updated_entry.pop('transcription_url', None)
    updated_entry.pop('provider_task_status', None)
    updated_entry.pop('provider_failure_code', None)
    updated_entry.pop('provider_failure_message', None)
    return updated_entry


def _fun_asr_provider_context(job: Any) -> Optional[ProviderCallContext]:
    project_id = str(getattr(job, 'project_id', '') or '').strip()
    job_id = str(getattr(job, 'id', '') or '').strip()
    if not project_id or not job_id:
        return None
    return ProviderCallContext(
        project_id=project_id,
        job_id=job_id,
        pipeline_scope='subtitle_legacy',
    )


def _is_transient_provider_failure(provider_output: dict[str, Any]) -> bool:
    output = provider_output.get('output') if isinstance(provider_output.get('output'), dict) else {}
    codes = {
        str(provider_output.get('code') or '').upper(),
        str(output.get('code') or '').upper(),
    }
    for result in output.get('results') or []:
        if isinstance(result, dict):
            codes.add(str(result.get('code') or '').upper())
    if any(code in _TRANSIENT_PROVIDER_FAILURE_CODES for code in codes if code):
        return True
    message = ' '.join([
        str(provider_output.get('message') or ''),
        str(output.get('message') or ''),
        ' '.join(str(result.get('message') or '') for result in output.get('results') or [] if isinstance(result, dict)),
    ]).upper()
    return 'INSTANCE POOL EXHAUSTED' in message or 'RESOURCE EXHAUSTED' in message


def _safe_optional_int(value: object) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _build_provider_retry_message(entry: dict[str, Any], index: int, total_parts: int) -> str:
    retry_count = max(0, int(entry.get('provider_retry_count') or 0))
    retry_limit = max(0, int(settings.dashscope_asr_transient_retry_limit or 0))
    return (
        f'百炼资源繁忙，已自动重新提交第 {index + 1}/{max(total_parts, 1)} 段转写'
        f'（第 {retry_count}/{retry_limit} 次重试），等待云端重新排队'
    )


def _persist_provider_task_retry(
    session: Session,
    job_id: str,
    provider_task_meta: list[dict[str, Any]],
    retry_index: int,
):
    job = _persist_job_provider_task_meta(session, job_id, provider_task_meta)
    if retry_index == 0 and 0 <= retry_index < len(provider_task_meta):
        primary_entry = provider_task_meta[retry_index]
        job.provider_task_id = str(primary_entry.get('task_id') or '').strip() or job.provider_task_id
        job.provider_request_id = str(primary_entry.get('request_id') or '').strip() or job.provider_request_id
        session.commit()
        session.refresh(job)
    return job


def _persist_job_provider_task_meta(session: Session, job_id: str, provider_task_meta: list[dict[str, Any]]):
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    job.provider_task_meta = provider_task_meta
    session.commit()
    session.refresh(job)
    return job


def _start_finalize_if_all_provider_tasks_ready(session: Session, job, provider_task_meta: list[dict[str, Any]]):
    job = _persist_job_provider_task_meta(session, job.id, provider_task_meta)
    if is_finalize_transcription_job_running(job.id):
        return job
    job = update_job_processing(
        session=session,
        job_id=job.id,
        status='finalizing',
        progress=68,
        message=f'云端转写已完成，共 {len(provider_task_meta)} 段，正在后台整理结果',
    )
    start_finalize_transcription_job(job.id, provider_task_meta)
    return job


def _query_provider_tasks(client: FunAsrClient, provider_task_meta: list[dict]) -> list[dict]:
    if len(provider_task_meta) <= 1:
        return [client.query_task(str(provider_task_meta[0].get('task_id') or ''))] if provider_task_meta else []

    max_workers = min(4, len(provider_task_meta))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(
            lambda entry: client.query_task(str(entry.get('task_id') or '')),
            provider_task_meta,
        ))
