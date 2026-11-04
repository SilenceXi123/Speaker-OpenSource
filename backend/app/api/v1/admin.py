from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel
from app.core.config import settings
from app.db.session import SessionLocal
from app.schemas.admin import (
    AdminProviderCostReportResponse,
    JobEventListResponse,
    JobEventResponse,
    AdminLoginResponse,
    PriceCatalogCreateRequest,
    PriceCatalogEntryResponse,
    ProviderCostReadinessResponse,
    StageTwoSmokeReportResponse,
    StorageCleanupReportResponse,
)
from app.schemas.client_update import ClientUpdateAdminRegisterRequest, ClientUpdateReleaseSummary
from app.schemas.job import PipelineArtifactResponse, PipelineArtifactSummary
from app.services.admin_auth import (
    authenticate_admin,
    clear_admin_session_cookie,
    require_admin_session,
    write_admin_session_cookie,
)
from app.services.admin_usage import (
    build_admin_provider_cost_report,
    create_price_catalog_version,
    export_admin_provider_cost_csv,
    list_price_catalog_entries,
)
from app.services.client_updates import (
    ClientUpdateManifestError,
    list_client_update_releases,
    register_client_update_release,
)
from app.services.job_events import append_job_event, count_job_events, list_job_events
from app.services.jobs import get_job
from app.services.pipeline_artifact_schema import PipelineArtifactSchemaError, summarize_pipeline_artifacts
from app.services.provider_cost_readiness import build_provider_cost_readiness
from app.services.stage_two_smoke import build_stage_two_smoke_report
from app.services.storage_cleanup import build_storage_cleanup_plan, run_storage_cleanup
from app.services.voice_clone_identity_review import (
    list_pending_identity_reviews,
    resolve_identity_review,
)

router = APIRouter()
CHINA_TZ = timezone(timedelta(hours=8))


class VoiceCloneIdentityReviewResolveRequest(BaseModel):
    action: str
    reason: Optional[str] = None


def _require_same_origin_admin(
    request: Request,
    username: str = Depends(require_admin_session),
) -> str:
    origin = request.headers.get('origin')
    if origin is not None and origin != f'{request.url.scheme}://{request.headers.get("host", "")}':
        raise HTTPException(status_code=403, detail='跨源管理员请求已拒绝')
    return username


def _build_admin_pipeline_artifact_response(session, job_id: str) -> PipelineArtifactResponse:
    job = get_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail='任务不存在')
    artifacts = dict(job.pipeline_artifacts or {})
    try:
        summary = summarize_pipeline_artifacts(artifacts)
    except PipelineArtifactSchemaError as exc:
        raise HTTPException(status_code=409, detail=f'任务缺少有效的 pipeline artifacts：{exc}') from exc
    return PipelineArtifactResponse(
        summary=PipelineArtifactSummary(**summary),
        artifacts=artifacts,
    )

def _sanitize_update_channel(channel: str) -> str:
    safe_channel = channel.strip()
    if not safe_channel or Path(safe_channel).name != safe_channel:
        raise HTTPException(status_code=400, detail='客户端更新通道不合法')
    return safe_channel


def _sanitize_update_filename(upload: UploadFile, expected_suffix: str, label: str) -> str:
    filename = Path(upload.filename or '').name
    if not filename:
        raise HTTPException(status_code=400, detail=f'{label}文件名不能为空')
    if filename != (upload.filename or ''):
        raise HTTPException(status_code=400, detail=f'{label}文件名不允许包含路径')
    if not filename.endswith(expected_suffix):
        raise HTTPException(status_code=400, detail=f'{label}必须是 {expected_suffix} 文件')
    return filename


def _save_update_upload(upload: UploadFile, destination: Path, label: str) -> None:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('wb') as file_obj:
            shutil.copyfileobj(upload.file, file_obj)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f'{label}保存失败: {exc}') from exc


@router.post('/login', response_model=AdminLoginResponse)
def post_admin_login(
    response: Response,
    username: Annotated[str, Form(...)],
    password: Annotated[str, Form(...)],
) -> AdminLoginResponse:
    authenticated_username = authenticate_admin(username, password)
    write_admin_session_cookie(response, authenticated_username)
    return AdminLoginResponse(username=authenticated_username)


@router.post('/logout', status_code=status.HTTP_204_NO_CONTENT)
def post_admin_logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_admin_session_cookie(response)
    return response


@router.get('/me', response_model=AdminLoginResponse)
def get_admin_me(username: str = Depends(require_admin_session)) -> AdminLoginResponse:
    return AdminLoginResponse(username=username)


@router.get('/voice-clone-identities/reviews')
def get_admin_voice_clone_identity_reviews(
    _username: str = Depends(_require_same_origin_admin),
) -> list[dict]:
    session = SessionLocal()
    try:
        return list_pending_identity_reviews(session)
    finally:
        session.close()


@router.post('/voice-clone-identities/reviews/{review_id}/resolve')
def post_admin_voice_clone_identity_review_resolution(
    review_id: str,
    payload: VoiceCloneIdentityReviewResolveRequest,
    username: str = Depends(_require_same_origin_admin),
) -> dict:
    session = SessionLocal()
    try:
        try:
            review = resolve_identity_review(
                session,
                review_id,
                payload.action,
                username,
                payload.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            'id': review.id,
            'sample_id': review.sample_id,
            'status': review.status,
            'resolved_by': review.resolved_by,
            'resolved_at': review.resolved_at,
            'resolution_reason': review.resolution_reason,
        }
    finally:
        session.close()


@router.get('/client-updates/releases', response_model=list[ClientUpdateReleaseSummary])
def get_admin_client_update_releases(
    _username: str = Depends(require_admin_session),
) -> list[ClientUpdateReleaseSummary]:
    try:
        return list_client_update_releases(settings.client_update_dir_path)
    except ClientUpdateManifestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post('/client-updates/releases', response_model=list[ClientUpdateReleaseSummary])
def post_admin_client_update_release(
    payload: ClientUpdateAdminRegisterRequest,
    _username: str = Depends(require_admin_session),
) -> list[ClientUpdateReleaseSummary]:
    try:
        register_client_update_release(settings.client_update_dir_path, payload)
    except ClientUpdateManifestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return list_client_update_releases(settings.client_update_dir_path)


@router.post('/client-updates/releases/upload', response_model=list[ClientUpdateReleaseSummary])
def post_admin_client_update_upload_release(
    channel: Annotated[str, Form(...)],
    version: Annotated[str, Form(...)],
    package: Annotated[UploadFile, File(...)],
    signature_file: Annotated[UploadFile, File(...)],
    target: Annotated[str, Form()] = 'darwin',
    arch: Annotated[str, Form()] = 'universal',
    notes: Annotated[str, Form()] = '',
    mandatory: Annotated[bool, Form()] = False,
    minimum_supported_version: Annotated[Optional[str], Form()] = None,
    _username: str = Depends(require_admin_session),
) -> list[ClientUpdateReleaseSummary]:
    safe_channel = _sanitize_update_channel(channel)
    package_filename = _sanitize_update_filename(package, '.app.tar.gz', '客户端更新包')
    signature_filename = _sanitize_update_filename(signature_file, '.sig', '签名文件')
    expected_signature_filename = f'{package_filename}.sig'
    if signature_filename != expected_signature_filename:
        raise HTTPException(
            status_code=400,
            detail=f'签名文件名需要与更新包匹配，例如 {expected_signature_filename}',
        )

    package_relative_path = f'packages/{safe_channel}/{package_filename}'
    signature_relative_path = f'packages/{safe_channel}/{signature_filename}'
    package_path = settings.client_update_dir_path / package_relative_path
    signature_path = settings.client_update_dir_path / signature_relative_path

    _save_update_upload(package, package_path, '客户端更新包')
    _save_update_upload(signature_file, signature_path, '签名文件')

    if package_path.stat().st_size <= 0:
        raise HTTPException(status_code=400, detail='客户端更新包不能为空')

    try:
        signature = signature_path.read_text(encoding='utf-8').strip()
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail='签名文件必须是 UTF-8 文本') from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f'签名文件读取失败: {exc}') from exc

    if not signature:
        raise HTTPException(status_code=400, detail='签名文件内容不能为空')

    payload = ClientUpdateAdminRegisterRequest(
        channel=safe_channel,
        version=version.strip(),
        target=target.strip() or 'darwin',
        arch=arch.strip() or 'universal',
        package_file=package_relative_path,
        signature=signature,
        pub_date=datetime.now(CHINA_TZ).isoformat(timespec='seconds'),
        notes=notes.strip(),
        mandatory=mandatory,
        minimum_supported_version=minimum_supported_version.strip() if minimum_supported_version else None,
        mark_latest=True,
    )

    try:
        register_client_update_release(settings.client_update_dir_path, payload)
    except ClientUpdateManifestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return list_client_update_releases(settings.client_update_dir_path)


@router.get('/provider-costs/readiness', response_model=ProviderCostReadinessResponse)
def get_admin_provider_cost_readiness(
    _username: str = Depends(require_admin_session),
) -> ProviderCostReadinessResponse:
    session = SessionLocal()
    try:
        return build_provider_cost_readiness(session)
    finally:
        session.close()


@router.get('/provider-costs', response_model=AdminProviderCostReportResponse)
def get_admin_provider_costs(
    project_id: Annotated[Optional[str], Query()] = None,
    job_id: Annotated[Optional[str], Query()] = None,
    provider: Annotated[Optional[str], Query()] = None,
    model: Annotated[Optional[str], Query()] = None,
    date_from: Annotated[Optional[date], Query()] = None,
    date_to: Annotated[Optional[date], Query()] = None,
    _username: str = Depends(require_admin_session),
) -> AdminProviderCostReportResponse:
    session = SessionLocal()
    try:
        return build_admin_provider_cost_report(
            session,
            project_id=project_id,
            job_id=job_id,
            provider=provider,
            model=model,
            date_from=date_from,
            date_to=date_to,
        )
    finally:
        session.close()


@router.get('/provider-costs/export.csv')
def get_admin_provider_costs_export_csv(
    project_id: Annotated[Optional[str], Query()] = None,
    job_id: Annotated[Optional[str], Query()] = None,
    provider: Annotated[Optional[str], Query()] = None,
    model: Annotated[Optional[str], Query()] = None,
    date_from: Annotated[Optional[date], Query()] = None,
    date_to: Annotated[Optional[date], Query()] = None,
    _username: str = Depends(require_admin_session),
) -> Response:
    session = SessionLocal()
    try:
        content = export_admin_provider_cost_csv(
            session,
            project_id=project_id,
            job_id=job_id,
            provider=provider,
            model=model,
            date_from=date_from,
            date_to=date_to,
        )
    finally:
        session.close()
    return Response(
        content=content,
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename="speaker-provider-costs.csv"'},
    )


@router.get('/provider-costs/prices', response_model=list[PriceCatalogEntryResponse])
def get_admin_provider_cost_prices(
    provider: Annotated[Optional[str], Query()] = None,
    model: Annotated[Optional[str], Query()] = None,
    _username: str = Depends(require_admin_session),
) -> list[PriceCatalogEntryResponse]:
    session = SessionLocal()
    try:
        return list_price_catalog_entries(session, provider=provider, model=model)
    finally:
        session.close()


@router.post('/provider-costs/prices', response_model=PriceCatalogEntryResponse)
def post_admin_provider_cost_price(
    payload: PriceCatalogCreateRequest,
    _username: str = Depends(require_admin_session),
) -> PriceCatalogEntryResponse:
    session = SessionLocal()
    try:
        try:
            return create_price_catalog_version(session, payload)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.get('/pipeline-artifacts/jobs/{job_id}', response_model=PipelineArtifactResponse)
def get_admin_job_pipeline_artifacts(
    job_id: str,
    _username: str = Depends(require_admin_session),
) -> PipelineArtifactResponse:
    session = SessionLocal()
    try:
        response = _build_admin_pipeline_artifact_response(session, job_id)
        job = get_job(session, job_id)
        if job:
            append_job_event(
                session,
                job=job,
                event_type='pipeline_artifact_checked',
                stage='pipeline_artifacts',
                status=job.status,
                progress=job.processing_progress,
                message='管理员检查 pipeline artifacts',
                details={'cue_hash': response.summary.cue_hash, 'p0_count': response.summary.p0_count},
            )
            session.refresh(job)
        return response
    finally:
        session.close()


@router.get('/pipeline-artifacts/jobs/{job_id}/export.json')
def get_admin_job_pipeline_artifacts_export(
    job_id: str,
    _username: str = Depends(require_admin_session),
) -> Response:
    session = SessionLocal()
    try:
        artifact_response = _build_admin_pipeline_artifact_response(session, job_id)
        job = get_job(session, job_id)
        if job:
            append_job_event(
                session,
                job=job,
                event_type='pipeline_artifact_exported',
                stage='pipeline_artifacts',
                status=job.status,
                progress=job.processing_progress,
                message='管理员下载 pipeline artifacts 验收包',
                details={'cue_hash': artifact_response.summary.cue_hash, 'p0_count': artifact_response.summary.p0_count},
            )
            session.refresh(job)
        payload = artifact_response.model_dump(mode='json')
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        safe_job_id = ''.join(char if char.isalnum() or char in ('-', '_') else '-' for char in job_id)
        return Response(
            content=content,
            media_type='application/json; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename="speaker-pipeline-artifacts-{safe_job_id}.json"',
            },
        )
    finally:
        session.close()


@router.get('/jobs/{job_id}/events', response_model=JobEventListResponse)
def get_admin_job_events(
    job_id: str,
    _username: str = Depends(require_admin_session),
) -> JobEventListResponse:
    session = SessionLocal()
    try:
        job = get_job(session, job_id)
        events = list_job_events(session, job_id)
        total = count_job_events(session, job_id)
        if not job and not events:
            raise HTTPException(status_code=404, detail='任务不存在')
        return JobEventListResponse(
            job_id=str(job_id),
            total=total,
            events=[
                JobEventResponse(
                    id=event.id,
                    job_id=event.job_id,
                    job_kind=event.job_kind,
                    program_key=event.program_key,
                    program_name=event.program_name,
                    event_type=event.event_type,
                    stage=event.stage,
                    status=event.status,
                    progress=event.progress,
                    message=event.message,
                    details=event.details,
                    created_at=event.created_at,
                )
                for event in events
            ],
        )
    finally:
        session.close()


@router.get('/stage-two-smoke/jobs/{job_id}', response_model=StageTwoSmokeReportResponse)
def get_admin_job_stage_two_smoke(
    job_id: str,
    _username: str = Depends(require_admin_session),
) -> StageTwoSmokeReportResponse:
    session = SessionLocal()
    try:
        job = get_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail='任务不存在')
        events = list_job_events(session, job_id)
        return StageTwoSmokeReportResponse(**build_stage_two_smoke_report(job=job, events=events))
    finally:
        session.close()


def _build_storage_cleanup_response(report) -> StorageCleanupReportResponse:
    return StorageCleanupReportResponse(
        dry_run=report.dry_run,
        total_bytes=report.total_bytes,
        deleted_bytes=report.deleted_bytes,
        item_count=report.item_count,
        deleted_count=report.deleted_count,
        items=[item.__dict__ for item in report.items],
    )


@router.get('/storage-cleanup/preview', response_model=StorageCleanupReportResponse)
def get_admin_storage_cleanup_preview(
    _username: str = Depends(require_admin_session),
) -> StorageCleanupReportResponse:
    session = SessionLocal()
    try:
        return _build_storage_cleanup_response(build_storage_cleanup_plan(session))
    finally:
        session.close()


@router.post('/storage-cleanup/run', response_model=StorageCleanupReportResponse)
def post_admin_storage_cleanup_run(
    _username: str = Depends(require_admin_session),
) -> StorageCleanupReportResponse:
    session = SessionLocal()
    try:
        return _build_storage_cleanup_response(run_storage_cleanup(session))
    finally:
        session.close()
