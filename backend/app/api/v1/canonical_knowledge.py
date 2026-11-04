from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.canonical_passage_variant import CanonicalPassageVariant
from app.services.canonical_passages import (
    add_canonical_variant,
    list_canonical_passages,
    upsert_canonical_passage,
)


router = APIRouter()
TENANT_ID = 'default'
SourceType = Literal[
    'manually_reviewed',
    'authoritative_edition',
    'curated_dataset',
    'community_dataset',
]
ReviewStatus = Literal['approved', 'pending', 'rejected']


class CanonicalPassageBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    canonical_text: str = Field(min_length=1)
    author: str | None = Field(default=None, max_length=255)
    dynasty: str | None = Field(default=None, max_length=64)
    genre: str | None = Field(default=None, max_length=64)
    tune_pattern: str | None = Field(default=None, max_length=255)
    domain_id: Literal['classical_poetry'] = 'classical_poetry'
    source_type: SourceType
    source_ref: str | None = None
    review_status: ReviewStatus = 'approved'
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CanonicalVariantBody(BaseModel):
    variant_text: str = Field(min_length=1)
    source_type: SourceType
    label: str | None = Field(default=None, max_length=255)
    source_ref: str | None = None
    review_status: ReviewStatus = 'approved'


@router.post('/passages')
def create_or_update_passage(payload: CanonicalPassageBody) -> dict[str, Any]:
    session = SessionLocal()
    try:
        passage = upsert_canonical_passage(
            session,
            tenant_id=TENANT_ID,
            **payload.model_dump(),
        )
        session.commit()
        session.refresh(passage)
        return _passage_payload(passage, variants=[])
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.post('/passages/{passage_id}/variants')
def create_passage_variant(
    passage_id: str,
    payload: CanonicalVariantBody,
) -> dict[str, Any]:
    session = SessionLocal()
    try:
        variant = add_canonical_variant(
            session,
            passage_id=passage_id,
            **payload.model_dump(),
        )
        session.commit()
        session.refresh(variant)
        return _variant_payload(variant)
    except ValueError as exc:
        session.rollback()
        status_code = 404 if 'not found' in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    finally:
        session.close()


@router.get('/passages')
def get_passages(
    domain_id: Literal['classical_poetry'] = Query(default='classical_poetry'),
) -> dict[str, Any]:
    session = SessionLocal()
    try:
        passages = list_canonical_passages(
            session,
            tenant_id=TENANT_ID,
            domain_id=domain_id,
        )
        variants = list(session.scalars(select(CanonicalPassageVariant).where(
            CanonicalPassageVariant.passage_id.in_([item.id for item in passages])
        ))) if passages else []
        variants_by_passage: dict[str, list[CanonicalPassageVariant]] = {}
        for variant in variants:
            variants_by_passage.setdefault(variant.passage_id, []).append(variant)
        return {
            'items': [
                _passage_payload(
                    passage,
                    variants=variants_by_passage.get(passage.id, []),
                )
                for passage in passages
            ],
            'count': len(passages),
        }
    finally:
        session.close()


def _passage_payload(passage, *, variants) -> dict[str, Any]:
    return {
        'id': passage.id,
        'domain_id': passage.domain_id,
        'title': passage.title,
        'author': passage.author,
        'dynasty': passage.dynasty,
        'genre': passage.genre,
        'tune_pattern': passage.tune_pattern,
        'canonical_text': passage.canonical_text,
        'lines': list(passage.lines_json or []),
        'status': passage.status,
        'review_status': passage.review_status,
        'source_type': passage.source_type,
        'source_ref': passage.source_ref,
        'source_priority': passage.source_priority,
        'content_hash': passage.content_hash,
        'variants': [_variant_payload(item) for item in variants],
    }

def _variant_payload(variant) -> dict[str, Any]:
    return {
        'id': variant.id,
        'passage_id': variant.passage_id,
        'label': variant.label,
        'variant_text': variant.variant_text,
        'lines': list(variant.lines_json or []),
        'status': variant.status,
        'review_status': variant.review_status,
        'source_type': variant.source_type,
        'source_ref': variant.source_ref,
        'source_priority': variant.source_priority,
        'content_hash': variant.content_hash,
    }
