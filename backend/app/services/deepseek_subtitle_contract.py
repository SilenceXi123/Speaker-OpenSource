from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.script_boundary_hints import ScriptBoundaryHint
from app.services.script_correction_candidates import CorrectionCandidate
from app.services.subtitle_atoms import (
    AtomProtection,
    AtomSource,
    TokenBoundLexicalAtom,
)
from app.services.subtitle_legal_gaps import (
    GapDisposition,
    GapReason,
    GapTimeEvidence,
    SubtitleLegalGap,
)
from app.services.subtitle_pipeline_v2_schema import (
    RawAsrSnapshot,
    SemanticUtteranceArtifact,
    canonical_sha256,
)


DEEPSEEK_PLAN_WINDOW_SCHEMA_VERSION = 'deepseek-subtitle-window.v1'
BoundaryRating = Literal['preferred', 'allowed', 'discouraged', 'forbidden']
BoundaryDecisionSource = Literal['ai', 'script_fallback']
BoundaryVoteReason = Literal[
    'pause',
    'terminal_punctuation',
    'semantic_boundary',
    'enumeration',
    'phrase_integrity',
    'length_balance',
]
ValidationStatus = Literal['accepted', 'accepted_with_rejections', 'rejected']
RejectionReason = Literal[
    'response_contract_invalid',
    'window_id_mismatch',
    'duplicate_gap_vote',
    'unknown_gap',
    'gap_token_mismatch',
    'speaker_boundary_required',
    'hard_atom_forbidden',
    'illegal_gap',
    'overlap_read_only',
    'unknown_evidence_ref',
    'duplicate_candidate',
    'unknown_candidate',
    'candidate_overlap_read_only',
]
ExecutionStatus = Literal['accepted', 'accepted_with_rejections', 'degraded']
ExecutionFailureCode = Literal[
    'transport_failure',
    'response_contract_invalid',
    'suggestions_rejected',
]


def _is_safe_script_boundary_target(
    *,
    disposition: GapDisposition,
    legal_gap: bool,
    atom_protection: AtomProtection | None,
) -> bool:
    return (
        disposition == 'allowed'
        and legal_gap
        and atom_protection != 'hard'
    )


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class DeepSeekWindowProfile(_FrozenStrictModel):
    profile_version: str = Field(min_length=1)
    core_token_limit: int = Field(gt=0)
    overlap_tokens: int = Field(ge=0)
    combine_semantic_utterances: bool = False


class DeepSeekLanguageProfile(_FrozenStrictModel):
    language: str = Field(min_length=1)
    display_mode: Literal['cjk_character', 'latin_word', 'mixed_width']


class DeepSeekLengthProfile(_FrozenStrictModel):
    profile_version: str = Field(min_length=1)
    mode: Literal['smart', 'strict']
    target_display_units: int = Field(gt=0)
    soft_max_display_units: int = Field(gt=0)

    @model_validator(mode='after')
    def validate_limits(self) -> 'DeepSeekLengthProfile':
        if self.soft_max_display_units < self.target_display_units:
            raise ValueError('soft length limit must not be below its target')
        return self


DEFAULT_DEEPSEEK_WINDOW_PROFILE = DeepSeekWindowProfile(
    profile_version='deepseek-window.v1',
    core_token_limit=64,
    overlap_tokens=32,
)
DEFAULT_DEEPSEEK_LENGTH_PROFILE = DeepSeekLengthProfile(
    profile_version='subtitle-length.v1',
    mode='smart',
    target_display_units=14,
    soft_max_display_units=16,
)


class DeepSeekPlanToken(_FrozenStrictModel):
    token_id: str = Field(min_length=1)
    token_index: int = Field(ge=0)
    text: str
    punctuation: str = ''
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    relative_pause_ms: int | None = Field(default=None, ge=0)
    speaker_key: str | None = None
    in_core: bool


class DeepSeekPlanAtom(_FrozenStrictModel):
    atom_id: str = Field(min_length=1)
    token_ids: tuple[str, ...] = Field(min_length=1)
    protection: AtomProtection
    source: AtomSource
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode='after')
    def validate_references(self) -> 'DeepSeekPlanAtom':
        _require_unique(self.token_ids, 'atom token IDs')
        _require_sorted_unique(self.evidence_refs, 'atom evidence refs')
        return self


class DeepSeekPlanGap(_FrozenStrictModel):
    gap_id: str = Field(min_length=1)
    left_token_id: str = Field(min_length=1)
    right_token_id: str = Field(min_length=1)
    left_token_index: int = Field(ge=0)
    right_token_index: int = Field(gt=0)
    disposition: GapDisposition
    legal_gap: bool
    reason: GapReason
    time_evidence: GapTimeEvidence
    relative_pause_ms: int | None = Field(default=None, ge=0)
    atom_id: str | None = None
    atom_protection: AtomProtection | None = None
    evidence_refs: tuple[str, ...] = ()
    script_boundary_strength: Literal['strong', 'weak'] | None = None
    script_boundary_evidence_refs: tuple[str, ...] = ()
    core_owned: bool

    @model_validator(mode='after')
    def validate_references(self) -> 'DeepSeekPlanGap':
        if self.right_token_index != self.left_token_index + 1:
            raise ValueError('DeepSeek gaps must bind adjacent tokens')
        if (self.atom_id is None) != (self.atom_protection is None):
            raise ValueError('gap atom reference and protection must be paired')
        _require_sorted_unique(self.evidence_refs, 'gap evidence refs')
        if (
            (self.script_boundary_strength is None)
            != (not self.script_boundary_evidence_refs)
        ):
            raise ValueError(
                'script boundary strength and evidence refs must be present together'
            )
        _require_sorted_unique(
            self.script_boundary_evidence_refs,
            'script boundary evidence refs',
        )
        if (
            self.script_boundary_strength is not None
            and not _is_safe_script_boundary_target(
                disposition=self.disposition,
                legal_gap=self.legal_gap,
                atom_protection=self.atom_protection,
            )
        ):
            raise ValueError(
                'script boundary hint must target a safe legal gap'
            )
        return self


class DeepSeekCorrectionSummary(_FrozenStrictModel):
    candidate_id: str = Field(min_length=1)
    token_ids: tuple[str, ...] = Field(min_length=1)
    source_text: str = Field(min_length=1)
    replacement_text: str = Field(min_length=1)
    candidate_type: Literal['script', 'lexicon', 'grammar', 'canonical_passage'] = 'script'
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    core_owned: bool

    @model_validator(mode='after')
    def validate_references(self) -> 'DeepSeekCorrectionSummary':
        _require_unique(self.token_ids, 'candidate token IDs')
        _require_sorted_unique(self.evidence_refs, 'candidate evidence refs')
        return self


class DeepSeekPlanWindow(_FrozenStrictModel):
    schema_version: Literal['deepseek-subtitle-window.v1']
    raw_snapshot_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    window_id: str = Field(min_length=1)
    utterance_id: str = Field(min_length=1)
    window_token_start: int = Field(ge=0)
    window_token_end: int = Field(gt=0)
    core_token_start: int = Field(ge=0)
    core_token_end: int = Field(gt=0)
    tokens: tuple[DeepSeekPlanToken, ...] = Field(min_length=1)
    atoms: tuple[DeepSeekPlanAtom, ...]
    gaps: tuple[DeepSeekPlanGap, ...]
    correction_candidates: tuple[DeepSeekCorrectionSummary, ...]
    language_profile: DeepSeekLanguageProfile
    length_profile: DeepSeekLengthProfile

    @model_validator(mode='after')
    def validate_window_contract(self) -> 'DeepSeekPlanWindow':
        if not (
            self.window_token_start
            <= self.core_token_start
            < self.core_token_end
            <= self.window_token_end
        ):
            raise ValueError('core token range must be non-empty and inside its window')
        expected_indexes = list(range(self.window_token_start, self.window_token_end))
        if [token.token_index for token in self.tokens] != expected_indexes:
            raise ValueError('window token indexes must be ordered and contiguous')
        token_ids = [token.token_id for token in self.tokens]
        _require_unique(token_ids, 'window token IDs')
        token_id_set = set(token_ids)
        token_position = {
            token_id: position for position, token_id in enumerate(token_ids)
        }
        token_id_by_index = {
            token.token_index: token.token_id for token in self.tokens
        }
        for token in self.tokens:
            expected_core = self.core_token_start <= token.token_index < self.core_token_end
            if token.in_core != expected_core:
                raise ValueError('token in_core marker disagrees with the core range')

        atom_ids: set[str] = set()
        for atom in self.atoms:
            if atom.atom_id in atom_ids:
                raise ValueError('window contains duplicate atom IDs')
            atom_ids.add(atom.atom_id)
            if not set(atom.token_ids).issubset(token_id_set):
                raise ValueError('window atom references a token outside the window')
            _require_contiguous_token_ids(
                atom.token_ids,
                token_position,
                'window atom',
            )

        gap_ids: set[str] = set()
        gap_pairs: list[tuple[str, str]] = []
        for gap in self.gaps:
            if gap.gap_id in gap_ids:
                raise ValueError('window contains duplicate gap IDs')
            gap_ids.add(gap.gap_id)
            if gap.left_token_id not in token_id_set or gap.right_token_id not in token_id_set:
                raise ValueError('window gap references a token outside the window')
            if (
                token_id_by_index.get(gap.left_token_index) != gap.left_token_id
                or token_id_by_index.get(gap.right_token_index) != gap.right_token_id
            ):
                raise ValueError('window gap token IDs disagree with their indexes')
            gap_pairs.append((gap.left_token_id, gap.right_token_id))
            expected_owner = (
                self.core_token_start <= gap.right_token_index < self.core_token_end
            )
            if gap.core_owned != expected_owner:
                raise ValueError('gap core ownership disagrees with its token range')
            if (
                gap.script_boundary_strength is not None
                and not gap.core_owned
            ):
                raise ValueError(
                    'script boundary hints must belong to a core-owned gap'
                )
        if gap_pairs != list(zip(token_ids, token_ids[1:])):
            raise ValueError('window gaps must cover each adjacent token pair in order')

        candidate_ids: set[str] = set()
        for candidate in self.correction_candidates:
            if candidate.candidate_id in candidate_ids:
                raise ValueError('window contains duplicate candidate IDs')
            candidate_ids.add(candidate.candidate_id)
            if not set(candidate.token_ids).issubset(token_id_set):
                raise ValueError('window candidate references a token outside the window')
            _require_contiguous_token_ids(
                candidate.token_ids,
                token_position,
                'window candidate',
            )
            first_index = self.tokens[token_ids.index(candidate.token_ids[0])].token_index
            expected_owner = self.core_token_start <= first_index < self.core_token_end
            if candidate.core_owned != expected_owner:
                raise ValueError('candidate core ownership disagrees with its token range')

        if _contains_unsafe_content(self.model_dump(mode='python')):
            raise ValueError('DeepSeek plan window contains unsafe content')
        return self


class DeepSeekBoundaryVote(_FrozenStrictModel):
    gap_id: str = Field(min_length=1)
    left_token_id: str = Field(min_length=1)
    right_token_id: str = Field(min_length=1)
    rating: BoundaryRating
    reason_code: BoundaryVoteReason
    evidence_refs: tuple[str, ...]
    decision_source: BoundaryDecisionSource = 'ai'

    @model_validator(mode='after')
    def validate_evidence_refs(self) -> 'DeepSeekBoundaryVote':
        _require_sorted_unique(self.evidence_refs, 'boundary vote evidence refs')
        return self


class DeepSeekPlanResponse(_FrozenStrictModel):
    window_id: str = Field(min_length=1)
    boundary_votes: tuple[DeepSeekBoundaryVote, ...]
    correction_candidate_ids: tuple[str, ...]


class DeepSeekPlanRejection(_FrozenStrictModel):
    item_kind: Literal['response', 'boundary_vote', 'correction_candidate']
    item_id: str | None = None
    reason_code: RejectionReason


class DeepSeekPlanValidationResult(_FrozenStrictModel):
    window_id: str = Field(min_length=1)
    status: ValidationStatus
    boundary_votes: tuple[DeepSeekBoundaryVote, ...]
    correction_candidate_ids: tuple[str, ...]
    rejections: tuple[DeepSeekPlanRejection, ...]

    @model_validator(mode='after')
    def validate_status(self) -> 'DeepSeekPlanValidationResult':
        accepted_any = bool(self.boundary_votes or self.correction_candidate_ids)
        if self.status == 'accepted' and self.rejections:
            raise ValueError('accepted validation results cannot contain rejections')
        if self.status == 'accepted_with_rejections' and (
            not self.rejections or not accepted_any
        ):
            raise ValueError('partial validation requires accepted and rejected items')
        if self.status == 'rejected' and (accepted_any or not self.rejections):
            raise ValueError('rejected validation results require only rejected items')
        return self


class DeepSeekPlanExecutionResult(_FrozenStrictModel):
    window_id: str = Field(min_length=1)
    status: ExecutionStatus
    attempts: int = Field(ge=1, le=2)
    validation: DeepSeekPlanValidationResult | None = None
    failure_code: ExecutionFailureCode | None = None

    @model_validator(mode='after')
    def validate_execution_state(self) -> 'DeepSeekPlanExecutionResult':
        if self.status == 'degraded':
            if self.failure_code is None:
                raise ValueError('degraded execution requires a failure code')
        elif self.failure_code is not None or self.validation is None:
            raise ValueError('successful execution requires validation and no failure code')
        return self


DeepSeekTransport = Callable[[DeepSeekPlanWindow], Mapping[str, Any] | str]


def build_deepseek_plan_windows(
    *,
    raw_snapshot: RawAsrSnapshot,
    semantic_artifact: SemanticUtteranceArtifact,
    atoms: Sequence[TokenBoundLexicalAtom],
    gaps: Sequence[SubtitleLegalGap],
    correction_candidates: Sequence[CorrectionCandidate] = (),
    script_boundary_hints: Sequence[ScriptBoundaryHint] = (),
    profile: DeepSeekWindowProfile = DEFAULT_DEEPSEEK_WINDOW_PROFILE,
    length_profile: DeepSeekLengthProfile = DEFAULT_DEEPSEEK_LENGTH_PROFILE,
    language_profile: DeepSeekLanguageProfile | None = None,
) -> tuple[DeepSeekPlanWindow, ...]:
    _validate_source_artifacts(
        raw_snapshot,
        semantic_artifact,
        atoms,
        gaps,
        correction_candidates,
        script_boundary_hints,
    )
    if not raw_snapshot.tokens:
        return ()

    token_index = {
        token.token_id: index for index, token in enumerate(raw_snapshot.tokens)
    }
    gap_by_right_index = {gap.right_token_index: gap for gap in gaps}
    hint_by_gap_id = {hint.gap_id: hint for hint in script_boundary_hints}
    resolved_language = language_profile or _language_profile(raw_snapshot.language)
    result: list[DeepSeekPlanWindow] = []

    if profile.combine_semantic_utterances:
        utterance_ranges = ((
            f'batch-{semantic_artifact.artifact_hash[:16]}',
            0,
            len(raw_snapshot.tokens),
        ),)
    else:
        utterance_ranges = tuple(
            (
                utterance.utterance_id,
                token_index[utterance.token_ids[0]],
                token_index[utterance.token_ids[-1]] + 1,
            )
            for utterance in semantic_artifact.utterances
        )

    for utterance_id, utterance_start, utterance_end in utterance_ranges:
        for core_start in range(
            utterance_start,
            utterance_end,
            profile.core_token_limit,
        ):
            core_end = min(core_start + profile.core_token_limit, utterance_end)
            window_start = max(
                0,
                core_start - max(
                    profile.overlap_tokens,
                    1 if core_start > 0 else 0,
                ),
            )
            window_end = min(
                len(raw_snapshot.tokens),
                core_end + profile.overlap_tokens,
            )
            identity = {
                'raw_snapshot_hash': raw_snapshot.snapshot_hash,
                'utterance_id': utterance_id,
                'core_token_start': core_start,
                'core_token_end': core_end,
                'window_token_start': window_start,
                'window_token_end': window_end,
                'window_profile': profile.model_dump(mode='json'),
                'language_profile': resolved_language.model_dump(mode='json'),
                'length_profile': length_profile.model_dump(mode='json'),
            }
            result.append(DeepSeekPlanWindow(
                schema_version=DEEPSEEK_PLAN_WINDOW_SCHEMA_VERSION,
                raw_snapshot_hash=raw_snapshot.snapshot_hash,
                window_id=f'window-{canonical_sha256(identity)}',
                utterance_id=utterance_id,
                window_token_start=window_start,
                window_token_end=window_end,
                core_token_start=core_start,
                core_token_end=core_end,
                tokens=tuple(
                    _window_token(
                        raw_snapshot,
                        index,
                        gap_by_right_index,
                        core_start,
                        core_end,
                    )
                    for index in range(window_start, window_end)
                ),
                atoms=tuple(
                    _window_atom(atom)
                    for atom in atoms
                    if atom.token_start >= window_start and atom.token_end <= window_end
                ),
                gaps=tuple(
                    _window_gap(
                        gap,
                        hint_by_gap_id.get(gap.gap_id),
                        core_start,
                        core_end,
                    )
                    for gap in gaps
                    if (
                        gap.left_token_index >= window_start
                        and gap.right_token_index < window_end
                    )
                ),
                correction_candidates=tuple(
                    _window_candidate(candidate, token_index, core_start, core_end)
                    for candidate in correction_candidates
                    if _candidate_inside_window(
                        candidate,
                        token_index,
                        window_start,
                        window_end,
                    )
                ),
                language_profile=resolved_language,
                length_profile=length_profile,
            ))
    return tuple(result)


def validate_deepseek_plan(
    window: DeepSeekPlanWindow,
    response: Mapping[str, Any] | str | DeepSeekPlanResponse,
) -> DeepSeekPlanValidationResult:
    parsed = _parse_response(response)
    if parsed is None:
        return _rejected_response(window, 'response_contract_invalid')
    if parsed.window_id != window.window_id:
        return _rejected_response(window, 'window_id_mismatch')

    gap_by_id = {gap.gap_id: gap for gap in window.gaps}
    atom_evidence = {
        atom.atom_id: set(atom.evidence_refs) for atom in window.atoms
    }
    accepted_votes: list[DeepSeekBoundaryVote] = []
    accepted_candidates: list[str] = []
    rejections: list[DeepSeekPlanRejection] = []
    seen_gap_ids: set[str] = set()
    for vote in parsed.boundary_votes:
        if vote.gap_id in seen_gap_ids:
            rejections.append(_rejection(
                'boundary_vote', vote.gap_id, 'duplicate_gap_vote'
            ))
            continue
        seen_gap_ids.add(vote.gap_id)
        gap = gap_by_id.get(vote.gap_id)
        if gap is None:
            rejections.append(_rejection('boundary_vote', vote.gap_id, 'unknown_gap'))
            continue
        if (
            vote.left_token_id != gap.left_token_id
            or vote.right_token_id != gap.right_token_id
        ):
            rejections.append(_rejection(
                'boundary_vote', vote.gap_id, 'gap_token_mismatch'
            ))
            continue
        if gap.disposition == 'required':
            rejections.append(_rejection(
                'boundary_vote', vote.gap_id, 'speaker_boundary_required'
            ))
            continue
        if gap.atom_protection == 'hard' or gap.reason == 'hard_atom':
            rejections.append(_rejection(
                'boundary_vote', vote.gap_id, 'hard_atom_forbidden'
            ))
            continue
        if not gap.legal_gap or gap.disposition == 'forbidden':
            rejections.append(_rejection('boundary_vote', vote.gap_id, 'illegal_gap'))
            continue
        if not gap.core_owned:
            rejections.append(_rejection(
                'boundary_vote', vote.gap_id, 'overlap_read_only'
            ))
            continue
        allowed_evidence = set(gap.evidence_refs)
        allowed_evidence.update(gap.script_boundary_evidence_refs)
        if gap.atom_id is not None:
            allowed_evidence.update(atom_evidence.get(gap.atom_id, set()))
        if not set(vote.evidence_refs).issubset(allowed_evidence):
            rejections.append(_rejection(
                'boundary_vote', vote.gap_id, 'unknown_evidence_ref'
            ))
            continue
        accepted_votes.append(vote.model_copy(update={'decision_source': 'ai'}))

    candidate_by_id = {
        candidate.candidate_id: candidate
        for candidate in window.correction_candidates
    }
    seen_candidate_ids: set[str] = set()
    for candidate_id in parsed.correction_candidate_ids:
        if candidate_id in seen_candidate_ids:
            rejections.append(_rejection(
                'correction_candidate', candidate_id, 'duplicate_candidate'
            ))
            continue
        seen_candidate_ids.add(candidate_id)
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            rejections.append(_rejection(
                'correction_candidate', candidate_id, 'unknown_candidate'
            ))
            continue
        if not candidate.core_owned:
            rejections.append(_rejection(
                'correction_candidate',
                candidate_id,
                'candidate_overlap_read_only',
            ))
            continue
        accepted_candidates.append(candidate_id)

    return DeepSeekPlanValidationResult(
        window_id=window.window_id,
        status=_validation_status(
            bool(accepted_votes or accepted_candidates),
            bool(rejections),
        ),
        boundary_votes=tuple(accepted_votes),
        correction_candidate_ids=tuple(accepted_candidates),
        rejections=tuple(rejections),
    )


def run_deepseek_plan_window(
    window: DeepSeekPlanWindow,
    transport: DeepSeekTransport,
) -> DeepSeekPlanExecutionResult:
    try:
        response = transport(window)
    except (
        TimeoutError,
        ConnectionError,
        httpx.TimeoutException,
        httpx.NetworkError,
    ):
        return DeepSeekPlanExecutionResult(
            window_id=window.window_id,
            status='degraded',
            attempts=1,
            failure_code='transport_failure',
        )
    except Exception:
        return DeepSeekPlanExecutionResult(
            window_id=window.window_id,
            status='degraded',
            attempts=1,
            failure_code='transport_failure',
        )

    validation = validate_deepseek_plan(window, response)
    if validation.status == 'rejected':
        contract_invalid = validation.rejections[0].reason_code in {
            'response_contract_invalid',
            'window_id_mismatch',
        }
        return DeepSeekPlanExecutionResult(
            window_id=window.window_id,
            status='degraded',
            attempts=1,
            validation=validation,
            failure_code=(
                'response_contract_invalid'
                if contract_invalid
                else 'suggestions_rejected'
            ),
        )
    return DeepSeekPlanExecutionResult(
        window_id=window.window_id,
        status=validation.status,
        attempts=1,
        validation=validation,
    )


def _window_token(
    raw_snapshot: RawAsrSnapshot,
    index: int,
    gap_by_right_index: Mapping[int, SubtitleLegalGap],
    core_start: int,
    core_end: int,
) -> DeepSeekPlanToken:
    token = raw_snapshot.tokens[index]
    preceding_gap = gap_by_right_index.get(index)
    return DeepSeekPlanToken(
        token_id=token.token_id,
        token_index=index,
        text=token.text,
        punctuation=token.punctuation,
        start_ms=token.start_ms,
        end_ms=token.end_ms,
        relative_pause_ms=(
            preceding_gap.pause_ms if preceding_gap is not None else None
        ),
        speaker_key=token.speaker_key,
        in_core=core_start <= index < core_end,
    )


def _window_atom(atom: TokenBoundLexicalAtom) -> DeepSeekPlanAtom:
    return DeepSeekPlanAtom(
        atom_id=atom.atom_id,
        token_ids=atom.token_ids,
        protection=atom.protection,
        source=atom.source,
        evidence_refs=atom.evidence_refs,
    )


def _window_gap(
    gap: SubtitleLegalGap,
    script_boundary_hint: ScriptBoundaryHint | None,
    core_start: int,
    core_end: int,
) -> DeepSeekPlanGap:
    core_owned = (
        core_start <= gap.right_token_index < core_end
    )
    return DeepSeekPlanGap(
        gap_id=gap.gap_id,
        left_token_id=gap.left_token_id,
        right_token_id=gap.right_token_id,
        left_token_index=gap.left_token_index,
        right_token_index=gap.right_token_index,
        disposition=gap.disposition,
        legal_gap=gap.legal_gap,
        reason=gap.reason,
        time_evidence=gap.time_evidence,
        relative_pause_ms=gap.pause_ms,
        atom_id=gap.atom_id,
        atom_protection=gap.atom_protection,
        evidence_refs=gap.evidence_refs,
        script_boundary_strength=(
            script_boundary_hint.strength
            if core_owned and script_boundary_hint is not None
            else None
        ),
        script_boundary_evidence_refs=(
            script_boundary_hint.evidence_refs
            if core_owned and script_boundary_hint is not None
            else ()
        ),
        core_owned=core_owned,
    )


def _window_candidate(
    candidate: CorrectionCandidate,
    token_index: Mapping[str, int],
    core_start: int,
    core_end: int,
) -> DeepSeekCorrectionSummary:
    first_index = token_index[candidate.token_ids[0]]
    evidence_refs = [
        candidate.script_line_id,
        f'script-source:{candidate.script_source_hash}',
        f'phonetic:{candidate.phonetic_score:.6f}',
        f'context:{candidate.context_score:.6f}',
        f'margin:{candidate.margin:.6f}',
    ]
    evidence_refs.extend(candidate.evidence_refs)
    evidence_refs.append(f'candidate-type:{candidate.candidate_type}')
    if candidate.lexicon_term_id:
        evidence_refs.append(candidate.lexicon_term_id)
    return DeepSeekCorrectionSummary(
        candidate_id=candidate.candidate_id,
        token_ids=candidate.token_ids,
        source_text=candidate.source_text,
        replacement_text=candidate.replacement_text,
        candidate_type=candidate.candidate_type,
        evidence_refs=tuple(sorted(set(evidence_refs))),
        core_owned=core_start <= first_index < core_end,
    )


def _candidate_inside_window(
    candidate: CorrectionCandidate,
    token_index: Mapping[str, int],
    window_start: int,
    window_end: int,
) -> bool:
    indexes = [token_index[token_id] for token_id in candidate.token_ids]
    return indexes[0] >= window_start and indexes[-1] < window_end


def _validate_source_artifacts(
    raw_snapshot: RawAsrSnapshot,
    semantic_artifact: SemanticUtteranceArtifact,
    atoms: Sequence[TokenBoundLexicalAtom],
    gaps: Sequence[SubtitleLegalGap],
    candidates: Sequence[CorrectionCandidate],
    script_boundary_hints: Sequence[ScriptBoundaryHint],
) -> None:
    if semantic_artifact.raw_snapshot_hash != raw_snapshot.snapshot_hash:
        raise ValueError('semantic artifact references a different raw snapshot')
    raw_token_ids = [token.token_id for token in raw_snapshot.tokens]
    semantic_token_ids = [
        token_id
        for utterance in semantic_artifact.utterances
        for token_id in utterance.token_ids
    ]
    if semantic_token_ids != raw_token_ids:
        raise ValueError('semantic artifact does not conserve the raw token tape')
    token_index = {token_id: index for index, token_id in enumerate(raw_token_ids)}
    utterance_by_token = {
        token_id: utterance.utterance_id
        for utterance in semantic_artifact.utterances
        for token_id in utterance.token_ids
    }

    atom_ids: set[str] = set()
    for atom in atoms:
        if atom.atom_id in atom_ids:
            raise ValueError('atoms must have unique IDs')
        atom_ids.add(atom.atom_id)
        if any(token_id not in token_index for token_id in atom.token_ids):
            raise ValueError('atom references an unknown token')
        indexes = [token_index[token_id] for token_id in atom.token_ids]
        if indexes != list(range(atom.token_start, atom.token_end)):
            raise ValueError('atom range disagrees with its raw token IDs')

    expected_pairs = [
        (raw_token_ids[index - 1], raw_token_ids[index])
        for index in range(1, len(raw_token_ids))
    ]
    if [(gap.left_token_id, gap.right_token_id) for gap in gaps] != expected_pairs:
        raise ValueError('gaps must cover each adjacent raw token pair in order')
    if len({gap.gap_id for gap in gaps}) != len(gaps):
        raise ValueError('gaps must have unique IDs')
    gap_by_id = {gap.gap_id: gap for gap in gaps}

    if len({hint.hint_id for hint in script_boundary_hints}) != len(
        script_boundary_hints
    ):
        raise ValueError('script boundary hints must have unique IDs')
    if len({hint.gap_id for hint in script_boundary_hints}) != len(
        script_boundary_hints
    ):
        raise ValueError('script boundary hints must target unique gap IDs')
    for hint in script_boundary_hints:
        if hint.raw_snapshot_hash != raw_snapshot.snapshot_hash:
            raise ValueError('script boundary hint references a different raw snapshot')
        gap = gap_by_id.get(hint.gap_id)
        if gap is None:
            raise ValueError('script boundary hint references an unknown gap')
        if (
            hint.left_token_id != gap.left_token_id
            or hint.right_token_id != gap.right_token_id
            or hint.left_token_index != gap.left_token_index
            or hint.right_token_index != gap.right_token_index
        ):
            raise ValueError('script boundary hint token binding disagrees with its gap')
        if not _is_safe_script_boundary_target(
            disposition=gap.disposition,
            legal_gap=gap.legal_gap,
            atom_protection=gap.atom_protection,
        ):
            raise ValueError('script boundary hint must target a safe legal gap')

    candidate_ids: set[str] = set()
    for candidate in candidates:
        if candidate.candidate_id in candidate_ids:
            raise ValueError('correction candidate IDs must be unique')
        candidate_ids.add(candidate.candidate_id)
        if any(token_id not in token_index for token_id in candidate.token_ids):
            raise ValueError('correction candidate references an unknown token')
        indexes = [token_index[token_id] for token_id in candidate.token_ids]
        if indexes != list(range(indexes[0], indexes[-1] + 1)):
            raise ValueError('correction candidate token range must be contiguous')
        if any(
            utterance_by_token[token_id] != candidate.utterance_id
            for token_id in candidate.token_ids
        ):
            raise ValueError('correction candidate crosses its semantic utterance')


def _parse_response(
    response: Mapping[str, Any] | str | DeepSeekPlanResponse,
) -> DeepSeekPlanResponse | None:
    try:
        if isinstance(response, DeepSeekPlanResponse):
            return response
        payload = json.loads(response) if isinstance(response, str) else response
        return DeepSeekPlanResponse.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
        return None


def _rejected_response(
    window: DeepSeekPlanWindow,
    reason_code: Literal['response_contract_invalid', 'window_id_mismatch'],
) -> DeepSeekPlanValidationResult:
    return DeepSeekPlanValidationResult(
        window_id=window.window_id,
        status='rejected',
        boundary_votes=(),
        correction_candidate_ids=(),
        rejections=(DeepSeekPlanRejection(
            item_kind='response',
            reason_code=reason_code,
        ),),
    )


def _rejection(
    item_kind: Literal['boundary_vote', 'correction_candidate'],
    item_id: str,
    reason_code: RejectionReason,
) -> DeepSeekPlanRejection:
    return DeepSeekPlanRejection(
        item_kind=item_kind,
        item_id=item_id,
        reason_code=reason_code,
    )


def _validation_status(accepted: bool, rejected: bool) -> ValidationStatus:
    if not rejected:
        return 'accepted'
    if accepted:
        return 'accepted_with_rejections'
    return 'rejected'


def _language_profile(language: str) -> DeepSeekLanguageProfile:
    normalized = language.strip().lower()
    if normalized.startswith(('zh', 'ja', 'ko')):
        display_mode = 'cjk_character'
    elif normalized.startswith('en'):
        display_mode = 'latin_word'
    else:
        display_mode = 'mixed_width'
    return DeepSeekLanguageProfile(language=language, display_mode=display_mode)


_PATH_PREFIX = r'(?:^|[\s"\'`(\[{=:：，。；、（）【】「」『』《》])'
_UNIX_ABSOLUTE_PATH = re.compile(rf'{_PATH_PREFIX}/(?!/)[^\s]+')
_KNOWN_UNIX_ABSOLUTE_PATH = re.compile(
    r'(?<![A-Za-z0-9.])/(?:'
    r'Applications|Library|Network|System|Users|Volumes|app|bin|boot|cache|code|'
    r'config|data|dev|etc|home|lib|lib64|logs|media|mnt|nix|opt|private|proc|root|'
    r'run|sbin|snap|src|srv|sys|tmp|usr|var|workspace|www'
    r')(?:/|$)',
    re.IGNORECASE,
)
_TIGHT_UNIX_ABSOLUTE_PATH = re.compile(
    r'(?:'
    r'路径(?:是|为)/(?!/)(?:[^\s/]+/[^\s]+|(?!否)[^\s/]+)|'
    r'(?:文件|目录|音频|视频)(?:位于|来自)/(?!/)[^\s]+|'
    r'(?:文件|目录|音频|视频)在/(?!/)(?:[^\s/]+/[^\s]+|(?!不)[^\s/]+)|'
    r'(?:文件|目录|音频|视频)(?:是|为)/(?!/)'
    r'(?:[^\s/]+/[^\s]+|(?!否)[^\s/]+)'
    r')'
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r'(?<![A-Za-z])[A-Za-z]:[\\/][^\s]+')
_UNC_ABSOLUTE_PATH = re.compile(r'\\\\[^\\\s]+\\[^\s]+')
_FORWARD_UNC_ABSOLUTE_PATH = re.compile(r'(?<!:)//[^/\s]+/[^\s]+')
_URL_STOP = r"\s,;!)}\]>\x22\x27，。；、：！？（）【】「」『』《》“”‘’"
_HTTP_URL = re.compile(rf'https?://[^{_URL_STOP}]+', re.IGNORECASE)
_PROTOCOL_RELATIVE_URL = re.compile(
    rf'(?<![:/])//(?P<host>[^/\s]+)/[^{_URL_STOP}]+',
)
_OSS_URL = re.compile(
    r'(?:https?:)?//(?:oss[.-]|[^/\s]+\.oss[.-])[^/\s]*(?:/[^\s]*)?',
    re.IGNORECASE,
)
_SIGNED_URL_CREDENTIAL = re.compile(
    r'[?&](?:'
    r'GoogleAccessId|OSSAccessKeyId|Signature|X-Amz-Credential|X-Amz-Signature|'
    r'X-OSS-Credential|X-OSS-Security-Token|X-OSS-Signature'
    r')=',
    re.IGNORECASE,
)
_CREDENTIAL = re.compile(
    r'(?:'
    r'\bBearer\s+[\x22\x27]?|'
    r'(?<![A-Za-z0-9])api[\s_-]*key[\x22\x27]?\s*[:=]\s*[\x22\x27]?|'
    r'\bsk-'
    r')'
    r'[A-Za-z0-9._-]{4,}',
    re.IGNORECASE,
)


def _contains_unsafe_content(value: Any) -> bool:
    for text in _iter_strings(value):
        if (
            'file://' in text.lower()
            or _OSS_URL.search(text)
            or _SIGNED_URL_CREDENTIAL.search(text)
        ):
            return True
        if _CREDENTIAL.search(text):
            return True
        path_text = _HTTP_URL.sub('', text)
        path_text = _PROTOCOL_RELATIVE_URL.sub(
            _strip_protocol_relative_public_url,
            path_text,
        )
        if (
            _UNIX_ABSOLUTE_PATH.search(path_text)
            or _KNOWN_UNIX_ABSOLUTE_PATH.search(path_text)
            or _TIGHT_UNIX_ABSOLUTE_PATH.search(path_text)
            or _WINDOWS_ABSOLUTE_PATH.search(path_text)
            or _UNC_ABSOLUTE_PATH.search(path_text)
            or _FORWARD_UNC_ABSOLUTE_PATH.search(path_text)
        ):
            return True
    return False


def _strip_protocol_relative_public_url(match: re.Match[str]) -> str:
    host = match.group('host').split(':', 1)[0].lower()
    internal_suffixes = ('.corp', '.home', '.internal', '.lan', '.local', '.localdomain')
    is_ipv4 = all(part.isdigit() for part in host.split('.'))
    if '.' not in host or host.endswith(internal_suffixes) or is_ipv4:
        return match.group(0)
    return ''


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _iter_strings(item)


def _require_unique(values: Sequence[str], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f'{label} must be unique')


def _require_sorted_unique(values: Sequence[str], label: str) -> None:
    if tuple(sorted(set(values))) != tuple(values):
        raise ValueError(f'{label} must be unique and sorted')


def _require_contiguous_token_ids(
    token_ids: Sequence[str],
    token_position: Mapping[str, int],
    label: str,
) -> None:
    positions = [token_position[token_id] for token_id in token_ids]
    if positions != list(range(positions[0], positions[-1] + 1)):
        raise ValueError(f'{label} token IDs must be ordered and contiguous')
