from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from app.services.chinese_text import normalize_to_simplified_chinese
from app.services.subtitle_pipeline_v2_schema import RawAsrToken, SemanticUtterance


_MATCH_TEXT_RE = re.compile(r'[^\u3400-\u9fffA-Za-z0-9]+')


@dataclass(frozen=True, slots=True)
class CanonicalCorrectionProposal:
    utterance_id: str
    token_ids: tuple[str, ...]
    source_text: str
    replacement_text: str
    passage_id: str
    passage_title: str
    line_index: int
    confidence: float
    margin: float
    source_priority: int
    content_hash: str


def build_canonical_correction_proposals(
    utterance: SemanticUtterance,
    tokens: list[RawAsrToken],
    runtime_lexicon_bundle: Mapping[str, Any] | None,
) -> tuple[CanonicalCorrectionProposal, ...]:
    if not runtime_lexicon_bundle:
        return ()
    normalized_tokens = tuple(_normalize(token.text) for token in tokens)
    text = ''.join(normalized_tokens)
    if not text:
        return ()
    token_ranges: list[tuple[int, int]] = []
    cursor = 0
    for token_text in normalized_tokens:
        token_ranges.append((cursor, cursor + len(token_text)))
        cursor += len(token_text)

    matches: list[tuple[int, int, CanonicalCorrectionProposal]] = []
    for item in runtime_lexicon_bundle.get('canonical_corrections') or []:
        if not isinstance(item, Mapping):
            continue
        source = _normalize(str(item.get('source_text') or ''))
        replacement = _normalize(str(item.get('replacement_text') or ''))
        passage_id = str(item.get('passage_id') or '').strip()
        content_hash = str(item.get('content_hash') or '').strip()
        if not source or not replacement or source == replacement or not passage_id:
            continue
        search_from = 0
        while True:
            position = text.find(source, search_from)
            if position < 0:
                break
            end_position = position + len(source)
            first_token = _token_at(token_ranges, position)
            last_token = _token_at(token_ranges, end_position - 1)
            search_from = position + 1
            if first_token is None or last_token is None:
                continue
            span_start = token_ranges[first_token][0]
            span_end = token_ranges[last_token][1]
            source_span = text[span_start:span_end]
            relative_start = position - span_start
            replacement_span = (
                source_span[:relative_start]
                + replacement
                + source_span[relative_start + len(source):]
            )
            proposal = CanonicalCorrectionProposal(
                utterance_id=utterance.utterance_id,
                token_ids=tuple(
                    token.token_id for token in tokens[first_token:last_token + 1]
                ),
                source_text=source_span,
                replacement_text=replacement_span,
                passage_id=passage_id,
                passage_title=str(item.get('passage_title') or '').strip(),
                line_index=int(item.get('line_index') or 0),
                confidence=float(item.get('confidence') or 0.0),
                margin=float(item.get('margin') or 0.0),
                source_priority=int(item.get('source_priority') or 0),
                content_hash=content_hash,
            )
            matches.append((first_token, last_token + 1, proposal))

    accepted: list[CanonicalCorrectionProposal] = []
    occupied: set[int] = set()
    for start, end, proposal in sorted(
        matches,
        key=lambda item: (
            -item[2].confidence,
            -item[2].margin,
            -item[2].source_priority,
            item[0],
            item[2].passage_id,
        ),
    ):
        indexes = set(range(start, end))
        if indexes & occupied:
            continue
        occupied.update(indexes)
        accepted.append(proposal)
    return tuple(accepted)


def _normalize(value: str) -> str:
    simplified = normalize_to_simplified_chinese(str(value or ''))
    return _MATCH_TEXT_RE.sub('', simplified).lower()


def _token_at(ranges: list[tuple[int, int]], char_index: int) -> int | None:
    for index, (start, end) in enumerate(ranges):
        if start <= char_index < end:
            return index
    return None
