from __future__ import annotations

from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import re
from typing import Any

from pypinyin import Style, lazy_pinyin
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.canonical_passage import CanonicalPassage
from app.models.canonical_passage_variant import CanonicalPassageVariant
from app.services.lexicon_normalize import normalize_term, stable_hash_json


SOURCE_PRIORITIES = {
    'manually_reviewed': 100,
    'authoritative_edition': 90,
    'curated_dataset': 70,
    'community_dataset': 40,
}
MIN_AUTOMATIC_SOURCE_PRIORITY = 80
_LINE_SPLIT_RE = re.compile(r'[，。！？；：、,.!?;:\n]+')
_MIN_LINE_CHARS = 4
_MAX_LINE_CHARS = 48


@dataclass(frozen=True, slots=True)
class CanonicalCorrectionMatch:
    source_text: str
    replacement_text: str
    passage_id: str
    passage_title: str
    author: str | None
    dynasty: str | None
    line_index: int
    query_start: int
    query_end: int
    confidence: float
    margin: float
    source_priority: int
    content_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            'source_text': self.source_text,
            'replacement_text': self.replacement_text,
            'passage_id': self.passage_id,
            'passage_title': self.passage_title,
            'author': self.author,
            'dynasty': self.dynasty,
            'line_index': self.line_index,
            'query_start': self.query_start,
            'query_end': self.query_end,
            'confidence': self.confidence,
            'margin': self.margin,
            'source_priority': self.source_priority,
            'content_hash': self.content_hash,
        }


def upsert_canonical_passage(
    session: Session,
    *,
    tenant_id: str,
    title: str,
    canonical_text: str,
    author: str | None = None,
    dynasty: str | None = None,
    genre: str | None = None,
    tune_pattern: str | None = None,
    domain_id: str = 'classical_poetry',
    source_type: str,
    source_ref: str | None = None,
    review_status: str = 'approved',
    metadata_json: dict[str, Any] | None = None,
) -> CanonicalPassage:
    normalized_title = str(title or '').strip()
    normalized_text = normalize_term(canonical_text)
    lines = _passage_lines(canonical_text)
    if not normalized_title:
        raise ValueError('title 不能为空')
    if not normalized_text or not lines:
        raise ValueError('canonical_text 不能为空')
    if source_type not in SOURCE_PRIORITIES:
        raise ValueError('source_type 无效')
    if review_status not in {'approved', 'pending', 'rejected'}:
        raise ValueError('review_status 无效')
    content_hash = stable_hash_json({
        'domain_id': domain_id,
        'normalized_text': normalized_text,
    })
    passage = session.scalar(select(CanonicalPassage).where(
        CanonicalPassage.tenant_id == tenant_id,
        CanonicalPassage.domain_id == domain_id,
        CanonicalPassage.content_hash == content_hash,
    ))
    if passage is None:
        passage = CanonicalPassage(
            tenant_id=tenant_id,
            domain_id=domain_id,
            title=normalized_title,
            author=str(author or '').strip() or None,
            dynasty=str(dynasty or '').strip() or None,
            genre=str(genre or '').strip() or None,
            tune_pattern=str(tune_pattern or '').strip() or None,
            canonical_text=str(canonical_text).strip(),
            normalized_text=normalized_text,
            lines_json=list(lines),
            content_hash=content_hash,
            status='active',
            review_status=review_status,
            source_type=source_type,
            source_ref=str(source_ref or '').strip() or None,
            source_priority=SOURCE_PRIORITIES[source_type],
            metadata_json=dict(metadata_json or {}),
        )
        session.add(passage)
    elif SOURCE_PRIORITIES[source_type] >= passage.source_priority:
        passage.title = normalized_title
        passage.author = str(author or '').strip() or passage.author
        passage.dynasty = str(dynasty or '').strip() or passage.dynasty
        passage.genre = str(genre or '').strip() or passage.genre
        passage.tune_pattern = str(tune_pattern or '').strip() or passage.tune_pattern
        passage.source_type = source_type
        passage.source_ref = str(source_ref or '').strip() or passage.source_ref
        passage.source_priority = SOURCE_PRIORITIES[source_type]
        passage.review_status = review_status
        passage.metadata_json = dict(metadata_json or passage.metadata_json or {})
    session.flush()
    return passage


def add_canonical_variant(
    session: Session,
    *,
    passage_id: str,
    variant_text: str,
    source_type: str,
    label: str | None = None,
    source_ref: str | None = None,
    review_status: str = 'approved',
    import_batch_id: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> CanonicalPassageVariant:
    passage = session.get(CanonicalPassage, passage_id)
    if passage is None:
        raise ValueError('passage not found')
    if source_type not in SOURCE_PRIORITIES:
        raise ValueError('source_type 无效')
    normalized_text = normalize_term(variant_text)
    lines = _passage_lines(variant_text)
    if not normalized_text or not lines:
        raise ValueError('variant_text 不能为空')
    content_hash = stable_hash_json({
        'passage_id': passage.id,
        'normalized_text': normalized_text,
    })
    variant = session.scalar(select(CanonicalPassageVariant).where(
        CanonicalPassageVariant.passage_id == passage.id,
        CanonicalPassageVariant.content_hash == content_hash,
    ))
    if variant is None:
        variant = CanonicalPassageVariant(
            tenant_id=passage.tenant_id,
            passage_id=passage.id,
            label=str(label or '').strip() or None,
            variant_text=str(variant_text).strip(),
            normalized_text=normalized_text,
            lines_json=list(lines),
            content_hash=content_hash,
            status='active',
            review_status=review_status,
            source_type=source_type,
            source_ref=str(source_ref or '').strip() or None,
            source_priority=SOURCE_PRIORITIES[source_type],
            import_batch_id=str(import_batch_id or '').strip() or None,
            metadata_json=dict(metadata_json or {}),
        )
        session.add(variant)
    session.flush()
    return variant


def retrieve_canonical_corrections(
    session: Session,
    *,
    tenant_id: str,
    query_text: str,
    domain_id: str = 'classical_poetry',
    limit: int = 12,
) -> tuple[CanonicalCorrectionMatch, ...]:
    query = normalize_term(query_text)
    if len(query) < _MIN_LINE_CHARS:
        return ()
    passages = list(session.scalars(select(CanonicalPassage).where(
        CanonicalPassage.tenant_id == tenant_id,
        CanonicalPassage.domain_id == domain_id,
        CanonicalPassage.status == 'active',
        CanonicalPassage.review_status == 'approved',
        CanonicalPassage.source_priority >= MIN_AUTOMATIC_SOURCE_PRIORITY,
    ).order_by(
        CanonicalPassage.source_priority.desc(),
        CanonicalPassage.title.asc(),
        CanonicalPassage.id.asc(),
    )))
    if not passages:
        return ()
    variants_by_passage = _approved_variants(session, passages)
    valid_lines = {
        line
        for passage in passages
        for line in (
            *tuple(passage.lines_json or []),
            *tuple(
                variant_line
                for variant in variants_by_passage.get(passage.id, ())
                for variant_line in (variant.lines_json or [])
            ),
        )
    }
    options_by_range: dict[tuple[int, int], list[CanonicalCorrectionMatch]] = {}
    for passage in passages:
        for line_index, canonical_line in enumerate(passage.lines_json or []):
            line = normalize_term(canonical_line)
            if not (_MIN_LINE_CHARS <= len(line) <= _MAX_LINE_CHARS):
                continue
            if len(query) < len(line):
                continue
            for start in range(0, len(query) - len(line) + 1):
                source = query[start:start + len(line)]
                if source == line or source in valid_lines:
                    continue
                pinyin_score = _pinyin_similarity(source, line)
                if pinyin_score + 1e-12 < 0.98:
                    continue
                text_score = SequenceMatcher(None, source, line, autojunk=False).ratio()
                if text_score + 1e-12 < 0.72 or _change_group_count(source, line) > 2:
                    continue
                confidence = round(
                    min(1.0, 0.65 * pinyin_score + 0.25 * text_score + 0.10),
                    6,
                )
                options_by_range.setdefault((start, start + len(line)), []).append(
                    CanonicalCorrectionMatch(
                        source_text=source,
                        replacement_text=line,
                        passage_id=passage.id,
                        passage_title=passage.title,
                        author=passage.author,
                        dynasty=passage.dynasty,
                        line_index=line_index,
                        query_start=start,
                        query_end=start + len(line),
                        confidence=confidence,
                        margin=0.0,
                        source_priority=passage.source_priority,
                        content_hash=passage.content_hash,
                    )
                )

    accepted: list[CanonicalCorrectionMatch] = []
    occupied: set[int] = set()
    for (start, end), options in sorted(options_by_range.items()):
        ranked = sorted(
            options,
            key=lambda item: (
                -item.confidence,
                -item.source_priority,
                item.replacement_text,
                item.passage_id,
            ),
        )
        distinct_replacements = []
        for item in ranked:
            if item.replacement_text not in {
                value.replacement_text for value in distinct_replacements
            }:
                distinct_replacements.append(item)
        margin = (
            1.0
            if len(distinct_replacements) == 1
            else distinct_replacements[0].confidence - distinct_replacements[1].confidence
        )
        if margin + 1e-12 < 0.08:
            continue
        best = distinct_replacements[0]
        indexes = set(range(start, end))
        if indexes & occupied:
            continue
        occupied.update(indexes)
        accepted.append(replace(best, margin=round(min(1.0, margin), 6)))
        if len(accepted) >= max(1, int(limit)):
            break
    return tuple(accepted)


def list_canonical_passages(
    session: Session,
    *,
    tenant_id: str,
    domain_id: str = 'classical_poetry',
) -> list[CanonicalPassage]:
    return list(session.scalars(select(CanonicalPassage).where(
        CanonicalPassage.tenant_id == tenant_id,
        CanonicalPassage.domain_id == domain_id,
    ).order_by(
        CanonicalPassage.dynasty.asc(),
        CanonicalPassage.author.asc(),
        CanonicalPassage.title.asc(),
    )))


def _passage_lines(text: str) -> tuple[str, ...]:
    return tuple(
        normalized
        for item in _LINE_SPLIT_RE.split(str(text or ''))
        if (normalized := normalize_term(item))
    )


def _approved_variants(
    session: Session,
    passages: list[CanonicalPassage],
) -> dict[str, tuple[CanonicalPassageVariant, ...]]:
    if not passages:
        return {}
    result: dict[str, list[CanonicalPassageVariant]] = {}
    variants = session.scalars(select(CanonicalPassageVariant).where(
        CanonicalPassageVariant.passage_id.in_([item.id for item in passages]),
        CanonicalPassageVariant.status == 'active',
        CanonicalPassageVariant.review_status == 'approved',
        CanonicalPassageVariant.source_priority >= MIN_AUTOMATIC_SOURCE_PRIORITY,
    ))
    for variant in variants:
        result.setdefault(variant.passage_id, []).append(variant)
    return {key: tuple(value) for key, value in result.items()}


def _pinyin_similarity(source: str, target: str) -> float:
    source_pinyin = tuple(lazy_pinyin(source, style=Style.NORMAL))
    target_pinyin = tuple(lazy_pinyin(target, style=Style.NORMAL))
    return SequenceMatcher(None, source_pinyin, target_pinyin, autojunk=False).ratio()


def _change_group_count(source: str, target: str) -> int:
    return sum(
        operation != 'equal'
        for operation, *_ in SequenceMatcher(
            None, source, target, autojunk=False
        ).get_opcodes()
    )
