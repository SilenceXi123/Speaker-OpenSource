from __future__ import annotations

from copy import deepcopy
from time import perf_counter

import pytest
from pydantic import ValidationError

from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2
from app.services.subtitle_pipeline_v2_schema import SemanticUtteranceArtifact


def _snapshot(sentences: list[dict]):
    max_end = max(
        [int(sentence.get('end_time') or 0) for sentence in sentences] or [0]
    )
    return build_raw_asr_snapshot(
        {'transcripts': [{'sentences': sentences}]},
        provider_model='fun-asr',
        provider_task_id='task-semantic-utterances',
        language='zh',
        request_metadata={},
        preflight_metadata={
            'duration_ms': max_end,
            'sample_rate_hz': 24000,
            'channel_count': 1,
            'status': 'accepted',
        },
    )


def _sentence(
    text: str,
    *,
    start: int | None,
    end: int | None,
    speaker: int | None = 0,
    punctuation: str = '',
    words: list[dict] | None = None,
) -> dict:
    sentence = {'text': text}
    if start is not None:
        sentence['begin_time'] = start
    if end is not None:
        sentence['end_time'] = end
    if speaker is not None:
        sentence['speaker_id'] = speaker
    if words is None:
        word = {'text': text, 'punctuation': punctuation}
        if start is not None:
            word['begin_time'] = start
        if end is not None:
            word['end_time'] = end
        sentence['words'] = [word]
    else:
        sentence['words'] = words
    return sentence


def _token_groups(artifact) -> list[tuple[str, ...]]:
    return [utterance.token_ids for utterance in artifact.utterances]


def _boundary_reasons(artifact) -> list[str]:
    return [utterance.start_boundary.reason for utterance in artifact.utterances]


def test_different_speakers_are_hard_boundaries_and_short_tails_are_not_reassigned() -> None:
    snapshot = _snapshot([
        _sentence('国际赛艇', start=0, end=500, speaker=0),
        _sentence('中心哦', start=500, end=800, speaker=1),
        _sentence('听说过', start=800, end=1100, speaker=1),
    ])

    artifact = build_semantic_utterances_v2(snapshot)

    assert _token_groups(artifact) == [
        ('token-000000',),
        ('token-000001', 'token-000002'),
    ]
    assert [utterance.speaker_key for utterance in artifact.utterances] == [
        'speaker_0',
        'speaker_1',
    ]
    assert _boundary_reasons(artifact) == ['tape_start', 'speaker_change']


def test_known_unknown_transitions_split_while_consecutive_unknown_evidence_can_merge() -> None:
    snapshot = _snapshot([
        _sentence('已知甲', start=0, end=200, speaker=0),
        _sentence('未知甲', start=200, end=400, speaker=None),
        _sentence('未知乙', start=400, end=600, speaker=None),
        _sentence('已知乙', start=600, end=800, speaker=0),
    ])

    artifact = build_semantic_utterances_v2(snapshot)

    assert _token_groups(artifact) == [
        ('token-000000',),
        ('token-000001', 'token-000002'),
        ('token-000003',),
    ]
    assert _boundary_reasons(artifact) == [
        'tape_start',
        'speaker_evidence_transition',
        'speaker_evidence_transition',
    ]
    assert artifact.utterances[1].speaker_key is None


def test_any_change_in_complete_speaker_evidence_is_a_hard_boundary() -> None:
    snapshot = _snapshot([
        {
            **_sentence('甲', start=0, end=200, speaker=0),
            'speaker_key': 'shared-key',
        },
        {
            **_sentence('乙', start=200, end=400, speaker=1),
            'speaker_key': 'shared-key',
        },
    ])

    artifact = build_semantic_utterances_v2(snapshot)

    assert _token_groups(artifact) == [('token-000000',), ('token-000001',)]
    assert _boundary_reasons(artifact) == ['tape_start', 'speaker_change']
    assert [item.speaker_id for item in artifact.utterances] == ['0', '1']


@pytest.mark.parametrize(
    ('left', 'right', 'expected_reason'),
    [
        (
            _sentence('前句', start=0, end=100, speaker=0),
            _sentence('长停顿后', start=1301, end=1500, speaker=0),
            'long_pause',
        ),
        (
            _sentence('前句', start=0, end=500, speaker=0),
            _sentence('时间倒退', start=400, end=800, speaker=0),
            'time_discontinuity',
        ),
        (
            _sentence('无时间甲', start=None, end=None, speaker=0),
            _sentence('无时间乙', start=None, end=None, speaker=0),
            'missing_time_segment_boundary',
        ),
    ],
)
def test_time_hard_boundaries_split_between_segments(
    left: dict,
    right: dict,
    expected_reason: str,
) -> None:
    artifact = build_semantic_utterances_v2(_snapshot([left, right]))

    assert _token_groups(artifact) == [('token-000000',), ('token-000001',)]
    assert _boundary_reasons(artifact) == ['tape_start', expected_reason]


@pytest.mark.parametrize('with_sentence_time', [True, False])
def test_same_segment_coarse_or_missing_timing_does_not_invent_internal_boundaries(
    with_sentence_time: bool,
) -> None:
    sentence = {
        'speaker_id': 0,
        'text': '共享时间证据',
        'words': [{'text': '共享'}, {'text': '时间'}, {'text': '证据'}],
    }
    if with_sentence_time:
        sentence.update({'begin_time': 100, 'end_time': 900})
    snapshot = _snapshot([sentence])

    artifact = build_semantic_utterances_v2(snapshot)

    assert _token_groups(artifact) == [
        ('token-000000', 'token-000001', 'token-000002')
    ]


def test_same_segment_mixed_word_and_coarse_timing_does_not_invent_a_boundary() -> None:
    snapshot = _snapshot([{
        'speaker_id': 0,
        'begin_time': 0,
        'end_time': 900,
        'text': '混合时间证据',
        'words': [
            {'text': '混合', 'begin_time': 0, 'end_time': 100},
            {'text': '时间'},
            {'text': '证据', 'begin_time': 800, 'end_time': 900},
        ],
    }])

    artifact = build_semantic_utterances_v2(snapshot)

    assert _token_groups(artifact) == [
        ('token-000000', 'token-000001', 'token-000002')
    ]


@pytest.mark.parametrize(
    ('gap_ms', 'expected_count'),
    [(319, 1), (320, 2)],
)
def test_terminal_punctuation_requires_a_real_pause_at_the_profile_threshold(
    gap_ms: int,
    expected_count: int,
) -> None:
    snapshot = _snapshot([
        _sentence('第一句', start=0, end=100, speaker=0, punctuation='。'),
        _sentence('第二句', start=100 + gap_ms, end=600 + gap_ms, speaker=0),
    ])

    artifact = build_semantic_utterances_v2(snapshot)

    assert len(artifact.utterances) == expected_count
    if expected_count == 2:
        assert artifact.utterances[1].start_boundary.reason == 'terminal_punctuation_pause'
        assert artifact.utterances[1].start_boundary.gap_ms == 320


def test_context_limit_splits_before_overflow_but_keeps_one_oversized_token_whole() -> None:
    limited = _snapshot([
        _sentence(
            'ignored',
            start=0,
            end=200,
            words=[
                {'text': '甲' * 120, 'begin_time': 0, 'end_time': 100},
                {'text': '乙', 'begin_time': 100, 'end_time': 200},
            ],
        )
    ])
    oversized = _snapshot([
        _sentence('丙' * 121, start=0, end=100, speaker=0),
    ])

    limited_artifact = build_semantic_utterances_v2(limited)
    oversized_artifact = build_semantic_utterances_v2(oversized)

    assert _token_groups(limited_artifact) == [
        ('token-000000',),
        ('token-000001',),
    ]
    assert _boundary_reasons(limited_artifact) == ['tape_start', 'max_context']
    assert _token_groups(oversized_artifact) == [('token-000000',)]


def test_token_conservation_source_references_and_content_addresses_are_stable() -> None:
    snapshot = _snapshot([
        _sentence('甲', start=0, end=100, speaker=0),
        _sentence('乙', start=100, end=200, speaker=0),
        _sentence('丙', start=200, end=300, speaker=1),
    ])
    raw_before = snapshot.model_dump(mode='json')

    first = build_semantic_utterances_v2(snapshot)
    second = build_semantic_utterances_v2(snapshot)

    flattened = [
        token_id
        for utterance in first.utterances
        for token_id in utterance.token_ids
    ]
    assert flattened == [token.token_id for token in snapshot.tokens]
    assert len(flattened) == len(set(flattened))
    assert [utterance.source_segment_ids for utterance in first.utterances] == [
        ('segment-000000', 'segment-000001'),
        ('segment-000002',),
    ]
    assert first == second
    assert first.artifact_hash == second.artifact_hash
    assert snapshot.model_dump(mode='json') == raw_before


def test_semantic_artifact_is_frozen_strict_and_rejects_hash_tampering() -> None:
    artifact = build_semantic_utterances_v2(
        _snapshot([_sentence('测试', start=0, end=100, speaker=0)])
    )

    with pytest.raises(ValidationError):
        artifact.artifact_hash = artifact.artifact_hash

    with_extra = artifact.model_dump(mode='json')
    with_extra['unexpected'] = True
    with pytest.raises(ValidationError):
        SemanticUtteranceArtifact.model_validate(with_extra)

    tampered = deepcopy(artifact.model_dump(mode='json'))
    tampered['utterances'][0]['speaker_key'] = 'speaker_tampered'
    with pytest.raises(ValidationError, match='artifact_hash'):
        SemanticUtteranceArtifact.model_validate(tampered)


def test_empty_snapshot_returns_a_stable_empty_artifact() -> None:
    snapshot = _snapshot([])

    first = build_semantic_utterances_v2(snapshot)
    second = build_semantic_utterances_v2(snapshot)

    assert first.utterances == ()
    assert first == second
    assert first.raw_snapshot_hash == snapshot.snapshot_hash


def test_large_empty_token_tape_is_grouped_in_linear_time() -> None:
    token_count = 20_000
    snapshot = _snapshot([{
        'speaker_id': 0,
        'begin_time': 0,
        'end_time': token_count,
        'text': '',
        'words': [
            {'text': '', 'begin_time': index, 'end_time': index + 1}
            for index in range(token_count)
        ],
    }])

    started_at = perf_counter()
    artifact = build_semantic_utterances_v2(snapshot)
    elapsed_seconds = perf_counter() - started_at

    assert len(artifact.utterances) == 1
    assert len(artifact.utterances[0].token_ids) == token_count
    assert elapsed_seconds < 2.0
