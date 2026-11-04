from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TimingPrecision = Literal['word', 'coarse_shared_span', 'missing']
SemanticBoundaryReason = Literal[
    'tape_start',
    'speaker_change',
    'speaker_evidence_transition',
    'missing_time_segment_boundary',
    'time_discontinuity',
    'long_pause',
    'terminal_punctuation_pause',
    'max_context',
]
_LOWER_HEX_SHA256 = r'^[0-9a-f]{64}$'


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class ImmutableMetadataEntry(_FrozenStrictModel):
    key: str = Field(min_length=1)
    value_json: str

    @field_validator('value_json')
    @classmethod
    def require_canonical_json(cls, value: str) -> str:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError('metadata value_json must be valid JSON') from exc
        if canonical_json(decoded) != value:
            raise ValueError('metadata value_json must be canonical JSON')
        return value


class SemanticUtteranceProfile(_FrozenStrictModel):
    profile_version: str = Field(min_length=1)
    max_continuation_gap_ms: int = Field(gt=0)
    terminal_pause_ms: int = Field(ge=0)
    max_display_chars: int = Field(gt=0)

    @model_validator(mode='after')
    def validate_pause_thresholds(self) -> 'SemanticUtteranceProfile':
        if self.terminal_pause_ms > self.max_continuation_gap_ms:
            raise ValueError('terminal pause must not exceed the continuation gap')
        return self


class SemanticUtteranceBoundary(_FrozenStrictModel):
    reason: SemanticBoundaryReason
    left_token_id: str | None = None
    right_token_id: str = Field(min_length=1)
    gap_ms: int | None = None

    @model_validator(mode='after')
    def validate_tape_start(self) -> 'SemanticUtteranceBoundary':
        if self.reason == 'tape_start':
            if self.left_token_id is not None or self.gap_ms is not None:
                raise ValueError('tape_start must not have left-token or gap evidence')
        elif self.left_token_id is None:
            raise ValueError('non-initial boundaries require left_token_id evidence')
        return self


class SemanticUtterance(_FrozenStrictModel):
    utterance_id: str = Field(min_length=1)
    token_ids: tuple[str, ...] = Field(min_length=1)
    source_segment_ids: tuple[str, ...] = Field(min_length=1)
    speaker_id: str | None = None
    speaker_key: str | None = None
    start_boundary: SemanticUtteranceBoundary

    @model_validator(mode='after')
    def validate_references(self) -> 'SemanticUtterance':
        if len(set(self.token_ids)) != len(self.token_ids):
            raise ValueError('semantic utterance contains duplicate token references')
        if len(set(self.source_segment_ids)) != len(self.source_segment_ids):
            raise ValueError('semantic utterance contains duplicate segment references')
        if self.start_boundary.right_token_id != self.token_ids[0]:
            raise ValueError('semantic utterance boundary must reference its first token')
        return self


class SemanticUtteranceArtifact(_FrozenStrictModel):
    schema_version: str = Field(min_length=1)
    raw_snapshot_hash: str = Field(pattern=_LOWER_HEX_SHA256)
    profile: SemanticUtteranceProfile
    utterances: tuple[SemanticUtterance, ...]
    artifact_hash: str = Field(pattern=_LOWER_HEX_SHA256)

    @model_validator(mode='after')
    def validate_content_and_hash(self) -> 'SemanticUtteranceArtifact':
        utterance_ids = [item.utterance_id for item in self.utterances]
        if len(set(utterance_ids)) != len(utterance_ids):
            raise ValueError('semantic artifact contains duplicate utterance IDs')

        token_ids = [
            token_id
            for utterance in self.utterances
            for token_id in utterance.token_ids
        ]
        if len(set(token_ids)) != len(token_ids):
            raise ValueError('semantic artifact contains duplicate token references')

        for index, utterance in enumerate(self.utterances):
            boundary = utterance.start_boundary
            if index == 0:
                if boundary.reason != 'tape_start':
                    raise ValueError('first semantic utterance must start at tape_start')
                continue
            if boundary.reason == 'tape_start':
                raise ValueError('only the first semantic utterance may use tape_start')
            if boundary.left_token_id != self.utterances[index - 1].token_ids[-1]:
                raise ValueError('semantic boundary must reference the preceding token')

        content = self.model_dump(mode='json', exclude={'artifact_hash'})
        if canonical_sha256(content) != self.artifact_hash:
            raise ValueError('artifact_hash does not match semantic artifact content')
        return self


class RawAsrPreflightMetadata(_FrozenStrictModel):
    duration_ms: int = Field(ge=0)
    sample_rate_hz: int = Field(gt=0)
    channel_count: int = Field(gt=0)
    status: str = Field(min_length=1)
    extra: tuple[ImmutableMetadataEntry, ...]

    @model_validator(mode='after')
    def require_sorted_unique_extra(self) -> 'RawAsrPreflightMetadata':
        _require_sorted_unique_entries(self.extra)
        return self

    def extra_dict(self) -> dict[str, Any]:
        return {entry.key: json.loads(entry.value_json) for entry in self.extra}


class RawAsrToken(_FrozenStrictModel):
    token_id: str = Field(min_length=1)
    text: str
    punctuation: str = ''
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    timing_precision: TimingPrecision
    segment_id: str = Field(min_length=1)
    provider_word_index: int | None = Field(default=None, ge=0)
    speaker_id: str | None = None
    speaker_key: str | None = None

    @model_validator(mode='after')
    def validate_timing_evidence(self) -> 'RawAsrToken':
        if self.timing_precision == 'missing':
            if self.start_ms is not None or self.end_ms is not None:
                raise ValueError('missing timing must not contain start_ms or end_ms')
            return self
        if self.start_ms is None or self.end_ms is None or self.end_ms < self.start_ms:
            raise ValueError(f'{self.timing_precision} timing requires a valid start/end span')
        return self


class RawAsrSegmentEvidence(_FrozenStrictModel):
    segment_id: str = Field(min_length=1)
    transcript_index: int = Field(ge=0)
    sentence_index: int = Field(ge=0)
    provider_segment_id: str | None = None
    provider_sentence_id: str | None = None
    speaker_id: str | None = None
    speaker_key: str | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    text: str
    token_ids: tuple[str, ...]

    @model_validator(mode='after')
    def validate_timing_evidence(self) -> 'RawAsrSegmentEvidence':
        if self.start_ms is None and self.end_ms is None:
            return self
        if self.start_ms is None or self.end_ms is None or self.end_ms < self.start_ms:
            raise ValueError('segment timing must be absent as a pair or contain a valid start/end span')
        return self


class RawAsrSnapshot(_FrozenStrictModel):
    schema_version: str = Field(min_length=1)
    provider_model: str = Field(min_length=1)
    provider_task_id: str = Field(min_length=1)
    language: str = Field(min_length=1)
    request_metadata: tuple[ImmutableMetadataEntry, ...]
    provider_payload_hash: str = Field(pattern=_LOWER_HEX_SHA256)
    preflight_metadata: RawAsrPreflightMetadata
    tokens: tuple[RawAsrToken, ...]
    segments: tuple[RawAsrSegmentEvidence, ...]
    snapshot_hash: str = Field(pattern=_LOWER_HEX_SHA256)

    @model_validator(mode='after')
    def validate_content_hash_and_metadata(self) -> 'RawAsrSnapshot':
        _require_sorted_unique_entries(self.request_metadata)
        self._validate_evidence_graph()
        content = self.model_dump(mode='json', exclude={'snapshot_hash'})
        if canonical_sha256(content) != self.snapshot_hash:
            raise ValueError('snapshot_hash does not match snapshot content')
        return self

    def request_metadata_dict(self) -> dict[str, Any]:
        return {entry.key: json.loads(entry.value_json) for entry in self.request_metadata}

    def _validate_evidence_graph(self) -> None:
        token_by_id = {token.token_id: token for token in self.tokens}
        if len(token_by_id) != len(self.tokens):
            raise ValueError('evidence graph contains duplicate token_id values')

        segment_by_id = {segment.segment_id: segment for segment in self.segments}
        if len(segment_by_id) != len(self.segments):
            raise ValueError('evidence graph contains duplicate segment_id values')

        for token in self.tokens:
            segment = segment_by_id.get(token.segment_id)
            if segment is None:
                raise ValueError('evidence graph token references a missing segment')
            if token.token_id not in segment.token_ids:
                raise ValueError('evidence graph token is missing from its owning segment')
            if (
                token.speaker_id is not None
                and segment.speaker_id is not None
                and token.speaker_id != segment.speaker_id
            ):
                raise ValueError('evidence graph speaker_id evidence conflicts')
            if (
                token.speaker_key is not None
                and segment.speaker_key is not None
                and token.speaker_key != segment.speaker_key
            ):
                raise ValueError('evidence graph speaker_key evidence conflicts')

        for segment in self.segments:
            if len(set(segment.token_ids)) != len(segment.token_ids):
                raise ValueError('evidence graph contains duplicate segment token references')
            for token_id in segment.token_ids:
                token = token_by_id.get(token_id)
                if token is None:
                    raise ValueError('evidence graph segment references a missing token')
                if token.segment_id != segment.segment_id:
                    raise ValueError('evidence graph token and segment references disagree')


def _require_sorted_unique_entries(entries: tuple[ImmutableMetadataEntry, ...]) -> None:
    keys = [entry.key for entry in entries]
    if keys != sorted(set(keys)):
        raise ValueError('metadata entries must have unique keys in sorted order')
