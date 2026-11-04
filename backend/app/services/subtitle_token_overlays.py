from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.chinese_text import normalize_to_simplified_chinese
from app.services.script_correction_candidates import CorrectionCandidate
from app.services.subtitle_linguistic_spans import numeric_fact_signature
from app.services.subtitle_pipeline_v2_schema import (
    RawAsrSnapshot,
    RawAsrToken,
    canonical_sha256,
)


FillerMaskReason = Literal['standalone_filler', 'discourse_filler']

_LOWER_HEX_SHA256 = r'^[0-9a-f]{64}$'
_MATCH_TEXT_RE = re.compile(r'[^\u3400-\u9fffA-Za-z0-9]+')
_FILLER_TEXTS = frozenset({'嗯', '啊', '哦', '哎', '哇', '呢', '嘛', '呃'})
_DISCOURSE_FILLER_TEXTS = _FILLER_TEXTS - {'呢', '嘛'}
_DISCOURSE_PAUSE_PUNCTUATION = frozenset('，,、')
_REPEATED_PUNCTUATION_RE = re.compile(r'([，,。！？!?；;：:、])\1+')
_CORRECTION_LEFT_BINDERS = frozenset({'第', '多', '非', '无', '高', '低', '超'})
_CORRECTION_RIGHT_BINDERS = frozenset({
    '村',
    '镇',
    '乡',
    '县',
    '区',
    '市',
    '省',
    '州',
    '路',
})


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


@dataclass(frozen=True)
class CompactedCorrectionSpan:
    local_start: int
    local_end: int
    token_ids: tuple[str, ...]
    source_text: str
    replacement_text: str


class AuthorizedTextOverlay(_FrozenStrictModel):
    overlay_id: str = Field(min_length=1)
    raw_snapshot_hash: str = Field(pattern=_LOWER_HEX_SHA256)
    candidate_id: str = Field(min_length=1)
    token_start: int = Field(ge=0)
    token_end: int = Field(gt=0)
    token_ids: tuple[str, ...] = Field(min_length=1)
    source_text: str = Field(min_length=1)
    replacement_text: str = Field(min_length=1)
    speaker_id: str | None = None
    speaker_key: str | None = None

    @model_validator(mode='after')
    def validate_content(self) -> 'AuthorizedTextOverlay':
        if len(self.token_ids) != self.token_end - self.token_start:
            raise ValueError('overlay token IDs must cover its half-open token range')
        if len(set(self.token_ids)) != len(self.token_ids):
            raise ValueError('overlay token IDs must be unique')
        if self.overlay_id != _overlay_id(
            raw_snapshot_hash=self.raw_snapshot_hash,
            candidate_id=self.candidate_id,
            token_start=self.token_start,
            token_end=self.token_end,
            token_ids=self.token_ids,
            source_text=self.source_text,
            replacement_text=self.replacement_text,
            speaker_id=self.speaker_id,
            speaker_key=self.speaker_key,
        ):
            raise ValueError('overlay_id does not match authorized overlay content')
        return self


class FillerTokenMask(_FrozenStrictModel):
    mask_id: str = Field(min_length=1)
    raw_snapshot_hash: str = Field(pattern=_LOWER_HEX_SHA256)
    token_index: int = Field(ge=0)
    token_id: str = Field(min_length=1)
    source_text: str = Field(min_length=1)
    reason: FillerMaskReason
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    reversible: Literal[True] = True

    @model_validator(mode='after')
    def validate_content(self) -> 'FillerTokenMask':
        if tuple(sorted(set(self.evidence_refs))) != self.evidence_refs:
            raise ValueError('mask evidence refs must be unique and sorted')
        if any(not ref for ref in self.evidence_refs):
            raise ValueError('mask evidence refs must not be empty')
        if self.mask_id != _mask_id(
            raw_snapshot_hash=self.raw_snapshot_hash,
            token_index=self.token_index,
            token_id=self.token_id,
            source_text=self.source_text,
            reason=self.reason,
            evidence_refs=self.evidence_refs,
        ):
            raise ValueError('mask_id does not match filler mask content')
        return self


def apply_authorized_overlays(
    *,
    raw_snapshot: RawAsrSnapshot,
    correction_candidates: Sequence[CorrectionCandidate],
    accepted_candidate_ids: Sequence[str],
) -> tuple[AuthorizedTextOverlay, ...]:
    candidates_by_id: dict[str, CorrectionCandidate] = {}
    for candidate in correction_candidates:
        if candidate.candidate_id in candidates_by_id:
            raise ValueError('correction candidate IDs must be unique')
        candidates_by_id[candidate.candidate_id] = candidate

    accepted_ids = tuple(accepted_candidate_ids)
    if len(set(accepted_ids)) != len(accepted_ids):
        raise ValueError('accepted correction candidate IDs must be unique')
    unknown_ids = [item for item in accepted_ids if item not in candidates_by_id]
    if unknown_ids:
        raise ValueError(f'unknown correction candidate: {unknown_ids[0]}')

    token_index = {token.token_id: index for index, token in enumerate(raw_snapshot.tokens)}
    overlays: list[AuthorizedTextOverlay] = []
    occupied: set[int] = set()
    selected = sorted(
        (candidates_by_id[item] for item in accepted_ids),
        key=lambda candidate: token_index.get(candidate.token_ids[0], len(token_index)),
    )
    for candidate in selected:
        indexes = _candidate_token_indexes(candidate, raw_snapshot, token_index)
        compacted_spans = compact_correction_token_spans(candidate, raw_snapshot.tokens)
        if occupied.intersection(indexes):
            raise ValueError('authorized correction overlays must not overlap')
        occupied.update(indexes)
        candidate_tokens = raw_snapshot.tokens[indexes[0]:indexes[-1] + 1]
        speaker_evidence = {(token.speaker_id, token.speaker_key) for token in candidate_tokens}
        if len(speaker_evidence) != 1:
            raise ValueError('authorized correction overlay crosses a speaker boundary')
        source_text = ''.join(token.text for token in candidate_tokens)
        if _normalize_match_text(source_text) != _normalize_match_text(candidate.source_text):
            raise ValueError('authorized correction source text disagrees with raw tokens')
        if numeric_fact_signature(source_text) != numeric_fact_signature(candidate.replacement_text):
            raise ValueError('numeric_fact_change: authorized correction changes a numeric fact')
        speaker_id, speaker_key = next(iter(speaker_evidence))
        for compacted in compacted_spans:
            values = {
                'raw_snapshot_hash': raw_snapshot.snapshot_hash,
                'candidate_id': candidate.candidate_id,
                'token_start': compacted.local_start,
                'token_end': compacted.local_end,
                'token_ids': compacted.token_ids,
                'source_text': compacted.source_text,
                'replacement_text': compacted.replacement_text,
                'speaker_id': speaker_id,
                'speaker_key': speaker_key,
            }
            overlays.append(AuthorizedTextOverlay(
                overlay_id=_overlay_id(**values),
                **values,
            ))
    return tuple(overlays)


def compact_correction_token_spans(
    candidate: CorrectionCandidate,
    tokens: Sequence[RawAsrToken],
) -> tuple[CompactedCorrectionSpan, ...]:
    """Keep contextual evidence outside the indivisible correction overlay.

    Script matching may need a long source span to prove a correction, but only
    raw tokens that actually change should become hard subtitle atoms. Separate
    edits remain separate when unchanged raw tokens lie between them, allowing
    the cue planner to use those internal semantic boundaries.
    """
    token_index = {token.token_id: index for index, token in enumerate(tokens)}
    if any(token_id not in token_index for token_id in candidate.token_ids):
        raise ValueError('authorized correction references an unknown raw token')
    indexes = [token_index[token_id] for token_id in candidate.token_ids]
    if indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise ValueError('authorized correction token range must be contiguous')
    expected_ids = tuple(tokens[index].token_id for index in indexes)
    if candidate.token_ids != expected_ids:
        raise ValueError('authorized correction token order disagrees with raw tokens')

    candidate_tokens = tuple(tokens[index] for index in indexes)
    token_match_parts = tuple(
        _normalize_match_text(_token_display_text(token))
        for token in candidate_tokens
    )
    source_match = _normalize_match_text(candidate.source_text)
    replacement_match = _normalize_match_text(candidate.replacement_text)
    if ''.join(token_match_parts) != source_match:
        raise ValueError('authorized correction source text disagrees with raw tokens')

    token_ranges: list[tuple[int, int]] = []
    cursor = 0
    for part in token_match_parts:
        token_ranges.append((cursor, cursor + len(part)))
        cursor += len(part)
    opcodes = SequenceMatcher(
        None,
        source_match,
        replacement_match,
        autojunk=False,
    ).get_opcodes()
    raw_fragments: list[tuple[int, int, int, int]] = []
    safe_to_fragment = True
    for tag, source_start, source_end, replacement_start, replacement_end in opcodes:
        if tag == 'equal':
            continue
        if source_start == source_end or replacement_start == replacement_end:
            safe_to_fragment = False
            break
        changed_tokens = [
            index
            for index, (token_start, token_end) in enumerate(token_ranges)
            if token_end > source_start and token_start < source_end
        ]
        if not changed_tokens:
            safe_to_fragment = False
            break
        local_start = changed_tokens[0]
        local_end = changed_tokens[-1] + 1
        if (
            local_start > 0
            and not candidate_tokens[local_start - 1].punctuation
            and token_match_parts[local_start - 1] in _CORRECTION_LEFT_BINDERS
        ):
            local_start -= 1
        if (
            local_end < len(candidate_tokens)
            and not candidate_tokens[local_end - 1].punctuation
            and token_match_parts[local_end] in _CORRECTION_RIGHT_BINDERS
        ):
            local_end += 1
        expanded_source_start = token_ranges[local_start][0]
        expanded_source_end = token_ranges[local_end - 1][1]
        expanded_replacement_start = _replacement_offset_for_source_boundary(
            opcodes,
            expanded_source_start,
            side='left',
        )
        expanded_replacement_end = _replacement_offset_for_source_boundary(
            opcodes,
            expanded_source_end,
            side='right',
        )
        if (
            expanded_replacement_start is None
            or expanded_replacement_end is None
            or expanded_replacement_end <= expanded_replacement_start
        ):
            safe_to_fragment = False
            break
        raw_fragments.append((
            local_start,
            local_end,
            expanded_replacement_start,
            expanded_replacement_end,
        ))

    if safe_to_fragment and raw_fragments:
        merged: list[list[int]] = []
        for fragment in sorted(raw_fragments):
            if merged and fragment[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], fragment[1])
                merged[-1][2] = min(merged[-1][2], fragment[2])
                merged[-1][3] = max(merged[-1][3], fragment[3])
            else:
                merged.append(list(fragment))
        compacted = tuple(
            _compacted_span(
                candidate,
                candidate_tokens,
                indexes[0],
                local_start,
                local_end,
                replacement_start,
                replacement_end,
            )
            for local_start, local_end, replacement_start, replacement_end in merged
        )
        if all(
            item.source_text
            and item.replacement_text
            and _normalize_match_text(item.source_text)
            != _normalize_match_text(item.replacement_text)
            for item in compacted
        ):
            return compacted

    prefix_chars = _common_prefix_length(source_match, replacement_match)
    suffix_chars = _common_suffix_length(
        source_match,
        replacement_match,
        prefix_chars,
    )
    local_start = 0
    trimmed_prefix_chars = 0
    while local_start + 1 < len(candidate_tokens):
        part_length = len(token_match_parts[local_start])
        if trimmed_prefix_chars + part_length > prefix_chars:
            break
        trimmed_prefix_chars += part_length
        local_start += 1

    local_end = len(candidate_tokens)
    trimmed_suffix_chars = 0
    while local_end - 1 > local_start:
        part_length = len(token_match_parts[local_end - 1])
        boundary_token = candidate_tokens[local_end - 2]
        if (
            trimmed_suffix_chars + part_length > suffix_chars
            or boundary_token.punctuation
        ):
            break
        trimmed_suffix_chars += part_length
        local_end -= 1

    replacement_end = len(replacement_match) - trimmed_suffix_chars
    replacement_text = _slice_by_match_offsets(
        candidate.replacement_text,
        trimmed_prefix_chars,
        replacement_end,
    )
    selected = candidate_tokens[local_start:local_end]
    source_text = ''.join(token.text for token in selected)
    if (
        not source_text
        or not replacement_text
        or _normalize_match_text(source_text) == _normalize_match_text(replacement_text)
    ):
        local_start = 0
        local_end = len(candidate_tokens)
        selected = candidate_tokens
        source_text = candidate.source_text
        replacement_text = candidate.replacement_text

    return (CompactedCorrectionSpan(
        local_start=indexes[0] + local_start,
        local_end=indexes[0] + local_end,
        token_ids=tuple(token.token_id for token in selected),
        source_text=source_text,
        replacement_text=replacement_text,
    ),)


def _compacted_span(
    candidate: CorrectionCandidate,
    candidate_tokens: Sequence[RawAsrToken],
    global_start: int,
    local_start: int,
    local_end: int,
    replacement_start: int,
    replacement_end: int,
) -> CompactedCorrectionSpan:
    selected = tuple(candidate_tokens[local_start:local_end])
    return CompactedCorrectionSpan(
        local_start=global_start + local_start,
        local_end=global_start + local_end,
        token_ids=tuple(token.token_id for token in selected),
        source_text=''.join(token.text for token in selected),
        replacement_text=_slice_by_match_offsets(
            candidate.replacement_text,
            replacement_start,
            replacement_end,
        ),
    )


def _replacement_offset_for_source_boundary(
    opcodes: Sequence[tuple[str, int, int, int, int]],
    boundary: int,
    *,
    side: Literal['left', 'right'],
) -> int | None:
    offsets: list[int] = []
    for tag, source_start, source_end, replacement_start, replacement_end in opcodes:
        if tag == 'equal' and source_start <= boundary <= source_end:
            offsets.append(replacement_start + boundary - source_start)
        if boundary == source_start:
            offsets.append(replacement_start)
        if boundary == source_end:
            offsets.append(replacement_end)
    if not offsets:
        return None
    return min(offsets) if side == 'left' else max(offsets)


def build_filler_masks(
    *,
    raw_snapshot: RawAsrSnapshot,
) -> tuple[FillerTokenMask, ...]:
    segment_by_id = {segment.segment_id: segment for segment in raw_snapshot.segments}
    token_by_id = {token.token_id: token for token in raw_snapshot.tokens}
    masks: list[FillerTokenMask] = []
    for token_index, token in enumerate(raw_snapshot.tokens):
        filler_text = normalize_to_simplified_chinese(token.text).strip()
        if filler_text not in _FILLER_TEXTS:
            continue
        segment = segment_by_id[token.segment_id]
        if segment.token_ids == (token.token_id,):
            reason: FillerMaskReason = 'standalone_filler'
            evidence_refs = (f'segment_singleton:{segment.segment_id}',)
        elif _is_explicit_discourse_filler(token, segment.token_ids, token_by_id):
            reason = 'discourse_filler'
            evidence_refs = tuple(sorted((
                f'pause_punctuation:{token.punctuation}',
                f'segment_start:{segment.segment_id}',
            )))
        else:
            continue
        source_text = _token_display_text(token)
        values = {
            'raw_snapshot_hash': raw_snapshot.snapshot_hash,
            'token_index': token_index,
            'token_id': token.token_id,
            'source_text': source_text,
            'reason': reason,
            'evidence_refs': evidence_refs,
        }
        masks.append(FillerTokenMask(mask_id=_mask_id(**values), **values))
    return tuple(masks)


def render_token_span_text(
    *,
    raw_snapshot: RawAsrSnapshot,
    token_start: int,
    token_end: int,
    overlays: Sequence[AuthorizedTextOverlay] = (),
    masks: Sequence[FillerTokenMask] = (),
) -> str:
    if token_start < 0 or token_end <= token_start or token_end > len(raw_snapshot.tokens):
        raise ValueError('render token range must be a non-empty raw token span')

    normalized_overlays = _validate_overlays(raw_snapshot, overlays)
    normalized_masks = _validate_masks(raw_snapshot, masks)
    masked_indexes = {mask.token_index for mask in normalized_masks}
    overlay_indexes = {
        index
        for overlay in normalized_overlays
        for index in range(overlay.token_start, overlay.token_end)
    }
    if masked_indexes.intersection(overlay_indexes):
        raise ValueError('authorized overlays and filler masks must not overlap')

    overlay_by_start: dict[int, AuthorizedTextOverlay] = {}
    for overlay in normalized_overlays:
        intersects = overlay.token_start < token_end and overlay.token_end > token_start
        if intersects and (
            overlay.token_start < token_start or overlay.token_end > token_end
        ):
            raise ValueError('render token range cuts an authorized overlay')
        if token_start <= overlay.token_start and overlay.token_end <= token_end:
            overlay_by_start[overlay.token_start] = overlay

    parts: list[str] = []
    index = token_start
    while index < token_end:
        overlay = overlay_by_start.get(index)
        if overlay is not None:
            parts.append(overlay.replacement_text)
            parts.append(overlay_punctuation(
                raw_snapshot.tokens[overlay.token_start:overlay.token_end]
            ))
            index = overlay.token_end
            continue
        if index not in masked_indexes:
            parts.append(_token_display_text(raw_snapshot.tokens[index]))
        index += 1
    return normalize_to_simplified_chinese(''.join(parts))


def overlay_punctuation(tokens: Sequence[RawAsrToken]) -> str:
    punctuation = ''.join(token.punctuation for token in tokens)
    return _REPEATED_PUNCTUATION_RE.sub(r'\1', punctuation)


def _candidate_token_indexes(
    candidate: CorrectionCandidate,
    raw_snapshot: RawAsrSnapshot,
    token_index: dict[str, int],
) -> list[int]:
    if any(token_id not in token_index for token_id in candidate.token_ids):
        raise ValueError('authorized correction references an unknown raw token')
    indexes = [token_index[token_id] for token_id in candidate.token_ids]
    if indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise ValueError('authorized correction token range must be contiguous')
    expected_ids = tuple(raw_snapshot.tokens[index].token_id for index in indexes)
    if candidate.token_ids != expected_ids:
        raise ValueError('authorized correction token order disagrees with raw tokens')
    return indexes


def _validate_overlays(
    raw_snapshot: RawAsrSnapshot,
    overlays: Sequence[AuthorizedTextOverlay],
) -> tuple[AuthorizedTextOverlay, ...]:
    result: list[AuthorizedTextOverlay] = []
    occupied: set[int] = set()
    seen_ids: set[str] = set()
    for item in sorted(overlays, key=lambda value: (value.token_start, value.token_end)):
        overlay = AuthorizedTextOverlay.model_validate(item.model_dump(mode='json'))
        if overlay.overlay_id in seen_ids:
            raise ValueError('authorized overlay IDs must be unique')
        seen_ids.add(overlay.overlay_id)
        if overlay.raw_snapshot_hash != raw_snapshot.snapshot_hash:
            raise ValueError('authorized overlay references a different raw snapshot')
        if overlay.token_end > len(raw_snapshot.tokens):
            raise ValueError('authorized overlay token range exceeds the raw token tape')
        tokens = raw_snapshot.tokens[overlay.token_start:overlay.token_end]
        if overlay.token_ids != tuple(token.token_id for token in tokens):
            raise ValueError('authorized overlay token range disagrees with raw tokens')
        if {(token.speaker_id, token.speaker_key) for token in tokens} != {
            (overlay.speaker_id, overlay.speaker_key)
        }:
            raise ValueError('authorized overlay speaker evidence disagrees with raw tokens')
        if _normalize_match_text(''.join(token.text for token in tokens)) != (
            _normalize_match_text(overlay.source_text)
        ):
            raise ValueError('authorized overlay source text disagrees with raw tokens')
        indexes = set(range(overlay.token_start, overlay.token_end))
        if occupied.intersection(indexes):
            raise ValueError('authorized correction overlays must not overlap')
        occupied.update(indexes)
        result.append(overlay)
    return tuple(result)


def _validate_masks(
    raw_snapshot: RawAsrSnapshot,
    masks: Sequence[FillerTokenMask],
) -> tuple[FillerTokenMask, ...]:
    result: list[FillerTokenMask] = []
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    authorized_by_id = {
        mask.mask_id: mask
        for mask in build_filler_masks(raw_snapshot=raw_snapshot)
    }
    for item in sorted(masks, key=lambda value: value.token_index):
        mask = FillerTokenMask.model_validate(item.model_dump(mode='json'))
        if mask.mask_id in seen_ids or mask.token_index in seen_indexes:
            raise ValueError('filler masks must reference unique tokens')
        seen_ids.add(mask.mask_id)
        seen_indexes.add(mask.token_index)
        if mask.raw_snapshot_hash != raw_snapshot.snapshot_hash:
            raise ValueError('filler mask references a different raw snapshot')
        if mask.token_index >= len(raw_snapshot.tokens):
            raise ValueError('filler mask token index exceeds the raw token tape')
        token = raw_snapshot.tokens[mask.token_index]
        if mask.token_id != token.token_id or mask.source_text != _token_display_text(token):
            raise ValueError('filler mask token evidence disagrees with raw tokens')
        if authorized_by_id.get(mask.mask_id) != mask:
            raise ValueError('filler mask is not authorized by raw filler evidence')
        result.append(mask)
    return tuple(result)


def _is_explicit_discourse_filler(
    token: RawAsrToken,
    segment_token_ids: tuple[str, ...],
    token_by_id: dict[str, RawAsrToken],
) -> bool:
    filler_text = normalize_to_simplified_chinese(token.text).strip()
    if filler_text not in _DISCOURSE_FILLER_TEXTS:
        return False
    if segment_token_ids[0] != token.token_id:
        return False
    if not any(char in _DISCOURSE_PAUSE_PUNCTUATION for char in token.punctuation):
        return False
    next_token = token_by_id[segment_token_ids[1]]
    return (token.speaker_id, token.speaker_key) == (
        next_token.speaker_id,
        next_token.speaker_key,
    )


def _overlay_id(**content: object) -> str:
    return f'overlay-{canonical_sha256(content)}'


def _mask_id(**content: object) -> str:
    return f'mask-{canonical_sha256(content)}'


def _token_display_text(token: RawAsrToken) -> str:
    return token.text + token.punctuation


def _normalize_match_text(value: str) -> str:
    simplified = normalize_to_simplified_chinese(str(value or ''))
    return _MATCH_TEXT_RE.sub('', simplified).lower()


def _common_prefix_length(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def _common_suffix_length(left: str, right: str, prefix_length: int) -> int:
    limit = min(len(left), len(right)) - prefix_length
    length = 0
    while length < limit and left[-length - 1] == right[-length - 1]:
        length += 1
    return length


def _slice_by_match_offsets(value: str, start: int, end: int) -> str:
    if start < 0 or end < start:
        raise ValueError('normalized text slice is invalid')
    positions: list[tuple[int, int]] = []
    for index, char in enumerate(str(value or '')):
        normalized = _normalize_match_text(char)
        positions.extend((index, index + 1) for _ in normalized)
    if end > len(positions):
        raise ValueError('normalized text slice exceeds replacement text')
    if start == end:
        return ''
    return str(value or '')[positions[start][0]:positions[end - 1][1]]
