"""Accept device ASR evidence without submitting a second recognition request."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.models.job import Job
from app.models.project import Project
from app.services.local_files import _sha256_file, copy_local_file_to_library, remove_local_file, save_upload_file_locally
from app.services.media import ASR_RECORDED_FILE_MAX_BYTES, get_media_duration_seconds, validate_recognition_source_constraints


async def import_local_transcription(session, *, project_id, file, recognition, client_job_id,
                                     speaker_name='', job_kind='transcription', export_frame_rate='25',
                                     client_source_path=None, subtitle_length_mode='smart', subtitle_max_chars=None,
                                     script_files=None, script_text=None, use_project_scripts=True,
                                     lyrics_correction_mode=False):
    from app.api.v1.transcriptions import _resolve_script_context_inputs
    from app.services import transcription_tasks
    from app.services.jobs import initialize_job_v2_pipeline_run
    from app.services.local_subtitles import recognition_to_payload
    from app.services.lexicon_imports import build_project_script_context, save_project_script_evidence
    from app.services.script_context import merge_script_contexts
    from app.services.subtitle_length_profiles import build_subtitle_length_profile

    project = session.get(Project, str(project_id))
    if project is None:
        raise HTTPException(404, '项目不存在，请重新选择服务器项目')
    if project.status != 'active':
        raise HTTPException(409, '项目已归档，不能创建任务')
    if len(recognition.encode('utf-8')) > 32 * 1024 * 1024:
        raise HTTPException(413, '识别结果过大，请分段处理')
    if len(speaker_name.strip()) > 120:
        raise HTTPException(422, '人物姓名不能超过 120 个字符')
    try:
        parsed = json.loads(recognition)
        payload = recognition_to_payload(parsed, speaker_name.strip())
        client_id = str(UUID(str(client_job_id)))
        length_profile = build_subtitle_length_profile(subtitle_length_mode, subtitle_max_chars)
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, f'本地识别结果无效：{error}') from error
    server_id = str(uuid5(NAMESPACE_URL, f'speaker-local:{project.id}:{client_id}'))
    local_path = library_path = None
    persisted = False
    try:
        local_path = await save_upload_file_locally(file, max_bytes=ASR_RECORDED_FILE_MAX_BYTES)
        await run_in_threadpool(validate_recognition_source_constraints, local_path)
        actual_duration = await run_in_threadpool(get_media_duration_seconds, local_path)
        expected_duration = parsed['duration_ms'] / 1000
        if not actual_duration or abs(actual_duration - expected_duration) > max(0.25, actual_duration * 0.001):
            raise HTTPException(422, '识别时长与上传音频不一致，请使用识别时的原文件')
        audio_hash = await run_in_threadpool(_sha256_file, local_path)
        script_context, script_name, script_metrics, script_sources = await _resolve_script_context_inputs(
            script_file=None, script_files=script_files, script_text=script_text,
            lyrics_correction_mode=lyrics_correction_mode,
        )
        stable_request = {
            'payload': payload, 'audio_sha256': audio_hash, 'kind': job_kind,
            'export_frame_rate': export_frame_rate, 'length': length_profile,
            'use_project_scripts': use_project_scripts,
            'lyrics_correction_mode': lyrics_correction_mode,
        }
        stable_hash = hashlib.sha256(json.dumps(stable_request, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
        request_hash = hashlib.sha256(json.dumps({**stable_request, 'script_context': script_context},
            ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
        supplied_script = any(getattr(item, 'filename', None) for item in (script_files or [])) or bool(str(script_text or '').strip())

        def matching_existing():
            existing = session.get(Job, server_id)
            if existing:
                metrics = existing.processing_metrics or {}
                stored_stable_hash = metrics.get('local_import_stable_hash')
                stable_matches = stored_stable_hash == stable_hash if stored_stable_hash else metrics.get('local_import_hash') == request_hash
                if existing.project_id != project_id or not stable_matches or (supplied_script and metrics.get('local_import_hash') != request_hash):
                    raise HTTPException(409, '同一提交编号对应的音频、识别内容或选项已变化，请新建任务')
            return existing

        job = matching_existing()
        if job is None:
            session.refresh(project)
            if project.status != 'active':
                raise HTTPException(409, '项目已归档，不能创建任务')
            if use_project_scripts:
                script_context = merge_script_contexts(script_context, build_project_script_context(
                    session, tenant_id='default', project_id=project.id,
                ))
            if script_context:
                script_context = {**script_context, 'lyrics_correction_mode': bool(lyrics_correction_mode)}
            library_path = await run_in_threadpool(
                copy_local_file_to_library, local_path, settings.media_library_dir_path, file.filename,
            )
            job = Job(
                id=server_id, project_id=project.id, program_name=project.name,
                program_key=project.legacy_program_key or project.name_key,
                kind=job_kind, status='finalizing', subtitle_strategy='evidence' if job_kind == 'transcription' else 'standard',
                source_url=f'local-asr://{server_id}', source_local_path=str(library_path),
                source_export_path=client_source_path or Path(file.filename or 'audio.wav').name,
                source_media_kind='audio', source_duration_seconds=actual_duration,
                export_frame_rate=export_frame_rate, raw_payload=payload,
                speaker_mappings={'speaker_0': speaker_name.strip() or '未区分说话人'},
                script_context=script_context, script_source_name=script_name,
                processing_progress=75, processing_message='本机识别已保存，正在服务器校对字幕',
                processing_metrics={**script_metrics, 'storage_policy_version': 2,
                                    'local_import_hash': request_hash, 'local_import_stable_hash': stable_hash,
                                    'local_client_job_id': client_id,
                                    'local_source_sha256': audio_hash, 'asr_engine': 'apple_speech',
                                    'subtitle_length_profile': length_profile,
                                    'speaker_identity_source': 'user_assigned' if speaker_name.strip() else 'unknown'},
            )
            session.add(job)
            try:
                session.commit()
                persisted = True
            except IntegrityError:
                session.rollback()
                job = matching_existing()
                if job is None:
                    raise
            if persisted:
                for source in script_sources:
                    save_project_script_evidence(session, tenant_id='default', project_id=project.id,
                                                 source_name=source['source_name'], text=source['text'],
                                                 script_context=source.get('script_context'))
        if job.status not in {'completed', 'completed_with_warnings', 'aborted', 'failed'}:
            initialize_job_v2_pipeline_run(session, job, asr_total_chunks=1)
            transcription_tasks.start_finalize_transcription_job(job.id, [])
        session.refresh(job)
        return job
    finally:
        remove_local_file(local_path)
        if not persisted:
            remove_local_file(library_path)
