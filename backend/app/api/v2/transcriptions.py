from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.api.v1.transcriptions import (
    _fetch_remote_asset_bytes,
    _normalize_optional_export_path,
    _normalize_subtitle_backplate_role,
    _subtitle_backplate_object_key,
    submit_media_transcription_job,
)
from app.core.config import settings
from app.db.session import SessionLocal
from app.schemas.job import JobDetailResponse, SubtitlePipelineProgressResponse
from app.schemas.project import ProjectSubtitleBackplateAsset, ProjectSubtitleBackplateAssets
from app.schemas.transcription import MediaTranscriptionResponse
from app.services.jobs import (
    get_project_job,
    load_project_subtitle_backplates_into_job,
    normalize_subtitle_backplate_config,
    select_subtitle_backplate_asset,
    sync_project_subtitle_backplate_asset,
    update_subtitle_backplate_asset,
)
from app.services.local_files import copy_local_file_to_library, remove_local_file, save_upload_file_locally
from app.services.subtitle_pipeline_runs import build_pipeline_progress, get_latest_pipeline_run

router = APIRouter()


@router.post('/projects/{project_id}/transcriptions/local-jobs', status_code=201)
async def submit_local_transcription_job(
    project_id: str,
    file: UploadFile = File(...), recognition: str = Form(...), client_job_id: UUID = Form(...),
    speaker_name: str = Form(default=''),
    job_kind: Literal['transcription', 'transcript'] = Form(default='transcription'),
    export_frame_rate: Literal['25', '29.97', '50'] = Form(default='25'),
    client_source_path: Optional[str] = Form(default=None),
    subtitle_length_mode: Literal['smart', 'strict'] = Form(default='smart'),
    subtitle_max_chars: Optional[int] = Form(default=None),
    script_files: Optional[list[UploadFile]] = File(default=None),
    script_text: Optional[str] = Form(default=None),
    use_project_scripts: bool = Form(default=True),
    lyrics_correction_mode: bool = Form(default=False),
) -> dict:
    from app.services.local_transcription_import import import_local_transcription

    session = SessionLocal()
    try:
        job = await import_local_transcription(
            session, project_id=project_id, file=file, recognition=recognition, client_job_id=client_job_id,
            speaker_name=speaker_name, job_kind=job_kind, export_frame_rate=export_frame_rate,
            client_source_path=client_source_path, subtitle_length_mode=subtitle_length_mode,
            subtitle_max_chars=subtitle_max_chars, script_files=script_files, script_text=script_text,
            use_project_scripts=use_project_scripts, lyrics_correction_mode=lyrics_correction_mode,
        )
        return {'job': JobDetailResponse.model_validate(job)}
    finally:
        session.close()


@router.get(
    '/projects/{project_id}/transcriptions/jobs/{job_id}/progress',
    response_model=SubtitlePipelineProgressResponse,
)
def get_project_transcription_progress(
    project_id: str,
    job_id: str,
) -> SubtitlePipelineProgressResponse:
    session = SessionLocal()
    try:
        _, job = _require_project_job(session, project_id, job_id)
        run = get_latest_pipeline_run(session, job.id)
        if run is None:
            raise HTTPException(status_code=404, detail='字幕流水线运行记录不存在')
        return SubtitlePipelineProgressResponse.model_validate(
            build_pipeline_progress(run),
        )
    finally:
        session.close()


@router.post(
    '/projects/{project_id}/transcriptions/media-jobs',
    response_model=MediaTranscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_project_media_transcription_job(
    project_id: str,
    file: Optional[UploadFile] = File(default=None),
    language_hints: Optional[str] = Form(default=None),
    speaker_count: Optional[int] = Form(default=None),
    vocabulary_id: Optional[str] = Form(default=None),
    client_source_path: Optional[str] = Form(default=None),
    job_kind: Optional[str] = Form(default='transcription'),
    export_frame_rate: Optional[str] = Form(default='25'),
    subtitle_strategy: Optional[str] = Form(default='evidence'),
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
    return await submit_media_transcription_job(
        file=file,
        chunk_files=chunk_files,
        chunk_manifest=chunk_manifest,
        language_hints=language_hints,
        speaker_count=speaker_count,
        vocabulary_id=vocabulary_id,
        project_id=project_id,
        program_name=None,
        client_source_path=client_source_path,
        job_kind=job_kind,
        export_frame_rate=export_frame_rate,
        subtitle_strategy=subtitle_strategy,
        script_file=script_file,
        script_files=script_files,
        script_text=script_text,
        use_project_scripts=use_project_scripts,
        lyrics_correction_mode=lyrics_correction_mode,
        remove_standalone_fillers=remove_standalone_fillers,
        subtitle_length_mode=subtitle_length_mode,
        subtitle_max_chars=subtitle_max_chars,
    )


@router.post(
    '/projects/{project_id}/transcriptions/jobs/{job_id}/subtitle-backplate',
    response_model=ProjectSubtitleBackplateAsset,
    status_code=status.HTTP_201_CREATED,
)
async def upload_project_job_subtitle_backplate(
    project_id: str,
    job_id: str,
    file: UploadFile = File(...),
    role: str = Form(...),
    client_path: Optional[str] = Form(default=None),
) -> ProjectSubtitleBackplateAsset:
    session = SessionLocal()
    local_path: Optional[Path] = None
    try:
        project, _ = _require_project_job(session, project_id, job_id, require_active=True)
        normalized_role = _normalize_subtitle_backplate_role(role)
        local_path = await save_upload_file_locally(file)
        library_path = copy_local_file_to_library(
            source_path=local_path,
            target_dir=(
                settings.lower_third_library_dir_path
                / 'projects'
                / project.id
                / 'subtitle-backplates'
            ),
            original_name=file.filename,
        )
        asset = {
            'asset_id': str(uuid4()),
            'file_name': Path(file.filename or local_path.name).name,
            'object_key': _subtitle_backplate_object_key(library_path),
            'local_path': str(library_path),
            'sha256': hashlib.sha256(library_path.read_bytes()).hexdigest(),
            'role': normalized_role,
            'library_scope': 'project',
        }
        export_path = _normalize_optional_export_path(client_path)
        if export_path:
            asset['export_path'] = export_path
        _, job = sync_project_subtitle_backplate_asset(
            session,
            project_id=project.id,
            job_id=job_id,
            role=normalized_role,
            asset=asset,
        )
        stored_asset = select_subtitle_backplate_asset(
            job.subtitle_backplate_asset,
            normalized_role,
        )
        return ProjectSubtitleBackplateAsset.model_validate(stored_asset or asset)
    finally:
        session.close()
        remove_local_file(local_path)


@router.delete(
    '/projects/{project_id}/transcriptions/jobs/{job_id}/subtitle-backplate',
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_project_job_subtitle_backplate(
    project_id: str,
    job_id: str,
    role: Optional[str] = Query(default=None),
) -> Response:
    session = SessionLocal()
    try:
        _, job = _require_project_job(session, project_id, job_id, require_active=True)
        normalized_role = _normalize_subtitle_backplate_role(role)
        update_subtitle_backplate_asset(
            session,
            job.id,
            None,
            role=normalized_role,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()


@router.get('/projects/{project_id}/transcriptions/jobs/{job_id}/subtitle-backplate/file')
def get_project_job_subtitle_backplate_file(
    project_id: str,
    job_id: str,
    role: Optional[str] = Query(default=None),
):
    session = SessionLocal()
    try:
        _, job = _require_project_job(session, project_id, job_id)
        normalized_role = _normalize_subtitle_backplate_role(role)
        asset = select_subtitle_backplate_asset(
            job.subtitle_backplate_asset,
            normalized_role,
        )
        if not asset:
            raise HTTPException(status_code=404, detail='唱词底图不存在')
        local_path = str(asset.get('local_path') or '').strip()
        if local_path:
            local_file = Path(local_path)
            if not local_file.exists():
                raise HTTPException(status_code=404, detail='唱词底图文件不存在')
            media_type = mimetypes.guess_type(local_file.name)[0] or 'application/octet-stream'
            return FileResponse(path=local_file, media_type=media_type)
        public_url = str(asset.get('public_url') or '').strip()
        if public_url:
            content, media_type = _fetch_remote_asset_bytes(public_url, asset.get('file_name'))
            return Response(content=content, media_type=media_type)
        raise HTTPException(status_code=404, detail='唱词底图文件不存在')
    finally:
        session.close()


@router.post(
    '/projects/{project_id}/transcriptions/jobs/{job_id}/subtitle-backplate/from-project-library',
    response_model=ProjectSubtitleBackplateAssets,
)
def load_project_job_subtitle_backplate_from_library(
    project_id: str,
    job_id: str,
    role: Optional[str] = Query(default=None),
) -> ProjectSubtitleBackplateAssets:
    session = SessionLocal()
    try:
        _, job = _require_project_job(session, project_id, job_id, require_active=True)
        normalized_role = _normalize_subtitle_backplate_role(role)
        try:
            job = load_project_subtitle_backplates_into_job(
                session,
                project_id=project_id,
                job_id=job.id,
                role=normalized_role,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        config = normalize_subtitle_backplate_config(job.subtitle_backplate_asset)
        if not config:
            raise HTTPException(status_code=404, detail='唱词底图不存在')
        return ProjectSubtitleBackplateAssets.model_validate(config)
    finally:
        session.close()


def _require_project_job(session, project_id: str, job_id: str, *, require_active: bool = False):
    try:
        return get_project_job(
            session,
            project_id,
            job_id,
            require_active=require_active,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
