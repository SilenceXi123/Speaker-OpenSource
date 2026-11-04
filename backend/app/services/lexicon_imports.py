from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.lexicon_candidate import LexiconCandidate
from app.models.lexicon_import import LexiconImport
from app.models.lexicon_occurrence import LexiconOccurrence
from app.services.lexicon_normalize import build_normalized_text_with_offset_map, hash_text, normalize_term
from app.services.lexicon_repository import upsert_candidate
from app.services.script_context import extract_script_context, extract_text_from_script_bytes, merge_script_contexts


PARSER_VERSION = "lexicon_imports.v1"
ORG_SUFFIXES = ("有限责任公司", "股份有限公司", "集团有限公司", "有限公司", "公司", "中心", "研究院", "学院", "大学", "委员会", "协会", "工作室")
LOCATION_SUFFIXES = ("古镇", "湾", "市", "区", "县", "镇", "村", "山", "湖", "河", "园", "街", "路")

TITLE_RE = re.compile(r"[《「“\"']([^》」”\"']{2,30})[》」”\"']")
ORG_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{2,48}?(?:" + "|".join(map(re.escape, ORG_SUFFIXES)) + r")")
LOCATION_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]{1,24}(?:" + "|".join(map(re.escape, LOCATION_SUFFIXES)) + r")")
CJK_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,12}")
LEADING_CONTEXT_RE = re.compile(r"^(?:.*?(?:来自|提到了|提到|地点是|位于|进入|到达|包括|以及)|.*[、，。：:])")
TRAILING_PUNCTUATION = " \t\r\n，。！？；：、,.!?;:()（）[]【】<>《》\"“”'「」"


@dataclass(frozen=True)
class LexiconImportResult:
    import_record: LexiconImport
    candidates: list[LexiconCandidate]
    rejected_count: int


@dataclass(frozen=True)
class _CandidateSpan:
    display_text: str
    term_type: str
    status: str
    protection_level: str
    extraction_method: str
    raw_start: int
    raw_end: int
    confidence: float


def save_project_script_evidence(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    source_name: str,
    text: str,
    script_context: dict[str, Any] | None = None,
) -> LexiconImport:
    raw_text = str(text or '').strip()
    source_text_hash = hash_text(raw_text)
    existing = session.scalar(
        select(LexiconImport).where(
            LexiconImport.tenant_id == tenant_id,
            LexiconImport.project_id == project_id,
            LexiconImport.source_type == 'task_script',
            LexiconImport.source_text_hash == source_text_hash,
        )
    )
    if existing is not None:
        if script_context and script_context.get('lyrics_references'):
            existing.metrics_json = {
                **(existing.metrics_json or {}),
                'script_context': script_context,
            }
            session.flush()
        return existing
    context = script_context or extract_script_context(raw_text, source_name=source_name)
    record = LexiconImport(
        tenant_id=tenant_id,
        project_id=project_id,
        source_name=source_name,
        source_type='task_script',
        source_text_hash=source_text_hash,
        parser_version=PARSER_VERSION,
        status='parsed',
        candidate_count=0,
        promoted_count=0,
        rejected_count=0,
        metrics_json={'raw_text_chars': len(raw_text), 'script_context': context},
        created_by='client',
    )
    session.add(record)
    session.flush()
    return record


def list_project_script_evidence(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
) -> list[LexiconImport]:
    return list(session.scalars(
        select(LexiconImport)
        .where(
            LexiconImport.tenant_id == tenant_id,
            LexiconImport.project_id == project_id,
            LexiconImport.source_type == 'task_script',
            LexiconImport.status == 'parsed',
        )
        .order_by(LexiconImport.created_at.asc())
    ))


def build_project_script_context(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
) -> dict[str, Any] | None:
    records = list_project_script_evidence(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
    )
    return merge_script_contexts(*[
        (record.metrics_json or {}).get('script_context')
        for record in records
    ])


def import_lexicon_candidates_from_text(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    source_name: str,
    text: str,
    source_type: str = "paste",
    forced_candidates: Iterable[Any] | None = None,
) -> LexiconImportResult:
    raw_text = str(text or "")
    forced_candidate_items = list(forced_candidates or [])
    normalized_map = build_normalized_text_with_offset_map(raw_text)
    source_text_hash = hash_text(raw_text)
    import_record = LexiconImport(
        tenant_id=str(tenant_id or "").strip(),
        project_id=str(project_id or "").strip(),
        source_name=str(source_name or "").strip() or "untitled",
        source_type=str(source_type or "").strip() or "paste",
        source_text_hash=source_text_hash,
        parser_version=PARSER_VERSION,
        status="parsed",
        candidate_count=0,
        promoted_count=0,
        rejected_count=0,
        error_message=None,
        metrics_json={"raw_text_chars": len(raw_text), "normalized_text_chars": len(normalized_map.normalized_text)},
    )
    session.add(import_record)
    session.flush()

    extracted = _extract_candidate_spans(raw_text)
    forced, forced_rejected = _extract_forced_candidate_spans(raw_text, forced_candidate_items)
    extracted.extend(forced)

    candidates: list[LexiconCandidate] = []
    seen: set[tuple[str, int, int]] = set()
    rejected_count = forced_rejected
    created_count = 0
    updated_count = 0
    for span in sorted(extracted, key=lambda item: (item.raw_start, item.raw_end, item.display_text)):
        normalized = normalize_term(span.display_text)
        if not normalized:
            rejected_count += 1
            continue
        normalized_offsets = _normalized_offsets_for_raw_span(normalized_map.raw_to_normalized, span.raw_start, span.raw_end)
        if normalized_offsets is None:
            rejected_count += 1
            continue
        normalized_start, normalized_end = normalized_offsets
        key = (normalized, span.raw_start, span.raw_end)
        if key in seen:
            continue
        seen.add(key)
        existing_candidate = _find_existing_candidate(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            normalized_term=normalized,
            extraction_method=span.extraction_method,
            source_text_hash=source_text_hash,
            raw_start_char=span.raw_start,
            raw_end_char=span.raw_end,
            normalized_start_char=normalized_start,
            normalized_end_char=normalized_end,
        )
        candidate = upsert_candidate(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            normalized_term=normalized,
            display_text=span.display_text,
            term_type=span.term_type,
            status=span.status,
            suggested_protection_level=span.protection_level,
            extraction_method=span.extraction_method,
            source_text_hash=source_text_hash,
            raw_start_char=span.raw_start,
            raw_end_char=span.raw_end,
            normalized_start_char=normalized_start,
            normalized_end_char=normalized_end,
            confidence=span.confidence,
            context=_context_for_span(raw_text, span.raw_start, span.raw_end),
        )
        if existing_candidate is None:
            created_count += 1
        else:
            updated_count += 1
        _create_import_occurrence(session, import_record=import_record, candidate=candidate)
        candidates.append(candidate)

    import_record.candidate_count = len(candidates)
    import_record.rejected_count = rejected_count
    import_record.metrics_json = {
        **(import_record.metrics_json or {}),
        "forced_candidate_count": len(forced_candidate_items),
        "returned_candidate_count": len(candidates),
        "created_candidate_count": created_count,
        "updated_candidate_count": updated_count,
    }
    session.flush()
    return LexiconImportResult(import_record=import_record, candidates=candidates, rejected_count=rejected_count)


def import_lexicon_candidates_from_bytes(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    file_name: str,
    payload: bytes,
    source_type: str = "upload",
) -> LexiconImportResult:
    text = extract_text_from_script_bytes(payload, file_name)
    return import_lexicon_candidates_from_text(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        source_name=file_name,
        text=text,
        source_type=source_type,
    )


def _extract_candidate_spans(text: str) -> list[_CandidateSpan]:
    spans: list[_CandidateSpan] = []
    occupied: list[tuple[int, int]] = []

    for match in TITLE_RE.finditer(text):
        start, end = match.span(1)
        display_text = _clean_display_text(match.group(1))
        if display_text:
            spans.append(_CandidateSpan(display_text, "program", "suggested_active", "hard", "title_rule", start, end, 0.94))
            occupied.append((start, end))

    for match in ORG_RE.finditer(text):
        start, end, display_text = _trim_candidate_match(text, match.start(), match.end())
        if display_text:
            spans.append(
                _CandidateSpan(display_text, "organization", "suggested_active", "hard", "org_suffix_rule", start, end, 0.92)
            )
            occupied.append((start, end))

    for match in LOCATION_RE.finditer(text):
        start, end, display_text = _trim_candidate_match(text, match.start(), match.end())
        if display_text and not _overlaps(start, end, occupied):
            spans.append(_CandidateSpan(display_text, "place", "suggested_active", "hard", "location_suffix_rule", start, end, 0.88))
            occupied.append((start, end))

    spans.extend(_extract_repeated_cjk_terms(text, occupied))
    return spans


def _extract_repeated_cjk_terms(text: str, occupied: list[tuple[int, int]]) -> list[_CandidateSpan]:
    tokens = _jieba_tokens(text)
    counts = Counter(token for token, _start, _end in tokens if _is_repeated_term_candidate(token))
    spans: list[_CandidateSpan] = []
    for token, start, end in tokens:
        if counts[token] < 2 or _overlaps(start, end, occupied):
            continue
        spans.append(_CandidateSpan(token, "term", "candidate", "soft", "jieba_repeated_cjk", start, end, 0.55))
    return spans


def _jieba_tokens(text: str) -> list[tuple[str, int, int]]:
    try:
        import jieba  # type: ignore
    except Exception:
        return [(match.group(0), match.start(), match.end()) for match in CJK_TERM_RE.finditer(text)]

    tokenizer = jieba.Tokenizer()
    tokens: list[tuple[str, int, int]] = []
    cursor = 0
    for token in tokenizer.cut(text, cut_all=False):
        if not token:
            continue
        start = text.find(token, cursor)
        if start < 0:
            start = text.find(token)
        if start < 0:
            continue
        end = start + len(token)
        tokens.append((token, start, end))
        cursor = end
    return tokens


def _extract_forced_candidate_spans(text: str, forced_candidates: Iterable[Any] | None) -> tuple[list[_CandidateSpan], int]:
    spans: list[_CandidateSpan] = []
    rejected_count = 0
    for item in forced_candidates or []:
        display_text, term_type = _forced_candidate_identity(item)
        display_text = _clean_display_text(display_text)
        if not display_text:
            rejected_count += 1
            continue
        start = text.find(display_text)
        if start < 0:
            rejected_count += 1
            continue
        spans.append(
            _CandidateSpan(
                display_text=display_text,
                term_type=term_type,
                status="candidate",
                protection_level="soft",
                extraction_method="forced_candidate",
                raw_start=start,
                raw_end=start + len(display_text),
                confidence=0.8,
            )
        )
    return spans, rejected_count


def _forced_candidate_identity(item: Any) -> tuple[str, str]:
    if isinstance(item, dict):
        text = item.get("display_text") or item.get("text") or item.get("term") or item.get("value") or ""
        term_type = str(item.get("term_type") or "term")
        return str(text), term_type
    return str(item), "term"


def _find_existing_candidate(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    normalized_term: str,
    extraction_method: str,
    source_text_hash: str,
    raw_start_char: int,
    raw_end_char: int,
    normalized_start_char: int,
    normalized_end_char: int,
) -> LexiconCandidate | None:
    return session.scalar(
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


def _create_import_occurrence(
    session: Session,
    *,
    import_record: LexiconImport,
    candidate: LexiconCandidate,
) -> None:
    session.add(
        LexiconOccurrence(
            tenant_id=import_record.tenant_id,
            candidate_id=candidate.id,
            import_id=import_record.id,
            project_id=import_record.project_id,
            source_type="lexicon_import",
            source_ref=import_record.source_name,
            raw_context=candidate.context,
            normalized_context=candidate.context,
            raw_start_char=candidate.raw_start_char,
            raw_end_char=candidate.raw_end_char,
            normalized_start_char=candidate.normalized_start_char,
            normalized_end_char=candidate.normalized_end_char,
        )
    )


def _trim_candidate_match(text: str, start: int, end: int) -> tuple[int, int, str]:
    raw = text[start:end]
    prefix_match = LEADING_CONTEXT_RE.match(raw)
    if prefix_match:
        start += prefix_match.end()
        raw = text[start:end]
    while raw and raw[0] in TRAILING_PUNCTUATION:
        start += 1
        raw = text[start:end]
    while raw and raw[-1] in TRAILING_PUNCTUATION:
        end -= 1
        raw = text[start:end]
    return start, end, _clean_display_text(raw)


def _clean_display_text(value: str) -> str:
    return str(value or "").strip(TRAILING_PUNCTUATION)


def _is_repeated_term_candidate(token: str) -> bool:
    token = _clean_display_text(token)
    if len(token) < 2 or len(token) > 12:
        return False
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,12}", token):
        return False
    if token.endswith(ORG_SUFFIXES) or token.endswith(LOCATION_SUFFIXES):
        return False
    return True


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _normalized_offsets_for_raw_span(raw_to_normalized: list[int | None], raw_start: int, raw_end: int) -> tuple[int, int] | None:
    if raw_start < 0 or raw_end <= raw_start or raw_end > len(raw_to_normalized):
        return None
    mapped = [offset for offset in raw_to_normalized[raw_start:raw_end] if offset is not None]
    if not mapped:
        return None
    return mapped[0], mapped[-1] + 1


def _context_for_span(text: str, start: int, end: int, radius: int = 40) -> str:
    return text[max(0, start - radius): min(len(text), end + radius)]
