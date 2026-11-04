from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.deepseek_subtitle_contract import (
    BoundaryRating,
    DeepSeekBoundaryVote,
)
from app.services.subtitle_atoms import TokenBoundLexicalAtom
from app.services.subtitle_boundary_grammar import grammar_boundary_penalty
from app.services.subtitle_legal_gaps import SubtitleLegalGap
from app.services.subtitle_pipeline_v2_schema import (
    RawAsrSnapshot,
    RawAsrToken,
    canonical_sha256,
)
from app.services.subtitle_token_overlays import overlay_punctuation


SUBTITLE_GLOBAL_PLANNER_SCHEMA_VERSION = 'subtitle-global-plan.v1'
PLANNER_PROFILE_VERSION = 'subtitle-global-planner.v7'
PlannerMode = Literal['smart', 'strict']
PlannerLanguageMode = Literal['auto', 'cjk_character', 'english_word']
ResolvedLanguageMode = Literal['cjk_character', 'english_word']
OverflowReason = Literal[
    'unbreakable_atom',
    'no_legal_gap',
    'terminal_punctuation',
]
StartBoundaryKind = Literal['tape_start', 'planned_legal_gap', 'required_speaker']

_TERMINAL_PUNCTUATION = '。！？!?；;'
_CLAUSE_PUNCTUATION = '，,、：:'
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
_LOWER_HEX_SHA256 = r'^[0-9a-f]{64}$'
_GRAMMAR_CONTEXT_TOKENS = 8
_MIN_READABLE_CUE_DURATION_MS = 500
_UNSELECTED_AI_BOUNDARY_COST = 55
_SHORT_CUE_COSTS = {
    1: 360,
    2: 220,
    3: 80,
    4: 60,
    5: 40,
    6: 20,
}
AFFILIATION_LEAD_INS = frozenset(('来自', '来自于', '任职于', '就职于', '服务于', '属于'))


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class CuePlannerProfile(_FrozenStrictModel):
    profile_version: Literal[
        'subtitle-global-planner.v1',
        'subtitle-global-planner.v2',
        'subtitle-global-planner.v3',
        'subtitle-global-planner.v4',
        'subtitle-global-planner.v5',
        'subtitle-global-planner.v6',
        'subtitle-global-planner.v7',
    ] = PLANNER_PROFILE_VERSION
    mode: PlannerMode = 'smart'
    language_mode: PlannerLanguageMode = 'auto'
    target_display_units: int = Field(default=14, gt=0, le=30)
    soft_max_display_units: int = Field(default=16, gt=0, le=40)
    safety_max_display_units: int = Field(default=28, gt=0, le=60)
    strict_max_display_units: int | None = Field(default=None, ge=6, le=30)
    english_target_words: int = Field(default=7, gt=0, le=20)
    english_soft_max_words: int = Field(default=10, gt=0, le=30)
    english_safety_max_words: int = Field(default=18, gt=0, le=40)
    max_cue_duration_ms: int = Field(default=7000, gt=0)
    max_cjk_chars_per_second: int = Field(default=12, gt=0)
    max_english_words_per_minute: int = Field(default=240, gt=0)

    @model_validator(mode='after')
    def validate_profile(self) -> 'CuePlannerProfile':
        if self.soft_max_display_units < self.target_display_units:
            raise ValueError('smart soft maximum must not be below its target')
        if self.safety_max_display_units < self.soft_max_display_units:
            raise ValueError('smart safety maximum must not be below its soft maximum')
        if self.english_soft_max_words < self.english_target_words:
            raise ValueError('English soft maximum must not be below its target')
        if self.english_safety_max_words < self.english_soft_max_words:
            raise ValueError('English safety maximum must not be below its soft maximum')
        if self.mode == 'strict' and self.strict_max_display_units is None:
            raise ValueError('strict mode requires strict_max_display_units')
        if self.mode == 'smart' and self.strict_max_display_units is not None:
            raise ValueError('smart mode must not set strict_max_display_units')
        return self


DEFAULT_CUE_PLANNER_PROFILE = CuePlannerProfile()


class PlannedSubtitleCue(_FrozenStrictModel):
    cue_id: str = Field(min_length=1)
    token_start: int = Field(ge=0)
    token_end: int = Field(gt=0)
    token_ids: tuple[str, ...] = Field(min_length=1)
    start_gap_id: str | None = None
    start_boundary_kind: StartBoundaryKind
    speaker_id: str | None = None
    speaker_key: str | None = None
    language_mode: ResolvedLanguageMode
    display_unit_count: int = Field(ge=0)
    display_char_count: int = Field(ge=0)
    display_word_count: int = Field(ge=0)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    strict_overflow_reason: OverflowReason | None = None

    @model_validator(mode='after')
    def validate_cue(self) -> 'PlannedSubtitleCue':
        if len(self.token_ids) != self.token_end - self.token_start:
            raise ValueError('cue token IDs must cover its half-open token range')
        if len(set(self.token_ids)) != len(self.token_ids):
            raise ValueError('cue token IDs must be unique')
        if self.start_boundary_kind == 'tape_start':
            if self.start_gap_id is not None or self.token_start != 0:
                raise ValueError('tape-start cue cannot reference a preceding gap')
        elif self.start_gap_id is None:
            raise ValueError('non-initial cues require a start gap reference')
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError('cue timing must be absent as a pair')
        if self.start_ms is None:
            if self.duration_ms is not None:
                raise ValueError('untimed cue cannot claim a duration')
        elif self.end_ms < self.start_ms or self.duration_ms != self.end_ms - self.start_ms:
            raise ValueError('cue duration disagrees with its raw time span')
        expected_units = (
            self.display_word_count
            if self.language_mode == 'english_word'
            else self.display_char_count
        )
        if self.display_unit_count != expected_units:
            raise ValueError('cue display units disagree with its language mode')
        return self


class SubtitleCuePlanArtifact(_FrozenStrictModel):
    schema_version: Literal['subtitle-global-plan.v1']
    raw_snapshot_hash: str = Field(pattern=_LOWER_HEX_SHA256)
    raw_token_count: int = Field(ge=0)
    profile: CuePlannerProfile
    atoms_hash: str = Field(pattern=_LOWER_HEX_SHA256)
    gaps_hash: str = Field(pattern=_LOWER_HEX_SHA256)
    boundary_votes_hash: str = Field(pattern=_LOWER_HEX_SHA256)
    cues: tuple[PlannedSubtitleCue, ...]
    artifact_hash: str = Field(pattern=_LOWER_HEX_SHA256)

    @model_validator(mode='after')
    def validate_artifact(self) -> 'SubtitleCuePlanArtifact':
        cue_ids = [cue.cue_id for cue in self.cues]
        if len(set(cue_ids)) != len(cue_ids):
            raise ValueError('cue plan contains duplicate cue IDs')
        expected_start = 0
        token_ids: list[str] = []
        profile_hash = canonical_sha256(self.profile.model_dump(mode='json'))
        start_gap_ids: list[str] = []
        for index, cue in enumerate(self.cues):
            if cue.token_start != expected_start:
                raise ValueError('cue plan token ranges must be ordered and contiguous')
            if index == 0 and cue.start_boundary_kind != 'tape_start':
                raise ValueError('first cue must start at tape_start')
            if index > 0 and cue.start_boundary_kind == 'tape_start':
                raise ValueError('only the first cue may start at tape_start')
            identity = {
                'raw_snapshot_hash': self.raw_snapshot_hash,
                'profile_hash': profile_hash,
                'token_ids': cue.token_ids,
                'start_gap_id': cue.start_gap_id,
                'start_boundary_kind': cue.start_boundary_kind,
            }
            if cue.cue_id != f'cue-{canonical_sha256(identity)}':
                raise ValueError('cue_id does not match cue plan content')
            if cue.start_gap_id is not None:
                start_gap_ids.append(cue.start_gap_id)
            _validate_cue_overflow(cue, self.profile)
            expected_start = cue.token_end
            token_ids.extend(cue.token_ids)
        if expected_start != self.raw_token_count:
            raise ValueError('cue plan must cover the complete raw token tape')
        if len(set(token_ids)) != len(token_ids):
            raise ValueError('cue plan repeats raw token IDs')
        if len(set(start_gap_ids)) != len(start_gap_ids):
            raise ValueError('cue plan repeats a selected start gap')
        if self.raw_token_count == 0 and self.cues:
            raise ValueError('empty raw token tape cannot contain cues')
        if self.raw_token_count > 0 and not self.cues:
            raise ValueError('non-empty raw token tape requires cues')
        content = self.model_dump(mode='json', exclude={'artifact_hash'})
        if canonical_sha256(content) != self.artifact_hash:
            raise ValueError('artifact_hash does not match cue plan content')
        return self


@dataclass(frozen=True)
class _Edge:
    start: int
    end: int
    units: int
    overflow_reason: OverflowReason | None
    cost: int


@dataclass(frozen=True)
class _PathState:
    cost: int
    edges: tuple[_Edge, ...]
    cut_token_ids: tuple[str, ...]
    last_units: int


@dataclass(frozen=True)
class _PlannerContext:
    raw_snapshot: RawAsrSnapshot
    profile: CuePlannerProfile
    language_mode: ResolvedLanguageMode
    gaps_by_right_index: Mapping[int, SubtitleLegalGap]
    votes_by_gap_id: Mapping[str, DeepSeekBoundaryVote]
    hard_atom_boundaries: frozenset[int]
    unbreakable_ranges: frozenset[tuple[int, int]]
    char_count_prefix: tuple[int, ...]
    word_count_prefix: tuple[int, ...]


def build_subtitle_global_plan(
    *,
    raw_snapshot: RawAsrSnapshot,
    atoms: Sequence[TokenBoundLexicalAtom],
    gaps: Sequence[SubtitleLegalGap],
    boundary_votes: Sequence[DeepSeekBoundaryVote] = (),
    profile: CuePlannerProfile = DEFAULT_CUE_PLANNER_PROFILE,
) -> SubtitleCuePlanArtifact:
    normalized_atoms, normalized_gaps, normalized_votes = _validate_inputs(
        raw_snapshot,
        atoms,
        gaps,
        boundary_votes,
    )
    language_mode = _resolve_language_mode(raw_snapshot.language, profile.language_mode)
    gaps_by_right_index = {gap.right_token_index: gap for gap in normalized_gaps}
    votes_by_gap_id = {vote.gap_id: vote for vote in normalized_votes}
    unbreakable_ranges = frozenset({
        *(
            (atom.token_start, atom.token_end)
            for atom in normalized_atoms
            if atom.protection == 'hard' or atom.token_end - atom.token_start == 1
        ),
        *_preferred_affiliation_ranges(
            raw_snapshot,
            normalized_atoms,
            normalized_gaps,
            normalized_votes,
        ),
        *_dependent_particle_ranges(normalized_atoms),
    })
    hard_boundaries = frozenset(
        boundary
        for start, end in unbreakable_ranges
        for boundary in range(start + 1, end)
    )
    context = _PlannerContext(
        raw_snapshot=raw_snapshot,
        profile=profile,
        language_mode=language_mode,
        gaps_by_right_index=gaps_by_right_index,
        votes_by_gap_id=votes_by_gap_id,
        hard_atom_boundaries=hard_boundaries,
        unbreakable_ranges=unbreakable_ranges,
        char_count_prefix=_display_count_prefix(
            raw_snapshot,
            normalized_atoms,
            _text_char_count,
        ),
        word_count_prefix=_display_count_prefix(
            raw_snapshot,
            normalized_atoms,
            _text_word_count,
        ),
    )
    required_boundaries = _required_speaker_boundaries(raw_snapshot, normalized_gaps)
    if required_boundaries.intersection(hard_boundaries):
        raise ValueError('a hard atom crosses a required speaker boundary')

    edges: list[_Edge] = []
    partition_boundaries = [0, *sorted(required_boundaries), len(raw_snapshot.tokens)]
    for partition_start, partition_end in zip(
        partition_boundaries,
        partition_boundaries[1:],
    ):
        if partition_start == partition_end:
            continue
        edges.extend(_plan_partition(context, partition_start, partition_end))

    profile_hash = canonical_sha256(profile.model_dump(mode='json'))
    cues = tuple(
        _planned_cue(
            context,
            edge,
            profile_hash,
            required_boundaries,
        )
        for edge in edges
    )
    content = {
        'schema_version': SUBTITLE_GLOBAL_PLANNER_SCHEMA_VERSION,
        'raw_snapshot_hash': raw_snapshot.snapshot_hash,
        'raw_token_count': len(raw_snapshot.tokens),
        'profile': profile.model_dump(mode='json'),
        'atoms_hash': canonical_sha256([
            atom.model_dump(mode='json') for atom in normalized_atoms
        ]),
        'gaps_hash': canonical_sha256([
            gap.model_dump(mode='json') for gap in normalized_gaps
        ]),
        'boundary_votes_hash': canonical_sha256([
            vote.model_dump(mode='json') for vote in normalized_votes
        ]),
        'cues': [cue.model_dump(mode='json') for cue in cues],
    }
    return SubtitleCuePlanArtifact(
        **content,
        artifact_hash=canonical_sha256(content),
    )


def _plan_partition(
    context: _PlannerContext,
    partition_start: int,
    partition_end: int,
) -> tuple[_Edge, ...]:
    candidates = [partition_start]
    for boundary in range(partition_start + 1, partition_end):
        gap = context.gaps_by_right_index[boundary]
        if (
            gap.disposition == 'allowed'
            and gap.legal_gap
            and boundary not in context.hard_atom_boundaries
        ):
            candidates.append(boundary)
    candidates.append(partition_end)
    boundary_costs = {
        boundary: _boundary_cost(
            context,
            boundary,
            partition_start,
            partition_end,
        )
        for boundary in candidates[1:-1]
    }
    states: dict[int, dict[int, _PathState]] = {
        partition_start: {
            0: _PathState(cost=0, edges=(), cut_token_ids=(), last_units=0),
        },
    }

    for end_offset in range(1, len(candidates)):
        end = candidates[end_offset]
        end_states: dict[int, _PathState] = {}
        for start_offset in range(end_offset - 1, -1, -1):
            start = candidates[start_offset]
            edge = _evaluate_edge(
                context,
                start,
                end,
                has_internal_candidate=start_offset + 1 < end_offset,
                boundary_cost=boundary_costs.get(end, 0),
            )
            if edge is None:
                break
            for state in states.get(start, {}).values():
                visual_jump = (
                    abs(state.last_units - edge.units) * 2
                    if state.edges
                    else 0
                )
                is_internal_cut = end < partition_end
                cut_ids = state.cut_token_ids + (
                    (context.raw_snapshot.tokens[end].token_id,)
                    if is_internal_cut
                    else ()
                )
                candidate = _PathState(
                    cost=state.cost + edge.cost + visual_jump,
                    edges=state.edges + (edge,),
                    cut_token_ids=cut_ids,
                    last_units=edge.units,
                )
                current = end_states.get(edge.units)
                if current is None or _path_key(candidate) < _path_key(current):
                    end_states[edge.units] = candidate
        if not end_states:
            raise ValueError('no deterministic cue plan satisfies the legal gap contract')
        states[end] = end_states

    final = min(states[partition_end].values(), key=_path_key)
    return final.edges


def _evaluate_edge(
    context: _PlannerContext,
    start: int,
    end: int,
    *,
    has_internal_candidate: bool,
    boundary_cost: int,
) -> _Edge | None:
    chars = context.char_count_prefix[end] - context.char_count_prefix[start]
    words = context.word_count_prefix[end] - context.word_count_prefix[start]
    units = words if context.language_mode == 'english_word' else chars
    target, soft_limit, safety_limit = _length_targets(
        context.profile,
        context.language_mode,
    )
    overflow_reason: OverflowReason | None = None
    if units > safety_limit:
        if (start, end) in context.unbreakable_ranges:
            overflow_reason = 'unbreakable_atom'
        elif not has_internal_candidate:
            overflow_reason = 'no_legal_gap'
        elif (
            context.profile.mode == 'smart'
            and units == safety_limit + 1
            and _ends_with_terminal_punctuation(context, end)
            and not _has_ai_permitted_internal_gap(context, start, end)
        ):
            overflow_reason = 'terminal_punctuation'
        else:
            return None

    cost = _length_cost(
        units,
        target,
        soft_limit,
        safety_limit,
        overflow_reason,
    )
    cost += _duration_and_reading_cost(context, start, end, chars, words)
    cost += boundary_cost
    return _Edge(
        start=start,
        end=end,
        units=units,
        overflow_reason=overflow_reason,
        cost=cost,
    )


def _has_ai_permitted_internal_gap(
    context: _PlannerContext,
    start: int,
    end: int,
) -> bool:
    for boundary in range(start + 1, end):
        gap = context.gaps_by_right_index.get(boundary)
        if gap is None or gap.disposition != 'allowed' or not gap.legal_gap:
            continue
        vote = context.votes_by_gap_id.get(gap.gap_id)
        if vote is not None and vote.rating in {'preferred', 'allowed'}:
            return True
    return False


def _ends_with_terminal_punctuation(
    context: _PlannerContext,
    end: int,
) -> bool:
    if end <= 0:
        return False
    text = (
        context.raw_snapshot.tokens[end - 1].text
        + context.raw_snapshot.tokens[end - 1].punctuation
    ).rstrip()
    return bool(text and text[-1] in _TERMINAL_PUNCTUATION)


def _length_targets(
    profile: CuePlannerProfile,
    language_mode: ResolvedLanguageMode,
) -> tuple[int, int, int]:
    if profile.mode == 'strict':
        limit = profile.strict_max_display_units
        if limit is None:
            raise AssertionError('validated strict profile is missing its limit')
        return limit, limit, limit
    if language_mode == 'english_word':
        return (
            profile.english_target_words,
            profile.english_soft_max_words,
            profile.english_safety_max_words,
        )
    return (
        profile.target_display_units,
        profile.soft_max_display_units,
        profile.safety_max_display_units,
    )


def _validate_cue_overflow(
    cue: PlannedSubtitleCue,
    profile: CuePlannerProfile,
) -> None:
    _target, _soft_limit, safety_limit = _length_targets(
        profile,
        cue.language_mode,
    )
    if cue.display_unit_count > safety_limit and cue.strict_overflow_reason is None:
        raise ValueError('overflow cue requires an explicit reason')
    if cue.display_unit_count <= safety_limit and cue.strict_overflow_reason is not None:
        raise ValueError('in-limit cue cannot claim an overflow reason')


def _length_cost(
    units: int,
    target: int,
    soft_limit: int,
    safety_limit: int,
    overflow_reason: OverflowReason | None,
) -> int:
    if overflow_reason in {'no_legal_gap', 'terminal_punctuation'}:
        return 180 + max(0, units - safety_limit) ** 2 * 60
    if overflow_reason is not None:
        return 10000 + max(0, units - safety_limit) ** 2 * 500
    if target == soft_limit == safety_limit:
        cost = (target - units) ** 2
    else:
        # Smart mode keeps 18-19 unit phrases intact without semantic evidence,
        # but restores meaningful pressure once a cue grows clearly long.
        cost = max(0, units - target) ** 2 * 2
        cost += max(0, units - soft_limit) ** 2 * 2
    cost += _SHORT_CUE_COSTS.get(units, 0)
    return cost + 12


def _duration_and_reading_cost(
    context: _PlannerContext,
    start: int,
    end: int,
    chars: int,
    words: int,
) -> int:
    start_ms, end_ms, duration_ms = _raw_time_span_by_range(
        context.raw_snapshot,
        start,
        end,
    )
    if start_ms is None or end_ms is None or duration_ms is None or duration_ms == 0:
        return 0
    cost = (
        _MIN_READABLE_CUE_DURATION_MS
        if duration_ms < _MIN_READABLE_CUE_DURATION_MS
        else 0
    )
    if duration_ms > context.profile.max_cue_duration_ms:
        cost += (duration_ms - context.profile.max_cue_duration_ms) // 20
    if context.language_mode == 'english_word':
        rate = words * 60000 // duration_ms
        excess = rate - context.profile.max_english_words_per_minute
    else:
        rate = chars * 1000 // duration_ms
        excess = rate - context.profile.max_cjk_chars_per_second
    if duration_ms >= 500 and excess > 0:
        cost += excess * excess
    return cost


def _boundary_cost(
    context: _PlannerContext,
    boundary: int,
    partition_start: int,
    partition_end: int,
) -> int:
    gap = context.gaps_by_right_index[boundary]
    left = context.raw_snapshot.tokens[boundary - 1]
    vote = context.votes_by_gap_id.get(gap.gap_id)
    cost = 0
    pause_ms = gap.pause_ms or 0
    if pause_ms >= 600:
        cost -= 100
    elif pause_ms >= 320:
        cost -= 70
    elif pause_ms >= 120:
        cost -= 25
    left_text = (left.text + left.punctuation).rstrip()
    if left_text.endswith(tuple(_TERMINAL_PUNCTUATION)):
        cost -= 80
    elif left_text.endswith(tuple(_CLAUSE_PUNCTUATION)):
        cost -= 25
    if gap.atom_protection == 'strong':
        cost += 320
    elif gap.atom_protection == 'soft':
        cost += 25
    if vote is None:
        # The compact AI response lists semantic candidates only. A missing vote
        # therefore means "not selected", not a neutral invitation to cut.
        cost += _UNSELECTED_AI_BOUNDARY_COST
    else:
        if vote.decision_source == 'script_fallback':
            cost -= 10
        else:
            cost += _vote_cost(vote.rating)
            if vote.reason_code == 'enumeration':
                cost -= 45
            elif vote.reason_code == 'phrase_integrity':
                cost += 45
            elif vote.reason_code == 'terminal_punctuation':
                cost -= 20
        if (
            vote.rating in {'preferred', 'allowed'}
            and pause_ms < 320
            and _has_short_terminal_tail(context, boundary, partition_end)
        ):
            cost += 220
    prefix = _span_text(
        context.raw_snapshot.tokens[
            max(partition_start, boundary - _GRAMMAR_CONTEXT_TOKENS):boundary
        ],
        context.language_mode,
    )
    suffix = _span_text(
        context.raw_snapshot.tokens[
            boundary:min(partition_end, boundary + _GRAMMAR_CONTEXT_TOKENS)
        ],
        context.language_mode,
    )
    cost += grammar_boundary_penalty(prefix, suffix)
    return cost


def _has_short_terminal_tail(
    context: _PlannerContext,
    boundary: int,
    partition_end: int,
) -> bool:
    tail_end = min(partition_end, boundary + 2)
    for end in range(boundary + 1, tail_end + 1):
        if not _ends_with_terminal_punctuation(context, end):
            continue
        units = (
            context.word_count_prefix[end] - context.word_count_prefix[boundary]
            if context.language_mode == 'english_word'
            else context.char_count_prefix[end] - context.char_count_prefix[boundary]
        )
        return units <= 4
    return False


def _vote_cost(rating: BoundaryRating) -> int:
    return {
        'preferred': -180,
        # "Allowed" authorizes a boundary when duration or display safety needs
        # one; it must not by itself create another subtitle cue.
        'allowed': 0,
        'discouraged': 55,
        # Preserve a model-identified phrase whenever another safe plan exists.
        # The planner may still cross it when strict length safety leaves no path.
        'forbidden': 1_000_000,
    }[rating]


def _planned_cue(
    context: _PlannerContext,
    edge: _Edge,
    profile_hash: str,
    required_boundaries: frozenset[int],
) -> PlannedSubtitleCue:
    tokens = context.raw_snapshot.tokens[edge.start:edge.end]
    start_gap = context.gaps_by_right_index.get(edge.start)
    if edge.start == 0:
        boundary_kind: StartBoundaryKind = 'tape_start'
    elif edge.start in required_boundaries:
        boundary_kind = 'required_speaker'
    else:
        boundary_kind = 'planned_legal_gap'
    start_ms, end_ms, duration_ms = _raw_time_span_by_range(
        context.raw_snapshot,
        edge.start,
        edge.end,
    )
    token_ids = tuple(token.token_id for token in tokens)
    identity = {
        'raw_snapshot_hash': context.raw_snapshot.snapshot_hash,
        'profile_hash': profile_hash,
        'token_ids': token_ids,
        'start_gap_id': start_gap.gap_id if start_gap is not None else None,
        'start_boundary_kind': boundary_kind,
    }
    return PlannedSubtitleCue(
        cue_id=f'cue-{canonical_sha256(identity)}',
        token_start=edge.start,
        token_end=edge.end,
        token_ids=token_ids,
        start_gap_id=start_gap.gap_id if start_gap is not None else None,
        start_boundary_kind=boundary_kind,
        speaker_id=tokens[0].speaker_id,
        speaker_key=tokens[0].speaker_key,
        language_mode=context.language_mode,
        display_unit_count=edge.units,
        display_char_count=(
            context.char_count_prefix[edge.end]
            - context.char_count_prefix[edge.start]
        ),
        display_word_count=(
            context.word_count_prefix[edge.end]
            - context.word_count_prefix[edge.start]
        ),
        start_ms=start_ms,
        end_ms=end_ms,
        duration_ms=duration_ms,
        strict_overflow_reason=edge.overflow_reason,
    )


def _validate_inputs(
    raw_snapshot: RawAsrSnapshot,
    atoms: Sequence[TokenBoundLexicalAtom],
    gaps: Sequence[SubtitleLegalGap],
    boundary_votes: Sequence[DeepSeekBoundaryVote],
) -> tuple[
    tuple[TokenBoundLexicalAtom, ...],
    tuple[SubtitleLegalGap, ...],
    tuple[DeepSeekBoundaryVote, ...],
]:
    normalized_atoms = tuple(
        TokenBoundLexicalAtom.model_validate(atom.model_dump(mode='python'))
        for atom in atoms
    )
    normalized_gaps = tuple(
        SubtitleLegalGap.model_validate(gap.model_dump(mode='python'))
        for gap in gaps
    )
    validated_votes = tuple(
        DeepSeekBoundaryVote.model_validate(vote.model_dump(mode='python'))
        for vote in boundary_votes
    )
    raw_token_ids = [token.token_id for token in raw_snapshot.tokens]
    covered_ids: list[str] = []
    expected_start = 0
    atom_ids: set[str] = set()
    for atom in normalized_atoms:
        if atom.atom_id in atom_ids:
            raise ValueError('planner atoms must have unique IDs')
        atom_ids.add(atom.atom_id)
        if atom.token_start != expected_start or atom.token_end > len(raw_token_ids):
            raise ValueError('planner atoms must cover the raw token tape in order')
        expected_ids = tuple(raw_token_ids[atom.token_start:atom.token_end])
        if atom.token_ids != expected_ids:
            raise ValueError('planner atom range disagrees with raw token IDs')
        covered_ids.extend(atom.token_ids)
        expected_start = atom.token_end
    if covered_ids != raw_token_ids:
        raise ValueError('planner atoms must conserve the raw token tape')

    expected_pairs = [
        (raw_token_ids[index - 1], raw_token_ids[index])
        for index in range(1, len(raw_token_ids))
    ]
    actual_pairs = [(gap.left_token_id, gap.right_token_id) for gap in normalized_gaps]
    if actual_pairs != expected_pairs:
        raise ValueError('planner gaps must cover every adjacent token pair in order')
    if len({gap.gap_id for gap in normalized_gaps}) != len(normalized_gaps):
        raise ValueError('planner gap IDs must be unique')
    for index, gap in enumerate(normalized_gaps, start=1):
        if gap.left_token_index != index - 1 or gap.right_token_index != index:
            raise ValueError('planner gap indexes disagree with the raw token tape')

    gap_by_id = {gap.gap_id: gap for gap in normalized_gaps}
    vote_by_id: dict[str, DeepSeekBoundaryVote] = {}
    for vote in validated_votes:
        if vote.gap_id in vote_by_id:
            raise ValueError('planner boundary votes must have unique gap IDs')
        gap = gap_by_id.get(vote.gap_id)
        if gap is None:
            raise ValueError('planner boundary vote references an unknown gap')
        if (
            vote.left_token_id != gap.left_token_id
            or vote.right_token_id != gap.right_token_id
        ):
            raise ValueError('planner boundary vote token IDs disagree with its gap')
        if gap.disposition != 'allowed' or not gap.legal_gap or gap.reason == 'hard_atom':
            raise ValueError('planner boundary vote is not authorized for a legal gap')
        vote_by_id[vote.gap_id] = vote
    normalized_votes = tuple(
        vote_by_id[gap.gap_id]
        for gap in normalized_gaps
        if gap.gap_id in vote_by_id
    )
    return normalized_atoms, normalized_gaps, normalized_votes


def _required_speaker_boundaries(
    raw_snapshot: RawAsrSnapshot,
    gaps: Sequence[SubtitleLegalGap],
) -> frozenset[int]:
    result: set[int] = set()
    for boundary, (left, right) in enumerate(
        zip(raw_snapshot.tokens, raw_snapshot.tokens[1:]),
        start=1,
    ):
        gap = gaps[boundary - 1]
        if gap.disposition == 'required' or _speaker_identity(left) != _speaker_identity(right):
            result.add(boundary)
    return frozenset(result)


def _preferred_affiliation_ranges(
    raw_snapshot: RawAsrSnapshot,
    atoms: Sequence[TokenBoundLexicalAtom],
    gaps: Sequence[SubtitleLegalGap],
    votes: Sequence[DeepSeekBoundaryVote],
) -> frozenset[tuple[int, int]]:
    gap_by_id = {gap.gap_id: gap for gap in gaps}
    organizations = tuple(
        atom
        for atom in atoms
        if atom.source == 'organization' and atom.protection == 'hard'
    )
    ranges: set[tuple[int, int]] = set()
    for vote in votes:
        if (
            vote.decision_source != 'ai'
            or vote.rating != 'preferred'
            or vote.reason_code != 'semantic_boundary'
        ):
            continue
        start = gap_by_id[vote.gap_id].right_token_index
        for organization in organizations:
            if organization.token_start <= start:
                continue
            lead_in_tokens = raw_snapshot.tokens[start:organization.token_start]
            if any(token.punctuation for token in lead_in_tokens):
                continue
            lead_in = ''.join(token.text for token in lead_in_tokens).strip()
            if lead_in in AFFILIATION_LEAD_INS:
                ranges.add((start, organization.token_end))
                break
    return frozenset(ranges)


def _dependent_particle_ranges(
    atoms: Sequence[TokenBoundLexicalAtom],
) -> frozenset[tuple[int, int]]:
    ranges: set[tuple[int, int]] = set()
    punctuation = tuple(_TERMINAL_PUNCTUATION + _CLAUSE_PUNCTUATION)
    for index, atom in enumerate(atoms):
        text = atom.text.lstrip()
        bare_text = text.rstrip(''.join(punctuation)).rstrip()
        if not (
            (text.startswith('的') and not text.startswith('的确'))
            or bare_text in {'地', '得'}
        ):
            continue
        start_index = index
        for left_index in range(index - 1, max(-1, index - 3), -1):
            left = atoms[left_index]
            if (
                left.utterance_id != atom.utterance_id
                or left.text.rstrip().endswith(punctuation)
            ):
                break
            start_index = left_index
        if start_index < index:
            ranges.add((atoms[start_index].token_start, atom.token_end))
    return frozenset(ranges)


def _speaker_identity(token: RawAsrToken) -> tuple[str | None, str | None] | None:
    if token.speaker_key is None and token.speaker_id is None:
        return None
    return token.speaker_key, token.speaker_id


def _resolve_language_mode(
    language: str,
    requested: PlannerLanguageMode,
) -> ResolvedLanguageMode:
    if requested != 'auto':
        return requested
    return 'english_word' if language.strip().lower().startswith('en') else 'cjk_character'


def _token_char_count(token: RawAsrToken) -> int:
    return sum(
        1
        for character in token.text + token.punctuation
        if not character.isspace()
    )


def _token_word_count(token: RawAsrToken) -> int:
    return len(_WORD_RE.findall(token.text))


def _text_char_count(text: str) -> int:
    return sum(1 for character in text if not character.isspace())


def _text_word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _display_count_prefix(
    raw_snapshot: RawAsrSnapshot,
    atoms: Sequence[TokenBoundLexicalAtom],
    counter: Callable[[str], int],
) -> tuple[int, ...]:
    counts = [
        counter(token.text + token.punctuation)
        for token in raw_snapshot.tokens
    ]
    for atom in atoms:
        if atom.source != 'accepted_script_correction':
            continue
        rendered_text = atom.text + overlay_punctuation(
            raw_snapshot.tokens[atom.token_start:atom.token_end]
        )
        counts[atom.token_start] = counter(rendered_text)
        for token_index in range(atom.token_start + 1, atom.token_end):
            counts[token_index] = 0
    result = [0]
    for count in counts:
        result.append(result[-1] + count)
    return tuple(result)


def _span_text(
    tokens: Sequence[RawAsrToken],
    language_mode: ResolvedLanguageMode,
) -> str:
    values = [(token.text + token.punctuation).strip() for token in tokens]
    if language_mode == 'english_word':
        return ' '.join(value for value in values if value)
    return ''.join(values)


def _raw_time_span_by_range(
    raw_snapshot: RawAsrSnapshot,
    start: int,
    end: int,
) -> tuple[int | None, int | None, int | None]:
    if start >= end:
        return None, None, None
    first = raw_snapshot.tokens[start]
    last = raw_snapshot.tokens[end - 1]
    if first.start_ms is None or last.end_ms is None or last.end_ms < first.start_ms:
        return None, None, None
    return first.start_ms, last.end_ms, last.end_ms - first.start_ms


def _path_key(state: _PathState) -> tuple[int, tuple[str, ...]]:
    return state.cost, state.cut_token_ids
