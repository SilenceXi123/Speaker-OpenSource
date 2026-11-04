from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.job import Job
from app.models.project import Project
from app.models.subtitle_pipeline_run import SubtitlePipelineRun
from app.services.job_events import append_job_event
from app.services.knowledge_learning import learn_from_user_edit_patch
from app.services.dialogue_broadcast_normalization import (
    DIALOGUE_NORMALIZATION_VERSION,
    DialogueNormalizationResult,
    normalize_dialogue_segments,
)
from app.services.lyrics_alignment import LyricsCorrectionResult, apply_lyrics_correction_mode
from app.services.pipeline_artifacts import build_pipeline_artifacts
from app.services.asr_quality import sanitize_funasr_payload
from app.services.normalize import normalize_funasr_payload
from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.runtime_lexicon import build_runtime_lexicon_bundle
from app.services.script_context import extract_high_confidence_script_terms
from app.services.script_correction_candidates import build_script_snapshot
from app.services.subtitle_global_planner import PLANNER_PROFILE_VERSION, CuePlannerProfile, SubtitleCuePlanArtifact
from app.services.subtitle_pipeline_v2 import (
    SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
    build_evidence_subtitle_segments_v2,
    build_legacy_segments_from_v2_cues,
    correction_history_with_timing,
    run_subtitle_pipeline_v2_shadow,
)
from app.services.subtitle_strategy import (
    apply_subtitle_strategy,
    normalize_subtitle_strategy,
    resolve_job_language_profile,
)
from app.services.subtitle_cue_validator import validate_subtitle_cues
from app.services.subtitle_length_profiles import job_subtitle_length_profile
from app.services.subtitle_protected_terms import (
    normalize_runtime_lexicon_bundle,
    resolve_subtitle_protected_terms,
)
from app.services.subtitle_pipeline_runs import (
    bind_raw_snapshot_hash,
    cancel_active_pipeline_runs,
    claim_pipeline_run_lease,
    build_job_finalize_lease_owner,
    create_pipeline_run_before_asr,
    get_latest_pipeline_run,
    transition_pipeline_run,
    transition_pipeline_run_with_lease,
    update_pipeline_run_metrics,
)
from app.services.subtitles import build_subtitle_cues, collect_subtitle_protected_terms, stabilize_subtitle_segments
from app.services.utterance_rebuilder import rebuild_semantic_utterances


ACTIVE_JOB_STATUSES = {'pending', 'processing', 'running', 'finalizing', 'subtitle_enhancing'}
COMPLETED_JOB_STATUSES = {'completed', 'completed_with_warnings'}
VALID_STATUSES = ACTIVE_JOB_STATUSES | COMPLETED_JOB_STATUSES | {'failed', 'aborted'}
VALID_EXPORT_FRAME_RATES = {'25', '29.97', '50'}
VALID_JOB_KINDS = {'transcription', 'transcript'}
SUBTITLE_BACKPLATE_ROLES = ('with_speaker', 'without_speaker')
SUBTITLE_BACKPLATE_SCHEMA_VERSION = 1
_SCHEMA_ENSURED = False
TIME_DRIFT_WARN_MIN_MS = 2000
TIME_DRIFT_WARN_RATIO = 0.0005


class InactiveProjectError(RuntimeError):
    pass


def create_job(
    session: Session,
    source_url: str,
    kind: Optional[str] = None,
    source_local_path: Optional[str] = None,
    source_export_path: Optional[str] = None,
    source_media_kind: Optional[str] = None,
    export_frame_rate: Optional[str] = None,
    subtitle_strategy: Optional[str] = None,
    project_id: Optional[str] = None,
    program_key: Optional[str] = None,
    program_name: Optional[str] = None,
    source_duration_seconds: Optional[float] = None,
    processing_metrics: Optional[dict] = None,
    script_context: Optional[dict] = None,
    script_source_name: Optional[str] = None,
    remove_standalone_fillers: bool = False,
) -> Job:
    _ensure_schema_once()
    normalized_kind = _normalize_job_kind(kind)
    normalized_project_id = str(project_id).strip() if project_id else None
    subtitle_backplate_snapshot = _get_project_subtitle_backplate_snapshot(
        session,
        normalized_project_id,
    )
    job = Job(
        kind=normalized_kind,
        source_url=source_url,
        source_local_path=source_local_path,
        source_export_path=source_export_path,
        source_media_kind=source_media_kind,
        export_frame_rate=_normalize_export_frame_rate(export_frame_rate),
        subtitle_strategy=_normalize_job_subtitle_strategy(normalized_kind, subtitle_strategy),
        project_id=normalized_project_id,
        program_key=program_key,
        program_name=program_name,
        source_duration_seconds=source_duration_seconds,
        processing_metrics={
            **dict(processing_metrics or {}),
            'storage_policy_version': 2,
        },
        script_context=script_context,
        script_source_name=script_source_name,
        remove_standalone_fillers=bool(remove_standalone_fillers),
        subtitle_backplate_asset=subtitle_backplate_snapshot,
    )
    session.add(job)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if 'project_not_active' in str(error):
            raise InactiveProjectError('项目已归档，不能继续创建任务') from error
        raise
    session.refresh(job)
    append_job_event(
        session,
        job=job,
        event_type='job_created',
        stage='created',
        status=job.status,
        progress=job.processing_progress,
        message='任务已创建',
    )
    return job


def _normalize_job_kind(kind: Optional[str]) -> str:
    normalized = str(kind or 'transcription').strip().lower()
    return normalized if normalized in VALID_JOB_KINDS else 'transcription'


def _normalize_job_subtitle_strategy(kind: Optional[str], subtitle_strategy: Optional[str]) -> str:
    normalized_kind = _normalize_job_kind(kind)
    normalized_strategy = normalize_subtitle_strategy(subtitle_strategy)
    if normalized_kind == 'transcript':
        return 'standard'
    if normalized_strategy == 'standard':
        return 'enhanced'
    return normalized_strategy


def _ensure_schema_once() -> None:
    global _SCHEMA_ENSURED
    if _SCHEMA_ENSURED:
        return
    try:
        from app.db.session import ensure_schema

        ensure_schema()
    finally:
        _SCHEMA_ENSURED = True


def list_jobs(session: Session) -> list[Job]:
    return list(session.scalars(select(Job).order_by(Job.created_at.desc())))


def list_job_summaries(session: Session) -> list[dict[str, Any]]:
    columns = (
        Job.id,
        Job.kind,
        Job.status,
        Job.source_url,
        Job.source_local_path,
        Job.source_export_path,
        Job.export_frame_rate,
        Job.project_id,
        Job.program_name,
        Job.video_source_url,
        Job.video_source_local_path,
        Job.video_source_export_path,
        Job.render_status,
        Job.render_progress,
        Job.render_message,
        Job.processing_progress,
        Job.processing_message,
        Job.error_message,
        Job.created_at,
        Job.updated_at,
    )
    rows = session.execute(
        select(*columns).order_by(Job.created_at.desc())
    ).mappings()
    return [dict(row) for row in rows]


def get_job(session: Session, job_id: str) -> Optional[Job]:
    return session.get(Job, str(job_id))


def find_active_job_by_provider_task_id(session: Session, provider_task_id: str) -> Optional[Job]:
    normalized_task_id = str(provider_task_id or '').strip()
    if not normalized_task_id:
        return None
    candidates = session.scalars(
        select(Job)
        .where(Job.status.in_(ACTIVE_JOB_STATUSES))
        .order_by(Job.created_at.desc())
    )
    for job in candidates:
        if str(job.provider_task_id or '').strip() == normalized_task_id:
            return job
        for item in job.provider_task_meta or []:
            if isinstance(item, dict) and str(item.get('task_id') or '').strip() == normalized_task_id:
                return job
    return None


def delete_job(session: Session, job_id: str) -> None:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    if job.status in ACTIVE_JOB_STATUSES or job.render_status in {'queued', 'running'}:
        raise RuntimeError('任务仍在处理中，暂不允许删除')
    local_paths = _collect_job_local_files(session, job)
    session.delete(job)
    session.commit()
    for path_value in local_paths:
        _remove_local_file(path_value)


def update_job_provider(
    session: Session,
    job_id: str,
    provider_task_id: str,
    provider_request_id: Optional[str],
    status: str,
    provider_task_meta: Optional[list[dict]] = None,
) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    job.provider_task_id = provider_task_id
    job.provider_request_id = provider_request_id
    job.status = status
    if provider_task_meta is not None:
        job.provider_task_meta = provider_task_meta
    job.processing_progress = 0
    job.processing_message = None
    job.error_message = None
    session.commit()
    session.refresh(job)
    append_job_event(
        session,
        job=job,
        event_type='provider_task_submitted',
        stage='provider',
        status=status,
        progress=0,
        message='已提交云端转写任务',
        details={
            'provider_task_id': provider_task_id,
            'provider_request_id': provider_request_id,
            'provider_task_count': len(provider_task_meta or []),
        },
    )
    return job


def update_job_video_source(
    session: Session,
    job_id: str,
    video_source_url: str,
    video_source_local_path: Optional[str] = None,
    video_source_export_path: Optional[str] = None,
) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    job.video_source_url = video_source_url
    if video_source_local_path:
        job.video_source_local_path = video_source_local_path
    if video_source_export_path:
        job.video_source_export_path = video_source_export_path
    session.commit()
    session.refresh(job)
    return job


def update_job_source_export_path(session: Session, job_id: str, export_path: Optional[str]) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    normalized_path = str(export_path or '').strip() or None
    job.source_export_path = normalized_path
    if job.video_source_url or job.video_source_local_path or job.video_source_export_path or job.source_media_kind == 'video':
        job.video_source_export_path = normalized_path
    session.commit()
    session.refresh(job)
    return job


def update_render_state(session: Session, job_id: str, status: str, progress: int, message: Optional[str] = None) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    job.render_status = status
    job.render_progress = max(0, min(progress, 100))
    job.render_message = message
    session.commit()
    session.refresh(job)
    return job


def update_job_status(session: Session, job_id: str, status: str) -> Job:
    if status not in VALID_STATUSES:
        raise ValueError(f'invalid status: {status}')
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    job.status = status
    session.commit()
    session.refresh(job)
    append_job_event(
        session,
        job=job,
        event_type='status_changed',
        stage=status,
        status=status,
        progress=job.processing_progress,
    )
    return job


def update_job_processing(
    session: Session,
    job_id: str,
    status: str,
    progress: int,
    message: Optional[str] = None,
) -> Job:
    if status not in VALID_STATUSES:
        raise ValueError(f'invalid status: {status}')
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    job.status = status
    job.processing_progress = max(0, min(progress, 100))
    job.processing_message = message
    session.commit()
    session.refresh(job)
    append_job_event(
        session,
        job=job,
        event_type='processing_update',
        stage=status,
        status=status,
        progress=job.processing_progress,
        message=message,
    )
    return job


def update_job_processing_metrics(session: Session, job_id: str, metrics: dict) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    merged = dict(job.processing_metrics or {})
    merged.update(_normalize_processing_metrics(metrics))
    job.processing_metrics = merged
    session.commit()
    session.refresh(job)
    return job


def update_job_script_context(
    session: Session,
    job_id: str,
    *,
    script_context: Optional[dict] = None,
    script_source_name: Optional[str] = None,
    processing_metrics: Optional[dict] = None,
) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    job.script_context = script_context
    job.script_source_name = script_source_name
    if processing_metrics:
        merged = dict(job.processing_metrics or {})
        merged.update(_normalize_processing_metrics(processing_metrics))
        job.processing_metrics = merged
    _refresh_job_pipeline_artifacts(job, refreshed_by='update_job_script_context')
    session.commit()
    session.refresh(job)
    return job


def update_job_failure(session: Session, job_id: str, error_message: str) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    job.status = 'failed'
    job.error_message = error_message
    job.processing_progress = 100
    job.processing_message = error_message
    session.commit()
    session.refresh(job)
    append_job_event(
        session,
        job=job,
        event_type='job_failed',
        stage='failed',
        status='failed',
        progress=100,
        message=error_message,
    )
    return job


def abort_job(session: Session, job_id: str, message: Optional[str] = None) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    if job.render_status in {'queued', 'running'}:
        raise RuntimeError('任务正在渲染中，暂不支持中止')
    if job.status in COMPLETED_JOB_STATUSES:
        raise RuntimeError('任务已经完成，无需中止')
    if job.status == 'failed':
        raise RuntimeError('任务已经失败，可直接删除')
    if job.status == 'aborted':
        return job

    abort_message = message or '任务已由用户中止'
    job.status = 'aborted'
    job.processing_progress = 100
    job.processing_message = abort_message
    job.error_message = abort_message
    from app.db.schema_bootstrap import subtitle_pipeline_run_schema_status

    if subtitle_pipeline_run_schema_status(session.get_bind())['ready']:
        cancel_active_pipeline_runs(session, job.id, commit=False)
    session.commit()
    session.refresh(job)
    append_job_event(
        session,
        job=job,
        event_type='job_aborted',
        stage='aborted',
        status='aborted',
        progress=100,
        message=abort_message,
    )
    return job


def save_transcription_result(session: Session, job_id: str, transcription_url: str, raw_payload: dict) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    normalize_started = perf_counter()
    asr_quality_result = sanitize_funasr_payload(raw_payload)
    pipeline_raw_payload = asr_quality_result.payload
    normalized_segments = normalize_funasr_payload(pipeline_raw_payload)
    normalize_seconds = perf_counter() - normalize_started
    subtitle_started = perf_counter()
    job.kind = _normalize_job_kind(job.kind)
    job.subtitle_strategy = _normalize_job_subtitle_strategy(job.kind, job.subtitle_strategy)
    use_evidence_v2 = job.kind == 'transcription' and job.subtitle_strategy == 'evidence'
    subtitle_input_segments = (
        rebuild_semantic_utterances(normalized_segments)
        if job.kind == 'transcription' and not use_evidence_v2
        else normalized_segments
    )
    utterance_rebuilder_metrics = _build_utterance_rebuilder_metrics(
        job.kind,
        normalized_segments,
        subtitle_input_segments,
    )
    if use_evidence_v2:
        utterance_rebuilder_metrics['utterance_rebuilder_enabled'] = 0

    def report_subtitle_progress(completed: int, total: int, message: str) -> None:
        safe_total = max(int(total or 0), 1)
        safe_completed = max(0, min(int(completed or 0), safe_total))
        current_job = get_job(session, job_id)
        current_progress = int(current_job.processing_progress or 92) if current_job else 92
        progress = max(current_progress, min(98, 92 + max(1, int((safe_completed / safe_total) * 6))))
        update_job_processing(
            session=session,
            job_id=job_id,
            status='subtitle_enhancing',
            progress=progress,
            message=message,
        )

    runtime_lexicon_bundle = _load_job_runtime_lexicon_bundle(
        session,
        job,
        canonical_query_text=''.join(
            str(segment.get('text') or '')
            for segment in normalized_segments
            if isinstance(segment, dict)
        ),
    )
    evidence_artifact: dict[str, Any] | None = None
    if use_evidence_v2:
        try:
            update_job_processing(
                session=session,
                job_id=job_id,
                status='subtitle_enhancing',
                progress=82,
                message='AI正在进行一次校词与语义断句',
            )
            job = get_job(session, job_id) or job
            enhanced_segments, evidence_artifact = build_job_evidence_subtitle_segments(
                session,
                job,
                raw_payload=pipeline_raw_payload,
                runtime_lexicon_bundle=runtime_lexicon_bundle,
            )
            subtitle_enhanced = True
            ai_total_windows = int(evidence_artifact.get('ai_total_windows') or 0)
            ai_completed_windows = int(evidence_artifact.get('ai_completed_windows') or 0)
            subtitle_enhancement_error = (
                'AI语义决策部分降级，已使用确定性字幕结果'
                if ai_completed_windows < ai_total_windows
                else None
            )
            update_job_processing(
                session=session,
                job_id=job_id,
                status='finalizing',
                progress=96,
                message='正在校验说话人、时间轴与字幕守恒',
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            subtitle_enhanced = False
            if pipeline_raw_payload.get('_apple_speech'):
                enhanced_segments, evidence_artifact = _build_apple_run_fallback(job, pipeline_raw_payload)
                subtitle_enhancement_error = f'Apple 证据字幕校对失败，已按原始识别时间范围保留整段文字，未估算内部时间：{exc}'
                evidence_artifact['warnings'] = [subtitle_enhancement_error]
            else:
                enhanced_segments = normalized_segments
                subtitle_enhancement_error = f'证据字幕生成失败，已回退原始转写：{exc}'
    else:
        enhanced_segments, subtitle_enhanced, subtitle_enhancement_error = apply_subtitle_strategy(
            job,
            subtitle_input_segments,
            progress_callback=report_subtitle_progress,
        )
    if _normalize_job_kind(job.kind) == 'transcription' and evidence_artifact is None:
        subtitle_length_profile = job_subtitle_length_profile(job)
        enhanced_segments = stabilize_subtitle_segments(
            enhanced_segments,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            subtitle_length_mode=str(subtitle_length_profile['mode']),
            subtitle_max_chars=int(subtitle_length_profile['max_chars']),
        )
    lyrics_result = _apply_job_lyrics_correction(job, enhanced_segments)
    enhanced_segments = lyrics_result.segments
    dialogue_result = normalize_dialogue_segments(
        enhanced_segments,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        enabled=(
            _normalize_job_kind(job.kind) == 'transcription'
            and not use_evidence_v2
        ),
    )
    enhanced_segments = dialogue_result.segments
    if lyrics_result.report.get('enabled') and lyrics_result.report.get('status') != 'applied':
        lyrics_warning = _lyrics_correction_warning(lyrics_result)
        subtitle_enhancement_error = (
            f'{subtitle_enhancement_error}；{lyrics_warning}'
            if subtitle_enhancement_error
            else lyrics_warning
        )
    subtitle_strategy_seconds = perf_counter() - subtitle_started
    session.refresh(job)
    if job.status == 'aborted':
        return job
    processing_metrics = dict(job.processing_metrics or {})
    processing_metrics.update(_normalize_processing_metrics({
        'normalize_seconds': normalize_seconds,
        'subtitle_strategy_seconds': subtitle_strategy_seconds,
        **utterance_rebuilder_metrics,
        'subtitle_v2_evidence_enabled': 1 if use_evidence_v2 else 0,
        'subtitle_v2_evidence_cue_count': (
            int(evidence_artifact.get('cue_count') or 0) if evidence_artifact else 0
        ),
        'subtitle_v2_evidence_correction_count': (
            len(evidence_artifact.get('corrections') or []) if evidence_artifact else 0
        ),
        'subtitle_v2_ai_total_windows': (
            int(evidence_artifact.get('ai_total_windows') or 0) if evidence_artifact else 0
        ),
        'subtitle_v2_ai_completed_windows': (
            int(evidence_artifact.get('ai_completed_windows') or 0) if evidence_artifact else 0
        ),
        'asr_quality_anomaly_count': int(asr_quality_result.report.get('anomaly_count') or 0),
        'asr_quality_removed_word_count': int(asr_quality_result.report.get('removed_word_count') or 0),
        **_build_lyrics_correction_metrics(lyrics_result),
        **_build_dialogue_normalization_metrics(dialogue_result),
        **_build_subtitle_cue_validator_metrics(job, enhanced_segments, runtime_lexicon_bundle),
    }))
    runtime_lexicon_warning = _runtime_lexicon_bundle_warning(runtime_lexicon_bundle)
    if runtime_lexicon_warning:
        processing_metrics['runtime_lexicon_bundle_load_failed'] = 1.0
        processing_metrics['runtime_lexicon_bundle_error'] = runtime_lexicon_warning['message']
    time_drift_diagnostic = _build_time_drift_diagnostic(
        job,
        raw_segments=normalized_segments,
        semantic_utterances=subtitle_input_segments,
        normalized_segments=enhanced_segments,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    processing_metrics['time_drift_diagnostic'] = time_drift_diagnostic
    job.raw_payload = raw_payload
    pipeline_artifacts = build_pipeline_artifacts(
        job,
        raw_segments=normalized_segments,
        semantic_utterances=subtitle_input_segments,
        normalized_segments=enhanced_segments,
        refreshed_by='save_transcription_result',
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    pipeline_artifacts['time_drift_diagnostic'] = time_drift_diagnostic
    pipeline_artifacts['asr_quality_sanitization'] = asr_quality_result.report
    pipeline_artifacts['lyrics_correction'] = lyrics_result.report
    pipeline_artifacts['dialogue_broadcast_normalization'] = dialogue_result.report
    if evidence_artifact is not None:
        pipeline_artifacts['evidence_subtitle_v2'] = evidence_artifact
    job.status = 'completed_with_warnings' if subtitle_enhancement_error else 'completed'
    job.transcription_url = transcription_url
    job.normalized_segments = enhanced_segments
    job.subtitle_strategy = _normalize_job_subtitle_strategy(job.kind, job.subtitle_strategy)
    job.subtitle_enhanced = subtitle_enhanced
    job.subtitle_enhancement_error = subtitle_enhancement_error
    job.processing_metrics = processing_metrics
    job.pipeline_artifacts = pipeline_artifacts
    job.processing_progress = 100
    job.processing_message = (
        '转写结果已完成整理，部分字幕增强已自动回退'
        if subtitle_enhancement_error
        else (
            '转写与歌词校对已完成'
            if lyrics_result.report.get('status') == 'applied'
            else '转写结果已完成整理'
        )
    )
    job.error_message = None
    session.commit()
    session.refresh(job)
    append_job_event(
        session,
        job=job,
        event_type='job_completed',
        stage='finalize',
        status=job.status,
        progress=100,
        message=job.processing_message,
        details={
            'cue_hash': pipeline_artifacts.get('export_manifest', {}).get('cue_hash'),
            'subtitle_enhanced': bool(subtitle_enhanced),
            'subtitle_enhancement_error': subtitle_enhancement_error,
        },
    )
    append_job_event(
        session,
        job=job,
        event_type='time_drift_diagnostic',
        stage='diagnostics',
        status=time_drift_diagnostic.get('status'),
        progress=100,
        message=_build_time_drift_message(time_drift_diagnostic),
        details=_build_time_drift_event_details(time_drift_diagnostic),
    )
    return job


def build_job_evidence_subtitle_segments(
    session: Session,
    job: Job,
    *,
    raw_payload: dict[str, Any],
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(raw_payload, dict):
        raise ValueError('证据字幕需要原始识别结果')
    raw_snapshot = _build_job_raw_asr_snapshot(job, raw_payload)
    run = initialize_job_v2_pipeline_run(
        session,
        job,
        asr_total_chunks=max(1, len(job.provider_task_meta or [])),
    )
    if run is not None:
        result = _run_job_v2_pipeline(
            session,
            job=job,
            run=run,
            raw_snapshot=raw_snapshot,
            script_snapshot=_build_job_script_snapshot(job, runtime_lexicon_bundle),
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        )
        if result is not None:
            return result
    return build_evidence_subtitle_segments_v2(
        raw_snapshot=raw_snapshot,
        script_snapshot=_build_job_script_snapshot(job, runtime_lexicon_bundle),
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        cue_profile=_build_job_evidence_cue_profile(job),
    )


def replan_completed_evidence_job(session: Session, job_id: str) -> Job:
    """Rebuild a completed V2 job from stored ASR while replaying accepted AI decisions."""
    job = get_job(session, job_id)
    if job is None:
        raise ValueError('job not found')
    if job.status not in COMPLETED_JOB_STATUSES:
        raise ValueError('只能重算已完成的任务')
    if (
        _normalize_job_kind(job.kind) != 'transcription'
        or normalize_subtitle_strategy(job.subtitle_strategy) != 'evidence'
    ):
        raise ValueError('只能重算 V2 证据字幕任务')
    if not isinstance(job.raw_payload, dict):
        raise ValueError('任务没有保存可重算的原始识别结果')

    replay_source_runs = session.scalars(
        select(SubtitlePipelineRun)
        .where(
            SubtitlePipelineRun.job_id == str(job.id),
            SubtitlePipelineRun.stage.in_(('ready', 'ready_with_warnings')),
        )
        .order_by(SubtitlePipelineRun.run_sequence.asc())
    ).all()
    previous_run = max(
        replay_source_runs,
        key=_pipeline_replay_decision_richness,
        default=None,
    )
    if previous_run is None:
        raise ValueError('任务没有可回放的 V2 断句决策')
    previous_artifact = deepcopy(previous_run.artifacts or {})

    runtime_lexicon_bundle = _load_job_runtime_lexicon_bundle(
        session,
        job,
        canonical_query_text=''.join(
            str(segment.get('text') or '')
            for segment in (job.normalized_segments or [])
            if isinstance(segment, dict)
        ),
    )
    bundle_hash = str(runtime_lexicon_bundle.get('term_set_hash') or 'empty')[:16]
    lyrics_signature = _job_lyrics_reference_signature(job)
    run = create_pipeline_run_before_asr(
        session,
        job=job,
        submission_idempotency_key=(
            'speaker-v2-replan-v8:'
            f'{job.id}:{PLANNER_PROFILE_VERSION}:{bundle_hash}:{lyrics_signature}:'
            f'dialogue-v{DIALOGUE_NORMALIZATION_VERSION}'
        ),
    )
    run = initialize_job_v2_pipeline_run(
        session,
        job,
        asr_total_chunks=max(1, len(job.provider_task_meta or [])),
    ) or run
    asr_quality_result = sanitize_funasr_payload(job.raw_payload)
    pipeline_raw_payload = asr_quality_result.payload
    raw_snapshot = _build_job_raw_asr_snapshot(job, pipeline_raw_payload)
    result = _run_job_v2_pipeline(
        session,
        job=job,
        run=run,
        raw_snapshot=raw_snapshot,
        script_snapshot=_build_job_script_snapshot(job, runtime_lexicon_bundle),
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        replay_decisions_from_artifact=previous_artifact,
    )
    if result is None:
        raise RuntimeError('V2 字幕重算失败')
    enhanced_segments, evidence_artifact = result
    lyrics_result = _apply_job_lyrics_correction(job, enhanced_segments)
    enhanced_segments = lyrics_result.segments
    dialogue_result = normalize_dialogue_segments(
        enhanced_segments,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        enabled=False,
    )
    enhanced_segments = dialogue_result.segments

    raw_segments = normalize_funasr_payload(pipeline_raw_payload)
    time_drift_diagnostic = _build_time_drift_diagnostic(
        job,
        raw_segments=raw_segments,
        semantic_utterances=raw_segments,
        normalized_segments=enhanced_segments,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    processing_metrics = dict(job.processing_metrics or {})
    processing_metrics.update(_normalize_processing_metrics({
        'subtitle_v2_evidence_enabled': 1,
        'subtitle_v2_evidence_cue_count': int(evidence_artifact.get('cue_count') or 0),
        'subtitle_v2_evidence_correction_count': len(evidence_artifact.get('corrections') or []),
        'subtitle_v2_ai_total_windows': int(evidence_artifact.get('ai_total_windows') or 0),
        'subtitle_v2_ai_completed_windows': int(evidence_artifact.get('ai_completed_windows') or 0),
        'subtitle_v2_replanned': 1,
        'asr_quality_anomaly_count': int(asr_quality_result.report.get('anomaly_count') or 0),
        'asr_quality_removed_word_count': int(asr_quality_result.report.get('removed_word_count') or 0),
        **_build_lyrics_correction_metrics(lyrics_result),
        **_build_dialogue_normalization_metrics(dialogue_result),
        **_build_subtitle_cue_validator_metrics(job, enhanced_segments, runtime_lexicon_bundle),
    }))
    processing_metrics['time_drift_diagnostic'] = time_drift_diagnostic

    pipeline_artifacts = build_pipeline_artifacts(
        job,
        raw_segments=raw_segments,
        semantic_utterances=raw_segments,
        normalized_segments=enhanced_segments,
        refreshed_by='evidence_subtitle_replan_v2',
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    pipeline_artifacts['time_drift_diagnostic'] = time_drift_diagnostic
    pipeline_artifacts['asr_quality_sanitization'] = asr_quality_result.report
    pipeline_artifacts['lyrics_correction'] = lyrics_result.report
    pipeline_artifacts['dialogue_broadcast_normalization'] = dialogue_result.report
    pipeline_artifacts['evidence_subtitle_v2'] = evidence_artifact

    ai_total = int(evidence_artifact.get('ai_total_windows') or 0)
    ai_completed = int(evidence_artifact.get('ai_completed_windows') or 0)
    enhancement_error = (
        'AI语义决策部分降级，已使用确定性字幕结果'
        if ai_completed < ai_total
        else None
    )
    if lyrics_result.report.get('enabled') and lyrics_result.report.get('status') != 'applied':
        lyrics_warning = _lyrics_correction_warning(lyrics_result)
        enhancement_error = f'{enhancement_error}；{lyrics_warning}' if enhancement_error else lyrics_warning
    job.normalized_segments = enhanced_segments
    job.status = 'completed_with_warnings' if enhancement_error else 'completed'
    job.subtitle_enhanced = True
    job.subtitle_enhancement_error = enhancement_error
    job.processing_metrics = processing_metrics
    job.pipeline_artifacts = pipeline_artifacts
    job.processing_progress = 100
    job.processing_message = (
        'V2 字幕与歌词校对已使用保存数据重算完成'
        if lyrics_result.report.get('status') == 'applied'
        else 'V2 字幕已使用保存的 ASR 和 AI 决策重算完成'
    )
    job.error_message = None
    session.commit()
    session.refresh(job)
    append_job_event(
        session,
        job=job,
        event_type='subtitle_replanned',
        stage='finalize',
        status=job.status,
        progress=100,
        message=job.processing_message,
        details={
            'run_id': evidence_artifact.get('run_id'),
            'cue_count': evidence_artifact.get('cue_count'),
            'correction_count': len(evidence_artifact.get('corrections') or []),
            'planner_profile_version': PLANNER_PROFILE_VERSION,
        },
    )
    return job


def _pipeline_replay_decision_richness(run: SubtitlePipelineRun) -> tuple[int, int, int]:
    """Prefer the run retaining the richest original AI decision evidence.

    Replanned runs use regenerated candidate IDs. Replaying from the most recent
    replan can therefore progressively lose still-valid decisions. The richest
    saved artifact remains the stable source, with the earliest run winning ties.
    """
    artifacts = dict(getattr(run, 'artifacts', None) or {})
    ai_planning = dict((artifacts.get('checkpoints') or {}).get('ai_planning') or {})
    accepted_corrections = len(ai_planning.get('accepted_candidate_ids') or ())
    accepted_boundaries = len(ai_planning.get('accepted_boundary_votes') or ())
    windows = len(ai_planning.get('window_results') or {})
    return (
        accepted_corrections + accepted_boundaries,
        windows,
        -int(getattr(run, 'run_sequence', 0) or 0),
    )


def initialize_job_v2_pipeline_run(
    session: Session,
    job: Job,
    *,
    asr_total_chunks: int,
):
    if (
        _normalize_job_kind(job.kind) != 'transcription'
        or normalize_subtitle_strategy(job.subtitle_strategy) != 'evidence'
        or not str(job.project_id or '').strip()
    ):
        return None
    from app.db.schema_bootstrap import subtitle_pipeline_run_schema_status

    if not subtitle_pipeline_run_schema_status(session.get_bind())['ready']:
        return None
    run = get_latest_pipeline_run(session, job.id)
    if run is None:
        run = create_pipeline_run_before_asr(
            session,
            job=job,
            submission_idempotency_key=f'speaker-v2:{job.id}',
        )
    if run.stage == 'queued':
        run = transition_pipeline_run(session, run.id, 'asr_running')
    if run.stage == 'asr_running':
        run = update_pipeline_run_metrics(
            session,
            run.id,
            asr_total_chunks=max(1, int(asr_total_chunks or 1)),
            asr_completed_chunks=min(
                int(run.asr_completed_chunks or 0),
                max(1, int(asr_total_chunks or 1)),
            ),
            asr_provider_status=run.asr_provider_status or 'RUNNING',
        )
    return run


def update_job_v2_asr_progress(
    session: Session,
    job: Job,
    provider_statuses: list[str],
) -> None:
    statuses = [str(value or '').upper() for value in provider_statuses]
    run = initialize_job_v2_pipeline_run(
        session,
        job,
        asr_total_chunks=max(1, len(statuses)),
    )
    if run is None or run.stage != 'asr_running':
        return
    completed = sum(status == 'SUCCEEDED' for status in statuses)
    provider_status = (
        'FAILED'
        if any(status in {'FAILED', 'CANCELED', 'UNKNOWN'} for status in statuses)
        else 'SUCCEEDED'
        if completed == len(statuses) and statuses
        else 'RUNNING'
        if any(status == 'RUNNING' for status in statuses)
        else 'PENDING'
    )
    update_pipeline_run_metrics(
        session,
        run.id,
        asr_total_chunks=max(1, len(statuses)),
        asr_completed_chunks=completed,
        asr_provider_status=provider_status,
    )


def _run_job_v2_pipeline(
    session: Session,
    *,
    job: Job,
    run,
    raw_snapshot,
    script_snapshot,
    runtime_lexicon_bundle: Optional[dict[str, Any]],
    replay_decisions_from_artifact: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    if run.stage in {'ready', 'ready_with_warnings'}:
        return _restore_job_v2_segments(run, raw_snapshot)
    if run.stage in {'failed', 'canceled'}:
        return None
    if run.raw_snapshot_hash is None:
        run = bind_raw_snapshot_hash(
            session,
            run_id=run.id,
            raw_snapshot_hash=raw_snapshot.snapshot_hash,
            expected_lock_version=run.lock_version,
        )
    elif run.raw_snapshot_hash != raw_snapshot.snapshot_hash:
        raise ValueError('V2任务引用了不同的原始转写快照')
    if run.stage == 'asr_running':
        run = update_pipeline_run_metrics(
            session,
            run.id,
            asr_total_chunks=max(1, int(run.asr_total_chunks or 1)),
            asr_completed_chunks=max(1, int(run.asr_total_chunks or 1)),
            asr_provider_status='SUCCEEDED',
        )

    lease = claim_pipeline_run_lease(
        session,
        run.id,
        owner=build_job_finalize_lease_owner(job.id),
        now=datetime.now(timezone.utc),
        ttl_seconds=3600,
    )
    if lease is None:
        raise RuntimeError('V2字幕任务正在由另一处理进程执行')
    if run.stage == 'asr_running':
        transition_pipeline_run_with_lease(session, lease, 'evidence_matching')
    result = run_subtitle_pipeline_v2_shadow(
        session,
        lease,
        raw_snapshot=raw_snapshot,
        script_snapshot=script_snapshot,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        replay_decisions_from_artifact=replay_decisions_from_artifact,
        cue_profile=_build_job_evidence_cue_profile(job),
        window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
    )
    if result.stage == 'failed':
        raise ValueError('V2字幕事实守恒校验失败')
    segments, display_normalizations = build_legacy_segments_from_v2_cues(
        raw_snapshot=raw_snapshot,
        cues=result.cue_plan.cues,
        rendered_text_by_cue_id=result.rendered_text_by_cue_id,
    )
    session.expire_all()
    saved_run = get_latest_pipeline_run(session, job.id)
    warnings = list(saved_run.warnings or []) if saved_run is not None else []
    checkpoint = (
        (saved_run.artifacts or {}).get('checkpoints', {}).get('cue_validating', {})
        if saved_run is not None
        else {}
    )
    return segments, {
        'schema_version': 'evidence-subtitle.v2',
        'run_id': run.id,
        'raw_snapshot_hash': raw_snapshot.snapshot_hash,
        'cue_plan_hash': result.cue_plan.artifact_hash,
        'raw_token_count': len(raw_snapshot.tokens),
        'cue_count': len(result.cue_plan.cues),
        'timing_policy': 'apple_attributed_run' if raw_snapshot.provider_model == 'apple_speech' else 'direct_raw_word_span',
        'ai_policy': 'single_pass_non_overlapping_no_retry',
        'ai_total_windows': int(saved_run.ai_total_windows or 0) if saved_run else 0,
        'ai_completed_windows': int(saved_run.ai_completed_windows or 0) if saved_run else 0,
        'display_normalizations': display_normalizations,
        'corrections': correction_history_with_timing(raw_snapshot, checkpoint.get('overlays') or []),
        'rejected_corrections': list(checkpoint.get('rejected_corrections') or []),
        'warnings': warnings,
        'validation': result.validation.to_dict(),
    }


def _restore_job_v2_segments(
    run,
    raw_snapshot,
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    artifacts = dict(run.artifacts or {})
    checkpoint = artifacts.get('checkpoints', {}).get('cue_validating', {})
    final_artifact = artifacts.get('final_cue_artifact', {})
    if (
        artifacts.get('raw_snapshot_hash') != raw_snapshot.snapshot_hash
        or not isinstance(checkpoint.get('cue_plan'), dict)
        or not isinstance(final_artifact.get('cues'), list)
    ):
        return None
    cue_plan = SubtitleCuePlanArtifact.model_validate(checkpoint['cue_plan'])
    rendered = {
        str(item.get('cue_id')): str(item.get('text') or '')
        for item in final_artifact['cues']
        if isinstance(item, dict) and item.get('cue_id')
    }
    segments, display_normalizations = build_legacy_segments_from_v2_cues(
        raw_snapshot=raw_snapshot,
        cues=cue_plan.cues,
        rendered_text_by_cue_id=rendered,
    )
    return segments, {
        'schema_version': 'evidence-subtitle.v2',
        'run_id': run.id,
        'raw_snapshot_hash': raw_snapshot.snapshot_hash,
        'cue_plan_hash': cue_plan.artifact_hash,
        'raw_token_count': len(raw_snapshot.tokens),
        'cue_count': len(cue_plan.cues),
        'timing_policy': 'apple_attributed_run' if raw_snapshot.provider_model == 'apple_speech' else 'direct_raw_word_span',
        'ai_policy': 'single_pass_non_overlapping_no_retry',
        'ai_total_windows': int(run.ai_total_windows or 0),
        'ai_completed_windows': int(run.ai_completed_windows or 0),
        'display_normalizations': display_normalizations,
        'corrections': correction_history_with_timing(raw_snapshot, checkpoint.get('overlays') or []),
        'rejected_corrections': list(checkpoint.get('rejected_corrections') or []),
        'warnings': list(run.warnings or []),
        'validation': checkpoint.get('validation') or {},
        'restored_without_provider_call': True,
    }


def _build_apple_run_fallback(job: Job, raw_payload: dict[str, Any]) -> tuple[list[dict], dict]:
    """Keep indivisible device runs when V2 fails; never enter legacy text timing."""
    raw = _build_job_raw_asr_snapshot(job, raw_payload)
    segments = []
    for token in raw.tokens:
        if token.start_ms is None or token.end_ms is None or token.end_ms <= token.start_ms:
            raise ValueError('Apple 原始识别缺少有效时间范围，无法安全生成字幕')
        text = token.text + token.punctuation
        segments.append({
            'segmentId': f'apple-fallback-{token.token_id}',
            'speakerKey': token.speaker_key or 'speaker_0', 'speakerId': token.speaker_id,
            'startMs': token.start_ms, 'endMs': token.end_ms, 'text': text, 'words': [],
            'subtitleParts': [text],
            'subtitlePartRanges': [{'startMs': token.start_ms, 'endMs': token.end_ms}],
            'sourceTokenIds': [token.token_id], 'timingEvidence': 'apple_attributed_run', 'v2FinalCue': True,
        })
    return segments, {
        'schema_version': 'evidence-subtitle.v1', 'raw_snapshot_hash': raw.snapshot_hash,
        'raw_token_count': len(raw.tokens), 'cue_count': len(segments),
        'timing_policy': 'apple_attributed_run', 'ai_policy': 'failed_apple_run_fallback',
        'corrections': [], 'rejected_corrections': [],
    }


def _build_job_raw_asr_snapshot(
    job: Job,
    raw_payload: dict[str, Any],
):
    if raw_payload.get('_apple_speech'):
        from app.services.local_subtitles import build_local_raw_snapshot
        return build_local_raw_snapshot(raw_payload, provider_task_id=str(job.id))
    duration_ms = _duration_seconds_to_ms(getattr(job, 'source_duration_seconds', None))
    if not duration_ms:
        raise ValueError('缺少可验证的识别音频时长')
    return build_raw_asr_snapshot(
        raw_payload,
        provider_model=settings.dashscope_asr_model,
        provider_task_id=str(getattr(job, 'provider_task_id', None) or job.id),
        language=resolve_job_language_profile(job),
        request_metadata={},
        preflight_metadata={
            'duration_ms': duration_ms,
            'sample_rate_hz': 16000,
            'channel_count': 1,
            'status': 'normalized_pcm_s16le',
        },
    )


def _build_job_script_snapshot(
    job: Job,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
):
    context = getattr(job, 'script_context', None)
    reference_lines = context.get('reference_lines') if isinstance(context, dict) else None
    reference_sources = [
        source
        for source in ((context or {}).get('reference_sources') or [])
        if isinstance(source, dict)
    ]
    canonical_lines = [
        '人工金标规范人名——{display_text}；别名：{aliases}'.format(
            display_text=str(item.get('display_text') or '').strip(),
            aliases='、'.join(
                str(alias or '').strip()
                for alias in (item.get('aliases') or [])
                if str(alias or '').strip()
            ),
        )
        for item in ((context or {}).get('canonical_terms') or [])
        if isinstance(item, dict) and str(item.get('display_text') or '').strip()
    ]
    runtime_lines = [
        '项目强制词库{kind}——{display_text}；别名：{aliases}'.format(
            kind=(
                '人名'
                if str(item.get('term_type') or '').lower()
                in {'person', 'script_roster', 'name'}
                else '术语'
            ),
            display_text=str(item.get('display_text') or '').strip(),
            aliases='、'.join(
                str(alias or '').strip()
                for alias in (item.get('aliases') or [])
                if str(alias or '').strip()
            ),
        )
        for item in ((runtime_lexicon_bundle or {}).get('terms') or [])
        if (
            isinstance(item, dict)
            and str(item.get('display_text') or '').strip()
            and str(item.get('protection_level') or '').lower() == 'hard'
            and str(item.get('scope_type') or '').lower() in {'project', 'temporary'}
        )
    ]
    source_sections: list[dict[str, Any]] = [
        dict(source)
        for source in reference_sources
    ]
    if not source_sections and reference_lines:
        legacy_lines = [
            str(line).strip()
            for line in reference_lines
            if str(line).strip()
        ]
        source_sections.append({
            'source_name': getattr(job, 'script_source_name', None),
            'reference_lines': legacy_lines,
            'physical_line_ends': [False] * len(legacy_lines),
        })
    synthetic_lines = [*canonical_lines, *runtime_lines]
    if synthetic_lines:
        source_sections.append({
            'source_name': '规范词与项目强制词库',
            'reference_lines': synthetic_lines,
            'physical_line_ends': [False] * len(synthetic_lines),
        })
    return build_script_snapshot(
        '',
        source_name=getattr(job, 'script_source_name', None),
        source_sections=source_sections,
    )


def _build_job_evidence_cue_profile(job: Job) -> CuePlannerProfile:
    length_profile = job_subtitle_length_profile(job)
    language_mode = (
        'english_word' if resolve_job_language_profile(job) == 'en' else 'auto'
    )
    if length_profile['mode'] == 'strict':
        return CuePlannerProfile(
            mode='strict',
            language_mode=language_mode,
            strict_max_display_units=int(length_profile['max_chars']),
        )
    return CuePlannerProfile(language_mode=language_mode)


def _normalize_processing_metrics(metrics: dict) -> dict:
    normalized = {}
    for key, value in (metrics or {}).items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        normalized[str(key)] = round(max(number, 0.0), 3)
    return normalized


def _build_utterance_rebuilder_metrics(
    job_kind: str,
    input_segments: list[dict],
    output_segments: list[dict],
) -> dict:
    repair_count = sum(1 for segment in output_segments if segment.get('repairTags'))
    return {
        'utterance_rebuilder_version': 1,
        'utterance_rebuilder_enabled': 1 if job_kind == 'transcription' else 0,
        'utterance_rebuilder_input_segments': len(input_segments or []),
        'utterance_rebuilder_output_segments': len(output_segments or []),
        'utterance_rebuilder_repair_count': repair_count,
    }


def _apply_job_lyrics_correction(
    job: Job,
    segments: list[dict[str, Any]],
) -> LyricsCorrectionResult:
    context = getattr(job, 'script_context', None) or {}
    if (getattr(job, 'raw_payload', None) or {}).get('_apple_speech') and context.get('lyrics_correction_mode'):
        result = apply_lyrics_correction_mode(segments, {**context, 'lyrics_correction_mode': False})
        return LyricsCorrectionResult(result.segments, {
            **result.report, 'enabled': True, 'status': 'not_applied', 'reason': 'apple_timing_preserved',
            'warnings': ['Apple 本机识别保留原始时间范围，已跳过旧版歌词对齐；台本仍通过证据字幕候选校对。'],
        })
    duration_seconds = float(getattr(job, 'source_duration_seconds', None) or 0)
    source_duration_ms = int(round(duration_seconds * 1000)) if duration_seconds > 0 else None
    return apply_lyrics_correction_mode(
        segments,
        getattr(job, 'script_context', None),
        source_duration_ms=source_duration_ms,
    )


def _lyrics_correction_warning(result: LyricsCorrectionResult) -> str:
    if result.report.get('reason') == 'apple_timing_preserved':
        return '；'.join(result.report['warnings'])
    return '歌词校对未找到可靠时间锚点，已保留原字幕'


def _build_lyrics_correction_metrics(result: LyricsCorrectionResult) -> dict[str, int]:
    report = result.report or {}
    return {
        'lyrics_correction_enabled': 1 if report.get('enabled') else 0,
        'lyrics_correction_applied': 1 if report.get('status') == 'applied' else 0,
        'lyrics_line_count': int(report.get('lyrics_line_count') or 0),
        'lyrics_applied_line_count': int(report.get('applied_line_count') or 0),
        'lyrics_replaced_segment_count': int(report.get('replaced_segment_count') or 0),
        'lyrics_alignment_anchor_count': int(report.get('alignment_anchor_count') or 0),
    }


def _build_dialogue_normalization_metrics(
    result: DialogueNormalizationResult,
) -> dict[str, int]:
    report = result.report or {}
    return {
        'dialogue_normalization_version': int(
            report.get('version') or DIALOGUE_NORMALIZATION_VERSION
        ),
        'dialogue_normalization_applied': 1 if report.get('status') == 'applied' else 0,
        'dialogue_normalization_changed_segment_count': int(
            report.get('changed_segment_count') or 0
        ),
        'dialogue_normalization_removed_echo_segment_count': int(
            report.get('removed_echo_segment_count') or 0
        ),
        'dialogue_normalization_lexicon_replacement_count': int(
            report.get('lexicon_replacement_count') or 0
        ),
        'dialogue_normalization_bilingual_phrase_count': int(
            report.get('bilingual_phrase_replacement_count') or 0
        ),
        'dialogue_normalization_repetition_collapse_count': int(
            report.get('repetition_collapse_count') or 0
        ),
        'dialogue_normalization_protected_lyrics_count': int(
            report.get('protected_lyrics_segment_count') or 0
        ),
    }


def _job_lyrics_reference_signature(job: Job) -> str:
    context = getattr(job, 'script_context', None) or {}
    if not context.get('lyrics_correction_mode'):
        return 'disabled'
    hashes = [
        str(item.get('reference_hash') or '')[:12]
        for item in context.get('lyrics_references') or []
        if isinstance(item, dict) and str(item.get('reference_hash') or '')
    ]
    return '-'.join(hashes)[:48] or 'empty'


def _build_subtitle_cue_validator_metrics(
    job: Job,
    normalized_segments: list[dict],
    runtime_lexicon_bundle: Optional[dict] = None,
) -> dict:
    if _normalize_job_kind(job.kind) != 'transcription':
        return {
            'subtitle_cue_validator_version': 1,
            'subtitle_cue_validator_enabled': 0,
            'subtitle_cue_validator_cue_count': 0,
            'subtitle_cue_validator_p0_count': 0,
            'subtitle_cue_validator_issue_count': 0,
        }
    runtime_bundle = normalize_runtime_lexicon_bundle(runtime_lexicon_bundle)
    subtitle_length_profile = job_subtitle_length_profile(job)
    protected_terms = collect_subtitle_protected_terms(
        getattr(job, 'script_context', None),
        runtime_lexicon_bundle=runtime_bundle,
    )
    cues = build_subtitle_cues(
        normalized_segments,
        getattr(job, 'speaker_mappings', None) or {},
        export_frame_rate=getattr(job, 'export_frame_rate', None),
        remove_standalone_fillers=bool(getattr(job, 'remove_standalone_fillers', False)),
        script_context=getattr(job, 'script_context', None),
        runtime_lexicon_bundle=runtime_bundle,
        subtitle_length_mode=str(subtitle_length_profile['mode']),
        subtitle_max_chars=int(subtitle_length_profile['max_chars']),
    )
    report = validate_subtitle_cues(
        cues,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_bundle,
        max_cjk_chars=int(subtitle_length_profile['max_chars']),
    )
    return {
        'subtitle_cue_validator_version': 1,
        'subtitle_cue_validator_enabled': 1,
        'subtitle_cue_validator_cue_count': len(cues),
        'subtitle_cue_validator_p0_count': sum(1 for issue in report.issues if issue.severity == 'P0'),
        'subtitle_cue_validator_issue_count': len(report.issues),
    }


def _build_time_drift_diagnostic(
    job: Job,
    *,
    raw_segments: list[dict],
    semantic_utterances: list[dict],
    normalized_segments: list[dict],
    runtime_lexicon_bundle: Optional[dict] = None,
) -> dict[str, Any]:
    source_duration_ms = _duration_seconds_to_ms(getattr(job, 'source_duration_seconds', None))
    provider_chunks = _summarize_provider_chunks(getattr(job, 'provider_task_meta', None) or [])
    runtime_bundle = normalize_runtime_lexicon_bundle(runtime_lexicon_bundle)
    subtitle_length_profile = job_subtitle_length_profile(job)
    cues = (
        build_subtitle_cues(
            normalized_segments,
            getattr(job, 'speaker_mappings', None) or {},
            export_frame_rate=getattr(job, 'export_frame_rate', None),
            remove_standalone_fillers=bool(getattr(job, 'remove_standalone_fillers', False)),
            script_context=getattr(job, 'script_context', None),
            runtime_lexicon_bundle=runtime_bundle,
            subtitle_length_mode=str(subtitle_length_profile['mode']),
            subtitle_max_chars=int(subtitle_length_profile['max_chars']),
        )
        if _normalize_job_kind(getattr(job, 'kind', None)) == 'transcription'
        else []
    )
    tracks = {
        'chunk_theoretical': provider_chunks.get('theoretical_end_ms'),
        'raw_asr': _max_segment_end_ms(raw_segments),
        'semantic_utterance': _max_segment_end_ms(semantic_utterances),
        'final_segment': _max_segment_end_ms(normalized_segments),
        'subtitle_cue': _max_cue_end_ms(cues),
    }
    comparisons = {
        name: _build_time_drift_comparison(source_duration_ms, end_ms)
        for name, end_ms in tracks.items()
    }
    threshold_ms = _build_time_drift_threshold_ms(source_duration_ms)
    comparable_deltas = [
        abs(int(item.get('delta_ms')))
        for item in comparisons.values()
        if item.get('delta_ms') is not None
    ]
    status = 'insufficient_data'
    if comparable_deltas and source_duration_ms:
        status = 'warn' if max(comparable_deltas) > threshold_ms else 'ok'
    return {
        'schema_version': 1,
        'status': status,
        'source_duration_ms': source_duration_ms,
        'export_frame_rate': str(getattr(job, 'export_frame_rate', None) or '25'),
        'threshold_ms': threshold_ms,
        'cue_count': len(cues),
        'tracks': tracks,
        'comparisons': comparisons,
        'provider_chunks': provider_chunks,
    }


def _summarize_provider_chunks(provider_task_meta: list[dict]) -> dict[str, Any]:
    chunks: list[dict[str, Any]] = []
    theoretical_end_ms: Optional[int] = None
    previous_end_ms: Optional[int] = None
    max_gap_or_overlap_ms = 0
    ordered_entries = sorted(
        [dict(item) for item in provider_task_meta if isinstance(item, dict)],
        key=lambda item: _safe_int(item.get('chunk_index')) or 0,
    )
    for index, entry in enumerate(ordered_entries):
        chunk_index = _safe_int(entry.get('chunk_index'))
        offset_ms = _safe_int(entry.get('offset_ms')) or 0
        duration_ms = _duration_seconds_to_ms(entry.get('duration_seconds'))
        end_ms = offset_ms + duration_ms if duration_ms is not None else None
        if previous_end_ms is not None:
            max_gap_or_overlap_ms = max(max_gap_or_overlap_ms, abs(offset_ms - previous_end_ms))
        if end_ms is not None:
            theoretical_end_ms = end_ms if theoretical_end_ms is None else max(theoretical_end_ms, end_ms)
            previous_end_ms = end_ms
        chunks.append({
            'chunk_index': chunk_index if chunk_index is not None else index,
            'offset_ms': offset_ms,
            'duration_ms': duration_ms,
            'end_ms': end_ms,
            'speaker_offset': _safe_int(entry.get('speaker_offset')),
        })
    return {
        'count': len(chunks),
        'theoretical_end_ms': theoretical_end_ms,
        'max_gap_or_overlap_ms': max_gap_or_overlap_ms,
        'items': chunks[:20],
        'truncated': len(chunks) > 20,
    }


def _max_segment_end_ms(segments: list[dict]) -> Optional[int]:
    max_end: Optional[int] = None
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        end_ms = _safe_int(segment.get('endMs'))
        if end_ms is None:
            end_ms = _safe_int(segment.get('end_time'))
        if end_ms is None:
            for word in segment.get('words') or []:
                if isinstance(word, dict):
                    word_end = _safe_int(word.get('endMs'))
                    if word_end is None:
                        word_end = _safe_int(word.get('end_time'))
                    if word_end is not None:
                        end_ms = word_end if end_ms is None else max(end_ms, word_end)
        if end_ms is not None:
            max_end = end_ms if max_end is None else max(max_end, end_ms)
    return max_end


def _max_cue_end_ms(cues: list) -> Optional[int]:
    max_end: Optional[int] = None
    for cue in cues or []:
        end_ms = _safe_int(getattr(cue, 'end_ms', None))
        if end_ms is not None:
            max_end = end_ms if max_end is None else max(max_end, end_ms)
    return max_end


def _build_time_drift_comparison(source_duration_ms: Optional[int], end_ms: Optional[int]) -> dict[str, Any]:
    result: dict[str, Any] = {'end_ms': end_ms}
    if source_duration_ms is None or end_ms is None or source_duration_ms <= 0:
        result['delta_ms'] = None
        result['ratio'] = None
        return result
    delta_ms = int(end_ms) - int(source_duration_ms)
    result['delta_ms'] = delta_ms
    result['ratio'] = round(int(end_ms) / int(source_duration_ms), 6)
    return result


def _build_time_drift_threshold_ms(source_duration_ms: Optional[int]) -> int:
    if not source_duration_ms or source_duration_ms <= 0:
        return TIME_DRIFT_WARN_MIN_MS
    return max(TIME_DRIFT_WARN_MIN_MS, int(source_duration_ms * TIME_DRIFT_WARN_RATIO))


def _duration_seconds_to_ms(value: Any) -> Optional[int]:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return int(round(seconds * 1000))


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_time_drift_message(diagnostic: dict[str, Any]) -> str:
    status = str(diagnostic.get('status') or 'insufficient_data')
    if status == 'warn':
        comparisons = diagnostic.get('comparisons') or {}
        for track_name in ('subtitle_cue', 'final_segment', 'raw_asr', 'chunk_theoretical'):
            delta_ms = (comparisons.get(track_name) or {}).get('delta_ms')
            if delta_ms is not None:
                return f'时间漂移诊断提示：{track_name} 末尾与源音频相差 {delta_ms} ms'
        return '时间漂移诊断提示：存在末尾漂移，请查看诊断详情'
    if status == 'ok':
        return '时间漂移诊断完成：未发现明显末尾漂移'
    return '时间漂移诊断完成：源时长或字幕末尾数据不足'


def _build_time_drift_event_details(diagnostic: dict[str, Any]) -> dict[str, Any]:
    comparisons = diagnostic.get('comparisons') or {}
    subtitle_cue = comparisons.get('subtitle_cue') or {}
    raw_asr = comparisons.get('raw_asr') or {}
    final_segment = comparisons.get('final_segment') or {}
    provider_chunks = diagnostic.get('provider_chunks') or {}
    return {
        'status': diagnostic.get('status'),
        'source_duration_ms': diagnostic.get('source_duration_ms'),
        'subtitle_cue_end_ms': subtitle_cue.get('end_ms'),
        'subtitle_cue_delta_ms': subtitle_cue.get('delta_ms'),
        'subtitle_cue_ratio': subtitle_cue.get('ratio'),
        'raw_asr_end_ms': raw_asr.get('end_ms'),
        'raw_asr_delta_ms': raw_asr.get('delta_ms'),
        'final_segment_end_ms': final_segment.get('end_ms'),
        'final_segment_delta_ms': final_segment.get('delta_ms'),
        'chunk_count': provider_chunks.get('count'),
        'chunk_theoretical_end_ms': provider_chunks.get('theoretical_end_ms'),
        'chunk_gap_or_overlap_max_ms': provider_chunks.get('max_gap_or_overlap_ms'),
        'threshold_ms': diagnostic.get('threshold_ms'),
    }


def _load_job_runtime_lexicon_bundle(
    session: Session,
    job: Job,
    *,
    canonical_query_text: str | None = None,
) -> dict:
    if _normalize_job_kind(getattr(job, 'kind', None)) != 'transcription':
        return normalize_runtime_lexicon_bundle(None)
    project_id = str(
        getattr(job, 'project_id', None)
        or getattr(job, 'program_key', None)
        or getattr(job, 'program_name', None)
        or getattr(job, 'id', '')
        or 'default'
    )
    project_aliases = [
        str(value).strip()
        for value in (getattr(job, 'program_key', None), getattr(job, 'program_name', None))
        if str(value or '').strip() and str(value).strip() != project_id
    ]
    project = session.get(Project, project_id) if getattr(job, 'project_id', None) else None
    domain_id = str(
        getattr(project, 'knowledge_domain', None) or 'broadcast'
    ).strip() or 'broadcast'
    script_context = getattr(job, 'script_context', None)
    hard_script_terms = [
        {
            'display_text': term,
            'term_type': 'script_roster',
            'protection_level': 'hard',
        }
        for term in extract_high_confidence_script_terms(script_context)
    ]
    canonical_script_terms = [
        {
            'display_text': str(item.get('display_text') or '').strip(),
            'term_type': str(item.get('term_type') or 'term'),
            'protection_level': 'hard',
            'aliases': list(item.get('aliases') or []),
        }
        for item in ((script_context or {}).get('canonical_terms') or [])
        if isinstance(item, dict) and str(item.get('display_text') or '').strip()
    ]
    try:
        return normalize_runtime_lexicon_bundle(build_runtime_lexicon_bundle(
            session,
            tenant_id='default',
            project_id=project_id,
            project_aliases=project_aliases,
            domain_id=domain_id,
            job_id=str(getattr(job, 'id', '') or ''),
            temporary_terms=[
                *hard_script_terms,
                *canonical_script_terms,
                *resolve_subtitle_protected_terms(script_context, builtin_terms=()),
            ],
            canonical_query_text=canonical_query_text,
        ))
    except Exception as exc:
        bundle = normalize_runtime_lexicon_bundle(None)
        bundle['load_warning'] = {
            'code': 'runtime_lexicon_bundle_load_failed',
            'message': str(exc),
        }
        return bundle


def _runtime_lexicon_bundle_warning(runtime_lexicon_bundle: Optional[dict]) -> Optional[dict]:
    if not isinstance(runtime_lexicon_bundle, dict):
        return None
    warning = runtime_lexicon_bundle.get('load_warning')
    if not isinstance(warning, dict):
        return None
    message = str(warning.get('message') or '').strip()
    if not message:
        return None
    return {
        'code': str(warning.get('code') or 'runtime_lexicon_bundle_warning'),
        'message': message,
    }


def update_speaker_mappings(session: Session, job_id: str, mappings: dict[str, str]) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    current = dict(job.speaker_mappings or {})
    current.update(mappings)
    job.speaker_mappings = current
    _refresh_job_pipeline_artifacts(job, refreshed_by='update_speaker_mappings')
    session.commit()
    session.refresh(job)
    return job


def update_job_normalized_segments(
    session: Session,
    job_id: str,
    normalized_segments: list[dict],
    edit_patch: Optional[dict] = None,
) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    job.normalized_segments = normalized_segments
    if edit_patch:
        _append_user_edit_patch(job, edit_patch)
        learning_result = learn_from_user_edit_patch(
            session,
            job=job,
            edit_patch=edit_patch,
        )
        metrics = dict(job.processing_metrics or {})
        metrics['knowledge_learning_last'] = learning_result.as_dict()
        metrics['knowledge_learning_evidence_count'] = int(
            metrics.get('knowledge_learning_evidence_count') or 0
        ) + learning_result.evidence_added
        job.processing_metrics = metrics
    _refresh_job_pipeline_artifacts(job, refreshed_by='update_job_normalized_segments')
    session.commit()
    session.refresh(job)
    if edit_patch:
        append_job_event(
            session,
            job=job,
            event_type='user_edit_patch_applied',
            stage='user_edit',
            status=job.status,
            progress=job.processing_progress,
            message='用户编辑已写入任务快照',
            details={
                'source': edit_patch.get('source') if isinstance(edit_patch, dict) else None,
                'changed_count': edit_patch.get('changed_count') if isinstance(edit_patch, dict) else None,
            },
        )
    return job


def _append_user_edit_patch(job: Job, edit_patch: dict) -> None:
    metrics = dict(job.processing_metrics or {})
    patches = list(metrics.get('user_edit_patches') or [])
    patches.append(edit_patch)
    metrics['user_edit_patches'] = patches[-20:]
    metrics['user_edit_patch_count'] = len(patches)
    job.processing_metrics = metrics


def _refresh_job_pipeline_artifacts(job: Job, *, refreshed_by: str = 'unknown') -> None:
    if getattr(job, 'normalized_segments', None) is None:
        return
    previous_artifacts = dict(getattr(job, 'pipeline_artifacts', None) or {})
    raw_segments = normalize_funasr_payload(getattr(job, 'raw_payload', None) or {})
    semantic_utterances = (
        rebuild_semantic_utterances(raw_segments)
        if _normalize_job_kind(getattr(job, 'kind', None)) == 'transcription'
        else raw_segments
    )
    refreshed_artifacts = build_pipeline_artifacts(
        job,
        raw_segments=raw_segments,
        semantic_utterances=semantic_utterances,
        normalized_segments=job.normalized_segments or [],
        refreshed_by=refreshed_by,
        user_edit_patches=list((getattr(job, 'processing_metrics', None) or {}).get('user_edit_patches') or []),
    )
    history_keys = ('asr_quality_sanitization', 'lyrics_correction', 'evidence_subtitle_v2', 'dialogue_broadcast_normalization')
    for key in history_keys:
        previous_value = previous_artifacts.get(key)
        if isinstance(previous_value, dict):
            refreshed_artifacts[key] = deepcopy(previous_value)
    refreshed_artifacts['generation_report_keys'] = [key for key in history_keys if key in refreshed_artifacts]
    refreshed_artifacts['generation_timeline_revision'] = (
        previous_artifacts.get('generation_timeline_revision') or previous_artifacts.get('timeline_revision')
    )
    job.pipeline_artifacts = refreshed_artifacts
    time_drift_diagnostic = (getattr(job, 'processing_metrics', None) or {}).get('time_drift_diagnostic')
    if isinstance(time_drift_diagnostic, dict):
        job.pipeline_artifacts['time_drift_diagnostic'] = time_drift_diagnostic


def replace_lower_third_assets(session: Session, job_id: str, assets: list[dict]) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    job.lower_third_assets = assets
    session.commit()
    session.refresh(job)
    return job


def append_lower_third_assets(session: Session, job_id: str, assets: list[dict]) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')

    merged: dict[str, dict] = {}
    for asset in job.lower_third_assets or []:
        merged[_build_lower_third_asset_key(asset)] = asset
    for asset in assets:
        merged[_build_lower_third_asset_key(asset)] = asset

    job.lower_third_assets = list(merged.values())
    session.commit()
    session.refresh(job)
    return job


def update_lower_third_export_path(session: Session, job_id: str, object_key: str, export_path: Optional[str]) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')

    updated_assets: list[dict] = []
    matched = False
    normalized_path = str(export_path or '').strip() or None
    for asset in job.lower_third_assets or []:
        copied = dict(asset)
        if copied.get('object_key') == object_key:
            matched = True
            if normalized_path:
                copied['export_path'] = normalized_path
            else:
                copied.pop('export_path', None)
        updated_assets.append(copied)

    if not matched:
        raise ValueError('lower third asset not found')

    job.lower_third_assets = updated_assets
    session.commit()
    session.refresh(job)
    return job


def update_subtitle_backplate_asset(session: Session, job_id: str, asset: Optional[dict], role: Optional[str] = None) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')

    session.refresh(job, attribute_names=['subtitle_backplate_asset'])
    old_config = deepcopy(job.subtitle_backplate_asset) if job.subtitle_backplate_asset else None
    new_config = (
        merge_subtitle_backplate_role(old_config, role, asset)
        if role
        else normalize_subtitle_backplate_config(asset)
    )
    old_local_paths = (
        set()
        if job.project_id
        else _collect_disposable_subtitle_backplate_local_paths(old_config)
    )
    new_local_paths = _collect_subtitle_backplate_local_paths(new_config)
    protected_local_paths = _collect_project_subtitle_backplate_local_paths(session, job.project_id)
    removable_local_paths = old_local_paths - new_local_paths - protected_local_paths

    job.subtitle_backplate_asset = new_config
    session.commit()
    session.refresh(job)
    for old_local_path in sorted(removable_local_paths):
        _remove_local_file(old_local_path)
    return job


def sync_project_subtitle_backplate_asset(
    session: Session,
    *,
    project_id: str,
    job_id: str,
    role: Optional[str],
    asset: dict,
) -> tuple[Project, Job]:
    project, job = get_project_job(session, project_id, job_id, require_active=True)
    copied_asset = {key: value for key, value in dict(asset).items() if value is not None}
    if not str(copied_asset.get('asset_id') or '').strip():
        raise ValueError('subtitle backplate asset_id is required')
    if not str(copied_asset.get('object_key') or '').strip():
        raise ValueError('subtitle backplate object_key is required')
    copied_asset['library_scope'] = 'project'

    session.refresh(project, attribute_names=['subtitle_backplate_assets'])
    session.refresh(job, attribute_names=['subtitle_backplate_asset'])
    roles = (_normalize_subtitle_backplate_role(role),) if role else SUBTITLE_BACKPLATE_ROLES
    for normalized_role in roles:
        project.subtitle_backplate_assets = merge_subtitle_backplate_role(
            project.subtitle_backplate_assets,
            normalized_role,
            copied_asset,
        )
        job.subtitle_backplate_asset = merge_subtitle_backplate_role(
            job.subtitle_backplate_asset,
            normalized_role,
            copied_asset,
        )
    session.commit()
    session.refresh(project)
    session.refresh(job)
    return project, job


def load_project_subtitle_backplates_into_job(
    session: Session,
    *,
    project_id: str,
    job_id: str,
    role: Optional[str] = None,
) -> Job:
    project, job = get_project_job(session, project_id, job_id, require_active=True)
    session.refresh(project, attribute_names=['subtitle_backplate_assets'])
    session.refresh(job, attribute_names=['subtitle_backplate_asset'])
    project_config = normalize_subtitle_backplate_config(project.subtitle_backplate_assets)
    if not project_config:
        raise LookupError('当前项目还没有保存过唱词底图')

    if role:
        normalized_role = _normalize_subtitle_backplate_role(role)
        asset = select_subtitle_backplate_asset(project_config, normalized_role)
        if not asset:
            raise LookupError('当前项目没有该角色的唱词底图')
        job.subtitle_backplate_asset = merge_subtitle_backplate_role(
            job.subtitle_backplate_asset,
            normalized_role,
            asset,
        )
    else:
        job.subtitle_backplate_asset = deepcopy(project_config)
    session.commit()
    session.refresh(job)
    return job


def get_project_job(
    session: Session,
    project_id: str,
    job_id: str,
    *,
    require_active: bool = False,
) -> tuple[Project, Job]:
    project = session.get(Project, str(project_id))
    if not project:
        raise LookupError('项目不存在')
    if require_active and project.status != 'active':
        raise RuntimeError('项目已归档')
    job = get_job(session, str(job_id))
    if not job or str(job.project_id or '') != project.id:
        raise LookupError('当前项目中不存在该任务')
    return project, job


def save_render_result(session: Session, job_id: str, render_output_url: str, matched_lower_thirds: dict[str, dict]) -> Job:
    job = get_job(session, job_id)
    if not job:
        raise ValueError('job not found')
    job.render_output_url = render_output_url
    job.matched_lower_thirds = matched_lower_thirds
    job.render_status = 'completed'
    job.render_progress = 100
    job.render_message = '渲染完成'
    session.commit()
    session.refresh(job)
    return job


def _build_lower_third_asset_key(asset: dict) -> str:
    speaker_name = str(asset.get('speaker_name') or '').strip().casefold()
    if speaker_name:
        return f'speaker:{speaker_name}'
    return f"file:{str(asset.get('file_name') or '').strip().casefold()}"


def _collect_job_local_files(session: Session, job: Job) -> set[str]:
    local_paths = {
        str(job.source_local_path or '').strip(),
        str(job.video_source_local_path or '').strip(),
    }
    for asset in job.lower_third_assets or []:
        if str(asset.get('library_scope') or '').strip() == 'program':
            continue
        local_paths.add(str(asset.get('local_path') or '').strip())
    if job.subtitle_backplate_asset and not job.project_id:
        local_paths.update(
            _collect_disposable_subtitle_backplate_local_paths(job.subtitle_backplate_asset)
        )
    local_paths.difference_update(
        _collect_project_subtitle_backplate_local_paths(session, job.project_id)
    )

    preview_audio_path = _build_preview_audio_path(job)
    if preview_audio_path:
        local_paths.add(str(preview_audio_path))
    return {path_value for path_value in local_paths if path_value}


def _build_preview_audio_path(job: Job) -> Optional[Path]:
    if not job.source_local_path:
        return None
    source_path = Path(job.source_local_path)
    return settings.audio_dir_path / f'job-{job.id}-{source_path.stem}-preview.wav'


def _remove_local_file(path_value: str) -> None:
    normalized = str(path_value or '').strip()
    if not normalized:
        return
    target = Path(normalized)
    if target.exists() and target.is_file():
        target.unlink()


def merge_subtitle_backplate_role(
    old_config: Optional[dict],
    role: Optional[str],
    asset: Optional[dict],
) -> Optional[dict]:
    normalized_role = _normalize_subtitle_backplate_role(role)
    role_assets = extract_subtitle_backplate_role_assets(old_config)
    if asset:
        copied = {key: value for key, value in dict(asset).items() if value is not None}
        copied['role'] = normalized_role
        role_assets[normalized_role] = copied
    else:
        role_assets.pop(normalized_role, None)
    return _build_subtitle_backplate_config(role_assets)


def normalize_subtitle_backplate_config(config: Optional[dict]) -> Optional[dict]:
    return _build_subtitle_backplate_config(extract_subtitle_backplate_role_assets(config))


def extract_subtitle_backplate_role_assets(config: Optional[dict]) -> dict[str, dict]:
    if not isinstance(config, dict) or not config:
        return {}
    source = config.get('roles') if isinstance(config.get('roles'), dict) else config
    role_assets: dict[str, dict] = {}
    for role in SUBTITLE_BACKPLATE_ROLES:
        asset = source.get(role) if isinstance(source, dict) else None
        if isinstance(asset, dict) and asset:
            copied = {key: value for key, value in dict(asset).items() if value is not None}
            copied['role'] = role
            role_assets[role] = copied
    if role_assets:
        return role_assets
    if _looks_like_subtitle_backplate_asset(config):
        legacy_asset = {key: value for key, value in dict(config).items() if value is not None}
        return {
            role: {**legacy_asset, 'role': role}
            for role in SUBTITLE_BACKPLATE_ROLES
        }
    return {}


def select_subtitle_backplate_asset(config: Optional[dict], role: Optional[str]) -> Optional[dict]:
    role_assets = extract_subtitle_backplate_role_assets(config)
    if role:
        return deepcopy(role_assets.get(_normalize_subtitle_backplate_role(role)))
    for preferred_role in SUBTITLE_BACKPLATE_ROLES:
        if preferred_role in role_assets:
            return deepcopy(role_assets[preferred_role])
    return None


def count_subtitle_backplate_assets(config: Optional[dict]) -> int:
    identities: set[str] = set()
    for role, asset in extract_subtitle_backplate_role_assets(config).items():
        identity = next((
            f'{key}:{str(asset.get(key)).strip()}'
            for key in ('asset_id', 'object_key', 'local_path', 'public_url')
            if str(asset.get(key) or '').strip()
        ), f'role:{role}')
        identities.add(identity)
    return len(identities)


def _collect_subtitle_backplate_local_paths(config: Optional[dict]) -> set[str]:
    paths: set[str] = set()
    for asset in extract_subtitle_backplate_role_assets(config).values():
        path_value = str(asset.get('local_path') or '').strip()
        if path_value:
            paths.add(path_value)
    return paths


def _collect_disposable_subtitle_backplate_local_paths(config: Optional[dict]) -> set[str]:
    paths: set[str] = set()
    for asset in extract_subtitle_backplate_role_assets(config).values():
        if str(asset.get('library_scope') or '').strip() == 'project':
            continue
        path_value = str(asset.get('local_path') or '').strip()
        if path_value:
            paths.add(path_value)
    return paths


def _collect_project_subtitle_backplate_local_paths(
    session: Session,
    project_id: Optional[str],
) -> set[str]:
    normalized_project_id = str(project_id or '').strip()
    if not normalized_project_id:
        return set()
    project = session.get(Project, normalized_project_id)
    if not project:
        return set()
    return _collect_subtitle_backplate_local_paths(project.subtitle_backplate_assets)


def _get_project_subtitle_backplate_snapshot(
    session: Session,
    project_id: Optional[str],
) -> Optional[dict]:
    normalized_project_id = str(project_id or '').strip()
    if not normalized_project_id:
        return None
    project = session.get(Project, normalized_project_id)
    if not project:
        return None
    return deepcopy(normalize_subtitle_backplate_config(project.subtitle_backplate_assets))


def _build_subtitle_backplate_config(role_assets: dict[str, dict]) -> Optional[dict]:
    normalized_roles: dict[str, dict] = {}
    for role in SUBTITLE_BACKPLATE_ROLES:
        asset = role_assets.get(role)
        if not isinstance(asset, dict) or not asset:
            continue
        copied = {key: value for key, value in dict(asset).items() if value is not None}
        copied['role'] = role
        normalized_roles[role] = copied
    if not normalized_roles:
        return None
    return {
        'schema_version': SUBTITLE_BACKPLATE_SCHEMA_VERSION,
        'roles': normalized_roles,
    }


def _looks_like_subtitle_backplate_asset(value: dict) -> bool:
    return any(
        value.get(key)
        for key in (
            'asset_id',
            'file_name',
            'object_key',
            'export_path',
            'local_path',
            'public_url',
        )
    )


def _normalize_subtitle_backplate_role(role: Optional[str]) -> str:
    normalized_role = str(role or '').strip()
    if normalized_role not in SUBTITLE_BACKPLATE_ROLES:
        raise ValueError('invalid subtitle backplate role')
    return normalized_role


def _normalize_export_frame_rate(frame_rate: Optional[str]) -> str:
    normalized = str(frame_rate or '').strip() or '25'
    if normalized not in VALID_EXPORT_FRAME_RATES:
        return '25'
    return normalized
