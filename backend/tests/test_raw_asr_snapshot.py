from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.subtitle_pipeline_v2_schema import (
    RawAsrSegmentEvidence,
    RawAsrSnapshot,
    RawAsrToken,
    canonical_sha256,
)


def _provider_payload() -> dict:
    return {
        'transcripts': [
            {
                'sentences': [
                    {
                        'segment_id': 'provider-segment-a',
                        'sentence_id': 17,
                        'speaker_id': 2,
                        'begin_time': 100,
                        'end_time': 900,
                        'text': '你好，世界。',
                        'words': [
                            {'begin_time': 100, 'end_time': 300, 'text': '你好', 'punctuation': '，'},
                            {'begin_time': 300, 'end_time': 900, 'text': '世界', 'punctuation': '。'},
                        ],
                    }
                ]
            }
        ]
    }


def _preflight_metadata() -> dict:
    return {
        'duration_ms': 900,
        'sample_rate_hz': 24000,
        'channel_count': 1,
        'status': 'accepted',
    }


def _build_snapshot(payload: dict | None = None, **overrides):
    arguments = {
        'provider_model': 'fun-asr',
        'provider_task_id': 'task-123',
        'language': 'zh',
        'request_metadata': {},
        'preflight_metadata': _preflight_metadata(),
    }
    arguments.update(overrides)
    return build_raw_asr_snapshot(payload or _provider_payload(), **arguments)


def test_snapshot_hash_tokens_and_evidence_are_stable() -> None:
    payload = _provider_payload()
    first = _build_snapshot(payload)
    second = _build_snapshot(
        deepcopy(payload),
        preflight_metadata=deepcopy(_preflight_metadata()),
    )

    assert first.snapshot_hash == second.snapshot_hash
    assert first.provider_payload_hash == second.provider_payload_hash
    assert [token.token_id for token in first.tokens] == ['token-000000', 'token-000001']
    assert [token.text for token in first.tokens] == ['你好', '世界']
    assert [token.speaker_key for token in first.tokens] == ['speaker_2', 'speaker_2']
    assert first.segments[0].provider_segment_id == 'provider-segment-a'
    assert first.segments[0].provider_sentence_id == '17'
    assert first.segments[0].speaker_id == '2'
    assert first.segments[0].token_ids == ('token-000000', 'token-000001')


def test_snapshot_preserves_real_coarse_and_missing_timing_without_character_averaging() -> None:
    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 100,
                        'end_time': 500,
                        'text': '真实时间',
                        'words': [
                            {'begin_time': 100, 'end_time': 250, 'text': '真实'},
                            {'begin_time': 250, 'end_time': 500, 'text': '时间'},
                        ],
                    },
                    {
                        'sentence_id': 2,
                        'speaker_id': 1,
                        'begin_time': 600,
                        'end_time': 1000,
                        'text': '共享粗时间',
                        'words': [{'text': '共享'}, {'text': '粗时间'}],
                    },
                    {
                        'sentence_id': 3,
                        'speaker_id': 1,
                        'text': '没有时间',
                        'words': [{'text': '没有'}, {'text': '时间'}],
                    },
                ]
            }
        ]
    }

    snapshot = _build_snapshot(payload)

    assert [(token.start_ms, token.end_ms, token.timing_precision) for token in snapshot.tokens] == [
        (100, 250, 'word'),
        (250, 500, 'word'),
        (600, 1000, 'coarse_shared_span'),
        (600, 1000, 'coarse_shared_span'),
        (None, None, 'missing'),
        (None, None, 'missing'),
    ]


def test_snapshot_schema_requires_version_and_forbids_extra_fields() -> None:
    snapshot = _build_snapshot()
    data = snapshot.model_dump(mode='json')

    without_version = dict(data)
    without_version.pop('schema_version')
    with pytest.raises(ValidationError):
        RawAsrSnapshot.model_validate(without_version)

    with_extra = dict(data)
    with_extra['unexpected'] = True
    with pytest.raises(ValidationError):
        RawAsrSnapshot.model_validate(with_extra)


def test_snapshot_excludes_credentials_signed_queries_and_local_paths() -> None:
    payload = _provider_payload()
    payload['api_key'] = 'secret-provider-key'
    payload['result_url'] = 'https://oss.example.com/result.json?OSSAccessKeyId=secret&Signature=signed'
    payload['debug'] = {'local_path': '/Users/example/source.wav'}
    preflight = {
        **_preflight_metadata(),
        'authorization': 'Bearer secret-token',
        'source_url': 'https://oss.example.com/source.wav?token=secret',
        'local_path': '/Volumes/production/source.wav',
    }

    snapshot = _build_snapshot(payload, preflight_metadata=preflight)
    serialized = snapshot.model_dump_json()

    assert 'secret-provider-key' not in serialized
    assert 'OSSAccessKeyId' not in serialized
    assert 'Signature' not in serialized
    assert 'secret-token' not in serialized
    assert '/Users/' not in serialized
    assert '/Volumes/' not in serialized
    assert snapshot.preflight_metadata.extra_dict() == {
        'source_url': 'https://oss.example.com/source.wav',
    }


def test_snapshot_carries_optional_provider_context_without_sensitive_request_metadata() -> None:
    snapshot = build_raw_asr_snapshot(
        _provider_payload(),
        provider_model='fun-asr',
        provider_task_id='task-123',
        language='zh',
        request_metadata={
            'speaker_count': 2,
            'result_url': 'https://oss.example.com/result.json?Signature=secret',
            'api_key': 'secret-key',
            'local_path': '/Users/example/input.wav',
        },
        preflight_metadata=_preflight_metadata(),
    )

    assert snapshot.provider_task_id == 'task-123'
    assert snapshot.language == 'zh'
    assert snapshot.request_metadata_dict() == {
        'speaker_count': 2,
        'result_url': 'https://oss.example.com/result.json',
    }


def test_snapshot_does_not_invent_speaker_zero_when_provider_evidence_is_missing() -> None:
    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'begin_time': 0,
                        'end_time': 500,
                        'text': '说话人未知',
                        'words': [{'begin_time': 0, 'end_time': 500, 'text': '说话人未知'}],
                    }
                ]
            }
        ]
    }

    snapshot = _build_snapshot(payload)

    assert snapshot.tokens[0].speaker_id is None
    assert snapshot.tokens[0].speaker_key is None
    assert snapshot.segments[0].speaker_id is None
    assert snapshot.segments[0].speaker_key is None


def test_snapshot_token_tape_is_not_truncated() -> None:
    words = [
        {'begin_time': index * 10, 'end_time': (index + 1) * 10, 'text': f'word-{index}'}
        for index in range(750)
    ]
    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 0,
                        'end_time': 7500,
                        'text': ''.join(word['text'] for word in words),
                        'words': words,
                    }
                ]
            }
        ]
    }

    snapshot = _build_snapshot(
        payload,
        preflight_metadata={**_preflight_metadata(), 'duration_ms': 7500},
    )

    assert len(snapshot.tokens) == 750
    assert snapshot.tokens[-1].token_id == 'token-000749'


def test_snapshot_metadata_is_deeply_immutable_and_hash_tampering_is_rejected() -> None:
    snapshot = _build_snapshot(
        request_metadata={'nested': {'items': [1, 2]}},
        preflight_metadata={**_preflight_metadata(), 'probe': {'codec': 'pcm_s16le'}},
    )

    with pytest.raises(TypeError):
        snapshot.request_metadata[0] = snapshot.request_metadata[0]
    with pytest.raises(ValidationError):
        snapshot.request_metadata[0].key = 'changed'

    tampered = snapshot.model_dump(mode='json')
    tampered['tokens'][0]['text'] = '被篡改'
    with pytest.raises(ValidationError, match='snapshot_hash'):
        RawAsrSnapshot.model_validate(tampered)

    uppercase_hash = snapshot.model_dump(mode='json')
    uppercase_hash['snapshot_hash'] = snapshot.snapshot_hash.upper()
    with pytest.raises(ValidationError):
        RawAsrSnapshot.model_validate(uppercase_hash)


@pytest.mark.parametrize(
    ('timing_precision', 'start_ms', 'end_ms'),
    [
        ('word', None, None),
        ('coarse_shared_span', 100, None),
        ('coarse_shared_span', 200, 100),
        ('missing', 100, 200),
    ],
)
def test_token_timing_precision_contract_is_enforced(
    timing_precision: str,
    start_ms: int | None,
    end_ms: int | None,
) -> None:
    with pytest.raises(ValidationError):
        RawAsrToken(
            token_id='token-000000',
            text='测试',
            start_ms=start_ms,
            end_ms=end_ms,
            timing_precision=timing_precision,
            segment_id='segment-000000',
        )


@pytest.mark.parametrize(
    ('start_ms', 'end_ms'),
    [(100, None), (None, 100), (200, 100)],
)
def test_segment_timing_must_be_missing_as_a_pair_or_valid(
    start_ms: int | None,
    end_ms: int | None,
) -> None:
    with pytest.raises(ValidationError):
        RawAsrSegmentEvidence(
            segment_id='segment-000000',
            transcript_index=0,
            sentence_index=0,
            start_ms=start_ms,
            end_ms=end_ms,
            text='测试',
            token_ids=(),
        )


def test_provider_context_and_typed_preflight_are_explicitly_required() -> None:
    with pytest.raises(TypeError):
        build_raw_asr_snapshot(
            _provider_payload(),
            provider_model='fun-asr',
            preflight_metadata=_preflight_metadata(),
        )

    for missing_key in ('duration_ms', 'sample_rate_hz', 'channel_count', 'status'):
        preflight = _preflight_metadata()
        preflight.pop(missing_key)
        with pytest.raises(ValidationError):
            _build_snapshot(preflight_metadata=preflight)

    for invalid_context in (
        {'provider_task_id': None},
        {'language': None},
        {'provider_model': None},
    ):
        with pytest.raises(ValidationError):
            _build_snapshot(**invalid_context)


def test_snapshot_rejects_unknown_non_json_metadata_objects() -> None:
    class Unknown:
        pass

    with pytest.raises(TypeError, match='JSON'):
        _build_snapshot(request_metadata={'unknown': Unknown()})


def test_snapshot_filters_additional_credential_and_path_forms() -> None:
    snapshot = _build_snapshot(
        request_metadata={
            'SecurityToken': 'security-token',
            'AccessKeySecret': 'access-key-secret',
            'file_uri': 'file:///Users/example/source.wav',
            'unc_path': r'\\server\share\source.wav',
            'userinfo_url': 'https://username:password@example.com/source.wav',
            'safe_url': 'https://example.com/source.wav?Signature=secret',
        }
    )
    serialized = snapshot.model_dump_json()

    assert 'security-token' not in serialized
    assert 'access-key-secret' not in serialized
    assert 'file://' not in serialized
    assert 'server' not in serialized
    assert 'username' not in serialized
    assert 'password' not in serialized
    assert 'Signature' not in serialized
    assert snapshot.request_metadata_dict() == {
        'safe_url': 'https://example.com/source.wav',
    }


def test_snapshot_filters_credential_key_categories_without_dropping_token_metrics() -> None:
    snapshot = _build_snapshot(
        request_metadata={
            'token': 'plain-token-secret',
            'id_token': 'identity-token-secret',
            'client_secret': 'client-secret-value',
            'oauthToken': 'oauth-token-secret',
            'token_count': 321,
            'token_timing': {'enabled': True},
        }
    )
    serialized = snapshot.model_dump_json()

    assert 'plain-token-secret' not in serialized
    assert 'identity-token-secret' not in serialized
    assert 'client-secret-value' not in serialized
    assert 'oauth-token-secret' not in serialized
    assert snapshot.request_metadata_dict() == {
        'token_count': 321,
        'token_timing': {'enabled': True},
    }


def _rehash_snapshot_data(data: dict) -> dict:
    content = {key: value for key, value in data.items() if key != 'snapshot_hash'}
    data['snapshot_hash'] = canonical_sha256(content)
    return data


@pytest.mark.parametrize(
    'damage',
    [
        'duplicate_token_id',
        'duplicate_segment_id',
        'missing_segment_reference',
        'missing_token_reference',
        'token_missing_from_owning_segment',
        'duplicate_segment_token_reference',
        'speaker_id_mismatch',
        'speaker_key_mismatch',
    ],
)
def test_snapshot_rejects_broken_evidence_graph_even_with_recomputed_hash(damage: str) -> None:
    snapshot = _build_snapshot()
    data = snapshot.model_dump(mode='json')

    if damage == 'duplicate_token_id':
        data['tokens'][1]['token_id'] = data['tokens'][0]['token_id']
    elif damage == 'duplicate_segment_id':
        data['segments'].append(deepcopy(data['segments'][0]))
    elif damage == 'missing_segment_reference':
        data['tokens'][0]['segment_id'] = 'segment-missing'
    elif damage == 'missing_token_reference':
        data['segments'][0]['token_ids'][0] = 'token-missing'
    elif damage == 'token_missing_from_owning_segment':
        data['segments'][0]['token_ids'] = [data['tokens'][1]['token_id']]
    elif damage == 'duplicate_segment_token_reference':
        data['segments'][0]['token_ids'].append(data['segments'][0]['token_ids'][0])
    elif damage == 'speaker_id_mismatch':
        data['tokens'][0]['speaker_id'] = 'speaker-conflict'
    elif damage == 'speaker_key_mismatch':
        data['tokens'][0]['speaker_key'] = 'speaker_conflict'

    with pytest.raises(ValidationError, match='evidence graph'):
        RawAsrSnapshot.model_validate(_rehash_snapshot_data(data))
