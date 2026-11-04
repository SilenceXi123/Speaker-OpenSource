from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from app.api.v1.voice_cloning import (
    _build_record_audio_filename,
    _fetch_remote_audio,
    _guess_audio_media_type,
)
from app.db.session import SessionLocal
from app.schemas.voice_cloning import (
    VoiceCloneDeleteResponse,
    VoiceCloneLibraryPageResponse,
    VoiceCloneLibraryProfileResponse,
    VoiceClonePreviewRequest,
    VoiceCloneProfileCreateResponse,
    VoiceCloneProfileCreateRequest,
    VoiceCloneProfileResponse,
    VoiceCloneProviderStatusResponse,
    VoiceCloneSampleResponse,
    VoiceCloneSamplePageItemResponse,
    VoiceCloneSamplePageResponse,
    VoiceCloneSampleSaveResponse,
    VoiceCloneSynthesisRecordResponse,
)
from app.schemas.voiceprint import VoiceprintSampleResponse
from app.services.local_files import remove_local_file, save_upload_file_locally
from app.services.voice_cloning import (
    VoiceAssetExpiredError,
    VoiceCloneIdentityUnavailableError,
    VoiceCloneProjectConflictError,
    VoiceCloneSampleLimitError,
    VoiceCloneSampleReviewConflictError,
    VoiceCloneUnavailableError,
    build_voice_clone_profile_response,
    build_voice_clone_sample_response,
    build_voice_clone_sample_save_response,
    build_voice_clone_synthesis_record_response,
    delete_voice_clone_profile,
    delete_voice_clone_sample,
    delete_voice_clone_synthesis_record,
    complete_voice_clone_download,
    get_active_voice_clone_project,
    get_voice_clone_person_for_project,
    get_voice_clone_profile,
    get_voice_clone_profile_for_project,
    get_voice_clone_sample,
    get_voice_clone_sample_for_project,
    get_voice_clone_synthesis_record_for_project,
    get_voice_cloning_provider_status,
    find_or_create_voice_clone_sample,
    ensure_voice_cloning_enabled,
    refresh_voice_clone_profile,
    find_or_create_voice_clone_profile,
    get_voiceprint_sample_for_voice_clone_project,
    list_clone_samples,
    list_voice_clone_profiles,
    list_voice_clone_profiles_for_project,
    list_voice_clone_samples,
    list_voice_clone_samples_for_project,
    list_voice_clone_synthesis_records,
    prepare_voice_clone_sample_audio,
    synthesize_voice_clone_preview,
)
from app.services.voiceprints import build_sample_response
from app.services.voice_clone_library import (
    list_creating_voice_clone_profiles,
    list_voice_clone_group_profiles,
    list_voice_clone_group_samples,
    list_voice_clone_library,
    list_voice_clone_sample_page,
)


router = APIRouter()
ROUTE_PREFIX = '/projects/{project_id}/voice-cloning'


def _get_active_project_or_http(session, project_id: str):
    try:
        return get_active_voice_clone_project(session, project_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except VoiceCloneProjectConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _raise_service_error(error: Exception) -> None:
    if isinstance(error, VoiceAssetExpiredError):
        raise HTTPException(status_code=410, detail={'code': 'voice_asset_expired', 'message': str(error)}) from error
    if isinstance(error, LookupError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, VoiceCloneUnavailableError):
        raise HTTPException(
            status_code=409,
            detail={
                'code': error.code,
                'message': '该音色已失效并被清理，请重新选择或上传',
            },
        ) from error
    if isinstance(error, VoiceCloneIdentityUnavailableError):
        raise HTTPException(status_code=503, detail=str(error)) from error
    if isinstance(error, VoiceCloneSampleLimitError):
        raise HTTPException(status_code=409, detail=error.as_detail()) from error
    if isinstance(error, VoiceCloneSampleReviewConflictError):
        raise HTTPException(status_code=409, detail=error.as_detail()) from error
    if isinstance(error, VoiceCloneProjectConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    raise HTTPException(status_code=400, detail=str(error)) from error


@router.get(f'{ROUTE_PREFIX}/provider-status', response_model=VoiceCloneProviderStatusResponse)
def get_project_provider_status(project_id: str) -> VoiceCloneProviderStatusResponse:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        return get_voice_cloning_provider_status()
    finally:
        session.close()


@router.get(f'{ROUTE_PREFIX}/samples', response_model=list[VoiceCloneSampleResponse])
def get_project_samples(project_id: str) -> list[VoiceCloneSampleResponse]:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        return [
            build_voice_clone_sample_response(item)
            for item in list_voice_clone_samples(session)
        ]
    finally:
        session.close()


@router.get(
    f'{ROUTE_PREFIX}/samples/page',
    response_model=VoiceCloneSamplePageResponse,
)
def get_project_sample_page(
    project_id: str,
    q: Optional[str] = Query(None, max_length=255),
    status_filter: Optional[str] = Query(None, alias='status', max_length=32),
    limit: int = Query(30, ge=1, le=100),
    cursor: Optional[str] = Query(None, max_length=32),
) -> VoiceCloneSamplePageResponse:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        return list_voice_clone_sample_page(
            session,
            project_id,
            query=q,
            status=status_filter,
            limit=limit,
            cursor=cursor,
        )
    finally:
        session.close()


@router.get(
    f'{ROUTE_PREFIX}/library',
    response_model=VoiceCloneLibraryPageResponse,
)
def get_project_voice_library(
    project_id: str,
    q: Optional[str] = Query(None, max_length=255),
    limit: int = Query(30, ge=1, le=100),
    cursor: Optional[str] = Query(None, max_length=32),
) -> VoiceCloneLibraryPageResponse:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        return list_voice_clone_library(
            session,
            project_id,
            query=q,
            limit=limit,
            cursor=cursor,
        )
    finally:
        session.close()


@router.get(
    f'{ROUTE_PREFIX}/library/{{group_key}}/profiles',
    response_model=list[VoiceCloneLibraryProfileResponse],
)
def get_project_voice_library_profiles(
    project_id: str,
    group_key: str,
) -> list[VoiceCloneLibraryProfileResponse]:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        return list_voice_clone_group_profiles(session, project_id, group_key)
    finally:
        session.close()


@router.post(
    f'{ROUTE_PREFIX}/samples',
    response_model=VoiceCloneSampleSaveResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_project_sample(
    project_id: str,
    response: Response,
    display_name: str = Form(...),
    file: UploadFile = File(...),
    body_project_id: Optional[str] = Form(None, alias='project_id'),
    program_name: Optional[str] = Form(None),
) -> VoiceCloneSampleSaveResponse:
    session = SessionLocal()
    uploaded_path: Path | None = None
    prepared_path: Path | None = None
    try:
        project = _get_active_project_or_http(session, project_id)
        if str(body_project_id or '').strip() not in {'', project.id}:
            raise HTTPException(status_code=409, detail='请求中的 project_id 与 URL 项目不一致')
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
            project_id=project.id,
            persist_to_library=True,
        )
        if result.reused_existing:
            response.status_code = status.HTTP_200_OK
        return build_voice_clone_sample_save_response(result)
    except HTTPException:
        raise
    except (LookupError, VoiceCloneProjectConflictError, RuntimeError) as error:
        _raise_service_error(error)
        raise AssertionError('unreachable')
    finally:
        session.close()
        remove_local_file(uploaded_path)
        remove_local_file(prepared_path)


@router.get(f'{ROUTE_PREFIX}/samples/{{sample_id}}/audio')
def get_project_sample_audio(project_id: str, sample_id: str):
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
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


@router.get(f'{ROUTE_PREFIX}/samples/{{sample_id}}', response_model=VoiceCloneSampleResponse)
def get_project_sample(project_id: str, sample_id: str) -> VoiceCloneSampleResponse:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        sample = get_voice_clone_sample(session, sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail='声音复刻样本不存在')
        return build_voice_clone_sample_response(sample)
    finally:
        session.close()


@router.delete(f'{ROUTE_PREFIX}/samples/{{sample_id}}', status_code=status.HTTP_204_NO_CONTENT)
def delete_project_sample(project_id: str, sample_id: str) -> Response:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        sample = get_voice_clone_sample(session, sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail='声音复刻样本不存在')
        try:
            delete_voice_clone_sample(session, sample, project_id=project_id)
        except (LookupError, VoiceCloneProjectConflictError, RuntimeError) as error:
            _raise_service_error(error)
            raise AssertionError('unreachable')
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()


@router.get(
    f'{ROUTE_PREFIX}/people/{{person_id}}/samples',
    response_model=list[VoiceprintSampleResponse],
)
def get_project_person_samples(project_id: str, person_id: str) -> list[VoiceprintSampleResponse]:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        if not get_voice_clone_person_for_project(session, project_id, person_id):
            raise HTTPException(status_code=404, detail='当前项目中不存在该人物')
        return [
            build_sample_response(item)
            for item in list_clone_samples(session, person_id, project_id=project_id)
        ]
    finally:
        session.close()


@router.get(f'{ROUTE_PREFIX}/profiles', response_model=list[VoiceCloneProfileResponse])
def get_project_profiles(project_id: str) -> list[VoiceCloneProfileResponse]:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        profiles = list_voice_clone_profiles(session)
        clone_sample_map = {
            item.id: item for item in list_voice_clone_samples(session)
        }
        return [
            build_voice_clone_profile_response(
                profile,
                source_sample=(
                    get_voiceprint_sample_for_voice_clone_project(
                        session,
                        project_id,
                        profile.source_sample_id,
                    )
                    if profile.source_sample_id
                    else None
                ),
                source_clone_sample=clone_sample_map.get(profile.source_clone_sample_id or ''),
            )
            for profile in profiles
        ]
    finally:
        session.close()


@router.get(
    f'{ROUTE_PREFIX}/people/{{person_id}}/profiles',
    response_model=list[VoiceCloneProfileResponse],
)
def get_project_person_profiles(project_id: str, person_id: str) -> list[VoiceCloneProfileResponse]:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        if not get_voice_clone_person_for_project(session, project_id, person_id):
            raise HTTPException(status_code=404, detail='当前项目中不存在该人物')
        profiles = list_voice_clone_profiles_for_project(
            session,
            project_id,
            person_id=person_id,
        )
        sample_map = {item.id: item for item in list_clone_samples(session, person_id, project_id=project_id)}
        return [
            build_voice_clone_profile_response(
                profile,
                source_sample=sample_map.get(profile.source_sample_id or ''),
            )
            for profile in profiles
        ]
    finally:
        session.close()


@router.post(
    f'{ROUTE_PREFIX}/profiles',
    response_model=VoiceCloneProfileCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_project_profile(
    project_id: str,
    payload: VoiceCloneProfileCreateRequest,
) -> VoiceCloneProfileCreateResponse:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        try:
            result = find_or_create_voice_clone_profile(
                session,
                payload,
                project_id=project_id,
            )
        except (LookupError, VoiceCloneProjectConflictError, RuntimeError) as error:
            _raise_service_error(error)
            raise AssertionError('unreachable')
        profile = result.profile
        source_sample = (
            get_voiceprint_sample_for_voice_clone_project(
                session,
                project_id,
                profile.source_sample_id,
            )
            if profile.source_sample_id
            else None
        )
        source_clone_sample = (
            get_voice_clone_sample(session, profile.source_clone_sample_id)
            if profile.source_clone_sample_id
            else None
        )
        profile_response = build_voice_clone_profile_response(
            profile,
            source_sample=source_sample,
            source_clone_sample=source_clone_sample,
        )
        return VoiceCloneProfileCreateResponse(
            **profile_response.model_dump(),
            reused_existing=result.reused_existing,
            message=(
                '检测到相同音色，已直接复用'
                if result.reused_existing
                else {'creating': '音色正在云端创建', 'ready': '音色已创建'}.get(profile.status, '音色创建失败')
            ),
        )
    finally:
        session.close()


@router.get(
    f'{ROUTE_PREFIX}/profiles/creating',
    response_model=list[VoiceCloneLibraryProfileResponse],
)
def get_project_creating_voice_profiles(
    project_id: str,
) -> list[VoiceCloneLibraryProfileResponse]:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        return list_creating_voice_clone_profiles(session, project_id)
    finally:
        session.close()


@router.get(f'{ROUTE_PREFIX}/profiles/{{profile_id}}', response_model=VoiceCloneProfileResponse)
def get_project_profile(project_id: str, profile_id: str) -> VoiceCloneProfileResponse:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        profile = get_voice_clone_profile(session, profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail='音色不存在')
        try:
            profile = refresh_voice_clone_profile(session, profile)
        except VoiceAssetExpiredError as error:
            _raise_service_error(error)
        source_sample = (
            get_voiceprint_sample_for_voice_clone_project(
                session,
                project_id,
                profile.source_sample_id,
            )
            if profile.source_sample_id
            else None
        )
        source_clone_sample = (
            get_voice_clone_sample(session, profile.source_clone_sample_id)
            if profile.source_clone_sample_id
            else None
        )
        return build_voice_clone_profile_response(
            profile,
            source_sample=source_sample,
            source_clone_sample=source_clone_sample,
        )
    finally:
        session.close()


@router.delete(f'{ROUTE_PREFIX}/profiles/{{profile_id}}', response_model=VoiceCloneDeleteResponse)
def delete_project_profile(project_id: str, profile_id: str) -> VoiceCloneDeleteResponse:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        try:
            profile = delete_voice_clone_profile(session, profile_id, project_id=project_id)
        except (LookupError, VoiceCloneProjectConflictError, RuntimeError) as error:
            _raise_service_error(error)
            raise AssertionError('unreachable')
        return VoiceCloneDeleteResponse(id=profile.id, deleted=True)
    finally:
        session.close()


@router.post(
    f'{ROUTE_PREFIX}/profiles/{{profile_id}}/preview',
    response_model=VoiceCloneSynthesisRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project_profile_preview(
    project_id: str,
    profile_id: str,
    payload: VoiceClonePreviewRequest,
) -> VoiceCloneSynthesisRecordResponse:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        try:
            record = synthesize_voice_clone_preview(
                session,
                profile_id,
                payload,
                project_id=project_id,
            )
        except LookupError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    'code': VoiceCloneUnavailableError.code,
                    'message': '该音色已失效并被清理，请重新选择或上传',
                },
            ) from error
        except (VoiceCloneProjectConflictError, RuntimeError) as error:
            _raise_service_error(error)
            raise AssertionError('unreachable')
        return build_voice_clone_synthesis_record_response(record)
    finally:
        session.close()


@router.get(
    f'{ROUTE_PREFIX}/profiles/{{profile_id}}/synthesis-records',
    response_model=list[VoiceCloneSynthesisRecordResponse],
)
def get_project_profile_synthesis_records(
    project_id: str,
    profile_id: str,
) -> list[VoiceCloneSynthesisRecordResponse]:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        if not get_voice_clone_profile(session, profile_id):
            raise HTTPException(status_code=404, detail='音色不存在')
        return [
            build_voice_clone_synthesis_record_response(record)
            for record in list_voice_clone_synthesis_records(
                session,
                profile_id,
                project_id=project_id,
            )
        ]
    finally:
        session.close()


@router.get(f'{ROUTE_PREFIX}/synthesis-records', response_model=list[VoiceCloneSynthesisRecordResponse])
def get_project_synthesis_records(project_id: str, limit: int = Query(30, ge=1, le=100)) -> list[VoiceCloneSynthesisRecordResponse]:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        return [build_voice_clone_synthesis_record_response(record) for record in
                list_voice_clone_synthesis_records(session, project_id=project_id, limit=limit)]
    finally:
        session.close()


@router.get(
    f'{ROUTE_PREFIX}/synthesis-records/{{record_id}}',
    response_model=VoiceCloneSynthesisRecordResponse,
)
def get_project_synthesis_record(
    project_id: str,
    record_id: str,
) -> VoiceCloneSynthesisRecordResponse:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        record = get_voice_clone_synthesis_record_for_project(session, project_id, record_id)
        if not record:
            raise HTTPException(status_code=404, detail='当前项目中不存在该试听记录')
        return build_voice_clone_synthesis_record_response(record)
    finally:
        session.close()


@router.get(f'{ROUTE_PREFIX}/synthesis-records/{{record_id}}/audio')
def get_project_synthesis_record_audio(
    project_id: str,
    record_id: str,
):
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        record = get_voice_clone_synthesis_record_for_project(session, project_id, record_id)
        if not record:
            raise HTTPException(status_code=404, detail='当前项目中不存在该试听记录')
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


@router.post(f'{ROUTE_PREFIX}/synthesis-records/{{record_id}}/download-complete', status_code=status.HTTP_204_NO_CONTENT)
def post_project_download_complete(project_id: str, record_id: str, background_tasks: BackgroundTasks) -> Response:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        try:
            complete_voice_clone_download(session, project_id, record_id)
        except (LookupError, RuntimeError) as error:
            _raise_service_error(error)
        background_tasks.add_task(_drain_download_cleanup, project_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()


def _drain_download_cleanup(project_id: str) -> None:
    from app.services.voice_clone_cleanup import drain_voice_clone_deletion_tasks

    session = SessionLocal()
    try:
        drain_voice_clone_deletion_tasks(session, limit=100, project_id=project_id)
    finally:
        session.close()


@router.delete(
    f'{ROUTE_PREFIX}/synthesis-records/{{record_id}}',
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_project_synthesis_record(project_id: str, record_id: str) -> Response:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        record = get_voice_clone_synthesis_record_for_project(session, project_id, record_id)
        if not record:
            raise HTTPException(status_code=404, detail='当前项目中不存在该生成音频记录')
        delete_voice_clone_synthesis_record(session, record)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()


@router.get(f'{ROUTE_PREFIX}/library/{{group_key}}/samples', response_model=list[VoiceCloneSamplePageItemResponse])
def get_library_group_samples(project_id: str, group_key: str) -> list[VoiceCloneSamplePageItemResponse]:
    session = SessionLocal()
    try:
        _get_active_project_or_http(session, project_id)
        return list_voice_clone_group_samples(session, group_key, project_id)
    finally:
        session.close()
