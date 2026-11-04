from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.script_line_alignment import (
    ExactScriptLineAlignment,
    build_match_text_view,
    normalize_match_text,
)
from app.services.subtitle_legal_gaps import SubtitleLegalGap
from app.services.subtitle_pipeline_v2_schema import RawAsrSnapshot, canonical_sha256


ScriptBoundaryStrength = Literal['strong', 'weak']
_STRONG_PUNCTUATION = frozenset('。！？!?；;')
_WEAK_PUNCTUATION = frozenset('，,、：:')


class ScriptBoundaryHint(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    hint_id: str = Field(min_length=1)
    raw_snapshot_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    gap_id: str = Field(min_length=1)
    left_token_id: str = Field(min_length=1)
    right_token_id: str = Field(min_length=1)
    left_token_index: int = Field(ge=0)
    right_token_index: int = Field(gt=0)
    strength: ScriptBoundaryStrength
    script_line_id: str = Field(min_length=1)
    script_source_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def validate_evidence_refs(self) -> 'ScriptBoundaryHint':
        if tuple(sorted(set(self.evidence_refs))) != self.evidence_refs:
            raise ValueError('script boundary hint evidence refs must be unique and sorted')
        required_refs = {
            f'script-boundary:{self.strength}',
            self.script_line_id,
            f'script-source:{self.script_source_hash}',
        }
        if not required_refs.issubset(self.evidence_refs):
            raise ValueError(
                'script boundary hint evidence refs omit required script evidence'
            )
        return self


@dataclass(frozen=True)
class ScriptBoundaryHintBuildResult:
    hints: tuple[ScriptBoundaryHint, ...]
    candidate_count: int
    rejected_illegal_gap_count: int


@dataclass(frozen=True)
class _BoundaryCandidate:
    right_token_index: int
    strength: ScriptBoundaryStrength
    alignment: ExactScriptLineAlignment


def analyze_script_boundary_hints(
    *,
    raw_snapshot: RawAsrSnapshot,
    gaps: Sequence[SubtitleLegalGap],
    alignments: Sequence[ExactScriptLineAlignment],
) -> ScriptBoundaryHintBuildResult:
    _validate_alignments(raw_snapshot, alignments)
    gap_by_right_index = {gap.right_token_index: gap for gap in gaps}
    next_alignment_by_line_id = _next_contiguous_alignment_by_line_id(
        alignments,
    )
    candidates = tuple(
        candidate
        for alignment in alignments
        for candidate in _alignment_candidates(
            alignment,
            next_alignment=next_alignment_by_line_id.get(
                alignment.script_line_id,
            ),
        )
    )
    selected_by_gap_id: dict[str, tuple[_BoundaryCandidate, SubtitleLegalGap]] = {}
    rejected_illegal_gap_count = 0

    for candidate in candidates:
        gap = gap_by_right_index.get(candidate.right_token_index)
        if not _is_safe_target(raw_snapshot, gap):
            rejected_illegal_gap_count += 1
            continue
        assert gap is not None
        current = selected_by_gap_id.get(gap.gap_id)
        if current is None or _candidate_rank(candidate) < _candidate_rank(current[0]):
            selected_by_gap_id[gap.gap_id] = (candidate, gap)

    hints = tuple(
        _build_hint(raw_snapshot, candidate, gap)
        for candidate, gap in sorted(
            selected_by_gap_id.values(),
            key=lambda item: (
                item[1].right_token_index,
                _candidate_rank(item[0]),
                item[1].gap_id,
            ),
        )
    )
    return ScriptBoundaryHintBuildResult(
        hints=hints,
        candidate_count=len(candidates),
        rejected_illegal_gap_count=rejected_illegal_gap_count,
    )


def build_script_boundary_hints(
    *,
    raw_snapshot: RawAsrSnapshot,
    gaps: Sequence[SubtitleLegalGap],
    alignments: Sequence[ExactScriptLineAlignment],
) -> tuple[ScriptBoundaryHint, ...]:
    return analyze_script_boundary_hints(
        raw_snapshot=raw_snapshot,
        gaps=gaps,
        alignments=alignments,
    ).hints


def _alignment_candidates(
    alignment: ExactScriptLineAlignment,
    *,
    next_alignment: ExactScriptLineAlignment | None,
) -> tuple[_BoundaryCandidate, ...]:
    candidates: list[_BoundaryCandidate] = []
    contiguous_right_token_index = _contiguous_right_token_index(
        alignment,
        next_alignment,
    )
    if (
        alignment.ends_physical_line
        and contiguous_right_token_index is not None
    ):
        candidates.append(_BoundaryCandidate(
            right_token_index=contiguous_right_token_index,
            strength='strong',
            alignment=alignment,
        ))

    script_view = build_match_text_view(alignment.script_text)
    if script_view is None:
        return ()
    for raw_index, char in enumerate(alignment.script_text):
        if char in _STRONG_PUNCTUATION:
            strength: ScriptBoundaryStrength = 'strong'
        elif char in _WEAK_PUNCTUATION:
            strength = 'weak'
        else:
            continue
        match_boundary = bisect_right(script_view.normalized_to_raw, raw_index)
        if match_boundary <= 0:
            continue
        if match_boundary == len(alignment.token_index_by_match_char):
            if contiguous_right_token_index is None:
                continue
            candidates.append(_BoundaryCandidate(
                right_token_index=contiguous_right_token_index,
                strength=strength,
                alignment=alignment,
            ))
            continue
        if match_boundary > len(alignment.token_index_by_match_char):
            continue
        left_token_index = alignment.token_index_by_match_char[match_boundary - 1]
        right_token_index = alignment.token_index_by_match_char[match_boundary]
        if right_token_index != left_token_index + 1:
            continue
        candidates.append(_BoundaryCandidate(
            right_token_index=right_token_index,
            strength=strength,
            alignment=alignment,
        ))
    return tuple({
        (candidate.right_token_index, candidate.strength): candidate
        for candidate in candidates
    }.values())


def _next_contiguous_alignment_by_line_id(
    alignments: Sequence[ExactScriptLineAlignment],
) -> dict[str, ExactScriptLineAlignment]:
    by_source: dict[tuple[int, str], list[ExactScriptLineAlignment]] = {}
    for alignment in alignments:
        by_source.setdefault(
            (
                alignment.script_source_order,
                alignment.script_source_hash,
            ),
            [],
        ).append(alignment)
    result: dict[str, ExactScriptLineAlignment] = {}
    for source_alignments in by_source.values():
        ordered = sorted(
            source_alignments,
            key=lambda item: item.script_source_line_order,
        )
        for current, following in zip(ordered, ordered[1:]):
            if (
                following.script_source_line_order
                == current.script_source_line_order + 1
            ):
                result[current.script_line_id] = following
    return result


def _contiguous_right_token_index(
    alignment: ExactScriptLineAlignment,
    next_alignment: ExactScriptLineAlignment | None,
) -> int | None:
    if (
        next_alignment is None
        or not alignment.token_index_by_match_char
        or not next_alignment.token_index_by_match_char
        or alignment.raw_match_end != next_alignment.raw_match_start
        or alignment.right_token_index_after_line is None
        or alignment.right_token_index_after_line
        != next_alignment.token_index_by_match_char[0]
        or alignment.right_token_index_after_line
        != alignment.token_index_by_match_char[-1] + 1
    ):
        return None
    return alignment.right_token_index_after_line


def _validate_alignments(
    raw_snapshot: RawAsrSnapshot,
    alignments: Sequence[ExactScriptLineAlignment],
) -> None:
    raw_match_parts: list[str] = []
    token_index_by_match_char: list[int] = []
    for token_index, token in enumerate(raw_snapshot.tokens):
        token_match_text = normalize_match_text(token.text)
        raw_match_parts.append(token_match_text)
        token_index_by_match_char.extend(
            [token_index] * len(token_match_text)
        )
    raw_match_text = ''.join(raw_match_parts)

    for alignment in alignments:
        start = alignment.raw_match_start
        end = alignment.raw_match_end
        expected_right_token_index = (
            token_index_by_match_char[end]
            if 0 <= end < len(token_index_by_match_char)
            else None
        )
        if (
            start < 0
            or end <= start
            or end > len(raw_match_text)
            or raw_match_text[start:end] != alignment.script_match_text
            or tuple(token_index_by_match_char[start:end])
            != alignment.token_index_by_match_char
            or expected_right_token_index
            != alignment.right_token_index_after_line
        ):
            raise ValueError('script alignment disagrees with the raw token tape')


def _is_safe_target(
    raw_snapshot: RawAsrSnapshot,
    gap: SubtitleLegalGap | None,
) -> bool:
    if gap is None or gap.right_token_index >= len(raw_snapshot.tokens):
        return False
    left_token = raw_snapshot.tokens[gap.left_token_index]
    right_token = raw_snapshot.tokens[gap.right_token_index]
    return (
        gap.disposition == 'allowed'
        and gap.legal_gap
        and gap.atom_protection != 'hard'
        and gap.left_token_id == left_token.token_id
        and gap.right_token_id == right_token.token_id
    )


def _candidate_rank(
    candidate: _BoundaryCandidate,
) -> tuple[int, int, str]:
    return (
        0 if candidate.strength == 'strong' else 1,
        candidate.alignment.script_line_order,
        candidate.alignment.script_line_id,
    )


def _build_hint(
    raw_snapshot: RawAsrSnapshot,
    candidate: _BoundaryCandidate,
    gap: SubtitleLegalGap,
) -> ScriptBoundaryHint:
    alignment = candidate.alignment
    evidence_refs = tuple(sorted({
        f'script-boundary:{candidate.strength}',
        alignment.script_line_id,
        f'script-source:{alignment.script_source_hash}',
    }))
    identity = {
        'raw_snapshot_hash': raw_snapshot.snapshot_hash,
        'gap_id': gap.gap_id,
        'strength': candidate.strength,
        'script_line_id': alignment.script_line_id,
        'script_source_hash': alignment.script_source_hash,
    }
    return ScriptBoundaryHint(
        hint_id=f'script-boundary-hint-{canonical_sha256(identity)}',
        raw_snapshot_hash=raw_snapshot.snapshot_hash,
        gap_id=gap.gap_id,
        left_token_id=gap.left_token_id,
        right_token_id=gap.right_token_id,
        left_token_index=gap.left_token_index,
        right_token_index=gap.right_token_index,
        strength=candidate.strength,
        script_line_id=alignment.script_line_id,
        script_source_hash=alignment.script_source_hash,
        evidence_refs=evidence_refs,
    )
