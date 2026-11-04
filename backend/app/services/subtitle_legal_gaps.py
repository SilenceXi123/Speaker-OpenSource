from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.subtitle_atoms import AtomProtection, TokenBoundLexicalAtom
from app.services.subtitle_pipeline_v2_schema import (
    RawAsrSnapshot,
    RawAsrToken,
    SemanticUtteranceArtifact,
    canonical_sha256,
)


GapDisposition = Literal['required', 'allowed', 'forbidden']
GapTimeEvidence = Literal[
    'verified_acoustic_pause',
    'word_timing',
    'sortable_time',
    'coarse_shared_span',
    'time_overlap',
    'missing_time',
]
GapReason = Literal[
    'speaker_change',
    'speaker_evidence_transition',
    'hard_atom',
    'verified_acoustic_pause',
    'word_timing',
    'sortable_time',
    'coarse_shared_span',
    'time_overlap',
    'missing_time',
]


class SubtitleLegalGap(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    gap_id: str = Field(min_length=1)
    utterance_id: str = Field(min_length=1)
    left_token_id: str = Field(min_length=1)
    right_token_id: str = Field(min_length=1)
    left_token_index: int = Field(ge=0)
    right_token_index: int = Field(gt=0)
    disposition: GapDisposition
    legal_gap: bool
    reason: GapReason
    time_evidence: GapTimeEvidence
    pause_ms: int | None = Field(default=None, ge=0)
    atom_id: str | None = None
    atom_protection: AtomProtection | None = None
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode='after')
    def validate_gap_contract(self) -> 'SubtitleLegalGap':
        if self.right_token_index != self.left_token_index + 1:
            raise ValueError('subtitle gaps must bind adjacent token indexes')
        if (self.atom_id is None) != (self.atom_protection is None):
            raise ValueError('gap atom reference and protection must be present together')
        if tuple(sorted(set(self.evidence_refs))) != self.evidence_refs:
            raise ValueError('gap evidence refs must be unique and sorted')
        expected_disposition: GapDisposition
        if self.reason in {'speaker_change', 'speaker_evidence_transition'}:
            expected_disposition = 'required'
        elif self.reason == 'hard_atom':
            expected_disposition = 'forbidden'
        else:
            expected_disposition = 'allowed' if self.legal_gap else 'forbidden'
        if self.disposition != expected_disposition:
            raise ValueError('gap reason and disposition disagree')
        if self.reason == 'hard_atom' and self.atom_protection != 'hard':
            raise ValueError('hard-atom gaps require hard atom evidence')
        timing_is_legal = self.time_evidence in {
            'verified_acoustic_pause',
            'word_timing',
            'sortable_time',
        }
        if self.reason == 'hard_atom':
            if self.legal_gap:
                raise ValueError('hard-atom gaps must remain illegal')
        elif self.legal_gap != timing_is_legal:
            raise ValueError('legal_gap disagrees with its time evidence')
        if timing_is_legal and self.pause_ms is None:
            raise ValueError('legal time evidence requires pause duration evidence')
        if not timing_is_legal and self.pause_ms is not None:
            raise ValueError('illegal time evidence must not claim a pause duration')
        if self.reason not in {
            'speaker_change',
            'speaker_evidence_transition',
            'hard_atom',
        } and self.reason != self.time_evidence:
            raise ValueError('ordinary gap reason must match its time evidence')
        return self


def build_subtitle_legal_gaps(
    raw_snapshot: RawAsrSnapshot,
    semantic_artifact: SemanticUtteranceArtifact,
    *,
    atoms: Sequence[TokenBoundLexicalAtom],
    verified_pauses_ms: Mapping[tuple[str, str], int] | None = None,
) -> tuple[SubtitleLegalGap, ...]:
    _validate_inputs(raw_snapshot, semantic_artifact, atoms)
    verified_pauses = _validate_verified_pauses(raw_snapshot, verified_pauses_ms or {})
    utterance_by_token = {
        token_id: utterance
        for utterance in semantic_artifact.utterances
        for token_id in utterance.token_ids
    }
    atoms_by_boundary = _atoms_by_internal_boundary(atoms)
    gaps: list[SubtitleLegalGap] = []
    for right_index in range(1, len(raw_snapshot.tokens)):
        left_index = right_index - 1
        left = raw_snapshot.tokens[left_index]
        right = raw_snapshot.tokens[right_index]
        owner = utterance_by_token[right.token_id]
        crossing_atom = atoms_by_boundary.get(right_index)
        pair = (left.token_id, right.token_id)
        (
            disposition,
            legal_gap,
            reason,
            time_evidence,
            pause_ms,
            evidence_refs,
        ) = _classify_gap(
            left,
            right,
            crossing_atom,
            verified_pauses.get(pair),
        )
        identity = {
            'raw_snapshot_hash': raw_snapshot.snapshot_hash,
            'utterance_id': owner.utterance_id,
            'left_token_id': left.token_id,
            'right_token_id': right.token_id,
        }
        gaps.append(SubtitleLegalGap(
            gap_id=f'gap-{canonical_sha256(identity)}',
            utterance_id=owner.utterance_id,
            left_token_id=left.token_id,
            right_token_id=right.token_id,
            left_token_index=left_index,
            right_token_index=right_index,
            disposition=disposition,
            legal_gap=legal_gap,
            reason=reason,
            time_evidence=time_evidence,
            pause_ms=pause_ms,
            atom_id=crossing_atom.atom_id if crossing_atom is not None else None,
            atom_protection=(
                crossing_atom.protection if crossing_atom is not None else None
            ),
            evidence_refs=tuple(sorted(set(evidence_refs))),
        ))
    return tuple(gaps)


def _classify_gap(
    left: RawAsrToken,
    right: RawAsrToken,
    crossing_atom: TokenBoundLexicalAtom | None,
    verified_pause_ms: int | None,
) -> tuple[
    GapDisposition,
    bool,
    GapReason,
    GapTimeEvidence,
    int | None,
    tuple[str, ...],
]:
    timing_legal, time_evidence, pause_ms, timing_refs = _classify_time_evidence(
        left,
        right,
        verified_pause_ms,
    )
    left_speaker = _speaker_identity(left)
    right_speaker = _speaker_identity(right)
    if left_speaker != right_speaker:
        reason: GapReason = (
            'speaker_evidence_transition'
            if left_speaker is None or right_speaker is None
            else 'speaker_change'
        )
        return (
            'required',
            timing_legal,
            reason,
            time_evidence,
            pause_ms,
            timing_refs,
        )

    if crossing_atom is not None and crossing_atom.protection == 'hard':
        return (
            'forbidden',
            False,
            'hard_atom',
            time_evidence,
            pause_ms,
            timing_refs + (crossing_atom.atom_id,),
        )

    atom_refs = (crossing_atom.atom_id,) if crossing_atom is not None else ()
    disposition: GapDisposition = 'allowed' if timing_legal else 'forbidden'
    return (
        disposition,
        timing_legal,
        time_evidence,
        time_evidence,
        pause_ms,
        timing_refs + atom_refs,
    )


def _classify_time_evidence(
    left: RawAsrToken,
    right: RawAsrToken,
    verified_pause_ms: int | None,
) -> tuple[bool, GapTimeEvidence, int | None, tuple[str, ...]]:
    if (
        left.timing_precision == 'word'
        and right.timing_precision == 'word'
        and left.end_ms is not None
        and right.start_ms is not None
        and left.end_ms > right.start_ms
    ):
        return False, 'time_overlap', None, ()
    if verified_pause_ms is not None:
        return (
            True,
            'verified_acoustic_pause',
            verified_pause_ms,
            (f'verified-pause:{verified_pause_ms}ms',),
        )

    if (
        left.timing_precision == 'word'
        and right.timing_precision == 'word'
        and left.end_ms is not None
        and right.start_ms is not None
    ):
        return (
            True,
            'word_timing',
            right.start_ms - left.end_ms,
            (),
        )

    if (
        left.start_ms is None
        or left.end_ms is None
        or right.start_ms is None
        or right.end_ms is None
    ):
        return False, 'missing_time', None, ()

    if left.end_ms > right.start_ms:
        if (
            left.segment_id == right.segment_id
            and left.timing_precision == 'coarse_shared_span'
            and right.timing_precision == 'coarse_shared_span'
            and left.start_ms == right.start_ms
            and left.end_ms == right.end_ms
        ):
            return False, 'coarse_shared_span', None, ()
        return False, 'time_overlap', None, ()

    if left.segment_id == right.segment_id and (
        left.timing_precision != 'word' or right.timing_precision != 'word'
    ):
        return False, 'coarse_shared_span', None, ()

    return (
        True,
        'sortable_time',
        right.start_ms - left.end_ms,
        (),
    )


def _atoms_by_internal_boundary(
    atoms: Sequence[TokenBoundLexicalAtom],
) -> dict[int, TokenBoundLexicalAtom]:
    result: dict[int, TokenBoundLexicalAtom] = {}
    for atom in atoms:
        for boundary in range(atom.token_start + 1, atom.token_end):
            if boundary in result:
                raise ValueError('token atoms overlap at an internal boundary')
            result[boundary] = atom
    return result


def _validate_inputs(
    raw_snapshot: RawAsrSnapshot,
    semantic_artifact: SemanticUtteranceArtifact,
    atoms: Sequence[TokenBoundLexicalAtom],
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
    utterance_by_id = {item.utterance_id: item for item in semantic_artifact.utterances}
    covered: list[str] = []
    previous_end = 0
    for atom in atoms:
        if atom.token_start != previous_end:
            raise ValueError('token atoms must cover the raw token tape without gaps')
        if atom.token_end > len(raw_token_ids):
            raise ValueError('token atom range exceeds the raw token tape')
        expected_ids = tuple(raw_token_ids[atom.token_start:atom.token_end])
        if atom.token_ids != expected_ids:
            raise ValueError('token atom IDs disagree with its raw token range')
        utterance = utterance_by_id.get(atom.utterance_id)
        if utterance is None or any(
            token_id not in utterance.token_ids for token_id in atom.token_ids
        ):
            raise ValueError('token atom crosses its semantic utterance')
        covered.extend(atom.token_ids)
        previous_end = atom.token_end
    if covered != raw_token_ids:
        raise ValueError('token atoms must conserve the raw token tape')


def _validate_verified_pauses(
    raw_snapshot: RawAsrSnapshot,
    pauses: Mapping[tuple[str, str], int],
) -> dict[tuple[str, str], int]:
    adjacent_pairs = {
        (left.token_id, right.token_id)
        for left, right in zip(raw_snapshot.tokens, raw_snapshot.tokens[1:])
    }
    result: dict[tuple[str, str], int] = {}
    for pair, pause_ms in pauses.items():
        if pair not in adjacent_pairs:
            raise ValueError('verified pause must reference adjacent raw tokens')
        if not isinstance(pause_ms, int) or isinstance(pause_ms, bool) or pause_ms < 0:
            raise ValueError('verified pause must be a non-negative integer')
        result[pair] = pause_ms
    return result


def _speaker_identity(token: RawAsrToken) -> tuple[str | None, str | None] | None:
    if token.speaker_key is None and token.speaker_id is None:
        return None
    return token.speaker_key, token.speaker_id
