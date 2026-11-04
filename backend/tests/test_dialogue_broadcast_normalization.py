import pytest

from app.services.dialogue_broadcast_normalization import normalize_dialogue_segments


def _segment(
    segment_id: str,
    start_ms: int,
    end_ms: int,
    text: str,
    **extra,
):
    return {
        'segmentId': segment_id,
        'startMs': start_ms,
        'endMs': end_ms,
        'text': text,
        'subtitleParts': [text],
        'subtitlePartRanges': [{'startMs': start_ms, 'endMs': end_ms}],
        **extra,
    }


def _bundle():
    return {
        'terms': [
            {
                'display_text': 'RANZER叶润泽',
                'normalized_term': 'RANZER叶润泽',
                'term_type': 'person',
                'protection_level': 'hard',
                'aliases': ['RANZER', 'render', '叶润泽'],
            },
            {
                'display_text': 'Rage（说唱风格）',
                'normalized_term': 'Rage',
                'term_type': 'term',
                'protection_level': 'hard',
                'aliases': [],
            },
        ],
    }


def test_normalizes_project_aliases_and_bilingual_dialogue():
    result = normalize_dialogue_segments(
        [
            _segment('one', 0, 800, 'render这个Rage'),
            _segment('two', 1000, 1800, "let's go"),
            _segment('three', 2000, 2800, 'I like that我喜欢'),
        ],
        runtime_lexicon_bundle=_bundle(),
    )

    assert [item['text'] for item in result.segments] == [
        'RANZER叶润泽这个Rage（说唱风格）',
        '走起',
        '我喜欢',
    ]
    assert result.report['lexicon_replacement_count'] == 2
    assert result.report['bilingual_phrase_replacement_count'] == 2


def test_does_not_expand_alias_inside_existing_canonical_name():
    result = normalize_dialogue_segments(
        [_segment('one', 0, 800, 'RANZER叶润泽和叶润泽')],
        runtime_lexicon_bundle=_bundle(),
    )

    assert result.segments[0]['text'] == 'RANZER叶润泽和RANZER叶润泽'


def test_collapses_full_cue_repetition_and_adjacent_echo():
    result = normalize_dialogue_segments(
        [
            _segment('one', 0, 1000, '可以可以可以可以可以', speakerKey='speaker_0'),
            _segment('two', 1000, 1400, '可以可以', speakerKey='speaker_0'),
            _segment('three', 1500, 2300, '继续说'),
        ],
    )

    assert [item['text'] for item in result.segments] == ['可以', '继续说']
    assert result.report['repetition_collapse_count'] == 2
    assert result.report['removed_echo_segment_count'] == 1


def test_stitches_split_bilingual_echo_and_removes_only_phrase_filler():
    result = normalize_dialogue_segments(
        [
            _segment('one', 0, 800, 'you got it man', speakerKey='speaker_0'),
            _segment('two', 800, 1000, 'you', speakerKey='speaker_0'),
            _segment('three', 1000, 1500, 'got it 那', speakerKey='speaker_0'),
        ],
    )

    assert [item['text'] for item in result.segments] == ['你做到了']
    assert result.report['removed_echo_segment_count'] == 2


def test_keeps_directing_call_but_trims_trailing_laughter():
    result = normalize_dialogue_segments(
        [
            _segment('one', 0, 800, '来来来'),
            _segment('two', 1000, 1800, '弄巧成拙哈哈哈'),
        ],
    )

    assert [item['text'] for item in result.segments] == ['来来来', '弄巧成拙']


def test_removes_standalone_laughter_without_emitting_empty_segment():
    result = normalize_dialogue_segments(
        [_segment('one', 0, 800, '哈哈哈')],
    )

    assert result.segments == []
    assert result.report['removed_echo_segment_count'] == 1


def test_collapses_translation_already_spoken_in_chinese():
    result = normalize_dialogue_segments(
        [
            _segment('one', 0, 800, '有一点往后 lay back有延迟'),
            _segment('two', 1000, 1800, '真的很难说，真的很难说。'),
        ],
    )

    assert result.segments[0]['text'] == '有一点往后有延迟'
    assert result.segments[1]['text'] == '真的很难说。'


def test_adds_broadcast_gloss_once():
    result = normalize_dialogue_segments(
        [
            _segment('one', 0, 800, 'Beat switch之后是Rage'),
            _segment('two', 1000, 1800, 'Rage（说唱风格）'),
        ],
    )

    assert [item['text'] for item in result.segments] == [
        'Beat switch（伴奏切换）之后是Rage（说唱风格）',
        'Rage（说唱风格）',
    ]


def test_lyrics_corrected_segments_are_byte_for_byte_protected():
    lyrics = _segment(
        'lyrics-one',
        0,
        1000,
        'respect respect respect',
        lyricsCorrectionMode=True,
        timingEvidence='lyrics_srt',
    )

    result = normalize_dialogue_segments([lyrics], runtime_lexicon_bundle=_bundle())

    assert result.segments == [lyrics]
    assert result.report['protected_lyrics_segment_count'] == 1
    assert result.report['status'] == 'not_applied'


@pytest.mark.parametrize('identities', [
    ({'speakerKey': 'speaker_0'}, {'speakerKey': 'speaker_1'}),
    ({}, {}),
    ({'speakerKey': 'unknown'}, {'speakerKey': 'unknown'}),
    ({'speakerKey': 'speaker_0', 'speakerId': 0}, {'speakerKey': 'speaker_0', 'speakerId': 1}),
])
def test_does_not_stitch_or_delete_replies_across_different_or_unknown_people(identities):
    left, right = identities
    split = normalize_dialogue_segments([
        _segment('a', 0, 500, 'you', **left),
        _segment('b', 500, 900, 'got it 那', **right),
    ])
    echoed = normalize_dialogue_segments([
        _segment('a', 0, 500, '走起', **left),
        _segment('b', 500, 900, '走起', **right),
    ])
    assert [item['text'] for item in split.segments] == ['you', 'got it 那']
    assert [item['text'] for item in echoed.segments] == ['走起', '走起']
    assert split.report['skipped_changes'][0]['reason'] == 'speaker_not_confirmed_same'
    assert echoed.report['skipped_changes'][0]['start_ms'] == 500


@pytest.mark.parametrize(('source', 'replacement'), [
    ('十五元', '五十元'), ('300元', '3000元'), ('3到4厘米', '三四厘米'),
])
def test_project_alias_cannot_change_numeric_fact_or_range(source, replacement):
    result = normalize_dialogue_segments([_segment('amount', 100, 900, source)], runtime_lexicon_bundle={
        'terms': [{'display_text': replacement, 'aliases': [source], 'protection_level': 'hard'}],
    })
    assert result.segments[0]['text'] == source
    assert result.report['changes'] == []
    assert result.report['lexicon_replacement_count'] == 0
    assert result.report['skipped_changes'][0]['reason'] == 'numeric_fact_change'
    assert result.report['skipped_changes'][0]['after'] == replacement
