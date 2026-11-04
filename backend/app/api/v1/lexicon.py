from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.job import Job
from app.models.lexicon_candidate import LexiconCandidate
from app.models.lexicon_import import LexiconImport
from app.models.lexicon_occurrence import LexiconOccurrence
from app.models.lexicon_term import LexiconTerm
from app.models.lexicon_term_override import LexiconTermOverride
from app.schemas.lexicon import LexiconCandidateResponse, LexiconTermResponse
from app.services.lexicon_imports import import_lexicon_candidates_from_text, list_project_script_evidence
from app.services.lexicon_normalize import normalize_term
from app.services.lexicon_repository import (
    CANDIDATE_STATUSES,
    PROTECTION_LEVELS,
    TERM_ACTIVE_STATUSES,
    create_project_term,
    disable_term_for_scope,
    promote_candidate_to_term,
    reject_candidate,
)
from app.services.normalize import normalize_funasr_payload
from app.services.pipeline_artifacts import build_pipeline_artifacts
from app.services.projects import get_project, resolve_project_scope_ids
from app.services.runtime_lexicon import build_runtime_lexicon_bundle
from app.services.script_context import extract_text_from_script_bytes
from app.services.utterance_rebuilder import rebuild_semantic_utterances


TENANT_ID = "default"
router = APIRouter()


class LexiconTermCreateBody(BaseModel):
    project_id: Optional[str] = None
    scope_type: str = "project"
    display_text: str
    term_type: str = "term"
    protection_level: str = "strong"
    source_type: str = "manual"
    created_by: str = "client"


class LexiconTermPatchBody(BaseModel):
    display_text: Optional[str] = None
    status: Optional[str] = None
    protection_level: Optional[str] = None


class DisableForProjectBody(BaseModel):
    project_id: str
    reason: str = ""
    created_by: str = "client"


class CandidateReviewBody(BaseModel):
    reviewed_by: str = "client"
    reason: str = ""


class RuntimeBundlePreviewBody(BaseModel):
    project_id: str
    temporary_terms: List[Union[str, Dict[str, Any]]] = Field(default_factory=list)


class RuntimeBundleRebuildBody(BaseModel):
    project_id: Optional[str] = None
    temporary_terms: List[Union[str, Dict[str, Any]]] = Field(default_factory=list)


class LexiconTermListResponse(BaseModel):
    items: list[LexiconTermResponse]


class LexiconCandidateListResponse(BaseModel):
    items: list[LexiconCandidateResponse]


class LexiconOccurrenceListResponse(BaseModel):
    items: list[dict[str, Any]]


def _session() -> Session:
    return SessionLocal()


def _term_response(term: LexiconTerm) -> LexiconTermResponse:
    return LexiconTermResponse.model_validate(term)


def _candidate_response(candidate: LexiconCandidate) -> LexiconCandidateResponse:
    return LexiconCandidateResponse.model_validate(candidate)


@router.get("/terms", response_model=LexiconTermListResponse)
def list_terms(
    project_id: Optional[str] = Query(default=None),
    scope_type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
) -> LexiconTermListResponse:
    session = _session()
    try:
        statement = select(LexiconTerm).where(LexiconTerm.tenant_id == TENANT_ID)
        if project_id:
            project_scope_ids = resolve_project_scope_ids(session, project_id, TENANT_ID)
            statement = statement.where(
                (LexiconTerm.scope_type != "project")
                | ((LexiconTerm.scope_type == "project") & (LexiconTerm.scope_id.in_(project_scope_ids)))
            )
        if scope_type:
            statement = statement.where(LexiconTerm.scope_type == scope_type)
        if status:
            statement = statement.where(LexiconTerm.status == status)
        statement = statement.order_by(LexiconTerm.created_at.asc(), LexiconTerm.normalized_term.asc())
        return LexiconTermListResponse(items=[_term_response(term) for term in session.scalars(statement)])
    finally:
        session.close()


@router.post("/terms", response_model=LexiconTermResponse, status_code=status.HTTP_201_CREATED)
def create_term(payload: LexiconTermCreateBody) -> LexiconTermResponse:
    session = _session()
    try:
        term = create_project_term(
            session,
            tenant_id=TENANT_ID,
            project_id=payload.project_id,
            scope_type=payload.scope_type,
            display_text=payload.display_text,
            term_type=payload.term_type,
            protection_level=payload.protection_level,
            source_type=payload.source_type,
            created_by=payload.created_by,
        )
        session.commit()
        session.refresh(term)
        return _term_response(term)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.patch("/terms/{term_id}", response_model=LexiconTermResponse)
def patch_term(term_id: str, payload: LexiconTermPatchBody) -> LexiconTermResponse:
    session = _session()
    try:
        term = session.get(LexiconTerm, term_id)
        if term is None:
            raise HTTPException(status_code=404, detail="term not found")
        if payload.display_text is not None:
            display_text = payload.display_text.strip()
            if not display_text:
                raise HTTPException(status_code=400, detail="display_text 不能为空")
            term.display_text = display_text
            term.normalized_term = normalize_term(display_text)
        if payload.status is not None:
            if payload.status not in TERM_ACTIVE_STATUSES:
                raise HTTPException(status_code=400, detail=f"status 无效: {payload.status}")
            term.status = payload.status
        if payload.protection_level is not None:
            if payload.protection_level not in PROTECTION_LEVELS:
                raise HTTPException(status_code=400, detail=f"protection_level 无效: {payload.protection_level}")
            term.protection_level = payload.protection_level
        term.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(term)
        return _term_response(term)
    finally:
        session.close()


@router.post("/terms/{term_id}/disable-for-project")
def disable_term_for_project(term_id: str, payload: DisableForProjectBody) -> dict[str, Any]:
    session = _session()
    try:
        term = session.get(LexiconTerm, term_id)
        if term is None:
            raise HTTPException(status_code=404, detail="term not found")
        override = disable_term_for_scope(
            session,
            term_id=term.id,
            normalized_term=term.normalized_term,
            scope_type="project",
            scope_id=payload.project_id,
            reason=payload.reason,
            created_by=payload.created_by,
        )
        session.commit()
        session.refresh(override)
        return _override_payload(override)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.get("/terms/{term_id}/occurrences", response_model=LexiconOccurrenceListResponse)
def get_term_occurrences(term_id: str) -> LexiconOccurrenceListResponse:
    session = _session()
    try:
        if session.get(LexiconTerm, term_id) is None:
            raise HTTPException(status_code=404, detail="term not found")
        statement = (
            select(LexiconOccurrence)
            .where(LexiconOccurrence.tenant_id == TENANT_ID, LexiconOccurrence.term_id == term_id)
            .order_by(LexiconOccurrence.created_at.asc(), LexiconOccurrence.id.asc())
        )
        return LexiconOccurrenceListResponse(
            items=[_occurrence_payload(occurrence) for occurrence in session.scalars(statement)]
        )
    finally:
        session.close()


@router.get("/candidates", response_model=LexiconCandidateListResponse)
def list_candidates(
    project_id: str = Query(...),
    status: Optional[str] = Query(default=None),
) -> LexiconCandidateListResponse:
    if status is not None and status not in CANDIDATE_STATUSES:
        raise HTTPException(status_code=400, detail=f"status 无效: {status}")
    session = _session()
    try:
        project_scope_ids = resolve_project_scope_ids(session, project_id, TENANT_ID)
        statement = select(LexiconCandidate).where(
            LexiconCandidate.tenant_id == TENANT_ID,
            LexiconCandidate.project_id.in_(project_scope_ids),
        )
        if status is not None:
            statement = statement.where(LexiconCandidate.status == status)
        statement = statement.order_by(LexiconCandidate.created_at.asc(), LexiconCandidate.normalized_term.asc())
        return LexiconCandidateListResponse(items=[_candidate_response(candidate) for candidate in session.scalars(statement)])
    finally:
        session.close()


@router.post("/candidates/{candidate_id}/promote", response_model=LexiconTermResponse)
def promote_candidate(candidate_id: str, payload: Optional[CandidateReviewBody] = None) -> LexiconTermResponse:
    session = _session()
    try:
        candidate = session.get(LexiconCandidate, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        if candidate.status not in {"candidate", "suggested_active"}:
            raise HTTPException(status_code=409, detail=f"candidate status cannot be promoted: {candidate.status}")
        term = promote_candidate_to_term(
            session,
            candidate_id,
            created_by=(payload.reviewed_by if payload else "client"),
        )
        session.commit()
        session.refresh(term)
        return _term_response(term)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        session.close()


@router.post("/candidates/{candidate_id}/reject", response_model=LexiconCandidateResponse)
def reject_candidate_review(candidate_id: str, payload: Optional[CandidateReviewBody] = None) -> LexiconCandidateResponse:
    session = _session()
    try:
        candidate_record = session.get(LexiconCandidate, candidate_id)
        if candidate_record is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        if candidate_record.status in {"rejected", "promoted"}:
            raise HTTPException(status_code=409, detail=f"candidate status cannot be rejected: {candidate_record.status}")
        candidate = reject_candidate(
            session,
            candidate_id,
            reviewed_by=(payload.reviewed_by if payload else "client"),
            reason=(payload.reason if payload else ""),
        )
        session.commit()
        session.refresh(candidate)
        return _candidate_response(candidate)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        session.close()


@router.post("/imports", status_code=status.HTTP_201_CREATED)
async def import_candidates(
    project_id: str = Form(...),
    source_name: Optional[str] = Form(default=None),
    text: Optional[str] = Form(default=None),
    script_file: Optional[UploadFile] = File(default=None),
) -> dict[str, Any]:
    session = _session()
    try:
        import_text, resolved_source_name, source_type = await _resolve_import_text(
            text=text,
            script_file=script_file,
            source_name=source_name,
        )
        result = import_lexicon_candidates_from_text(
            session,
            tenant_id=TENANT_ID,
            project_id=project_id,
            source_name=resolved_source_name,
            text=import_text,
            source_type=source_type,
        )
        session.commit()
        return _import_payload(result.import_record, result.candidates)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.get("/project-scripts")
def list_project_scripts(project_id: str = Query(...)) -> dict[str, Any]:
    session = _session()
    try:
        records = list_project_script_evidence(
            session,
            tenant_id=TENANT_ID,
            project_id=project_id,
        )
        return {
            'items': [
                {
                    'id': record.id,
                    'source_name': record.source_name,
                    'source_text_hash': record.source_text_hash,
                    'created_at': record.created_at,
                }
                for record in records
            ],
        }
    finally:
        session.close()


@router.get("/imports/{import_id}")
def get_import(import_id: str) -> dict[str, Any]:
    session = _session()
    try:
        import_record = session.get(LexiconImport, import_id)
        if import_record is None:
            raise HTTPException(status_code=404, detail="import not found")
        candidates = session.scalars(
            select(LexiconCandidate)
            .where(
                LexiconCandidate.tenant_id == import_record.tenant_id,
                LexiconCandidate.project_id == import_record.project_id,
                LexiconCandidate.source_text_hash == import_record.source_text_hash,
            )
            .order_by(LexiconCandidate.created_at.asc(), LexiconCandidate.normalized_term.asc())
        )
        return _import_payload(import_record, list(candidates))
    finally:
        session.close()


@router.post("/runtime-bundle/preview")
def preview_runtime_bundle(payload: RuntimeBundlePreviewBody) -> dict[str, Any]:
    session = _session()
    try:
        project_scope_ids = resolve_project_scope_ids(session, payload.project_id, TENANT_ID)
        primary_project_id = project_scope_ids[0]
        project = get_project(session, primary_project_id, TENANT_ID)
        return build_runtime_lexicon_bundle(
            session,
            tenant_id=TENANT_ID,
            project_id=primary_project_id,
            project_aliases=project_scope_ids[1:],
            domain_id=str(getattr(project, 'knowledge_domain', None) or 'broadcast'),
            temporary_terms=payload.temporary_terms,
        )
    finally:
        session.close()


@router.post("/jobs/{job_id}/runtime-bundle/rebuild")
def rebuild_job_runtime_bundle(job_id: str, payload: Optional[RuntimeBundleRebuildBody] = None) -> dict[str, Any]:
    session = _session()
    try:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.kind != "transcription":
            raise HTTPException(status_code=409, detail="only transcription jobs use runtime lexicon bundles")
        requested_project_id = str((payload.project_id if payload else None) or '').strip()
        locked_project_id = str(job.project_id or '').strip()
        legacy_job_aliases = {
            str(value).strip()
            for value in (job.program_key, job.program_name)
            if str(value or '').strip()
        }
        if locked_project_id:
            if requested_project_id and requested_project_id != locked_project_id:
                raise HTTPException(status_code=409, detail="词库项目与任务所属项目不一致")
            project_scope_ids = resolve_project_scope_ids(session, locked_project_id, TENANT_ID)
        elif requested_project_id:
            requested_project = get_project(session, requested_project_id, TENANT_ID)
            if requested_project:
                requested_aliases = set(resolve_project_scope_ids(session, requested_project_id, TENANT_ID)[1:])
                if legacy_job_aliases and not legacy_job_aliases.intersection(requested_aliases):
                    raise HTTPException(status_code=409, detail="词库项目与历史任务所属项目不一致")
                project_scope_ids = resolve_project_scope_ids(session, requested_project_id, TENANT_ID)
            elif legacy_job_aliases and requested_project_id not in legacy_job_aliases:
                raise HTTPException(status_code=409, detail="词库项目与历史任务所属项目不一致")
            else:
                project_scope_ids = (requested_project_id,)
        else:
            fallback_project_id = str(job.program_key or job.program_name or job.id)
            project_scope_ids = (fallback_project_id, *sorted(legacy_job_aliases - {fallback_project_id}))
        project_id = project_scope_ids[0]
        project_aliases = tuple(dict.fromkeys((*project_scope_ids[1:], *sorted(legacy_job_aliases))))
        project = get_project(session, str(project_id), TENANT_ID)
        bundle = build_runtime_lexicon_bundle(
            session,
            tenant_id=TENANT_ID,
            project_id=str(project_id),
            project_aliases=project_aliases,
            domain_id=str(getattr(project, 'knowledge_domain', None) or 'broadcast'),
            job_id=job.id,
            temporary_terms=(payload.temporary_terms if payload else []),
        )
        raw_segments = normalize_funasr_payload(job.raw_payload if isinstance(job.raw_payload, dict) else {})
        job.pipeline_artifacts = build_pipeline_artifacts(
            job,
            raw_segments=raw_segments,
            semantic_utterances=rebuild_semantic_utterances(raw_segments),
            normalized_segments=job.normalized_segments or [],
            refreshed_by="runtime_lexicon_bundle_rebuild",
            user_edit_patches=list((job.processing_metrics or {}).get("user_edit_patches") or []),
            runtime_lexicon_bundle=bundle,
        )
        session.commit()
        return bundle
    finally:
        session.close()


async def _resolve_import_text(
    *,
    text: Optional[str],
    script_file: Optional[UploadFile],
    source_name: Optional[str],
) -> tuple[str, str, str]:
    pieces: list[str] = []
    resolved_source_name = (source_name or "").strip()
    source_type = "paste"

    if script_file is not None and script_file.filename:
        payload = await script_file.read()
        file_text = extract_text_from_script_bytes(payload, script_file.filename)
        if file_text.strip():
            pieces.append(file_text)
        resolved_source_name = resolved_source_name or script_file.filename
        source_type = "upload"

    if text and text.strip():
        pieces.append(text)
        resolved_source_name = resolved_source_name or "粘贴文本"

    import_text = "\n".join(piece.strip() for piece in pieces if piece.strip()).strip()
    if not import_text:
        raise ValueError("请提供台本文本或上传台本文件")
    return import_text, resolved_source_name or "粘贴文本", source_type


def _import_payload(import_record: LexiconImport, candidates: list[LexiconCandidate]) -> dict[str, Any]:
    return {
        "id": import_record.id,
        "project_id": import_record.project_id,
        "source_name": import_record.source_name,
        "source_type": import_record.source_type,
        "status": import_record.status,
        "candidate_count": import_record.candidate_count,
        "promoted_count": import_record.promoted_count,
        "rejected_count": import_record.rejected_count,
        "metrics_json": import_record.metrics_json,
        "created_at": import_record.created_at,
        "candidates": [_candidate_response(candidate).model_dump(mode="json") for candidate in candidates],
    }


def _override_payload(override: LexiconTermOverride) -> dict[str, Any]:
    return {
        "id": override.id,
        "term_id": override.term_id,
        "normalized_term": override.normalized_term,
        "scope_type": override.scope_type,
        "scope_id": override.scope_id,
        "action": override.action,
        "reason": override.reason,
        "created_by": override.created_by,
        "created_at": override.created_at,
    }


def _occurrence_payload(occurrence: LexiconOccurrence) -> dict[str, Any]:
    return {
        "id": occurrence.id,
        "term_id": occurrence.term_id,
        "candidate_id": occurrence.candidate_id,
        "import_id": occurrence.import_id,
        "job_id": occurrence.job_id,
        "project_id": occurrence.project_id,
        "source_type": occurrence.source_type,
        "source_ref": occurrence.source_ref,
        "raw_context": occurrence.raw_context,
        "normalized_context": occurrence.normalized_context,
        "raw_start_char": occurrence.raw_start_char,
        "raw_end_char": occurrence.raw_end_char,
        "normalized_start_char": occurrence.normalized_start_char,
        "normalized_end_char": occurrence.normalized_end_char,
        "created_at": occurrence.created_at,
    }
