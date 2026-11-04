from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.lexicon_candidate import LexiconCandidate
from app.models.lexicon_occurrence import LexiconOccurrence
from app.models.lexicon_term import LexiconTerm, TERM_SCOPE_TYPES
from app.models.lexicon_term_override import LexiconTermOverride
from app.services.lexicon_normalize import normalize_term


TERM_ACTIVE_STATUSES = {"active", "disabled", "deprecated", "pending_review"}
CANDIDATE_STATUSES = {"candidate", "suggested_active", "rejected", "promoted", "deprecated"}
PROTECTION_LEVELS = {"hard", "strong", "soft"}
MAX_SENTENCE_LIKE_TERM_CHARS = 16
SENTENCE_LIKE_TERM_MARKERS = (
    "我们",
    "你们",
    "他们",
    "她们",
    "它们",
    "大家",
    "自己",
    "这个",
    "那个",
    "这些",
    "那些",
    "这里",
    "那里",
    "如何",
    "什么",
    "怎么",
    "为什么",
    "就是",
    "可以",
    "需要",
    "看一看",
)


def _require_value(name: str, value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{name} 不能为空")
    return cleaned


def _require_member(name: str, value: str, allowed: set[str]) -> str:
    cleaned = _require_value(name, value)
    if cleaned not in allowed:
        raise ValueError(f"{name} 无效: {cleaned}")
    return cleaned


def _require_span_offset(name: str, value: int) -> int:
    if value is None:
        raise ValueError(f"{name} 不能为空")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} 必须是整数")
    if value < 0:
        raise ValueError(f"{name} 必须大于等于 0")
    return value


def _require_ordered_span(start_name: str, start: int, end_name: str, end: int) -> None:
    if start > end:
        raise ValueError(f"{start_name} 不能大于 {end_name}")


def _compact_cjk_text(value: str) -> str:
    return "".join(ch for ch in str(value or "").strip() if "\u4e00" <= ch <= "\u9fff")


def _reject_sentence_like_term(display_text: str) -> None:
    compact = _compact_cjk_text(display_text)
    if len(compact) <= MAX_SENTENCE_LIKE_TERM_CHARS:
        return
    if any(marker in compact for marker in SENTENCE_LIKE_TERM_MARKERS):
        raise ValueError("词库只收录专名或固定短语，请不要把整句加入项目词库")


def upsert_candidate(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    normalized_term: str,
    display_text: str,
    term_type: str,
    status: str,
    suggested_protection_level: str,
    extraction_method: str,
    source_text_hash: str,
    raw_start_char: int,
    raw_end_char: int,
    normalized_start_char: int,
    normalized_end_char: int,
    confidence: Optional[float] = None,
    context: Optional[str] = None,
) -> LexiconCandidate:
    tenant_id = _require_value("tenant_id", tenant_id)
    project_id = _require_value("project_id", project_id)
    normalized_term = _require_value("normalized_term", normalized_term)
    display_text = _require_value("display_text", display_text)
    term_type = _require_value("term_type", term_type)
    status = _require_member("status", status, CANDIDATE_STATUSES)
    suggested_protection_level = _require_member(
        "suggested_protection_level",
        suggested_protection_level,
        PROTECTION_LEVELS,
    )
    extraction_method = _require_value("extraction_method", extraction_method)
    source_text_hash = _require_value("source_text_hash", source_text_hash)
    raw_start_char = _require_span_offset("raw_start_char", raw_start_char)
    raw_end_char = _require_span_offset("raw_end_char", raw_end_char)
    normalized_start_char = _require_span_offset("normalized_start_char", normalized_start_char)
    normalized_end_char = _require_span_offset("normalized_end_char", normalized_end_char)
    _require_ordered_span("raw_start_char", raw_start_char, "raw_end_char", raw_end_char)
    _require_ordered_span(
        "normalized_start_char",
        normalized_start_char,
        "normalized_end_char",
        normalized_end_char,
    )

    candidate = session.scalar(
        select(LexiconCandidate).where(
            LexiconCandidate.tenant_id == tenant_id,
            LexiconCandidate.project_id == project_id,
            LexiconCandidate.normalized_term == normalized_term,
            LexiconCandidate.extraction_method == extraction_method,
            LexiconCandidate.source_text_hash == source_text_hash,
            LexiconCandidate.raw_start_char == raw_start_char,
            LexiconCandidate.raw_end_char == raw_end_char,
            LexiconCandidate.normalized_start_char == normalized_start_char,
            LexiconCandidate.normalized_end_char == normalized_end_char,
        )
    )
    if candidate is None:
        candidate = LexiconCandidate(
            tenant_id=tenant_id,
            project_id=project_id,
            normalized_term=normalized_term,
            display_text=display_text,
            term_type=term_type,
            status=status,
            suggested_protection_level=suggested_protection_level,
            extraction_method=extraction_method,
            source_text_hash=source_text_hash,
            raw_start_char=raw_start_char,
            raw_end_char=raw_end_char,
            normalized_start_char=normalized_start_char,
            normalized_end_char=normalized_end_char,
            confidence=confidence,
            context=context,
        )
        session.add(candidate)
    else:
        candidate.display_text = display_text
        candidate.term_type = term_type
        candidate.status = status
        candidate.suggested_protection_level = suggested_protection_level
        candidate.confidence = confidence
        candidate.context = context
        candidate.updated_at = datetime.utcnow()

    session.flush()
    return candidate


def list_project_candidates(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    status: Optional[str] = None,
) -> list[LexiconCandidate]:
    tenant_id = _require_value("tenant_id", tenant_id)
    project_id = _require_value("project_id", project_id)
    statement = select(LexiconCandidate).where(
        LexiconCandidate.tenant_id == tenant_id,
        LexiconCandidate.project_id == project_id,
    )
    if status is not None:
        statement = statement.where(LexiconCandidate.status == _require_member("status", status, CANDIDATE_STATUSES))
    statement = statement.order_by(LexiconCandidate.created_at.asc(), LexiconCandidate.normalized_term.asc())
    return list(session.scalars(statement))


def promote_candidate_to_term(
    session: Session,
    candidate_id: str,
    created_by: str = "client",
) -> LexiconTerm:
    candidate = session.get(LexiconCandidate, _require_value("candidate_id", candidate_id))
    if candidate is None:
        raise ValueError("candidate not found")
    if candidate.status not in {"candidate", "suggested_active"}:
        raise ValueError(f"candidate status cannot be promoted: {candidate.status}")

    now = datetime.utcnow()
    term = LexiconTerm(
        tenant_id=candidate.tenant_id,
        scope_type="project",
        scope_id=candidate.project_id,
        normalized_term=candidate.normalized_term,
        display_text=candidate.display_text,
        term_type=candidate.term_type,
        status="active",
        protection_level=candidate.suggested_protection_level,
        review_status="approved",
        source_type="candidate_promotion",
        source_ref=candidate.id,
        created_by=created_by,
        reviewed_by=created_by,
        confidence=candidate.confidence,
        reviewed_at=now,
    )
    session.add(term)
    session.flush()

    candidate.status = "promoted"
    candidate.promoted_term_id = term.id
    candidate.reviewed_by = created_by
    candidate.reviewed_at = now
    candidate.updated_at = now
    _associate_candidate_occurrences_with_term(session, candidate, term)
    session.flush()
    return term


def reject_candidate(
    session: Session,
    candidate_id: str,
    reviewed_by: str = "client",
    reason: str = "",
) -> LexiconCandidate:
    candidate = session.get(LexiconCandidate, _require_value("candidate_id", candidate_id))
    if candidate is None:
        raise ValueError("candidate not found")
    if candidate.status == "promoted":
        raise ValueError("promoted candidate cannot be rejected")

    candidate.status = "rejected"
    candidate.reviewed_by = reviewed_by
    candidate.review_reason = reason
    candidate.reviewed_at = datetime.utcnow()
    session.flush()
    return candidate


def disable_term_for_scope(
    session: Session,
    *,
    term_id: str,
    normalized_term: str,
    scope_type: str,
    scope_id: str,
    reason: str = "",
    created_by: str = "client",
) -> LexiconTermOverride:
    term = session.get(LexiconTerm, _require_value("term_id", term_id))
    if term is None:
        raise ValueError("term not found")

    override = LexiconTermOverride(
        tenant_id=term.tenant_id,
        term_id=term.id,
        normalized_term=_require_value("normalized_term", normalized_term),
        scope_type=_require_member("scope_type", scope_type, TERM_SCOPE_TYPES),
        scope_id=_require_value("scope_id", scope_id),
        action="disable",
        reason=reason,
        created_by=created_by,
    )
    session.add(override)
    session.flush()
    return override


def _associate_candidate_occurrences_with_term(
    session: Session,
    candidate: LexiconCandidate,
    term: LexiconTerm,
) -> None:
    occurrences = list(
        session.scalars(
            select(LexiconOccurrence).where(
                LexiconOccurrence.tenant_id == candidate.tenant_id,
                LexiconOccurrence.candidate_id == candidate.id,
            )
        )
    )
    if occurrences:
        for occurrence in occurrences:
            occurrence.term_id = term.id
    else:
        session.add(
            LexiconOccurrence(
                tenant_id=candidate.tenant_id,
                term_id=term.id,
                candidate_id=candidate.id,
                project_id=candidate.project_id,
                source_type="candidate_promotion",
                source_ref=candidate.source_text_hash or candidate.id,
                raw_context=candidate.context,
                normalized_context=candidate.context,
                raw_start_char=candidate.raw_start_char,
                raw_end_char=candidate.raw_end_char,
                normalized_start_char=candidate.normalized_start_char,
                normalized_end_char=candidate.normalized_end_char,
            )
        )
    session.flush()
    term.occurrence_count = int(
        session.scalar(
            select(func.count())
            .select_from(LexiconOccurrence)
            .where(
                LexiconOccurrence.tenant_id == term.tenant_id,
                LexiconOccurrence.term_id == term.id,
            )
        )
        or 0
    )


def create_project_term(
    session: Session,
    *,
    tenant_id: str,
    project_id: Optional[str],
    scope_type: str = "project",
    display_text: str,
    term_type: str = "term",
    protection_level: str = "strong",
    source_type: str = "manual",
    created_by: str = "client",
) -> LexiconTerm:
    tenant_id = _require_value("tenant_id", tenant_id)
    scope_type = _require_member("scope_type", scope_type, TERM_SCOPE_TYPES)
    scope_id = None if scope_type == "global" else _require_value("project_id", project_id)
    display_text = _require_value("display_text", display_text)
    _reject_sentence_like_term(display_text)
    normalized = _require_value("normalized_term", normalize_term(display_text))
    term_type = _require_value("term_type", term_type)
    protection_level = _require_member("protection_level", protection_level, PROTECTION_LEVELS)
    source_type = _require_value("source_type", source_type)

    existing = session.scalar(
        select(LexiconTerm)
        .where(
            LexiconTerm.tenant_id == tenant_id,
            LexiconTerm.scope_type == scope_type,
            LexiconTerm.scope_id == scope_id,
            LexiconTerm.normalized_term == normalized,
            LexiconTerm.status == "active",
        )
        .order_by(LexiconTerm.created_at.asc(), LexiconTerm.id.asc())
    )
    if existing is not None:
        return existing

    term = LexiconTerm(
        tenant_id=tenant_id,
        scope_type=scope_type,
        scope_id=scope_id,
        normalized_term=normalized,
        display_text=display_text,
        term_type=term_type,
        status="active",
        protection_level=protection_level,
        review_status="approved",
        source_type=source_type,
        created_by=created_by,
        reviewed_by=created_by,
        reviewed_at=datetime.utcnow(),
    )
    session.add(term)
    session.flush()
    return term
