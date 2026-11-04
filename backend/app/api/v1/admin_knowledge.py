from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db.session import SessionLocal
from app.services.admin_auth import require_admin_session
from app.services.canonical_knowledge_admin import (
    build_knowledge_dashboard,
    get_knowledge_passage,
    list_knowledge_passages,
    review_all_passages,
    review_passages,
    review_variant,
)


router = APIRouter()
TENANT_ID = 'default'
ReviewStatus = Literal['approved', 'pending', 'rejected']


class PassageReviewBody(BaseModel):
    review_status: ReviewStatus


class BulkPassageReviewBody(PassageReviewBody):
    passage_ids: list[str] = Field(min_length=1, max_length=100)


class PassageReviewFilterBody(BaseModel):
    review_status: ReviewStatus | None = None
    dynasty: str | None = Field(default=None, max_length=64)
    genre: str | None = Field(default=None, max_length=64)
    q: str | None = Field(default=None, max_length=255)


class FilteredPassageReviewBody(PassageReviewBody):
    filters: PassageReviewFilterBody = Field(default_factory=PassageReviewFilterBody)


@router.get('/summary')
def get_admin_knowledge_summary(
    _username: str = Depends(require_admin_session),
) -> dict[str, Any]:
    session = SessionLocal()
    try:
        return build_knowledge_dashboard(session, tenant_id=TENANT_ID)
    finally:
        session.close()


@router.get('/passages')
def get_admin_knowledge_passages(
    review_status: Annotated[ReviewStatus | None, Query()] = None,
    dynasty: Annotated[str | None, Query(max_length=64)] = None,
    genre: Annotated[str | None, Query(max_length=64)] = None,
    q: Annotated[str | None, Query(max_length=255)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 30,
    _username: str = Depends(require_admin_session),
) -> dict[str, Any]:
    session = SessionLocal()
    try:
        return list_knowledge_passages(
            session,
            tenant_id=TENANT_ID,
            review_status=review_status,
            dynasty=dynasty,
            genre=genre,
            query=q,
            page=page,
            page_size=page_size,
        )
    finally:
        session.close()


@router.get('/passages/{passage_id}')
def get_admin_knowledge_passage(
    passage_id: str,
    _username: str = Depends(require_admin_session),
) -> dict[str, Any]:
    session = SessionLocal()
    try:
        payload = get_knowledge_passage(
            session,
            tenant_id=TENANT_ID,
            passage_id=passage_id,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail='作品不存在')
        return payload
    finally:
        session.close()


@router.post('/passages/{passage_id}/review')
def post_admin_knowledge_passage_review(
    passage_id: str,
    payload: PassageReviewBody,
    username: str = Depends(require_admin_session),
) -> dict[str, Any]:
    session = SessionLocal()
    try:
        review_passages(
            session,
            tenant_id=TENANT_ID,
            passage_ids=[passage_id],
            review_status=payload.review_status,
            reviewed_by=username,
        )
        session.commit()
        result = get_knowledge_passage(
            session,
            tenant_id=TENANT_ID,
            passage_id=passage_id,
        )
        if result is None:
            raise HTTPException(status_code=404, detail='作品不存在')
        return result
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.post('/passages/review')
def post_admin_knowledge_bulk_review(
    payload: BulkPassageReviewBody,
    username: str = Depends(require_admin_session),
) -> dict[str, Any]:
    session = SessionLocal()
    try:
        passages = review_passages(
            session,
            tenant_id=TENANT_ID,
            passage_ids=payload.passage_ids,
            review_status=payload.review_status,
            reviewed_by=username,
        )
        session.commit()
        return {'updated_count': len(passages), 'review_status': payload.review_status}
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.post('/passages/review-all')
def post_admin_knowledge_filtered_review(
    payload: FilteredPassageReviewBody,
    username: str = Depends(require_admin_session),
) -> dict[str, Any]:
    session = SessionLocal()
    try:
        filters = payload.filters
        updated_count = review_all_passages(
            session,
            tenant_id=TENANT_ID,
            review_status=payload.review_status,
            reviewed_by=username,
            matching_review_status=filters.review_status,
            dynasty=filters.dynasty,
            genre=filters.genre,
            query=filters.q,
        )
        session.commit()
        return {
            'updated_count': updated_count,
            'review_status': payload.review_status,
        }
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.post('/variants/{variant_id}/review')
def post_admin_knowledge_variant_review(
    variant_id: str,
    payload: PassageReviewBody,
    username: str = Depends(require_admin_session),
) -> dict[str, Any]:
    session = SessionLocal()
    try:
        variant = review_variant(
            session,
            tenant_id=TENANT_ID,
            variant_id=variant_id,
            review_status=payload.review_status,
            reviewed_by=username,
        )
        session.commit()
        return {
            'id': variant.id,
            'review_status': variant.review_status,
            'source_priority': variant.source_priority,
            'reviewed_by': variant.reviewed_by,
        }
    except ValueError as exc:
        session.rollback()
        status_code = 404 if '不存在' in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    finally:
        session.close()
