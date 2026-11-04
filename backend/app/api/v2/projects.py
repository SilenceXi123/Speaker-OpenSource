from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response

from app.db.session import SessionLocal
from app.schemas.project import ProjectCreateRequest, ProjectResponse, ProjectUpdateRequest
from app.services.projects import (
    build_project_response,
    create_project,
    get_project,
    list_projects,
    purge_project,
    set_project_status,
    update_project,
)

router = APIRouter()


@router.get('', response_model=list[ProjectResponse])
def get_projects(include_archived: bool = Query(default=False)) -> list[ProjectResponse]:
    session = SessionLocal()
    try:
        return [
            build_project_response(session, project)
            for project in list_projects(session, include_archived=include_archived)
        ]
    finally:
        session.close()


@router.post('', response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def post_project(payload: ProjectCreateRequest) -> ProjectResponse:
    session = SessionLocal()
    try:
        try:
            project = create_project(session, payload)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return build_project_response(session, project)
    finally:
        session.close()


@router.get('/{project_id}', response_model=ProjectResponse)
def get_project_detail(project_id: str) -> ProjectResponse:
    session = SessionLocal()
    try:
        project = get_project(session, project_id)
        if not project:
            raise HTTPException(status_code=404, detail='项目不存在')
        return build_project_response(session, project)
    finally:
        session.close()


@router.patch('/{project_id}', response_model=ProjectResponse)
def patch_project(project_id: str, payload: ProjectUpdateRequest) -> ProjectResponse:
    session = SessionLocal()
    try:
        try:
            project = update_project(session, project_id, payload)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return build_project_response(session, project)
    finally:
        session.close()


@router.post('/{project_id}/archive', response_model=ProjectResponse)
def archive_project(project_id: str) -> ProjectResponse:
    return _change_project_status(project_id, 'archived')


@router.post('/{project_id}/restore', response_model=ProjectResponse)
def restore_project(project_id: str) -> ProjectResponse:
    return _change_project_status(project_id, 'active')


@router.delete('/{project_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_project_permanently(project_id: str) -> Response:
    session = SessionLocal()
    try:
        try:
            purge_project(session, project_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()


def _change_project_status(project_id: str, project_status: str) -> ProjectResponse:
    session = SessionLocal()
    try:
        try:
            project = set_project_status(session, project_id, project_status)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return build_project_response(session, project)
    finally:
        session.close()
