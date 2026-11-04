from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.db.session import SessionLocal
from app.schemas.voiceprint import PersonCreateRequest, PersonResponse, VoiceprintSampleResponse
from app.services.projects import get_project
from app.services.local_files import remove_local_file, save_upload_file_locally
from app.services.voiceprints import (
    build_sample_response,
    build_person_response,
    create_person,
    get_person,
    list_people,
    list_person_samples,
    prepare_voiceprint_sample_assets,
    save_voiceprint_sample,
)

router = APIRouter()


def _get_active_project(session, project_id: str):
    project = get_project(session, project_id)
    if not project:
        raise HTTPException(status_code=404, detail='项目不存在')
    if project.status != 'active':
        raise HTTPException(status_code=409, detail='项目已归档')
    return project


@router.get('/projects/{project_id}/voiceprints/people', response_model=list[PersonResponse])
def get_project_people(project_id: str) -> list[PersonResponse]:
    session = SessionLocal()
    try:
        project = _get_active_project(session, project_id)
        people = list_people(
            session,
            project_id=project.id,
            include_global=True,
        )
        return [
            build_person_response(person, list_person_samples(session, person.id))
            for person in people
        ]
    finally:
        session.close()


@router.post(
    '/projects/{project_id}/voiceprints/people',
    response_model=PersonResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_project_person(project_id: str, payload: PersonCreateRequest) -> PersonResponse:
    session = SessionLocal()
    try:
        project = _get_active_project(session, project_id)
        try:
            person = create_person(session, payload, project_id=project.id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return build_person_response(person, [])
    finally:
        session.close()


@router.post(
    '/projects/{project_id}/voiceprints/people/{person_id}/samples',
    response_model=VoiceprintSampleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_project_person_sample(
    project_id: str,
    person_id: str,
    file: UploadFile = File(...),
) -> VoiceprintSampleResponse:
    session = SessionLocal()
    uploaded_path: Path | None = None
    try:
        project = _get_active_project(session, project_id)
        person = get_person(session, person_id)
        allowed_person_ids = {
            item.id for item in list_people(session, project_id=project.id, include_global=False)
        }
        if not person or person.id not in allowed_person_ids:
            raise HTTPException(status_code=404, detail='当前项目中不存在该人物')
        if not person.project_id:
            person.project_id = project.id
            session.commit()
            session.refresh(person)
        uploaded_path = await save_upload_file_locally(file)
        sample_assets = await run_in_threadpool(
            prepare_voiceprint_sample_assets,
            uploaded_path,
            file.filename or uploaded_path.name,
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
