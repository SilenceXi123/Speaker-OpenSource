from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.services.subtitle_pipeline_v2_schema import (
    ImmutableMetadataEntry,
    RawAsrPreflightMetadata,
    RawAsrSegmentEvidence,
    RawAsrSnapshot,
    RawAsrToken,
    canonical_json,
    canonical_sha256,
)


RAW_ASR_SNAPSHOT_SCHEMA_VERSION = 'raw-asr-snapshot.v1'
_DROP = object()
_SENSITIVE_KEYS = {
    'accesskey',
    'accesskeyid',
    'accesskeysecret',
    'accesstoken',
    'apikey',
    'authorization',
    'credential',
    'credentials',
    'ossaccesskeyid',
    'password',
    'privatekey',
    'refreshtoken',
    'secret',
    'secretaccesskey',
    'securitytoken',
    'sessiontoken',
    'signature',
    'xosssignature',
}
_WINDOWS_ABSOLUTE_PATH = re.compile(r'^[A-Za-z]:[\\/]')
_PREFLIGHT_FIELDS = {'duration_ms', 'sample_rate_hz', 'channel_count', 'status'}
_CREDENTIAL_KEY_TERMINALS = {
    'credential',
    'credentials',
    'password',
    'secret',
    'signature',
    'token',
}


def build_raw_asr_snapshot(
    provider_payload: Mapping[str, Any],
    *,
    provider_model: str,
    provider_task_id: str,
    language: str,
    request_metadata: Mapping[str, Any],
    preflight_metadata: Mapping[str, Any],
) -> RawAsrSnapshot:
    safe_payload = _sanitize_mapping(provider_payload)
    safe_request = _sanitize_mapping(request_metadata)
    safe_preflight = _sanitize_mapping(preflight_metadata)
    preflight = RawAsrPreflightMetadata(
        duration_ms=safe_preflight.get('duration_ms'),
        sample_rate_hz=safe_preflight.get('sample_rate_hz'),
        channel_count=safe_preflight.get('channel_count'),
        status=safe_preflight.get('status'),
        extra=_metadata_entries({
            key: value
            for key, value in safe_preflight.items()
            if key not in _PREFLIGHT_FIELDS
        }),
    )

    tokens: list[RawAsrToken] = []
    segments: list[RawAsrSegmentEvidence] = []
    transcripts = provider_payload.get('transcripts') or []
    for transcript_index, transcript in enumerate(transcripts):
        if not isinstance(transcript, Mapping):
            continue
        for sentence_index, sentence in enumerate(transcript.get('sentences') or []):
            if not isinstance(sentence, Mapping):
                continue
            segment_id = f'segment-{len(segments):06d}'
            speaker_id = _optional_text(sentence.get('speaker_id'))
            speaker_key = _optional_text(sentence.get('speaker_key'))
            if speaker_key is None and speaker_id is not None:
                speaker_key = f'speaker_{speaker_id}'
            sentence_start = _optional_int(sentence.get('begin_time'))
            sentence_end = _optional_int(sentence.get('end_time'))
            sentence_span = _valid_span(sentence_start, sentence_end)
            token_ids: list[str] = []
            words = sentence.get('words') or []

            if isinstance(words, Sequence) and not isinstance(words, (str, bytes)) and words:
                for word_index, word in enumerate(words):
                    if not isinstance(word, Mapping):
                        continue
                    word_start = _optional_int(word.get('begin_time'))
                    word_end = _optional_int(word.get('end_time'))
                    if _valid_span(word_start, word_end):
                        start_ms, end_ms, precision = word_start, word_end, 'word'
                    elif sentence_span:
                        start_ms, end_ms, precision = sentence_start, sentence_end, 'coarse_shared_span'
                    else:
                        start_ms, end_ms, precision = None, None, 'missing'
                    token = RawAsrToken(
                        token_id=f'token-{len(tokens):06d}',
                        text=str(word.get('text') or ''),
                        punctuation=str(word.get('punctuation') or ''),
                        start_ms=start_ms,
                        end_ms=end_ms,
                        timing_precision=precision,
                        segment_id=segment_id,
                        provider_word_index=word_index,
                        speaker_id=speaker_id,
                        speaker_key=speaker_key,
                    )
                    tokens.append(token)
                    token_ids.append(token.token_id)
            elif str(sentence.get('text') or ''):
                start_ms, end_ms, precision = (
                    (sentence_start, sentence_end, 'coarse_shared_span')
                    if sentence_span
                    else (None, None, 'missing')
                )
                token = RawAsrToken(
                    token_id=f'token-{len(tokens):06d}',
                    text=str(sentence.get('text') or ''),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    timing_precision=precision,
                    segment_id=segment_id,
                    speaker_id=speaker_id,
                    speaker_key=speaker_key,
                )
                tokens.append(token)
                token_ids.append(token.token_id)

            segments.append(
                RawAsrSegmentEvidence(
                    segment_id=segment_id,
                    transcript_index=transcript_index,
                    sentence_index=sentence_index,
                    provider_segment_id=_optional_text(sentence.get('segment_id')),
                    provider_sentence_id=_optional_text(sentence.get('sentence_id')),
                    speaker_id=speaker_id,
                    speaker_key=speaker_key,
                    start_ms=sentence_start,
                    end_ms=sentence_end,
                    text=str(sentence.get('text') or ''),
                    token_ids=tuple(token_ids),
                )
            )

    content = {
        'schema_version': RAW_ASR_SNAPSHOT_SCHEMA_VERSION,
        'provider_model': provider_model,
        'provider_task_id': provider_task_id,
        'language': language,
        'request_metadata': [entry.model_dump(mode='json') for entry in _metadata_entries(safe_request)],
        'provider_payload_hash': canonical_sha256(safe_payload),
        'preflight_metadata': preflight.model_dump(mode='json'),
        'tokens': [token.model_dump(mode='json') for token in tokens],
        'segments': [segment.model_dump(mode='json') for segment in segments],
    }
    return RawAsrSnapshot(**content, snapshot_hash=canonical_sha256(content))


def _metadata_entries(metadata: Mapping[str, Any]) -> tuple[ImmutableMetadataEntry, ...]:
    return tuple(
        ImmutableMetadataEntry(key=key, value_json=canonical_json(metadata[key]))
        for key in sorted(metadata)
    )


def _sanitize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = _sanitize(value)
    if not isinstance(cleaned, dict):
        raise TypeError('metadata must be a JSON object')
    return cleaned


def _sanitize(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return _DROP
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for item_key, item_value in value.items():
            if not isinstance(item_key, str):
                raise TypeError('JSON object keys must be strings')
            cleaned = _sanitize(item_value, key=item_key)
            if cleaned is not _DROP:
                result[item_key] = cleaned
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [cleaned for item in value if (cleaned := _sanitize(item)) is not _DROP]
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if isinstance(value, str):
        if value.startswith(('/', '\\')) or _WINDOWS_ABSOLUTE_PATH.match(value):
            return _DROP
        parsed = urlsplit(value)
        if parsed.scheme == 'file':
            return _DROP
        if parsed.scheme in {'http', 'https'}:
            if parsed.username is not None or parsed.password is not None:
                return _DROP
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError('metadata must contain finite JSON numbers')
        return value
    raise TypeError(f'metadata contains unsupported non-JSON type: {type(value).__name__}')


def _normalized_key(value: str) -> str:
    return re.sub(r'[^a-z0-9]', '', value.lower())


def _is_sensitive_key(value: str) -> bool:
    if _normalized_key(value) in _SENSITIVE_KEYS:
        return True
    separated = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', value)
    parts = [part for part in re.split(r'[^A-Za-z0-9]+', separated.lower()) if part]
    return bool(parts and parts[-1] in _CREDENTIAL_KEY_TERMINALS)


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _valid_span(start_ms: int | None, end_ms: int | None) -> bool:
    return start_ms is not None and end_ms is not None and end_ms >= start_ms
