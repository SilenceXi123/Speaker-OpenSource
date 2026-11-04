from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models.canonical_import_batch import CanonicalImportBatch
from app.models.canonical_passage import CanonicalPassage
from app.models.canonical_passage_variant import CanonicalPassageVariant
from app.services.canonical_passages import (
    MIN_AUTOMATIC_SOURCE_PRIORITY,
    SOURCE_PRIORITIES,
    add_canonical_variant,
    upsert_canonical_passage,
)
from app.services.chinese_text import normalize_to_simplified_chinese
from app.services.lexicon_normalize import normalize_term, stable_hash_json


REVIEW_STATUSES = frozenset({'approved', 'pending', 'rejected'})


@dataclass(frozen=True, slots=True)
class CanonicalImportItem:
    title: str
    canonical_text: str
    author: str | None = None
    dynasty: str | None = None
    genre: str | None = None
    tune_pattern: str | None = None
    source_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            'title': self.title,
            'canonical_text': self.canonical_text,
            'author': self.author,
            'dynasty': self.dynasty,
            'genre': self.genre,
            'tune_pattern': self.tune_pattern,
            'source_ref': self.source_ref,
        }


@dataclass(frozen=True, slots=True)
class CanonicalImportReport:
    batch: CanonicalImportBatch
    reused: bool


def load_chinese_poetry_selection(
    path: str | Path,
    *,
    dynasty: str,
    genre: str,
    source_path: str,
    source_version: str,
) -> list[CanonicalImportItem]:
    file_path = Path(path)
    payload = json.loads(file_path.read_text(encoding='utf-8'))
    if not isinstance(payload, list):
        raise ValueError(f'{file_path.name} 顶层必须是数组')

    base_url = (
        'https://github.com/chinese-poetry/chinese-poetry/blob/'
        f'{source_version}/{source_path}'
    )
    result: list[CanonicalImportItem] = []
    for index, raw_item in enumerate(payload):
        if not isinstance(raw_item, dict):
            continue
        raw_lines = raw_item.get('paragraphs')
        if not isinstance(raw_lines, list):
            continue
        lines = [
            normalize_to_simplified_chinese(str(line or '').strip())
            for line in raw_lines
            if str(line or '').strip()
        ]
        title = raw_item.get('title') or raw_item.get('rhythmic')
        if not title or not lines:
            continue
        tags = [
            normalize_to_simplified_chinese(str(tag or '').strip())
            for tag in (raw_item.get('tags') or [])
            if str(tag or '').strip()
        ]
        item_genre = _specific_genre(tags, fallback=genre)
        item_id = str(raw_item.get('id') or index)
        result.append(CanonicalImportItem(
            title=normalize_to_simplified_chinese(str(title).strip()),
            canonical_text='\n'.join(lines),
            author=(
                normalize_to_simplified_chinese(str(raw_item.get('author')).strip())
                if raw_item.get('author')
                else None
            ),
            dynasty=normalize_to_simplified_chinese(dynasty),
            genre=item_genre,
            tune_pattern=(
                normalize_to_simplified_chinese(str(raw_item.get('rhythmic')).strip())
                if raw_item.get('rhythmic')
                else None
            ),
            source_ref=f'{base_url}#item-{item_id}',
            metadata={
                'source_item_id': item_id,
                'source_path': source_path,
                'source_index': index,
                'tags': tags,
                'text_transformation': 'traditional_to_simplified',
            },
        ))
    return result


def import_canonical_candidates(
    session: Session,
    *,
    tenant_id: str,
    source_name: str,
    source_url: str,
    source_version: str,
    source_license: str | None,
    selection_label: str,
    items: Sequence[CanonicalImportItem],
    created_by: str | None = None,
    domain_id: str = 'classical_poetry',
    metadata: dict[str, Any] | None = None,
) -> CanonicalImportReport:
    if not items:
        raise ValueError('导入候选不能为空')
    source_name = str(source_name or '').strip()
    source_url = str(source_url or '').strip()
    source_version = str(source_version or '').strip()
    selection_label = str(selection_label or '').strip()
    if not source_name or not source_url or not source_version or not selection_label:
        raise ValueError('来源名称、地址、版本和选集名称均不能为空')

    selection_hash = stable_hash_json({
        'tenant_id': tenant_id,
        'domain_id': domain_id,
        'source_name': source_name,
        'source_version': source_version,
        'selection_label': selection_label,
        'items': [item.fingerprint_payload() for item in items],
    })
    existing_batch = session.scalar(select(CanonicalImportBatch).where(
        CanonicalImportBatch.tenant_id == tenant_id,
        CanonicalImportBatch.selection_hash == selection_hash,
    ))
    if existing_batch is not None:
        return CanonicalImportReport(batch=existing_batch, reused=True)

    batch = CanonicalImportBatch(
        tenant_id=tenant_id,
        domain_id=domain_id,
        source_name=source_name,
        source_url=source_url,
        source_version=source_version,
        source_license=str(source_license or '').strip() or None,
        selection_label=selection_label,
        selection_hash=selection_hash,
        status='processing',
        candidate_count=len(items),
        created_by=str(created_by or '').strip() or None,
        metadata_json=dict(metadata or {}),
    )
    session.add(batch)
    session.flush()

    passage_count = 0
    variant_count = 0
    duplicate_count = 0
    error_count = 0
    for item in items:
        try:
            normalized_text = normalize_term(item.canonical_text)
            if not normalized_text or not str(item.title or '').strip():
                error_count += 1
                continue
            content_hash = stable_hash_json({
                'domain_id': domain_id,
                'normalized_text': normalized_text,
            })
            exact = session.scalar(select(CanonicalPassage).where(
                CanonicalPassage.tenant_id == tenant_id,
                CanonicalPassage.domain_id == domain_id,
                CanonicalPassage.content_hash == content_hash,
            ))
            if exact is not None:
                duplicate_count += 1
                continue

            identities = list(session.scalars(select(CanonicalPassage).where(
                CanonicalPassage.tenant_id == tenant_id,
                CanonicalPassage.domain_id == domain_id,
                CanonicalPassage.title == item.title,
                CanonicalPassage.author == item.author,
                CanonicalPassage.dynasty == item.dynasty,
            ).order_by(CanonicalPassage.created_at.asc())))
            identity, variant_similarity = _probable_variant_identity(
                identities,
                normalized_text=normalized_text,
            )
            item_metadata = {
                **dict(item.metadata or {}),
                'imported_source_type': 'community_dataset',
                'import_batch_id': batch.id,
            }
            if identity is not None:
                item_metadata['variant_similarity'] = round(variant_similarity, 6)
                before = session.scalar(select(CanonicalPassageVariant).where(
                    CanonicalPassageVariant.passage_id == identity.id,
                    CanonicalPassageVariant.content_hash == stable_hash_json({
                        'passage_id': identity.id,
                        'normalized_text': normalized_text,
                    }),
                ))
                if before is not None:
                    duplicate_count += 1
                    continue
                add_canonical_variant(
                    session,
                    passage_id=identity.id,
                    variant_text=item.canonical_text,
                    source_type='community_dataset',
                    label=f'{selection_label}候选异文',
                    source_ref=item.source_ref,
                    review_status='pending',
                    import_batch_id=batch.id,
                    metadata_json=item_metadata,
                )
                variant_count += 1
                continue

            passage = upsert_canonical_passage(
                session,
                tenant_id=tenant_id,
                title=item.title,
                canonical_text=item.canonical_text,
                author=item.author,
                dynasty=item.dynasty,
                genre=item.genre,
                tune_pattern=item.tune_pattern,
                domain_id=domain_id,
                source_type='community_dataset',
                source_ref=item.source_ref,
                review_status='pending',
                metadata_json=item_metadata,
            )
            passage.import_batch_id = batch.id
            passage_count += 1
        except (TypeError, ValueError):
            error_count += 1

    batch.passage_count = passage_count
    batch.variant_count = variant_count
    batch.duplicate_count = duplicate_count
    batch.error_count = error_count
    batch.status = 'completed' if error_count == 0 else 'completed_with_warnings'
    batch.completed_at = datetime.utcnow()
    session.flush()
    return CanonicalImportReport(batch=batch, reused=False)


def review_passages(
    session: Session,
    *,
    tenant_id: str,
    passage_ids: Iterable[str],
    review_status: str,
    reviewed_by: str,
) -> list[CanonicalPassage]:
    review_status = _validate_review_status(review_status)
    ids = tuple(dict.fromkeys(str(item or '').strip() for item in passage_ids if item))
    if not ids:
        raise ValueError('至少选择一篇作品')
    passages = list(session.scalars(select(CanonicalPassage).where(
        CanonicalPassage.tenant_id == tenant_id,
        CanonicalPassage.id.in_(ids),
    )))
    if len(passages) != len(ids):
        raise ValueError('部分作品不存在或不属于当前知识库')
    for passage in passages:
        _apply_review(passage, review_status=review_status, reviewed_by=reviewed_by)
    session.flush()
    return passages


def review_all_passages(
    session: Session,
    *,
    tenant_id: str,
    review_status: str,
    reviewed_by: str,
    domain_id: str = 'classical_poetry',
    matching_review_status: str | None = None,
    dynasty: str | None = None,
    genre: str | None = None,
    query: str | None = None,
) -> int:
    review_status = _validate_review_status(review_status)
    if matching_review_status:
        matching_review_status = _validate_review_status(matching_review_status)
    conditions = _knowledge_passage_conditions(
        tenant_id=tenant_id,
        domain_id=domain_id,
        review_status=matching_review_status,
        dynasty=dynasty,
        genre=genre,
        query=query,
    )
    passages = list(session.scalars(select(CanonicalPassage).where(*conditions)))
    for passage in passages:
        _apply_review(passage, review_status=review_status, reviewed_by=reviewed_by)
    session.flush()
    return len(passages)


def review_variant(
    session: Session,
    *,
    tenant_id: str,
    variant_id: str,
    review_status: str,
    reviewed_by: str,
) -> CanonicalPassageVariant:
    review_status = _validate_review_status(review_status)
    variant = session.scalar(select(CanonicalPassageVariant).where(
        CanonicalPassageVariant.tenant_id == tenant_id,
        CanonicalPassageVariant.id == variant_id,
    ))
    if variant is None:
        raise ValueError('异文不存在')
    _apply_review(variant, review_status=review_status, reviewed_by=reviewed_by)
    session.flush()
    return variant


def build_knowledge_dashboard(
    session: Session,
    *,
    tenant_id: str,
    domain_id: str = 'classical_poetry',
) -> dict[str, Any]:
    base = (
        CanonicalPassage.tenant_id == tenant_id,
        CanonicalPassage.domain_id == domain_id,
    )
    status_rows = session.execute(select(
        CanonicalPassage.review_status,
        func.count(CanonicalPassage.id),
    ).where(*base).group_by(CanonicalPassage.review_status)).all()
    status_counts = {status: int(count) for status, count in status_rows}
    total = sum(status_counts.values())
    eligible = session.scalar(select(func.count(CanonicalPassage.id)).where(
        *base,
        CanonicalPassage.status == 'active',
        CanonicalPassage.review_status == 'approved',
        CanonicalPassage.source_priority >= MIN_AUTOMATIC_SOURCE_PRIORITY,
    )) or 0
    variant_rows = session.execute(select(
        CanonicalPassageVariant.review_status,
        func.count(CanonicalPassageVariant.id),
    ).where(
        CanonicalPassageVariant.tenant_id == tenant_id,
    ).group_by(CanonicalPassageVariant.review_status)).all()
    dynasty_rows = session.execute(select(
        CanonicalPassage.dynasty,
        func.count(CanonicalPassage.id),
    ).where(*base).group_by(CanonicalPassage.dynasty).order_by(
        func.count(CanonicalPassage.id).desc()
    )).all()
    genre_rows = session.execute(select(
        CanonicalPassage.genre,
        func.count(CanonicalPassage.id),
    ).where(*base).group_by(CanonicalPassage.genre).order_by(
        func.count(CanonicalPassage.id).desc()
    )).all()
    imports = list(session.scalars(select(CanonicalImportBatch).where(
        CanonicalImportBatch.tenant_id == tenant_id,
        CanonicalImportBatch.domain_id == domain_id,
    ).order_by(CanonicalImportBatch.created_at.desc()).limit(20)))
    return {
        'total': total,
        'automatic_eligible': int(eligible),
        'review_status': {
            'approved': status_counts.get('approved', 0),
            'pending': status_counts.get('pending', 0),
            'rejected': status_counts.get('rejected', 0),
        },
        'variant_status': {
            status: int(count) for status, count in variant_rows
        },
        'dynasties': [
            {'name': name or '未标注', 'count': int(count)}
            for name, count in dynasty_rows
        ],
        'genres': [
            {'name': name or '未标注', 'count': int(count)}
            for name, count in genre_rows
        ],
        'imports': [_import_payload(item) for item in imports],
    }


def list_knowledge_passages(
    session: Session,
    *,
    tenant_id: str,
    domain_id: str = 'classical_poetry',
    review_status: str | None = None,
    dynasty: str | None = None,
    genre: str | None = None,
    query: str | None = None,
    page: int = 1,
    page_size: int = 30,
) -> dict[str, Any]:
    conditions = _knowledge_passage_conditions(
        tenant_id=tenant_id,
        domain_id=domain_id,
        review_status=review_status,
        dynasty=dynasty,
        genre=genre,
        query=query,
    )
    total = session.scalar(select(func.count(CanonicalPassage.id)).where(*conditions)) or 0
    pending_first = case(
        (CanonicalPassage.review_status == 'pending', 0),
        (CanonicalPassage.review_status == 'approved', 1),
        else_=2,
    )
    items = list(session.scalars(select(CanonicalPassage).where(*conditions).order_by(
        pending_first,
        CanonicalPassage.dynasty.asc(),
        CanonicalPassage.author.asc(),
        CanonicalPassage.title.asc(),
    ).offset((page - 1) * page_size).limit(page_size)))
    batch_ids = {item.import_batch_id for item in items if item.import_batch_id}
    batches = {
        item.id: item
        for item in session.scalars(select(CanonicalImportBatch).where(
            CanonicalImportBatch.id.in_(batch_ids)
        ))
    } if batch_ids else {}
    variant_counts = dict(session.execute(select(
        CanonicalPassageVariant.passage_id,
        func.count(CanonicalPassageVariant.id),
    ).where(
        CanonicalPassageVariant.passage_id.in_([item.id for item in items])
    ).group_by(CanonicalPassageVariant.passage_id)).all()) if items else {}
    return {
        'items': [
            _passage_payload(
                item,
                batch=batches.get(item.import_batch_id),
                variant_count=int(variant_counts.get(item.id, 0)),
            )
            for item in items
        ],
        'count': int(total),
        'page': page,
        'page_size': page_size,
        'page_count': max(1, (int(total) + page_size - 1) // page_size),
    }


def get_knowledge_passage(
    session: Session,
    *,
    tenant_id: str,
    passage_id: str,
) -> dict[str, Any] | None:
    passage = session.scalar(select(CanonicalPassage).where(
        CanonicalPassage.tenant_id == tenant_id,
        CanonicalPassage.id == passage_id,
    ))
    if passage is None:
        return None
    variants = list(session.scalars(select(CanonicalPassageVariant).where(
        CanonicalPassageVariant.passage_id == passage.id,
    ).order_by(CanonicalPassageVariant.created_at.asc())))
    batch = session.get(CanonicalImportBatch, passage.import_batch_id) if passage.import_batch_id else None
    payload = _passage_payload(passage, batch=batch, variant_count=len(variants))
    payload['variants'] = [_variant_payload(item) for item in variants]
    return payload


def _specific_genre(tags: Sequence[str], *, fallback: str) -> str:
    preferred = (
        '五言绝句', '七言绝句', '五言律诗', '七言律诗',
        '五言古诗', '七言古诗', '乐府',
    )
    return next((tag for tag in tags if tag in preferred), fallback)


def _knowledge_passage_conditions(
    *,
    tenant_id: str,
    domain_id: str,
    review_status: str | None = None,
    dynasty: str | None = None,
    genre: str | None = None,
    query: str | None = None,
) -> list[Any]:
    conditions: list[Any] = [
        CanonicalPassage.tenant_id == tenant_id,
        CanonicalPassage.domain_id == domain_id,
    ]
    if review_status:
        conditions.append(CanonicalPassage.review_status == review_status)
    if dynasty:
        conditions.append(CanonicalPassage.dynasty == dynasty)
    if genre:
        conditions.append(CanonicalPassage.genre == genre)
    normalized_query = str(query or '').strip()
    if normalized_query:
        like = f'%{normalized_query}%'
        conditions.append(or_(
            CanonicalPassage.title.like(like),
            CanonicalPassage.author.like(like),
            CanonicalPassage.canonical_text.like(like),
        ))
    return conditions


def _probable_variant_identity(
    identities: Sequence[CanonicalPassage],
    *,
    normalized_text: str,
) -> tuple[CanonicalPassage | None, float]:
    if not identities:
        return None, 0.0
    scored = sorted(
        (
            (
                SequenceMatcher(
                    None,
                    normalized_text,
                    str(item.normalized_text or ''),
                    autojunk=False,
                ).ratio(),
                item,
            )
            for item in identities
        ),
        key=lambda value: (-value[0], value[1].id),
    )
    similarity, best = scored[0]
    # 词牌名和“无题”会被大量不同作品复用；只有正文高度相似时才视为异文。
    if similarity + 1e-12 < 0.78:
        return None, similarity
    return best, similarity


def _validate_review_status(value: str) -> str:
    normalized = str(value or '').strip()
    if normalized not in REVIEW_STATUSES:
        raise ValueError('review_status 无效')
    return normalized


def _apply_review(item, *, review_status: str, reviewed_by: str) -> None:
    metadata = dict(item.metadata_json or {})
    original_source_type = str(
        metadata.get('imported_source_type') or item.source_type or 'community_dataset'
    )
    if original_source_type not in SOURCE_PRIORITIES:
        original_source_type = 'community_dataset'
    metadata['imported_source_type'] = original_source_type
    metadata['last_review'] = {
        'status': review_status,
        'reviewed_by': str(reviewed_by or '').strip() or 'admin',
        'reviewed_at': datetime.utcnow().isoformat(timespec='seconds'),
    }
    item.review_status = review_status
    item.reviewed_by = str(reviewed_by or '').strip() or 'admin'
    item.reviewed_at = datetime.utcnow()
    item.metadata_json = metadata
    if review_status == 'approved':
        item.source_type = 'manually_reviewed'
        item.source_priority = SOURCE_PRIORITIES['manually_reviewed']
    else:
        item.source_type = original_source_type
        item.source_priority = SOURCE_PRIORITIES[original_source_type]


def _passage_payload(
    passage: CanonicalPassage,
    *,
    batch: CanonicalImportBatch | None,
    variant_count: int,
) -> dict[str, Any]:
    return {
        'id': passage.id,
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
        'import_batch_id': passage.import_batch_id,
        'import_batch': _import_payload(batch) if batch else None,
        'reviewed_by': passage.reviewed_by,
        'reviewed_at': passage.reviewed_at.isoformat() if passage.reviewed_at else None,
        'variant_count': variant_count,
        'metadata': dict(passage.metadata_json or {}),
        'created_at': passage.created_at.isoformat() if passage.created_at else None,
        'updated_at': passage.updated_at.isoformat() if passage.updated_at else None,
    }


def _variant_payload(variant: CanonicalPassageVariant) -> dict[str, Any]:
    return {
        'id': variant.id,
        'label': variant.label,
        'variant_text': variant.variant_text,
        'lines': list(variant.lines_json or []),
        'review_status': variant.review_status,
        'source_type': variant.source_type,
        'source_ref': variant.source_ref,
        'source_priority': variant.source_priority,
        'reviewed_by': variant.reviewed_by,
        'reviewed_at': variant.reviewed_at.isoformat() if variant.reviewed_at else None,
        'metadata': dict(variant.metadata_json or {}),
    }


def _import_payload(batch: CanonicalImportBatch | None) -> dict[str, Any] | None:
    if batch is None:
        return None
    return {
        'id': batch.id,
        'source_name': batch.source_name,
        'source_url': batch.source_url,
        'source_version': batch.source_version,
        'source_license': batch.source_license,
        'selection_label': batch.selection_label,
        'status': batch.status,
        'candidate_count': batch.candidate_count,
        'passage_count': batch.passage_count,
        'variant_count': batch.variant_count,
        'duplicate_count': batch.duplicate_count,
        'error_count': batch.error_count,
        'created_by': batch.created_by,
        'created_at': batch.created_at.isoformat() if batch.created_at else None,
        'completed_at': batch.completed_at.isoformat() if batch.completed_at else None,
        'metadata': dict(batch.metadata_json or {}),
    }
