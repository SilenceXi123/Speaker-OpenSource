from __future__ import annotations

from pathlib import Path
import mimetypes
import re
from typing import Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from app.db.session import SessionLocal
from app.services.local_files import remove_local_file, save_upload_file_locally
from app.models.voice_clone_sample import VoiceCloneSample
from app.models.voiceprint_sample import VoiceprintSample
from app.schemas.voice_cloning import (
    VoiceCloneDeleteResponse,
    VoiceClonePreviewRequest,
    VoiceCloneProfileCreateRequest,
    VoiceCloneProfileResponse,
    VoiceCloneProviderStatusResponse,
    VoiceCloneSampleResponse,
    VoiceCloneSamplePageItemResponse,
    VoiceCloneSampleSaveResponse,
    VoiceCloneSynthesisRecordResponse,
)
from app.schemas.voiceprint import VoiceprintSampleResponse
from app.services.voice_cloning import (
    VoiceAssetExpiredError,
    VoiceCloneIdentityUnavailableError,
    VoiceCloneProjectConflictError,
    VoiceCloneSampleLimitError,
    VoiceCloneSampleReviewConflictError,
    build_voice_clone_synthesis_record_response,
    build_voice_clone_sample_response,
    build_voice_clone_sample_save_response,
    build_voice_clone_profile_response,
    create_voice_clone_profile,
    delete_voice_clone_sample,
    delete_voice_clone_profile,
    delete_voice_clone_synthesis_record,
    get_voice_clone_sample,
    get_voice_clone_synthesis_record_for_project,
    get_active_voice_clone_project,
    get_voice_cloning_provider_status,
    find_or_create_voice_clone_sample,
    ensure_voice_cloning_enabled,
    list_clone_samples,
    list_voice_clone_profiles,
    list_voice_clone_samples,
    list_voice_clone_synthesis_records,
    prepare_voice_clone_sample_audio,
    synthesize_voice_clone_preview,
)
from app.services.voice_clone_library import list_voice_clone_group_samples
from app.services.voiceprints import build_sample_response

router = APIRouter()
SUPPORTED_AUDIO_SUFFIXES = {'.wav', '.mp3', '.m4a', '.mp4', '.aac', '.ogg', '.flac'}


def _get_active_project_or_http(session, project_id: str):
    try:
        return get_active_voice_clone_project(session, project_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except VoiceCloneProjectConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get('/provider-status', response_model=VoiceCloneProviderStatusResponse)
def get_provider_status() -> VoiceCloneProviderStatusResponse:
    return get_voice_cloning_provider_status()


@router.get('/samples', response_model=list[VoiceCloneSampleResponse])
def get_samples(program_name: Optional[str] = None) -> list[VoiceCloneSampleResponse]:
    session = SessionLocal()
    try:
        return [build_voice_clone_sample_response(item) for item in list_voice_clone_samples(session, program_name=program_name)]
    finally:
        session.close()


@router.post('/samples', response_model=VoiceCloneSampleSaveResponse, status_code=status.HTTP_201_CREATED)
async def post_sample(
    response: Response,
    display_name: str = Form(...),
    file: UploadFile = File(...),
    program_name: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
) -> VoiceCloneSampleSaveResponse:
    session = SessionLocal()
    uploaded_path: Path | None = None
    prepared_path: Path | None = None
    try:
        ensure_voice_cloning_enabled()
        uploaded_path = await save_upload_file_locally(file)
        prepared_path = await run_in_threadpool(prepare_voice_clone_sample_audio, uploaded_path)
        result = await run_in_threadpool(
            find_or_create_voice_clone_sample,
            session=session,
            display_name=display_name,
            audio_local_path=prepared_path,
            original_filename=file.filename or prepared_path.name,
            program_name=program_name,
            project_id=project_id,
            persist_to_library=True,
        )
        if result.reused_existing:
            response.status_code = status.HTTP_200_OK
        return build_voice_clone_sample_save_response(result)
    except VoiceCloneSampleLimitError as error:
        raise HTTPException(status_code=409, detail=error.as_detail()) from error
    except VoiceCloneProjectConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except VoiceCloneIdentityUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except VoiceAssetExpiredError as error:
        raise HTTPException(status_code=410, detail={'code': 'voice_asset_expired', 'message': str(error)}) from error
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        session.close()
        remove_local_file(uploaded_path)
        remove_local_file(prepared_path)


@router.delete('/samples/{sample_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_sample(sample_id: str) -> Response:
    session = SessionLocal()
    try:
        sample = get_voice_clone_sample(session, sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail='声音复刻样本不存在')
        try:
            delete_voice_clone_sample(session, sample)
        except VoiceCloneSampleReviewConflictError as error:
            raise HTTPException(status_code=409, detail=error.as_detail()) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()


@router.get('/samples/{sample_id}/audio')
def get_sample_audio(sample_id: str):
    session = SessionLocal()
    try:
        sample = get_voice_clone_sample(session, sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail='声音复刻样本不存在')
        audio_path_value = str(sample.audio_local_path or '').strip()
        audio_path = Path(audio_path_value) if audio_path_value else None
        if audio_path and audio_path.is_file():
            return FileResponse(path=audio_path, media_type=_guess_audio_media_type(audio_path.name))
        if sample.audio_public_url:
            return _fetch_remote_audio(sample.audio_public_url, sample.original_filename)
        raise HTTPException(status_code=404, detail='声音复刻样本音频不存在')
    finally:
        session.close()


@router.get('/people/{person_id}/samples', response_model=list[VoiceprintSampleResponse])
def get_person_samples(person_id: str) -> list[VoiceprintSampleResponse]:
    session = SessionLocal()
    try:
        samples = list_clone_samples(session, person_id)
        return [build_sample_response(item) for item in samples]
    finally:
        session.close()


@router.post('/profiles', response_model=VoiceCloneProfileResponse, status_code=status.HTTP_201_CREATED)
def post_profile(payload: VoiceCloneProfileCreateRequest) -> VoiceCloneProfileResponse:
    session = SessionLocal()
    try:
        try:
            profile = create_voice_clone_profile(session, payload)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except VoiceCloneProjectConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except VoiceAssetExpiredError as error:
            raise HTTPException(status_code=410, detail={'code': 'voice_asset_expired', 'message': str(error)}) from error
        except RuntimeError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        source_sample = session.get(VoiceprintSample, profile.source_sample_id) if profile.source_sample_id else None
        source_clone_sample = session.get(VoiceCloneSample, profile.source_clone_sample_id) if profile.source_clone_sample_id else None
        return build_voice_clone_profile_response(profile, source_sample=source_sample, source_clone_sample=source_clone_sample)
    finally:
        session.close()


@router.get('/profiles', response_model=list[VoiceCloneProfileResponse])
def get_all_profiles(program_name: Optional[str] = None) -> list[VoiceCloneProfileResponse]:
    session = SessionLocal()
    try:
        profiles = list_voice_clone_profiles(session, program_name=program_name)
        clone_sample_map = {item.id: item for item in list_voice_clone_samples(session, program_name=program_name)}
        return [
            build_voice_clone_profile_response(
                profile,
                source_clone_sample=clone_sample_map.get(profile.source_clone_sample_id or ''),
            )
            for profile in profiles
        ]
    finally:
        session.close()


@router.get('/people/{person_id}/profiles', response_model=list[VoiceCloneProfileResponse])
def get_profiles(person_id: str) -> list[VoiceCloneProfileResponse]:
    session = SessionLocal()
    try:
        profiles = list_voice_clone_profiles(session, person_id)
        sample_map = {item.id: item for item in list_clone_samples(session, person_id)}
        return [
            build_voice_clone_profile_response(profile, source_sample=sample_map.get(profile.source_sample_id or ''))
            for profile in profiles
        ]
    finally:
        session.close()


@router.delete('/profiles/{profile_id}', response_model=VoiceCloneDeleteResponse)
def delete_profile(profile_id: str) -> VoiceCloneDeleteResponse:
    session = SessionLocal()
    try:
        try:
            profile = delete_voice_clone_profile(session, profile_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except VoiceCloneProjectConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except VoiceAssetExpiredError as error:
            raise HTTPException(status_code=410, detail={'code': 'voice_asset_expired', 'message': str(error)}) from error
        except RuntimeError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return VoiceCloneDeleteResponse(id=profile.id, deleted=True)
    finally:
        session.close()


@router.post(
    '/profiles/{profile_id}/preview',
    response_model=VoiceCloneSynthesisRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_profile_preview(profile_id: str, payload: VoiceClonePreviewRequest) -> VoiceCloneSynthesisRecordResponse:
    session = SessionLocal()
    try:
        try:
            record = synthesize_voice_clone_preview(session, profile_id, payload)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except VoiceCloneProjectConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except VoiceAssetExpiredError as error:
            raise HTTPException(status_code=410, detail={'code': 'voice_asset_expired', 'message': str(error)}) from error
        except RuntimeError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return build_voice_clone_synthesis_record_response(record)
    finally:
        session.close()


@router.get('/profiles/{profile_id}/synthesis-records', response_model=list[VoiceCloneSynthesisRecordResponse])
def get_profile_synthesis_records(
    profile_id: str, project_id: str = Query(..., min_length=1),
) -> list[VoiceCloneSynthesisRecordResponse]:
    session = SessionLocal()
    try:
        project = _get_active_project_or_http(session, project_id)
        records = list_voice_clone_synthesis_records(session, profile_id, project_id=project.id)
        return [build_voice_clone_synthesis_record_response(record) for record in records]
    finally:
        session.close()


@router.get('/synthesis-records/{record_id}/audio')
def get_synthesis_record_audio(
    record_id: str, project_id: str = Query(..., min_length=1),
    consume: bool = Query(default=False),
):
    session = SessionLocal()
    try:
        project = _get_active_project_or_http(session, project_id)
        record = get_voice_clone_synthesis_record_for_project(session, project.id, record_id)
        if not record:
            raise HTTPException(status_code=404, detail='试听记录不存在')
        audio_path_value = str(record.output_audio_local_path or '').strip()
        audio_path = Path(audio_path_value) if audio_path_value else None
        filename = _build_record_audio_filename(record)
        if audio_path and audio_path.is_file():
            return FileResponse(
                path=audio_path,
                media_type=_guess_audio_media_type(audio_path.name),
                filename=filename,
            )
        if record.output_audio_public_url:
            return _fetch_remote_audio(record.output_audio_public_url, filename)
        raise HTTPException(status_code=404, detail='试听音频不存在')
    finally:
        session.close()


@router.delete('/synthesis-records/{record_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_synthesis_record(
    record_id: str, project_id: str = Query(..., min_length=1),
) -> Response:
    session = SessionLocal()
    try:
        project = _get_active_project_or_http(session, project_id)
        record = get_voice_clone_synthesis_record_for_project(session, project.id, record_id)
        if not record:
            raise HTTPException(status_code=404, detail='生成音频记录不存在')
        delete_voice_clone_synthesis_record(session, record)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()


def _guess_audio_media_type(name: str) -> str:
    lower = str(name or '').lower()
    if lower.endswith('.wav'):
        return 'audio/wav'
    if lower.endswith('.mp3'):
        return 'audio/mpeg'
    if lower.endswith('.m4a'):
        return 'audio/mp4'
    return mimetypes.guess_type(name)[0] or 'application/octet-stream'


def _build_record_audio_filename(record) -> str:
    text = re.sub(r'[，。！？、；：“”‘’（）()\[\]【】《》〈〉…,.!?;:\s]+', '', str(record.input_text or ''))
    normalized = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', text).strip()[:48]
    stem = normalized or str(record.id)
    return f'{stem}{_resolve_record_audio_suffix(record)}'


def _resolve_record_audio_suffix(record) -> str:
    local_suffix = Path(str(record.output_audio_local_path or '')).suffix.lower()
    if local_suffix in SUPPORTED_AUDIO_SUFFIXES:
        return local_suffix
    public_path = urlparse(str(record.output_audio_public_url or '')).path
    public_suffix = Path(public_path).suffix.lower()
    if public_suffix in SUPPORTED_AUDIO_SUFFIXES:
        return public_suffix
    return '.mp3'


def _build_attachment_headers(filename: str) -> dict[str, str]:
    safe_filename = filename.strip() or 'voice-audio.mp3'
    suffix = ''.join(Path(safe_filename).suffixes)
    ascii_stem = re.sub(r'[^A-Za-z0-9._-]+', '-', Path(safe_filename).stem).strip('-') or 'voice-audio'
    ascii_fallback = f'{ascii_stem[:48]}{suffix}'
    return {
        'Content-Disposition': (
            f'attachment; filename="{ascii_fallback}"; '
            f"filename*=UTF-8''{quote(safe_filename)}"
        )
    }


def _fetch_remote_audio(public_url: str, fallback_name: str) -> Response:
    try:
        response = httpx.get(public_url, timeout=30)
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f'远程音频读取失败：{error}') from error
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f'远程音频读取失败：{response.status_code}')
    media_type = response.headers.get('content-type') or _guess_audio_media_type(fallback_name)
    return Response(
        content=response.content,
        media_type=media_type,
        headers=_build_attachment_headers(fallback_name),
    )


@router.get('/library/{group_key}/samples', response_model=list[VoiceCloneSamplePageItemResponse])
def get_library_group_samples(group_key: str) -> list[VoiceCloneSamplePageItemResponse]:
    session = SessionLocal()
    try:
        return list_voice_clone_group_samples(session, group_key)
    finally:
        session.close()
