from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pypinyin import Style, lazy_pinyin

from app.services.chinese_text import normalize_to_simplified_chinese
from app.services.canonical_correction_candidates import (
    CanonicalCorrectionProposal,
    build_canonical_correction_proposals,
)
from app.services.grammar_particle_candidates import (
    GRAMMAR_PARTICLE_PROFILE_VERSION,
    GrammarParticleProposal,
    build_grammar_particle_proposals,
)
from app.services.learned_correction_candidates import (
    LearnedCorrectionProposal,
    build_learned_correction_proposals,
)
from app.services.script_context import REFERENCE_LINE_MAX_CHARS, extract_script_document
from app.services.script_line_alignment import (
    ExactScriptLineAlignment,
    build_exact_script_line_alignments,
    build_match_text_view,
    display_text_for_match_range,
    normalize_match_text,
    validate_exact_script_line_alignments,
)
from app.services.subtitle_pipeline_v2_schema import (
    RawAsrSnapshot,
    RawAsrToken,
    SemanticUtterance,
    SemanticUtteranceArtifact,
    canonical_sha256,
)
from app.services.subtitle_linguistic_spans import numeric_fact_signature


ScriptSourceKind = Literal['txt', 'md', 'srt', 'docx', 'pdf', 'pasted']
ScriptExtractionStatus = Literal['ready', 'unextractable']
SCRIPT_SNAPSHOT_SCHEMA_VERSION = 'script-snapshot.v2'
_LOWER_HEX_SHA256 = r'^[0-9a-f]{64}$'
_LEXICON_BOOST = {'hard': 0.10, 'strong': 0.07, 'soft': 0.04}
_CONTEXT_ANCHOR_MAX_CHARS = 4
_CONTEXT_ANCHOR_MIN_CHARS = 2
_MAX_ANCHORED_RANGES_PER_LINE = 24
_EXACT_REACQUIRE_MIN_CHARS = 8
_LOCAL_PHONETIC_THRESHOLD = 0.66
_LATIN_EDIT_RE = re.compile(r'^[A-Za-z]*$')
_CJK_TERM_RE = re.compile(r'^[\u3400-\u9fff]+$')
_LATIN_DISPLAY_TERM_RE = re.compile(
    r'[A-Za-z][A-Za-z0-9+_.-]*(?:\s+[A-Za-z0-9][A-Za-z0-9+_.-]*)*'
)
SCRIPT_DISPLAY_FIDELITY_EVIDENCE_REF = 'script-display-fidelity:case-space'
_PERSON_ROSTER_LINE_RE = re.compile(
    r'(?:人工金标规范人名|项目强制词库人名|第[一二三四五六七八九十\d]+位是|(?:有请|欢迎).{0,32}(?:成员|嘉宾|选手|朋友|艺人))'
)


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class ScriptLine(_FrozenStrictModel):
    line_id: str = Field(min_length=1)
    line_number: int = Field(ge=1)
    paragraph_index: int = Field(ge=0)
    order_index: int = Field(ge=0)
    source_hash: str = Field(pattern=_LOWER_HEX_SHA256)
    source_order: int = Field(ge=0)
    source_line_order: int = Field(ge=0)
    fragment_index: int = Field(ge=0)
    ends_physical_line: bool
    text: str = Field(min_length=1)
    match_text: str = Field(min_length=1)


class ScriptSnapshot(_FrozenStrictModel):
    schema_version: str = Field(min_length=1)
    source_kind: ScriptSourceKind
    source_name: str | None = None
    source_hash: str = Field(pattern=_LOWER_HEX_SHA256)
    extraction_status: ScriptExtractionStatus
    extraction_warning: str | None = None
    lines: tuple[ScriptLine, ...]
    snapshot_hash: str = Field(pattern=_LOWER_HEX_SHA256)

    @model_validator(mode='after')
    def validate_content(self) -> 'ScriptSnapshot':
        if self.extraction_status == 'ready' and self.extraction_warning is not None:
            raise ValueError('ready script snapshots must not contain extraction warnings')
        if self.extraction_status == 'unextractable' and (
            self.lines or not self.extraction_warning
        ):
            raise ValueError('unextractable script snapshots require a warning and no lines')
        line_ids = [line.line_id for line in self.lines]
        if len(line_ids) != len(set(line_ids)):
            raise ValueError('script snapshot contains duplicate line IDs')
        if [line.order_index for line in self.lines] != list(range(len(self.lines))):
            raise ValueError('script line order_index values must be contiguous')
        source_line_orders: dict[tuple[int, str], list[int]] = {}
        for line in self.lines:
            source_line_orders.setdefault(
                (line.source_order, line.source_hash),
                [],
            ).append(line.source_line_order)
        if any(
            orders != list(range(len(orders)))
            for orders in source_line_orders.values()
        ):
            raise ValueError(
                'script source_line_order values must be contiguous per source'
            )
        content = self.model_dump(mode='json', exclude={'snapshot_hash'})
        if canonical_sha256(content) != self.snapshot_hash:
            raise ValueError('snapshot_hash does not match script snapshot content')
        return self


class CorrectionThresholdProfile(_FrozenStrictModel):
    profile_version: str = Field(min_length=1)
    script_line_window: int = Field(ge=0)
    context_token_window: int = Field(gt=0)
    max_token_span: int = Field(gt=0)
    phonetic_threshold: float = Field(ge=0, le=1)
    context_threshold: float = Field(ge=0, le=1)
    margin_threshold: float = Field(ge=0, le=1)


class CorrectionCandidate(_FrozenStrictModel):
    candidate_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    utterance_id: str = Field(min_length=1)
    token_ids: tuple[str, ...] = Field(min_length=1)
    source_text: str = Field(min_length=1)
    replacement_text: str = Field(min_length=1)
    script_line_id: str = Field(min_length=1)
    script_line_order: int = Field(ge=0)
    script_source_hash: str = Field(pattern=_LOWER_HEX_SHA256)
    phonetic_score: float = Field(ge=0, le=1)
    context_score: float = Field(ge=0, le=1)
    ranking_score: float = Field(ge=0, le=1)
    margin: float = Field(ge=0, le=1)
    lexicon_term_id: str | None = None
    lexicon_protection_level: str | None = None
    candidate_type: Literal['script', 'lexicon', 'grammar', 'canonical_passage'] = 'script'
    evidence_refs: tuple[str, ...] = ()


def is_script_display_fidelity_change(
    *,
    source_text: str,
    replacement_text: str,
    evidence_refs: Sequence[str],
) -> bool:
    if tuple(evidence_refs) != (SCRIPT_DISPLAY_FIDELITY_EVIDENCE_REF,):
        return False
    if source_text == replacement_text:
        return False
    return _ascii_case_space_key(source_text) == _ascii_case_space_key(
        replacement_text,
    )


DEFAULT_CORRECTION_THRESHOLD_PROFILE = CorrectionThresholdProfile(
    profile_version='evidence-v1',
    script_line_window=12,
    context_token_window=8,
    max_token_span=32,
    phonetic_threshold=0.88,
    context_threshold=0.70,
    margin_threshold=0.08,
)


class _Proposal:
    __slots__ = (
        'context_score',
        'end_index',
        'lexicon_protection_level',
        'lexicon_term_id',
        'line',
        'phonetic_score',
        'ranking_score',
        'replacement_text',
        'source_text',
        'start_index',
        'token_ids',
        'utterance',
    )

    def __init__(
        self,
        *,
        utterance: SemanticUtterance,
        start_index: int,
        end_index: int,
        token_ids: tuple[str, ...],
        source_text: str,
        replacement_text: str,
        line: ScriptLine,
        phonetic_score: float,
        context_score: float,
        ranking_score: float,
        lexicon_term_id: str | None,
        lexicon_protection_level: str | None,
    ) -> None:
        self.utterance = utterance
        self.start_index = start_index
        self.end_index = end_index
        self.token_ids = token_ids
        self.source_text = source_text
        self.replacement_text = replacement_text
        self.line = line
        self.phonetic_score = phonetic_score
        self.context_score = context_score
        self.ranking_score = ranking_score
        self.lexicon_term_id = lexicon_term_id
        self.lexicon_protection_level = lexicon_protection_level


def build_script_snapshot(
    source: str | bytes,
    *,
    source_name: str | None = None,
    source_sections: Sequence[Mapping[str, Any]] | None = None,
) -> ScriptSnapshot:
    safe_source_name = (
        Path(source_name.replace('\\', '/')).name
        if source_name
        else None
    )
    if source_sections is not None:
        if isinstance(source, bytes):
            raise ValueError('composite script snapshots require text sections')
        section_records = tuple(
            _normalize_source_section(section)
            for section in source_sections
            if isinstance(section, Mapping)
        )
        source_kind: ScriptSourceKind = 'pasted'
        status: ScriptExtractionStatus = 'ready'
        warning = None
        lines_list: list[ScriptLine] = []
        for source_order, section in enumerate(section_records):
            section_lines = section['reference_lines']
            lines_list.extend(_build_script_lines(
                '\n'.join(section_lines),
                section['source_hash'],
                source_order=source_order,
                order_index_start=len(lines_list),
                physical_line_ends=section['physical_line_ends'],
            ))
        lines = tuple(lines_list)
        section_names = [
            str(section['source_name'])
            for section in section_records
            if section['source_name']
        ]
        safe_source_name = (
            safe_source_name
            or (' + '.join(section_names) if section_names else None)
        )
        source_hash = canonical_sha256({
            'source_sections': [
                {
                    'source_name': section['source_name'],
                    'source_hash': section['source_hash'],
                    'reference_lines': section['reference_lines'],
                    'physical_line_ends': section['physical_line_ends'],
                }
                for section in section_records
            ],
        })
    elif isinstance(source, bytes):
        if not source_name:
            raise ValueError('byte script sources require source_name')
        source_bytes = source
        extracted = extract_script_document(source, source_name)
        source_kind = extracted.source_kind
        text = extracted.text
        status = extracted.status
        warning = extracted.warning
    else:
        source_bytes = str(source or '').encode('utf-8')
        source_kind = 'pasted'
        text = str(source or '').replace('\ufeff', '')
        status = 'ready'
        warning = None

    if source_sections is None:
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        lines = (
            _build_script_lines(text, source_hash)
            if status == 'ready'
            else ()
        )
    content = {
        'schema_version': SCRIPT_SNAPSHOT_SCHEMA_VERSION,
        'source_kind': source_kind,
        'source_name': safe_source_name,
        'source_hash': source_hash,
        'extraction_status': status,
        'extraction_warning': warning,
        'lines': [line.model_dump(mode='json') for line in lines],
    }
    return ScriptSnapshot(**content, snapshot_hash=canonical_sha256(content))


def build_correction_candidates(
    *,
    run_id: str,
    raw_snapshot: RawAsrSnapshot,
    semantic_artifact: SemanticUtteranceArtifact,
    script_snapshot: ScriptSnapshot,
    runtime_lexicon_bundle: Mapping[str, Any] | None = None,
    profile: CorrectionThresholdProfile = DEFAULT_CORRECTION_THRESHOLD_PROFILE,
    script_alignments: Sequence[ExactScriptLineAlignment] | None = None,
    rejected_corrections: list[dict[str, Any]] | None = None,
) -> tuple[CorrectionCandidate, ...]:
    if not str(run_id or '').strip():
        raise ValueError('run_id must not be empty')
    _validate_inputs(raw_snapshot, semantic_artifact)
    token_by_id = {token.token_id: token for token in raw_snapshot.tokens}
    token_order = {token.token_id: index for index, token in enumerate(raw_snapshot.tokens)}
    display_candidates = _build_script_display_fidelity_candidates(
        run_id=run_id,
        raw_snapshot=raw_snapshot,
        semantic_artifact=semantic_artifact,
        script_snapshot=script_snapshot,
        script_alignments=script_alignments,
    )
    reserved_token_ids = {
        token_id
        for candidate in display_candidates
        for token_id in candidate.token_ids
    }
    lexicon_terms = _runtime_lexicon_terms(runtime_lexicon_bundle)
    lexicon_evidence = {}
    for term, (_term_id, _protection, display_text) in lexicon_terms.items():
        evidence_lines = tuple(
            line for line in script_snapshot.lines if term in line.match_text
        )
        if not evidence_lines:
            canonical_match_text = normalize_match_text(display_text)
            evidence_lines = tuple(
                line
                for line in script_snapshot.lines
                if canonical_match_text in line.match_text
            )
        lexicon_evidence[term] = evidence_lines
    lexicon_phonetic_index, lexicon_latin_length_index = _index_lexicon_terms(
        lexicon_evidence,
    )
    script_cursor = 0
    accepted: list[CorrectionCandidate] = list(display_candidates)

    for utterance in semantic_artifact.utterances:
        utterance_tokens = [token_by_id[token_id] for token_id in utterance.token_ids]
        window_start = max(0, script_cursor - profile.script_line_window)
        window_end = min(
            len(script_snapshot.lines),
            script_cursor + profile.script_line_window + 1,
        )
        lines = tuple(
            line
            for line in script_snapshot.lines[window_start:window_end]
            if line.order_index >= script_cursor
        )
        current_lexicon_evidence = {
            term: next(
                (line for line in evidence_lines if line.order_index >= script_cursor),
                evidence_lines[-1] if evidence_lines else None,
            )
            for term, evidence_lines in lexicon_evidence.items()
        }
        options = _utterance_options(
            utterance,
            utterance_tokens,
            lines,
            lexicon_terms,
            {
                term: line
                for term, line in current_lexicon_evidence.items()
                if line is not None
            },
            lexicon_phonetic_index,
            lexicon_latin_length_index,
            profile,
        )
        options = [
            item
            for item in options
            if not reserved_token_ids.intersection(item[0].token_ids)
        ]
        selected = _select_non_overlapping(options)
        for proposal, margin in sorted(selected, key=lambda item: item[0].start_index):
            if proposal.line.order_index < script_cursor and proposal.lexicon_term_id is None:
                continue
            accepted.append(
                _to_candidate(
                    proposal,
                    margin=margin,
                    run_id=run_id,
                    raw_snapshot=raw_snapshot,
                    script_snapshot=script_snapshot,
                )
            )
            script_cursor = max(script_cursor, proposal.line.order_index)

        if selected:
            continue
        aligned_line = _best_exact_context_line(utterance_tokens, lines)
        if aligned_line is None:
            aligned_line = _best_exact_context_line(
                utterance_tokens,
                script_snapshot.lines[script_cursor:],
            )
        if aligned_line is not None:
            script_cursor = aligned_line.order_index

    occupied_token_ids = {
        token_id for candidate in accepted for token_id in candidate.token_ids
    }
    for utterance in semantic_artifact.utterances:
        utterance_tokens = [token_by_id[token_id] for token_id in utterance.token_ids]
        for proposal in build_canonical_correction_proposals(
            utterance,
            utterance_tokens,
            runtime_lexicon_bundle,
        ):
            if occupied_token_ids.intersection(proposal.token_ids):
                continue
            candidate = _canonical_correction_candidate(
                proposal,
                run_id=run_id,
                raw_snapshot=raw_snapshot,
            )
            accepted.append(candidate)
            occupied_token_ids.update(candidate.token_ids)

    for utterance in semantic_artifact.utterances:
        utterance_tokens = [token_by_id[token_id] for token_id in utterance.token_ids]
        for proposal in build_learned_correction_proposals(
            utterance,
            utterance_tokens,
            runtime_lexicon_bundle,
        ):
            if occupied_token_ids.intersection(proposal.token_ids):
                continue
            candidate = _learned_correction_candidate(
                proposal,
                run_id=run_id,
                raw_snapshot=raw_snapshot,
            )
            accepted.append(candidate)
            occupied_token_ids.update(candidate.token_ids)

    for utterance in semantic_artifact.utterances:
        utterance_tokens = [token_by_id[token_id] for token_id in utterance.token_ids]
        for proposal in build_grammar_particle_proposals(utterance, utterance_tokens):
            if occupied_token_ids.intersection(proposal.token_ids):
                continue
            candidate = _grammar_candidate(
                proposal,
                run_id=run_id,
                raw_snapshot=raw_snapshot,
            )
            accepted.append(candidate)
            occupied_token_ids.update(candidate.token_ids)

    safe_candidates = []
    for candidate in accepted:
        if numeric_fact_signature(candidate.source_text) != numeric_fact_signature(candidate.replacement_text):
            if rejected_corrections is not None:
                rejected_corrections.append({
                    'candidate_id': candidate.candidate_id,
                    'token_ids': list(candidate.token_ids),
                    'source_text': candidate.source_text,
                    'replacement_text': candidate.replacement_text,
                    'start_ms': token_by_id[candidate.token_ids[0]].start_ms,
                    'end_ms': token_by_id[candidate.token_ids[-1]].end_ms,
                    'reason': 'numeric_fact_change',
                })
            continue
        safe_candidates.append(candidate)
    return tuple(sorted(safe_candidates, key=lambda item: token_order[item.token_ids[0]]))


def _build_script_display_fidelity_candidates(
    *,
    run_id: str,
    raw_snapshot: RawAsrSnapshot,
    semantic_artifact: SemanticUtteranceArtifact,
    script_snapshot: ScriptSnapshot,
    script_alignments: Sequence[ExactScriptLineAlignment] | None,
) -> tuple[CorrectionCandidate, ...]:
    token_by_id = {token.token_id: token for token in raw_snapshot.tokens}
    utterance_by_token_id = {
        token_id: utterance
        for utterance in semantic_artifact.utterances
        for token_id in utterance.token_ids
    }
    accepted: list[CorrectionCandidate] = []
    occupied_token_ids: set[str] = set()

    alignments = (
        tuple(script_alignments)
        if script_alignments is not None
        else build_exact_script_line_alignments(
            raw_snapshot=raw_snapshot,
            semantic_artifact=semantic_artifact,
            script_snapshot=script_snapshot,
        )
    )
    validate_exact_script_line_alignments(
        raw_snapshot=raw_snapshot,
        script_snapshot=script_snapshot,
        alignments=alignments,
    )
    for alignment in alignments:
        script_view = build_match_text_view(alignment.script_text)
        if script_view is None:
            continue
        for match in _LATIN_DISPLAY_TERM_RE.finditer(alignment.script_text):
            match_offsets = [
                offset
                for offset, raw_offset in enumerate(script_view.normalized_to_raw)
                if match.start() <= raw_offset < match.end()
            ]
            if not match_offsets:
                continue
            script_match_start = match_offsets[0]
            script_match_end = match_offsets[-1] + 1
            touching_slices = tuple(
                token_slice
                for token_slice in alignment.token_slices
                if (
                    token_slice.script_match_end > script_match_start
                    and token_slice.script_match_start < script_match_end
                )
            )
            if not touching_slices or any(
                right.token_index != left.token_index + 1
                for left, right in zip(touching_slices, touching_slices[1:])
            ):
                continue

            token_ids = tuple(
                raw_snapshot.tokens[token_slice.token_index].token_id
                for token_slice in touching_slices
            )
            if occupied_token_ids.intersection(token_ids):
                continue
            utterances = {
                utterance_by_token_id[token_id]
                for token_id in token_ids
            }
            if len(utterances) != 1:
                continue
            utterance = utterances.pop()
            source_text = ''.join(token_by_id[token_id].text for token_id in token_ids)
            replacement_text = display_text_for_match_range(
                alignment,
                touching_slices[0].script_match_start,
                touching_slices[-1].script_match_end,
            )
            evidence_refs = (SCRIPT_DISPLAY_FIDELITY_EVIDENCE_REF,)
            if not is_script_display_fidelity_change(
                source_text=source_text,
                replacement_text=replacement_text,
                evidence_refs=evidence_refs,
            ):
                continue

            identity = {
                'run_id': run_id,
                'raw_snapshot_hash': raw_snapshot.snapshot_hash,
                'token_ids': token_ids,
                'script_line_id': alignment.script_line_id,
                'replacement_text': replacement_text,
                'evidence_ref': SCRIPT_DISPLAY_FIDELITY_EVIDENCE_REF,
            }
            accepted.append(
                CorrectionCandidate(
                    candidate_id=f'correction-{canonical_sha256(identity)}',
                    run_id=run_id,
                    utterance_id=utterance.utterance_id,
                    token_ids=token_ids,
                    source_text=source_text,
                    replacement_text=replacement_text,
                    script_line_id=alignment.script_line_id,
                    script_line_order=alignment.script_line_order,
                    script_source_hash=alignment.script_source_hash,
                    phonetic_score=1.0,
                    context_score=1.0,
                    ranking_score=1.0,
                    margin=1.0,
                    candidate_type='script',
                    evidence_refs=evidence_refs,
                )
            )
            occupied_token_ids.update(token_ids)

    return tuple(accepted)

def _normalize_source_section(section: Mapping[str, Any]) -> dict[str, Any]:
    source_name_value = str(section.get('source_name') or '').strip()
    safe_source_name = (
        Path(source_name_value.replace('\\', '/')).name
        if source_name_value
        else None
    )
    reference_lines = tuple(
        str(line or '').strip()
        for line in (section.get('reference_lines') or [])
        if str(line or '').strip()
    )
    source_hash_value = str(section.get('source_hash') or '').strip()
    source_hash = (
        source_hash_value
        if re.fullmatch(_LOWER_HEX_SHA256, source_hash_value)
        else canonical_sha256({
            'source_name': safe_source_name,
            'reference_lines': reference_lines,
        })
    )
    provided_physical_line_ends = tuple(
        bool(value)
        for value in (section.get('physical_line_ends') or ())
    )
    physical_line_ends = tuple(
        provided_physical_line_ends[index]
        if index < len(provided_physical_line_ends)
        else False
        for index in range(len(reference_lines))
    )
    return {
        'source_name': safe_source_name,
        'source_hash': source_hash,
        'reference_lines': reference_lines,
        'physical_line_ends': physical_line_ends,
    }


def _build_script_lines(
    text: str,
    source_hash: str,
    *,
    source_order: int = 0,
    order_index_start: int = 0,
    physical_line_ends: Sequence[bool] | None = None,
) -> tuple[ScriptLine, ...]:
    lines: list[ScriptLine] = []
    paragraph_index = 0
    paragraph_break = False
    for line_number, raw_line in enumerate(str(text or '').splitlines(), start=1):
        cleaned = re.sub(r'[ \t]+', ' ', raw_line).strip()
        if not cleaned:
            if lines:
                paragraph_break = True
            continue
        if paragraph_break:
            paragraph_index += 1
            paragraph_break = False
        pieces = _split_script_line(cleaned)
        physical_line_has_boundary = (
            bool(physical_line_ends[line_number - 1])
            if physical_line_ends is not None
            and line_number - 1 < len(physical_line_ends)
            else physical_line_ends is None
        )
        for fragment_index, piece in enumerate(pieces):
            match_text = normalize_match_text(piece)
            if not match_text:
                continue
            order_index = order_index_start + len(lines)
            source_line_order = len(lines)
            identity = {
                'source_hash': source_hash,
                'line_number': line_number,
                'fragment_index': fragment_index,
                'source_order': source_order,
                'source_line_order': source_line_order,
                'order_index': order_index,
                'text': piece,
            }
            lines.append(
                ScriptLine(
                    line_id=f'script-line-{canonical_sha256(identity)}',
                    line_number=line_number,
                    paragraph_index=paragraph_index,
                    order_index=order_index,
                    source_hash=source_hash,
                    source_order=source_order,
                    source_line_order=source_line_order,
                    fragment_index=fragment_index,
                    ends_physical_line=(
                        physical_line_has_boundary
                        and fragment_index == len(pieces) - 1
                    ),
                    text=piece,
                    match_text=match_text,
                )
            )
    return tuple(lines)


def _split_script_line(line: str) -> tuple[str, ...]:
    if len(line) <= REFERENCE_LINE_MAX_CHARS:
        return (line,)
    sentence_pieces = tuple(
        item.strip()
        for item in re.split(r'(?<=[。！？!?；;])', line)
        if item.strip()
    ) or (line,)
    return tuple(
        piece[index:index + REFERENCE_LINE_MAX_CHARS]
        for piece in sentence_pieces
        for index in range(0, len(piece), REFERENCE_LINE_MAX_CHARS)
    )


def _validate_inputs(
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


def _utterance_options(
    utterance: SemanticUtterance,
    tokens: Sequence[RawAsrToken],
    lines: Sequence[ScriptLine],
    lexicon_terms: Mapping[str, tuple[str | None, str | None, str]],
    lexicon_evidence: Mapping[str, ScriptLine],
    lexicon_phonetic_index: Mapping[tuple[str, ...], tuple[str, ...]],
    lexicon_latin_length_index: Mapping[int, tuple[str, ...]],
    profile: CorrectionThresholdProfile,
) -> list[tuple[_Proposal, float]]:
    options: list[tuple[_Proposal, float]] = []
    for start_index in range(len(tokens)):
        max_end = min(len(tokens), start_index + profile.max_token_span)
        for end_index in range(start_index + 1, max_end + 1):
            source_text = _join_token_text(tokens[start_index:end_index])
            if len(source_text) < 2:
                continue
            proposals = _span_proposals(
                utterance=utterance,
                tokens=tokens,
                start_index=start_index,
                end_index=end_index,
                source_text=source_text,
                lines=lines,
                lexicon_terms=lexicon_terms,
                lexicon_evidence=lexicon_evidence,
                lexicon_phonetic_index=lexicon_phonetic_index,
                lexicon_latin_length_index=lexicon_latin_length_index,
                profile=profile,
            )
            if not proposals:
                continue
            by_replacement: dict[str, _Proposal] = {}
            for proposal in proposals:
                existing = by_replacement.get(proposal.replacement_text)
                if existing is None or _proposal_sort_key(proposal) < _proposal_sort_key(existing):
                    by_replacement[proposal.replacement_text] = proposal
            ranked = sorted(by_replacement.values(), key=_proposal_sort_key)
            margin = 1.0 if len(ranked) == 1 else max(
                ranked[0].ranking_score - ranked[1].ranking_score,
                0.0,
            )
            if margin + 1e-12 < profile.margin_threshold:
                continue
            options.append((ranked[0], round(min(margin, 1.0), 6)))
    return options


def _span_proposals(
    *,
    utterance: SemanticUtterance,
    tokens: Sequence[RawAsrToken],
    start_index: int,
    end_index: int,
    source_text: str,
    lines: Sequence[ScriptLine],
    lexicon_terms: Mapping[str, tuple[str | None, str | None, str]],
    lexicon_evidence: Mapping[str, ScriptLine],
    lexicon_phonetic_index: Mapping[tuple[str, ...], tuple[str, ...]],
    lexicon_latin_length_index: Mapping[int, tuple[str, ...]],
    profile: CorrectionThresholdProfile,
) -> list[_Proposal]:
    proposals = _lexicon_span_proposals(
        utterance=utterance,
        start_index=start_index,
        end_index=end_index,
        source_text=source_text,
        token_ids=tuple(token.token_id for token in tokens[start_index:end_index]),
        lexicon_terms=lexicon_terms,
        lexicon_evidence=lexicon_evidence,
        lexicon_phonetic_index=lexicon_phonetic_index,
        lexicon_latin_length_index=lexicon_latin_length_index,
    )
    token_ids = tuple(token.token_id for token in tokens[start_index:end_index])
    source_length = len(source_text)
    left_source = _join_token_text(
        tokens[max(0, start_index - profile.context_token_window):start_index]
    )
    right_source = _join_token_text(
        tokens[end_index:end_index + profile.context_token_window]
    )
    if not left_source or not right_source:
        return proposals
    for line in lines:
        replacement_ranges = _anchored_replacement_ranges(
            line.match_text,
            source_length=source_length,
            left_source=left_source,
            right_source=right_source,
        )
        for replacement_start, replacement_end in replacement_ranges:
            replacement = line.match_text[replacement_start:replacement_end]
            if replacement == source_text:
                continue
            if not _locally_supported_change(source_text, replacement):
                continue
            context_score = _context_similarity(
                tokens,
                start_index,
                end_index,
                line.match_text,
                replacement_start,
                replacement_end,
                profile.context_token_window,
            )
            if context_score + 1e-12 < profile.context_threshold:
                continue
            phonetic_score = _phonetic_similarity(source_text, replacement)
            if phonetic_score + 1e-12 < profile.phonetic_threshold:
                continue
            lexicon_term_id, protection_level, display_text = lexicon_terms.get(
                replacement,
                (None, None, replacement),
            )
            boost = _LEXICON_BOOST.get(protection_level or '', 0.0)
            ranking_score = min(
                1.0,
                0.55 * phonetic_score + 0.45 * context_score + boost,
            )
            proposals.append(
                _Proposal(
                    utterance=utterance,
                    start_index=start_index,
                    end_index=end_index,
                    token_ids=token_ids,
                    source_text=source_text,
                    replacement_text=display_text,
                    line=line,
                    phonetic_score=round(phonetic_score, 6),
                    context_score=round(context_score, 6),
                    ranking_score=round(ranking_score, 6),
                    lexicon_term_id=lexicon_term_id,
                    lexicon_protection_level=protection_level,
                )
            )
    return proposals


def _lexicon_span_proposals(
    *,
    utterance: SemanticUtterance,
    start_index: int,
    end_index: int,
    source_text: str,
    token_ids: tuple[str, ...],
    lexicon_terms: Mapping[str, tuple[str | None, str | None, str]],
    lexicon_evidence: Mapping[str, ScriptLine],
    lexicon_phonetic_index: Mapping[tuple[str, ...], tuple[str, ...]],
    lexicon_latin_length_index: Mapping[int, tuple[str, ...]],
) -> list[_Proposal]:
    proposals: list[_Proposal] = []
    candidate_terms = list(lexicon_phonetic_index.get(_pinyin_syllables(source_text), ()))
    if _LATIN_EDIT_RE.fullmatch(source_text):
        for term_length in range(max(1, len(source_text) - 2), len(source_text) + 3):
            candidate_terms.extend(lexicon_latin_length_index.get(term_length, ()))
    for replacement in dict.fromkeys(candidate_terms):
        line = lexicon_evidence.get(replacement)
        if line is None:
            continue
        term_id, protection_level, display_text = lexicon_terms[replacement]
        normalized_score = _normalized_similarity(source_text, replacement)
        phonetic_score = _phonetic_similarity(source_text, replacement)
        canonical_alias_expansion = (
            replacement == source_text
            and normalize_match_text(display_text) != source_text
        )
        roster_person = bool(_PERSON_ROSTER_LINE_RE.search(line.match_text))
        ranked_cjk_match = (
            roster_person
            and len(source_text) >= 3
            and normalized_score + 1e-12 >= 1 / 3
            and phonetic_score + 1e-12 >= 0.98
        )
        ranked_latin_match = (
            roster_person
            and _LATIN_EDIT_RE.fullmatch(source_text)
            and _LATIN_EDIT_RE.fullmatch(replacement)
            and abs(len(source_text) - len(replacement)) <= 2
            and normalized_score + 1e-12 >= 0.75
        )
        if (
            (replacement == source_text and not canonical_alias_expansion)
            or (len(replacement) != len(source_text) and not ranked_latin_match)
            or (
                normalized_score + 1e-12 < 0.75
                and (
                    len(source_text) < 3
                    or sum(left != right for left, right in zip(source_text, replacement)) != 1
                )
                and not ranked_cjk_match
            )
            or (phonetic_score + 1e-12 < 0.98 and not ranked_latin_match)
            or (
                not canonical_alias_expansion
                and not _locally_supported_change(source_text, replacement)
            )
        ):
            continue
        ranking_score = min(
            1.0,
            0.55 * phonetic_score
            + 0.45 * normalized_score
            + _LEXICON_BOOST.get(protection_level or '', 0.0),
        )
        proposals.append(
            _Proposal(
                utterance=utterance,
                start_index=start_index,
                end_index=end_index,
                token_ids=token_ids,
                source_text=source_text,
                replacement_text=display_text,
                line=line,
                phonetic_score=round(phonetic_score, 6),
                context_score=round(normalized_score, 6),
                ranking_score=round(ranking_score, 6),
                lexicon_term_id=term_id,
                lexicon_protection_level=protection_level,
            )
        )
    return proposals


def _index_lexicon_terms(
    lexicon_evidence: Mapping[str, Sequence[ScriptLine]],
) -> tuple[
    dict[tuple[str, ...], tuple[str, ...]],
    dict[int, tuple[str, ...]],
]:
    phonetic_terms: dict[tuple[str, ...], list[str]] = {}
    latin_terms: dict[int, list[str]] = {}
    for term, evidence_lines in lexicon_evidence.items():
        if not evidence_lines:
            continue
        phonetic_terms.setdefault(_pinyin_syllables(term), []).append(term)
        if _LATIN_EDIT_RE.fullmatch(term):
            latin_terms.setdefault(len(term), []).append(term)
    return (
        {key: tuple(sorted(values)) for key, values in phonetic_terms.items()},
        {key: tuple(sorted(values)) for key, values in latin_terms.items()},
    )


def _anchored_replacement_ranges(
    line_text: str,
    *,
    source_length: int,
    left_source: str,
    right_source: str,
) -> tuple[tuple[int, int], ...]:
    min_length = max(2, source_length - 2)
    max_length = source_length + 2
    left_positions = _context_anchor_positions(
        line_text,
        left_source,
        use_suffix=True,
    )
    right_positions = _context_anchor_positions(
        line_text,
        right_source,
        use_suffix=False,
    )
    if not left_positions and not right_positions:
        return ()

    paired = {
        (replacement_start, replacement_end)
        for replacement_start in left_positions
        for replacement_end in right_positions
        if min_length <= replacement_end - replacement_start <= max_length
    }
    return _bounded_replacement_ranges(paired, source_length)


def _context_anchor_positions(
    line_text: str,
    source_context: str,
    *,
    use_suffix: bool,
) -> tuple[int, ...]:
    max_length = min(_CONTEXT_ANCHOR_MAX_CHARS, len(source_context))
    for anchor_length in range(max_length, _CONTEXT_ANCHOR_MIN_CHARS - 1, -1):
        anchor = (
            source_context[-anchor_length:]
            if use_suffix
            else source_context[:anchor_length]
        )
        positions: list[int] = []
        search_from = 0
        while True:
            position = line_text.find(anchor, search_from)
            if position < 0:
                break
            positions.append(position + anchor_length if use_suffix else position)
            search_from = position + 1
        if positions:
            return tuple(positions)
    return ()


def _bounded_replacement_ranges(
    ranges: set[tuple[int, int]],
    source_length: int,
) -> tuple[tuple[int, int], ...]:
    if len(ranges) > _MAX_ANCHORED_RANGES_PER_LINE:
        return ()
    return tuple(sorted(
        ranges,
        key=lambda item: (
            abs((item[1] - item[0]) - source_length),
            item[0],
            item[1],
        ),
    ))


def _select_non_overlapping(
    options: Sequence[tuple[_Proposal, float]],
) -> list[tuple[_Proposal, float]]:
    selected: list[tuple[_Proposal, float]] = []
    occupied: set[int] = set()
    for proposal, margin in sorted(
        options,
        key=lambda item: (
            0 if item[0].lexicon_term_id is not None else 1,
            (
                -item[0].ranking_score
                if item[0].lexicon_term_id is not None
                else item[0].end_index - item[0].start_index
            ),
            (
                -(item[0].end_index - item[0].start_index)
                if item[0].lexicon_term_id is not None
                else -item[0].ranking_score
            ),
            -item[1],
            item[0].start_index,
            item[0].line.order_index,
            item[0].replacement_text,
        ),
    ):
        indexes = set(range(proposal.start_index, proposal.end_index))
        if indexes & occupied:
            continue
        occupied.update(indexes)
        selected.append((proposal, margin))
    return selected


def _to_candidate(
    proposal: _Proposal,
    *,
    margin: float,
    run_id: str,
    raw_snapshot: RawAsrSnapshot,
    script_snapshot: ScriptSnapshot,
) -> CorrectionCandidate:
    identity = {
        'run_id': run_id,
        'raw_snapshot_hash': raw_snapshot.snapshot_hash,
        'script_source_hash': proposal.line.source_hash,
        'utterance_id': proposal.utterance.utterance_id,
        'token_ids': proposal.token_ids,
        'script_line_id': proposal.line.line_id,
        'replacement_text': proposal.replacement_text,
    }
    return CorrectionCandidate(
        candidate_id=f'correction-{canonical_sha256(identity)}',
        run_id=run_id,
        utterance_id=proposal.utterance.utterance_id,
        token_ids=proposal.token_ids,
        source_text=proposal.source_text,
        replacement_text=proposal.replacement_text,
        script_line_id=proposal.line.line_id,
        script_line_order=proposal.line.order_index,
        script_source_hash=proposal.line.source_hash,
        phonetic_score=proposal.phonetic_score,
        context_score=proposal.context_score,
        ranking_score=proposal.ranking_score,
        margin=margin,
        lexicon_term_id=proposal.lexicon_term_id,
        lexicon_protection_level=proposal.lexicon_protection_level,
        candidate_type='lexicon' if proposal.lexicon_term_id else 'script',
    )


def _grammar_candidate(
    proposal: GrammarParticleProposal,
    *,
    run_id: str,
    raw_snapshot: RawAsrSnapshot,
) -> CorrectionCandidate:
    evidence_hash = canonical_sha256({
        'profile_version': GRAMMAR_PARTICLE_PROFILE_VERSION,
        'rule_id': proposal.rule_id,
    })
    identity = {
        'run_id': run_id,
        'raw_snapshot_hash': raw_snapshot.snapshot_hash,
        'utterance_id': proposal.utterance_id,
        'token_ids': proposal.token_ids,
        'replacement_text': proposal.replacement_text,
        'rule_id': proposal.rule_id,
        'profile_version': GRAMMAR_PARTICLE_PROFILE_VERSION,
    }
    return CorrectionCandidate(
        candidate_id=f'correction-{canonical_sha256(identity)}',
        run_id=run_id,
        utterance_id=proposal.utterance_id,
        token_ids=proposal.token_ids,
        source_text=proposal.source_text,
        replacement_text=proposal.replacement_text,
        script_line_id=f'grammar-rule-{proposal.rule_id}',
        script_line_order=0,
        script_source_hash=evidence_hash,
        phonetic_score=1.0,
        context_score=proposal.confidence,
        ranking_score=proposal.confidence,
        margin=proposal.confidence,
        candidate_type='grammar',
        evidence_refs=(
            f'grammar-profile:{GRAMMAR_PARTICLE_PROFILE_VERSION}',
            f'grammar-rule:{proposal.rule_id}',
        ),
    )


def _learned_correction_candidate(
    proposal: LearnedCorrectionProposal,
    *,
    run_id: str,
    raw_snapshot: RawAsrSnapshot,
) -> CorrectionCandidate:
    evidence_hash = canonical_sha256({
        'term_id': proposal.term_id,
        'scope_type': proposal.scope_type,
        'source_text': proposal.source_text,
        'replacement_text': proposal.replacement_text,
        'evidence_count': proposal.evidence_count,
    })
    identity = {
        'run_id': run_id,
        'raw_snapshot_hash': raw_snapshot.snapshot_hash,
        'utterance_id': proposal.utterance_id,
        'token_ids': proposal.token_ids,
        'term_id': proposal.term_id,
        'replacement_text': proposal.replacement_text,
        'evidence_hash': evidence_hash,
    }
    return CorrectionCandidate(
        candidate_id=f'correction-{canonical_sha256(identity)}',
        run_id=run_id,
        utterance_id=proposal.utterance_id,
        token_ids=proposal.token_ids,
        source_text=proposal.source_text,
        replacement_text=proposal.replacement_text,
        script_line_id=f'knowledge-term-{proposal.term_id}',
        script_line_order=0,
        script_source_hash=evidence_hash,
        phonetic_score=1.0,
        context_score=proposal.confidence,
        ranking_score=proposal.confidence,
        margin=proposal.confidence,
        lexicon_term_id=proposal.term_id,
        lexicon_protection_level=(
            'strong' if proposal.scope_type == 'project' else 'soft'
        ),
        candidate_type='lexicon',
        evidence_refs=(
            f'knowledge-scope:{proposal.scope_type}',
            f'knowledge-evidence-count:{proposal.evidence_count}',
            f'knowledge-project-count:{proposal.project_count}',
        ),
    )


def _canonical_correction_candidate(
    proposal: CanonicalCorrectionProposal,
    *,
    run_id: str,
    raw_snapshot: RawAsrSnapshot,
) -> CorrectionCandidate:
    evidence_hash = (
        proposal.content_hash
        if re.fullmatch(_LOWER_HEX_SHA256, proposal.content_hash)
        else canonical_sha256({'passage_id': proposal.passage_id})
    )
    identity = {
        'run_id': run_id,
        'raw_snapshot_hash': raw_snapshot.snapshot_hash,
        'utterance_id': proposal.utterance_id,
        'token_ids': proposal.token_ids,
        'passage_id': proposal.passage_id,
        'line_index': proposal.line_index,
        'replacement_text': proposal.replacement_text,
        'content_hash': evidence_hash,
    }
    return CorrectionCandidate(
        candidate_id=f'correction-{canonical_sha256(identity)}',
        run_id=run_id,
        utterance_id=proposal.utterance_id,
        token_ids=proposal.token_ids,
        source_text=proposal.source_text,
        replacement_text=proposal.replacement_text,
        script_line_id=(
            f'canonical-passage-{proposal.passage_id}-line-{proposal.line_index}'
        ),
        script_line_order=proposal.line_index,
        script_source_hash=evidence_hash,
        phonetic_score=1.0,
        context_score=proposal.confidence,
        ranking_score=proposal.confidence,
        margin=proposal.margin,
        candidate_type='canonical_passage',
        evidence_refs=(
            f'canonical-passage:{proposal.passage_id}',
            f'canonical-title:{proposal.passage_title}',
            f'canonical-source-priority:{proposal.source_priority}',
        ),
    )


def _best_exact_context_line(
    tokens: Sequence[RawAsrToken],
    lines: Sequence[ScriptLine],
) -> ScriptLine | None:
    utterance_text = _join_token_text(tokens)
    if not utterance_text:
        return None
    matches: list[tuple[int, ScriptLine]] = []
    for line in lines:
        matched_chars = (
            len(utterance_text)
            if utterance_text in line.match_text
            else len(line.match_text)
            if line.match_text in utterance_text
            else 0
        )
        if matched_chars >= _EXACT_REACQUIRE_MIN_CHARS:
            matches.append((matched_chars, line))
    if not matches:
        return None
    return min(
        matches,
        key=lambda item: (-item[0], item[1].order_index),
    )[1]


def _runtime_lexicon_terms(
    bundle: Mapping[str, Any] | None,
) -> dict[str, tuple[str | None, str | None, str]]:
    terms: dict[str, tuple[str | None, str | None, str]] = {}
    if not bundle:
        return terms
    for item in bundle.get('terms') or []:
        if not isinstance(item, Mapping):
            continue
        raw_display_text = str(item.get('display_text') or item.get('normalized_term') or '').strip()
        if not raw_display_text:
            continue
        protection_level = str(item.get('protection_level') or '').lower()
        if protection_level not in _LEXICON_BOOST:
            continue
        term_value = (
            str(item.get('term_id')) if item.get('term_id') is not None else None,
            protection_level,
            normalize_to_simplified_chinese(raw_display_text),
        )
        match_values = [
            item.get('normalized_term'),
            raw_display_text,
            *(item.get('aliases') or []),
        ]
        normalized_match_values: list[str] = []
        for raw_match_value in match_values:
            match_text = normalize_match_text(str(raw_match_value or ''))
            if not match_text:
                continue
            normalized_match_values.append(match_text)
            existing = terms.get(match_text)
            if (
                existing is None
                or _LEXICON_BOOST[protection_level]
                > _LEXICON_BOOST.get(existing[1] or '', 0.0)
            ):
                terms[match_text] = term_value
        latin_aliases = tuple(dict.fromkeys(
            value
            for value in normalized_match_values
            if len(value) >= 2 and _LATIN_EDIT_RE.fullmatch(value)
        ))
        cjk_aliases = tuple(dict.fromkeys(
            value
            for value in normalized_match_values
            if len(value) >= 2 and _CJK_TERM_RE.fullmatch(value)
        ))
        for latin_alias in latin_aliases:
            for cjk_alias in cjk_aliases:
                for combined_alias in (
                    latin_alias + cjk_alias,
                    cjk_alias + latin_alias,
                ):
                    existing = terms.get(combined_alias)
                    if (
                        existing is None
                        or _LEXICON_BOOST[protection_level]
                        > _LEXICON_BOOST.get(existing[1] or '', 0.0)
                    ):
                        terms[combined_alias] = term_value
    return terms


def _context_similarity(
    tokens: Sequence[RawAsrToken],
    start_index: int,
    end_index: int,
    line_text: str,
    replacement_start: int,
    replacement_end: int,
    context_window: int,
) -> float:
    left_source = _join_token_text(tokens[max(0, start_index - context_window):start_index])
    right_source = _join_token_text(tokens[end_index:end_index + context_window])
    left_script = line_text[:replacement_start][-len(left_source):] if left_source else ''
    right_script = (
        line_text[replacement_end:replacement_end + len(right_source)]
        if right_source
        else ''
    )
    return (
        _normalized_similarity(left_source, left_script)
        + _normalized_similarity(right_source, right_script)
    ) / 2.0


def _phonetic_similarity(left: str, right: str) -> float:
    left_syllables = _pinyin_syllables(left)
    right_syllables = _pinyin_syllables(right)
    if not left_syllables or not right_syllables:
        return 0.0
    rows = len(left_syllables) + 1
    columns = len(right_syllables) + 1
    distances = [[0.0] * columns for _ in range(rows)]
    for row in range(rows):
        distances[row][0] = float(row)
    for column in range(columns):
        distances[0][column] = float(column)
    for row in range(1, rows):
        for column in range(1, columns):
            substitution = 1.0 - _normalized_similarity(
                left_syllables[row - 1],
                right_syllables[column - 1],
            )
            distances[row][column] = min(
                distances[row - 1][column] + 1.0,
                distances[row][column - 1] + 1.0,
                distances[row - 1][column - 1] + substitution,
            )
    return max(
        0.0,
        1.0 - distances[-1][-1] / max(len(left_syllables), len(right_syllables)),
    )


def _locally_supported_change(source: str, replacement: str) -> bool:
    prefix_length = 0
    while (
        prefix_length < len(source)
        and prefix_length < len(replacement)
        and source[prefix_length] == replacement[prefix_length]
    ):
        prefix_length += 1

    source_tail = source[prefix_length:]
    replacement_tail = replacement[prefix_length:]
    suffix_length = 0
    while (
        suffix_length < len(source_tail)
        and suffix_length < len(replacement_tail)
        and source_tail[-suffix_length - 1] == replacement_tail[-suffix_length - 1]
    ):
        suffix_length += 1

    source_change = (
        source_tail[:-suffix_length] if suffix_length else source_tail
    )
    replacement_change = (
        replacement_tail[:-suffix_length] if suffix_length else replacement_tail
    )
    if _LATIN_EDIT_RE.fullmatch(source_change) and _LATIN_EDIT_RE.fullmatch(
        replacement_change
    ) and (source_change or replacement_change):
        return _normalized_similarity(source, replacement) >= 0.75
    if not source_change or not replacement_change:
        return False
    return (
        _phonetic_similarity(source_change, replacement_change)
        + 1e-12
        >= _LOCAL_PHONETIC_THRESHOLD
    )


@lru_cache(maxsize=32_768)
def _pinyin_syllables(value: str) -> tuple[str, ...]:
    return tuple(
        str(item).lower()
        for item in lazy_pinyin(
            value,
            style=Style.NORMAL,
            errors=lambda chars: list(chars),
        )
        if str(item)
    )


def _normalized_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_char != right_char),
            ))
        previous = current
    return 1.0 - previous[-1] / max(len(left), len(right))


def _proposal_sort_key(proposal: _Proposal) -> tuple[Any, ...]:
    return (
        -proposal.ranking_score,
        -proposal.phonetic_score,
        -proposal.context_score,
        proposal.line.order_index,
        proposal.replacement_text,
    )


def _join_token_text(tokens: Sequence[RawAsrToken]) -> str:
    return normalize_match_text(''.join(token.text for token in tokens))


def _ascii_case_space_key(value: str) -> str:
    return ''.join(
        chr(ord(char) + 32) if 'A' <= char <= 'Z' else char
        for char in str(value or '')
        if not char.isspace()
    )
