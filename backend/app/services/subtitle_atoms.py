from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.chinese_text import normalize_to_simplified_chinese
from app.services.subtitle_linguistic_spans import resolve_linguistic_spans
from app.services.subtitle_pipeline_v2_schema import (
    RawAsrSnapshot,
    RawAsrToken,
    SemanticUtterance,
    SemanticUtteranceArtifact,
    canonical_sha256,
)
from app.services.subtitle_token_overlays import compact_correction_token_spans

if TYPE_CHECKING:
    from app.services.script_correction_candidates import CorrectionCandidate


@dataclass(frozen=True)
class LexicalAtom:
    start: int
    end: int
    text: str
    kind: str


AtomProtection = Literal['hard', 'strong', 'soft']
AtomSource = Literal[
    'accepted_script_correction',
    'confirmed_name',
    'runtime_lexicon',
    'organization',
    'number_date_unit',
    'enumeration',
    'phrase_integrity',
    'common_term',
    'ascii_word',
    'cjk_reduplication',
    'jieba',
    'raw_token',
]


class TokenBoundLexicalAtom(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    atom_id: str = Field(min_length=1)
    utterance_id: str = Field(min_length=1)
    token_start: int = Field(ge=0)
    token_end: int = Field(gt=0)
    token_ids: tuple[str, ...] = Field(min_length=1)
    text: str
    protection: AtomProtection
    source: AtomSource
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode='after')
    def validate_token_range(self) -> 'TokenBoundLexicalAtom':
        if self.token_end <= self.token_start:
            raise ValueError('token atom end must be after its start')
        if len(self.token_ids) != self.token_end - self.token_start:
            raise ValueError('token atom IDs must cover its half-open token range')
        if len(set(self.token_ids)) != len(self.token_ids):
            raise ValueError('token atom contains duplicate token IDs')
        if any(not token_id for token_id in self.token_ids):
            raise ValueError('token atom IDs must not be empty')
        if tuple(sorted(set(self.evidence_refs))) != self.evidence_refs:
            raise ValueError('token atom evidence refs must be unique and sorted')
        if any(not ref for ref in self.evidence_refs):
            raise ValueError('token atom evidence refs must not be empty')
        if self.source == 'jieba' and self.protection != 'soft':
            raise ValueError('jieba atoms must use soft protection')
        if self.source not in {'runtime_lexicon', 'jieba'} and self.protection != 'hard':
            raise ValueError('non-lexicon factual atoms must use hard protection')
        return self


@dataclass(frozen=True)
class _TokenAtomProposal:
    local_start: int
    local_end: int
    text: str
    protection: AtomProtection
    source: AtomSource
    evidence_refs: tuple[str, ...]


_CONFIRMED_NAME_PREFIXES = (
    '联合创始人',
    '工作人员',
    '主持人',
    '负责人',
    '发言人',
    '制作人',
    '创始人',
)
_SOURCE_PRIORITY = {
    'accepted_script_correction': 0,
    'confirmed_name': 1,
    'runtime_lexicon': 2,
    'organization': 3,
    'number_date_unit': 4,
    'enumeration': 5,
    'phrase_integrity': 6,
    'common_term': 7,
    'ascii_word': 8,
    'cjk_reduplication': 9,
    'jieba': 10,
    'raw_token': 99,
}
_PROTECTION_PRIORITY = {'hard': 0, 'strong': 1, 'soft': 2}
_TOKEN_MATCH_RE = re.compile(r'[^\u3400-\u9fffA-Za-z0-9]+')


def build_token_bound_lexical_atoms(
    raw_snapshot: RawAsrSnapshot,
    semantic_artifact: SemanticUtteranceArtifact,
    *,
    runtime_lexicon_bundle: Mapping[str, Any] | None = None,
    confirmed_terms: Iterable[str] = (),
    accepted_corrections: Sequence[CorrectionCandidate] = (),
) -> tuple[TokenBoundLexicalAtom, ...]:
    _validate_token_artifacts(raw_snapshot, semantic_artifact)
    token_by_id = {token.token_id: token for token in raw_snapshot.tokens}
    token_index = {token.token_id: index for index, token in enumerate(raw_snapshot.tokens)}
    utterance_by_id = {item.utterance_id: item for item in semantic_artifact.utterances}
    accepted_by_utterance = _accepted_corrections_by_utterance(
        accepted_corrections,
        utterance_by_id,
        token_index,
        token_by_id,
    )
    cleaned_confirmed_terms = tuple(sorted({
        normalize_to_simplified_chinese(str(item or '').strip())
        for item in confirmed_terms
        if str(item or '').strip()
    }, key=lambda item: (-len(item), item)))

    result: list[TokenBoundLexicalAtom] = []
    for utterance in semantic_artifact.utterances:
        tokens = tuple(token_by_id[token_id] for token_id in utterance.token_ids)
        global_indexes = tuple(token_index[token.token_id] for token in tokens)
        proposals = _token_atom_proposals(
            tokens,
            runtime_lexicon_bundle,
            cleaned_confirmed_terms,
            accepted_by_utterance.get(utterance.utterance_id, ()),
        )
        selected = _select_token_atom_proposals(proposals, len(tokens))
        covered = {
            index
            for proposal in selected
            for index in range(proposal.local_start, proposal.local_end)
        }
        selected.extend(
            _TokenAtomProposal(
                local_start=index,
                local_end=index + 1,
                text=_token_display_text(tokens[index]),
                protection='hard',
                source='raw_token',
                evidence_refs=(tokens[index].token_id,),
            )
            for index in range(len(tokens))
            if index not in covered
        )
        for proposal in sorted(selected, key=lambda item: item.local_start):
            token_start = global_indexes[proposal.local_start]
            token_end = global_indexes[proposal.local_end - 1] + 1
            token_ids = tuple(
                token.token_id
                for token in tokens[proposal.local_start:proposal.local_end]
            )
            identity = {
                'raw_snapshot_hash': raw_snapshot.snapshot_hash,
                'utterance_id': utterance.utterance_id,
                'token_ids': token_ids,
                'text': proposal.text,
                'protection': proposal.protection,
                'source': proposal.source,
                'evidence_refs': proposal.evidence_refs,
            }
            result.append(TokenBoundLexicalAtom(
                atom_id=f'atom-{canonical_sha256(identity)}',
                utterance_id=utterance.utterance_id,
                token_start=token_start,
                token_end=token_end,
                token_ids=token_ids,
                text=proposal.text,
                protection=proposal.protection,
                source=proposal.source,
                evidence_refs=proposal.evidence_refs,
            ))
    return tuple(result)


def _token_atom_proposals(
    tokens: Sequence[RawAsrToken],
    runtime_lexicon_bundle: Mapping[str, Any] | None,
    confirmed_terms: Sequence[str],
    accepted_corrections: Sequence[CorrectionCandidate],
) -> list[_TokenAtomProposal]:
    text, token_char_ranges = _utterance_text_and_ranges(tokens)
    proposals: list[_TokenAtomProposal] = []

    for correction in accepted_corrections:
        for compacted in compact_correction_token_spans(correction, tokens):
            proposals.append(_TokenAtomProposal(
                local_start=compacted.local_start,
                local_end=compacted.local_end,
                text=compacted.replacement_text,
                protection='hard',
                source='accepted_script_correction',
                evidence_refs=(correction.candidate_id,),
            ))

    proposals.extend(_confirmed_term_proposals(text, token_char_ranges, confirmed_terms))
    bundle_dict = dict(runtime_lexicon_bundle) if runtime_lexicon_bundle is not None else None
    for span in resolve_linguistic_spans(
        text,
        protected_terms=confirmed_terms,
        runtime_lexicon_bundle=bundle_dict,
        include_v2_token_spans=True,
    ):
        local_range = _char_span_to_token_range(span.start, span.end, token_char_ranges)
        if local_range is None:
            continue
        source, protection = _span_source_and_protection(span.span_type, span.text)
        evidence_refs = (
            _runtime_term_evidence(span.text, span.span_type, bundle_dict)
            if source == 'runtime_lexicon'
            else (f'{span.span_type}:{span.start}:{span.end}',)
        )
        proposals.append(_TokenAtomProposal(
            local_start=local_range[0],
            local_end=local_range[1],
            text=_text_for_token_range(text, token_char_ranges, local_range),
            protection=protection,
            source=source,
            evidence_refs=tuple(sorted(set(evidence_refs))),
        ))
    return proposals


def _confirmed_term_proposals(
    text: str,
    token_char_ranges: Sequence[tuple[int, int]],
    confirmed_terms: Sequence[str],
) -> list[_TokenAtomProposal]:
    proposals: list[_TokenAtomProposal] = []
    for term in confirmed_terms:
        start = text.find(term)
        while start != -1:
            extended_start = start
            for prefix in _CONFIRMED_NAME_PREFIXES:
                prefix_start = start - len(prefix)
                if prefix_start >= 0 and text[prefix_start:start] == prefix:
                    extended_start = prefix_start
                    break
            local_range = _char_span_to_token_range(
                extended_start,
                start + len(term),
                token_char_ranges,
            )
            if local_range is not None:
                proposals.append(_TokenAtomProposal(
                    local_start=local_range[0],
                    local_end=local_range[1],
                    text=_text_for_token_range(text, token_char_ranges, local_range),
                    protection='hard',
                    source='confirmed_name',
                    evidence_refs=(f'confirmed:{term}',),
                ))
            start = text.find(term, start + 1)
    return proposals


def _accepted_corrections_by_utterance(
    corrections: Sequence[CorrectionCandidate],
    utterance_by_id: Mapping[str, SemanticUtterance],
    token_index: Mapping[str, int],
    token_by_id: Mapping[str, RawAsrToken],
) -> dict[str, tuple[CorrectionCandidate, ...]]:
    result: dict[str, list[CorrectionCandidate]] = {}
    seen_ids: set[str] = set()
    occupied: set[str] = set()
    for correction in corrections:
        if correction.candidate_id in seen_ids:
            raise ValueError('accepted correction candidate IDs must be unique')
        seen_ids.add(correction.candidate_id)
        utterance = utterance_by_id.get(correction.utterance_id)
        if utterance is None:
            raise ValueError('accepted correction references an unknown utterance')
        if any(token_id not in token_index for token_id in correction.token_ids):
            raise ValueError('accepted correction references an unknown token')
        indexes = [token_index[token_id] for token_id in correction.token_ids]
        if indexes != list(range(indexes[0], indexes[-1] + 1)):
            raise ValueError('accepted correction token range must be contiguous')
        utterance_ids = list(utterance.token_ids)
        if any(token_id not in utterance_ids for token_id in correction.token_ids):
            raise ValueError('accepted correction crosses its semantic utterance')
        source_text = _normalize_token_match_text(''.join(
            _token_display_text(token_by_id[token_id])
            for token_id in correction.token_ids
        ))
        if source_text != _normalize_token_match_text(correction.source_text):
            raise ValueError('accepted correction source text disagrees with its raw tokens')
        if occupied.intersection(correction.token_ids):
            raise ValueError('accepted corrections must not overlap')
        occupied.update(correction.token_ids)
        result.setdefault(correction.utterance_id, []).append(correction)
    return {
        utterance_id: tuple(sorted(items, key=lambda item: token_index[item.token_ids[0]]))
        for utterance_id, items in result.items()
    }


def _utterance_text_and_ranges(
    tokens: Sequence[RawAsrToken],
) -> tuple[str, tuple[tuple[int, int], ...]]:
    parts: list[str] = []
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for token in tokens:
        part = _token_display_text(token)
        parts.append(part)
        ranges.append((cursor, cursor + len(part)))
        cursor += len(part)
    return ''.join(parts), tuple(ranges)


def _char_span_to_token_range(
    start: int,
    end: int,
    token_char_ranges: Sequence[tuple[int, int]],
) -> tuple[int, int] | None:
    indexes = [
        index
        for index, (token_start, token_end) in enumerate(token_char_ranges)
        if token_end > start and token_start < end
    ]
    if not indexes:
        return None
    return indexes[0], indexes[-1] + 1


def _text_for_token_range(
    text: str,
    token_char_ranges: Sequence[tuple[int, int]],
    token_range: tuple[int, int],
) -> str:
    start, end = token_range
    return text[token_char_ranges[start][0]:token_char_ranges[end - 1][1]]


def _span_source_and_protection(
    span_type: str,
    span_text: str,
) -> tuple[AtomSource, AtomProtection]:
    if span_type.startswith('runtime_lexicon_'):
        level = span_type.removeprefix('runtime_lexicon_')
        return 'runtime_lexicon', level if level in _PROTECTION_PRIORITY else 'soft'
    mapping: dict[str, tuple[AtomSource, AtomProtection]] = {
        'protected_term': ('confirmed_name', 'hard'),
        'organization': ('organization', 'hard'),
        'numeric_expression': ('number_date_unit', 'hard'),
        'enumeration_marker': ('enumeration', 'hard'),
        'phrase_integrity': ('phrase_integrity', 'hard'),
        'common_term': ('common_term', 'hard'),
        'ascii_word': ('ascii_word', 'hard'),
        'cjk_reduplication': ('cjk_reduplication', 'hard'),
        'cws': ('jieba', 'soft'),
    }
    source, protection = mapping.get(span_type, ('common_term', 'soft'))
    if span_type == 'common_term' and _is_cjk_reduplication(span_text):
        return 'cjk_reduplication', protection
    return source, protection


def _is_cjk_reduplication(value: str) -> bool:
    return (
        len(value) == 2
        and value[0] == value[1]
        and '\u3400' <= value[0] <= '\u9fff'
    )


def _runtime_term_evidence(
    span_text: str,
    span_type: str,
    bundle: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    level = span_type.removeprefix('runtime_lexicon_')
    normalized_span = _normalize_evidence_text(span_text)
    refs = tuple(sorted({
        str(item.get('term_id'))
        for item in (bundle or {}).get('terms') or []
        if isinstance(item, Mapping)
        and str(item.get('protection_level') or '').lower() == level
        and _normalize_evidence_text(
            str(item.get('normalized_term') or item.get('display_text') or '')
        ) == normalized_span
        and item.get('term_id') is not None
    }))
    return refs or (f'runtime:{level}:{normalized_span}',)


def _normalize_evidence_text(value: str) -> str:
    return re.sub(r'\s+', '', normalize_to_simplified_chinese(str(value or ''))).lower()


def _normalize_token_match_text(value: str) -> str:
    simplified = normalize_to_simplified_chinese(str(value or ''))
    return _TOKEN_MATCH_RE.sub('', simplified).lower()


def _select_token_atom_proposals(
    proposals: Sequence[_TokenAtomProposal],
    token_count: int,
) -> list[_TokenAtomProposal]:
    occupied = [False] * token_count
    selected: list[_TokenAtomProposal] = []
    unique = {
        (
            item.local_start,
            item.local_end,
            item.text,
            item.protection,
            item.source,
            item.evidence_refs,
        ): item
        for item in proposals
    }
    for proposal in sorted(
        unique.values(),
        key=lambda item: (
            0 if item.source == 'accepted_script_correction' else 1,
            _PROTECTION_PRIORITY[item.protection],
            _SOURCE_PRIORITY.get(item.source, 98),
            -(item.local_end - item.local_start),
            item.local_start,
            item.text,
        ),
    ):
        if (
            proposal.local_start < 0
            or proposal.local_end <= proposal.local_start
            or proposal.local_end > token_count
            or any(occupied[proposal.local_start:proposal.local_end])
        ):
            continue
        for index in range(proposal.local_start, proposal.local_end):
            occupied[index] = True
        selected.append(proposal)
    return selected


def _validate_token_artifacts(
    raw_snapshot: RawAsrSnapshot,
    semantic_artifact: SemanticUtteranceArtifact,
) -> None:
    if semantic_artifact.raw_snapshot_hash != raw_snapshot.snapshot_hash:
        raise ValueError('semantic artifact references a different raw snapshot')
    semantic_token_ids = [
        token_id
        for utterance in semantic_artifact.utterances
        for token_id in utterance.token_ids
    ]
    if semantic_token_ids != [token.token_id for token in raw_snapshot.tokens]:
        raise ValueError('semantic artifact does not conserve the raw token tape')


def _token_display_text(token: RawAsrToken) -> str:
    return token.text + token.punctuation


def split_text_by_atom_length(
    text: str,
    max_chars: int,
    *,
    protected_terms: Iterable[str] = (),
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> list[str]:
    value = str(text or '').strip()
    if not value:
        return []
    if max_chars <= 0 or len(value) <= max_chars:
        return [value]

    chunks: list[str] = []
    current = ''
    for atom in build_lexical_atoms(
        value,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ):
        atom_text = atom.text
        if not atom_text:
            continue
        if atom_text.isspace() and not current:
            continue

        candidate = f'{current}{atom_text}' if current else atom_text.lstrip()
        if current and len(candidate.strip()) > max_chars:
            chunks.append(current.strip())
            current = atom_text.lstrip()
            continue
        current = candidate

    if current.strip():
        chunks.append(current.strip())
    return chunks or [value]


def build_lexical_atoms(
    text: str,
    *,
    protected_terms: Iterable[str] = (),
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> list[LexicalAtom]:
    value = str(text or '')
    if not value:
        return []

    terms = tuple(protected_terms)
    if len(value) <= 512 and len(terms) <= 1024 and all(isinstance(term, str) and len(term) <= 64 for term in terms):
        try:
            bundle_key = json.dumps(runtime_lexicon_bundle, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            bundle_key = ''
        if bundle_key and len(bundle_key) <= 32768:
            return list(_cached_lexical_atoms(value, terms, bundle_key))
    return _build_lexical_atoms(value, terms, runtime_lexicon_bundle)


@lru_cache(maxsize=512)
def _cached_lexical_atoms(text: str, terms: tuple[str, ...], bundle_key: str) -> tuple[LexicalAtom, ...]:
    # Each boundary reuses the same analysis; callers still receive their own list.
    return tuple(_build_lexical_atoms(text, terms, json.loads(bundle_key)))


def _build_lexical_atoms(
    value: str,
    protected_terms: Iterable[str],
    runtime_lexicon_bundle: Optional[dict[str, Any]],
) -> list[LexicalAtom]:

    protected_spans = _select_protected_spans(
        value,
        protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    atoms: list[LexicalAtom] = []
    cursor = 0
    for start, end, kind in protected_spans:
        if cursor < start:
            atoms.extend(_fallback_atoms(value, cursor, start))
        atoms.append(LexicalAtom(start=start, end=end, text=value[start:end], kind=kind))
        cursor = end
    if cursor < len(value):
        atoms.extend(_fallback_atoms(value, cursor, len(value)))
    return atoms


def _select_protected_spans(
    text: str,
    protected_terms: Iterable[str],
    *,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> list[tuple[int, int, str]]:
    candidates: list[tuple[int, int, str]] = []
    for span in resolve_linguistic_spans(
        text,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ):
        candidates.append((span.start, span.end, span.span_type))

    occupied = [False for _ in text]
    selected: list[tuple[int, int, str]] = []
    for start, end, kind in sorted(candidates, key=lambda item: (-(item[1] - item[0]), item[0])):
        if start < 0 or end <= start or end > len(text):
            continue
        if any(occupied[start:end]):
            continue
        for index in range(start, end):
            occupied[index] = True
        selected.append((start, end, kind))
    return sorted(selected, key=lambda item: item[0])


def _fallback_atoms(text: str, start: int, end: int) -> list[LexicalAtom]:
    atoms: list[LexicalAtom] = []
    cursor = start
    while cursor < end:
        atoms.append(LexicalAtom(start=cursor, end=cursor + 1, text=text[cursor:cursor + 1], kind='char'))
        cursor += 1
    return atoms
