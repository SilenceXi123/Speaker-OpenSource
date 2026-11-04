from __future__ import annotations

import shutil
import threading
from pathlib import Path

from fastapi import HTTPException

from app.core.config import settings
from app.db.session import SessionLocal
from app.schemas.provider_cost import ProviderCallContext
from app.services.jobs import get_job, save_render_result, update_render_state
from app.services.lower_thirds import match_lower_thirds
from app.services.oss_storage import upload_local_file_to_oss
from app.services.render import download_remote_file, render_video_with_assets, write_clean_srt
from app.services.subtitle_length_profiles import job_subtitle_length_profile
from app.services.subtitles import build_subtitle_cues


_active_render_threads: dict[str, threading.Thread] = {}
_render_lock = threading.Lock()


def start_render_job(job_id: str) -> None:
    with _render_lock:
        current = _active_render_threads.get(job_id)
        if current and current.is_alive():
            return
        thread = threading.Thread(target=_run_render_job, args=(job_id,), daemon=True)
        _active_render_threads[job_id] = thread
        thread.start()


def _run_render_job(job_id: str) -> None:
    session = SessionLocal()
    working_dir = settings.render_dir_path / str(job_id)
    try:
        update_render_state(session, job_id, 'running', 5, '开始准备渲染任务')
        job = get_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail='任务不存在')
        if not job.normalized_segments:
            raise HTTPException(status_code=400, detail='任务还没有可导出的转写结果')
        if not job.video_source_url:
            raise HTTPException(status_code=400, detail='当前任务缺少原视频地址')
        if not job.lower_third_assets:
            raise HTTPException(status_code=400, detail='当前任务还没有上传人名条图片')
        provider_context = (
            ProviderCallContext(
                project_id=job.project_id,
                job_id=job.id,
                pipeline_scope='subtitle_legacy',
            )
            if job.project_id
            else None
        )

        subtitle_length_profile = job_subtitle_length_profile(job)
        cues = build_subtitle_cues(
            job.normalized_segments or [],
            job.speaker_mappings or {},
            export_frame_rate=job.export_frame_rate,
            remove_standalone_fillers=bool(job.remove_standalone_fillers),
            script_context=job.script_context,
            subtitle_length_mode=str(subtitle_length_profile['mode']),
            subtitle_max_chars=int(subtitle_length_profile['max_chars']),
        )
        speaker_names = sorted({cue.speaker_name for cue in cues if cue.speaker_name})
        matched_assets, _missing_speakers = match_lower_thirds(speaker_names, job.lower_third_assets or [])

        update_render_state(session, job_id, 'running', 12, '已完成人名条匹配，正在下载原视频')
        video_path = download_remote_file(
            job.video_source_url,
            working_dir,
            file_name='source-video.mp4',
            provider_context=provider_context,
            observation_key=f'oss:get:render-source:{job.id}',
        )

        update_render_state(session, job_id, 'running', 24, '正在生成纯字幕文件')
        subtitle_path = write_clean_srt(cues, working_dir / 'clean-subtitles.srt')

        update_render_state(session, job_id, 'running', 36, '正在准备 PNG 人名条并启动 FFmpeg')
        rendered_path = render_video_with_assets(
            video_path=video_path,
            subtitle_path=subtitle_path,
            matched_assets=matched_assets,
            cues=cues,
            working_dir=working_dir,
            progress_callback=lambda progress, message: _update_progress(job_id, progress, message),
            provider_context=provider_context,
        )

        update_render_state(session, job_id, 'running', 92, '正在上传渲染结果到 OSS')
        render_upload = upload_local_file_to_oss(
            file_path=rendered_path,
            prefix=settings.oss_render_prefix,
            content_type='video/mp4',
            provider_context=provider_context,
            observation_key=f'oss:put:render-output:{job.id}',
        )
        save_render_result(
            session=session,
            job_id=job_id,
            render_output_url=render_upload.public_url,
            matched_lower_thirds=matched_assets,
        )
    except HTTPException as exc:
        update_render_state(session, job_id, 'failed', 100, exc.detail)
    except Exception as exc:  # pragma: no cover
        update_render_state(session, job_id, 'failed', 100, str(exc))
    finally:
        session.close()
        if working_dir.exists():
            shutil.rmtree(working_dir, ignore_errors=True)
        with _render_lock:
            _active_render_threads.pop(job_id, None)


def _update_progress(job_id: str, progress: int, message: str) -> None:
    session = SessionLocal()
    try:
        update_render_state(session, job_id, 'running', progress, message)
    finally:
        session.close()
