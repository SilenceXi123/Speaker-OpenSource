from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import FileResponse

from app.core.config import settings
from app.schemas.client_update import ClientUpdateEventRequest, TauriUpdateResponse
from app.services.client_updates import (
    ClientUpdateManifestError,
    build_tauri_update_response,
    load_client_update_manifest,
    resolve_client_update_package_path,
)

router = APIRouter()


def _public_update_base_url() -> str:
    configured_url = settings.client_update_public_base_url.strip().rstrip('/')
    return configured_url or '/api/v1/client-updates'


@router.get('/latest/{target}/{arch}/{current_version}', response_model=TauriUpdateResponse)
def get_latest_client_update(
    target: str,
    arch: str,
    current_version: str,
    channel: str = 'stable-macos12',
) -> TauriUpdateResponse | Response:
    if not settings.client_update_enabled:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    manifest_path = settings.client_update_dir_path / 'manifest.json'
    try:
        manifest = load_client_update_manifest(manifest_path)
        payload = build_tauri_update_response(
            manifest,
            channel=channel,
            target=target,
            arch=arch,
            current_version=current_version,
            public_base_url=_public_update_base_url(),
        )
    except ClientUpdateManifestError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if payload is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return payload


@router.get('/packages/{package_path:path}')
def download_client_update_package(package_path: str) -> FileResponse:
    package_file = package_path if package_path.startswith('packages/') else f'packages/{package_path}'
    try:
        path = resolve_client_update_package_path(settings.client_update_dir_path, package_file)
    except ClientUpdateManifestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail='客户端更新包不存在')

    return FileResponse(
        path,
        filename=path.name,
        media_type='application/octet-stream',
    )


@router.post('/events', status_code=status.HTTP_204_NO_CONTENT)
def post_client_update_event(payload: ClientUpdateEventRequest) -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)
