from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.db.session import SessionLocal
from app.schemas.voiceprint import (
    PersonCreateRequest,
    PersonResponse,
    ProgramCreateRequest,
    ProgramDeleteResponse,
    ProgramSummaryResponse,
    VoiceprintProviderStatusResponse,
    VoiceprintSampleResponse,
)
from app.services.local_files import remove_local_file, save_upload_file_locally
from app.services.voiceprint_provider import get_voiceprint_provider_status
from app.services.voiceprints import (
    build_person_response,
    build_sample_response,
    confirm_voiceprint_candidate,
    create_program_summary,
    create_person,
    delete_empty_program,
    delete_person,
    delete_voiceprint_sample,
    get_person,
    list_program_summaries,
    get_voiceprint_sample,
    list_people,
    list_person_samples,
    prepare_voiceprint_sample_assets,
    save_voiceprint_sample,
)
from app.services.speaker_memory import build_program_key, normalize_program_name
from app.services.projects import ensure_legacy_project, get_project_by_name, set_project_status

router = APIRouter()


@router.get('/provider-status', response_model=VoiceprintProviderStatusResponse)
def get_provider_status() -> VoiceprintProviderStatusResponse:
    status_payload = get_voiceprint_provider_status()
    return VoiceprintProviderStatusResponse(
        provider=status_payload.provider,
        model_id=status_payload.model_id,
        available=status_payload.available,
        message=status_payload.message,
    )


@router.get('/people', response_model=list[PersonResponse])
def get_people() -> list[PersonResponse]:
    session = SessionLocal()
    try:
        people = list_people(session)
        return [build_person_response(person, list_person_samples(session, person.id)) for person in people]
    finally:
        session.close()


@router.get('/programs', response_model=list[ProgramSummaryResponse])
def get_programs() -> list[ProgramSummaryResponse]:
    session = SessionLocal()
    try:
        return list_program_summaries(session)
    finally:
        session.close()


@router.post('/programs', response_model=ProgramSummaryResponse, status_code=status.HTTP_201_CREATED)
def post_program(payload: ProgramCreateRequest) -> ProgramSummaryResponse:
    session = SessionLocal()
    try:
        normalized_name = normalize_program_name(payload.program_name)
        if not normalized_name:
            raise HTTPException(status_code=400, detail='项目名称不能为空')
        for item in list_program_summaries(session):
            if item.program_key == build_program_key(normalized_name):
                ensure_legacy_project(session, normalized_name)
                return item
        result = create_program_summary(session, normalized_name)
        ensure_legacy_project(session, normalized_name)
        return result
    finally:
        session.close()


@router.delete('/programs/{program_key}', response_model=ProgramDeleteResponse)
def delete_program(program_key: str) -> ProgramDeleteResponse:
    session = SessionLocal()
    try:
        try:
            response = delete_empty_program(session, program_key)
            project = get_project_by_name(session, program_key)
            if project and project.status == 'active':
                set_project_status(session, project.id, 'archived')
            return response
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        session.close()


@router.post('/people', response_model=PersonResponse, status_code=status.HTTP_201_CREATED)
def post_person(payload: PersonCreateRequest) -> PersonResponse:
    session = SessionLocal()
    try:
        try:
            person = create_person(session, payload)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return build_person_response(person, [])
    finally:
        session.close()


@router.get('/people/{person_id}', response_model=PersonResponse)
def get_person_detail(person_id: str) -> PersonResponse:
    session = SessionLocal()
    try:
        person = get_person(session, person_id)
        if not person:
            raise HTTPException(status_code=404, detail='人物不存在')
        return build_person_response(person, list_person_samples(session, person.id))
    finally:
        session.close()


@router.delete('/people/{person_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_person_detail(person_id: str) -> Response:
    session = SessionLocal()
    try:
        person = get_person(session, person_id)
        if not person:
            raise HTTPException(status_code=404, detail='人物不存在')
        delete_person(session, person)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()


@router.post('/people/{person_id}/samples', response_model=VoiceprintSampleResponse, status_code=status.HTTP_201_CREATED)
async def post_person_sample(person_id: str, file: UploadFile = File(...)) -> VoiceprintSampleResponse:
    session = SessionLocal()
    uploaded_path: Path | None = None
    try:
        person = get_person(session, person_id)
        if not person:
            raise HTTPException(status_code=404, detail='人物不存在')
        uploaded_path = await save_upload_file_locally(file)
        sample_assets = await run_in_threadpool(
            prepare_voiceprint_sample_assets, uploaded_path, file.filename or uploaded_path.name,
        )
        sample = await run_in_threadpool(
            save_voiceprint_sample,
            session=session,
            person=person,
            audio_local_path=sample_assets.voiceprint_audio_path,
            clone_audio_local_path=sample_assets.clone_audio_path,
            original_filename=file.filename or sample_assets.voiceprint_audio_path.name,
        )
        return build_sample_response(sample)
    finally:
        session.close()
        remove_local_file(uploaded_path)


@router.get('/samples/{sample_id}/audio')
def get_sample_audio(sample_id: str):
    session = SessionLocal()
    try:
        sample = get_voiceprint_sample(session, sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail='样本不存在')
        local_path_value = str(sample.clone_audio_local_path or sample.audio_local_path or '').strip()
        local_path = Path(local_path_value) if local_path_value else None
        if local_path and local_path.is_file():
            media_type = mimetypes.guess_type(local_path.name)[0] or 'application/octet-stream'
            return FileResponse(path=local_path, media_type=media_type)
        public_url = str(sample.clone_audio_public_url or sample.audio_public_url or '').strip()
        if public_url:
            return RedirectResponse(public_url)
        raise HTTPException(status_code=404, detail='样本音频不存在')
    finally:
        session.close()


@router.delete('/samples/{sample_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_sample(sample_id: str) -> Response:
    session = SessionLocal()
    try:
        sample = get_voiceprint_sample(session, sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail='样本不存在')
        delete_voiceprint_sample(session, sample)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()


@router.post('/samples/{sample_id}/confirm', response_model=VoiceprintSampleResponse)
def confirm_sample(sample_id: str) -> VoiceprintSampleResponse:
    session = SessionLocal()
    try:
        sample = get_voiceprint_sample(session, sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail='样本不存在')
        sample = confirm_voiceprint_candidate(session, sample)
        return build_sample_response(sample)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    finally:
        session.close()


@router.post('/samples/{sample_id}/reject', status_code=status.HTTP_204_NO_CONTENT)
def reject_sample(sample_id: str) -> Response:
    session = SessionLocal()
    try:
        sample = get_voiceprint_sample(session, sample_id)
        if not sample:
            raise HTTPException(status_code=404, detail='样本不存在')
        delete_voiceprint_sample(session, sample)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()
