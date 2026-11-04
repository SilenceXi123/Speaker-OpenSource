import pytest

from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.script_correction_candidates import build_script_snapshot
from app.services.script_line_alignment import (
    build_exact_script_line_alignments,
    build_match_text_view,
    display_text_for_match_range,
)
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2


def _raw(words: list[str]):
    provider_words = [
        {'text': word, 'begin_time': index * 120, 'end_time': index * 120 + 80}
        for index, word in enumerate(words)
    ]
    return build_raw_asr_snapshot(
        {'transcripts': [{'sentences': [{
            'speaker_id': 0,
            'begin_time': 0,
            'end_time': len(words) * 120,
            'text': ''.join(words),
            'words': provider_words,
        }]}]},
        provider_model='fun-asr',
        provider_task_id='task-script-line-alignment',
        language='zh',
        request_metadata={},
        preflight_metadata={
            'duration_ms': len(words) * 120,
            'sample_rate_hz': 24000,
            'channel_count': 1,
            'status': 'accepted',
        },
    )


def test_match_view_preserves_display_offsets_while_matching_case_insensitively() -> None:
    view = build_match_text_view('欢迎 DeepSeek R1，进入AI时代')

    assert view is not None
    assert view.match_text == '欢迎deepseekr1进入ai时代'
    start = view.match_text.index('deepseekr1')
    end = start + len('deepseekr1')
    assert view.display_slice(start, end) == 'DeepSeek R1'


def test_exact_alignment_maps_multiple_script_lines_to_one_raw_token_tape() -> None:
    raw = _raw([
        '习近平主席出席',
        '2026世界人工智能大会',
        '暨人工智能全球治理',
        '高级别会议开幕式',
    ])
    semantic = build_semantic_utterances_v2(raw)
    script = build_script_snapshot(
        '习近平主席出席\n'
        '2026世界人工智能大会\n'
        '暨人工智能全球治理\n'
        '高级别会议开幕式'
    )

    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    assert [item.script_line_order for item in alignments] == [0, 1, 2, 3]
    assert [
        tuple(slice_.token_index for slice_ in item.token_slices)
        for item in alignments
    ] == [(0,), (1,), (2,), (3,)]
    assert alignments[0].right_token_index_after_line == 1
    assert alignments[-1].right_token_index_after_line is None


def test_alignment_is_monotonic_and_skips_short_or_missing_lines() -> None:
    raw = _raw(['现在进入正式环节', '欢迎来到AI盛典'])
    semantic = build_semantic_utterances_v2(raw)
    script = build_script_snapshot(
        '片头\n'
        '没有说出的介绍内容\n'
        '现在进入正式环节\n'
        '欢迎来到AI盛典'
    )

    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    assert [item.script_line_order for item in alignments] == [2, 3]
    assert display_text_for_match_range(
        alignments[1],
        alignments[1].script_match_text.index('ai'),
        alignments[1].script_match_text.index('ai') + 2,
    ) == 'AI'


def test_alignment_rejects_match_text_that_cannot_map_to_display_text() -> None:
    raw = _raw(['现在进入正式环节'])
    semantic = build_semantic_utterances_v2(raw)
    script = build_script_snapshot('现在进入正式环节')
    mismatched_line = script.lines[0].model_copy(
        update={'match_text': '没有说出的介绍内容'},
    )
    mismatched_script = script.model_copy(update={'lines': (mismatched_line,)})

    with pytest.raises(ValueError, match='script line match_text disagrees with display text'):
        build_exact_script_line_alignments(
            raw_snapshot=raw,
            semantic_artifact=semantic,
            script_snapshot=mismatched_script,
        )


def test_phrase_sensitive_opencc_alignment_uses_whole_string_offsets() -> None:
    raw = _raw(['乾隆皇帝出席', 'ai大会'])
    semantic = build_semantic_utterances_v2(raw)
    script = build_script_snapshot('乾隆皇帝出席AI大会')

    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    assert len(alignments) == 1
    alignment = alignments[0]
    assert alignment.script_match_text == script.lines[0].match_text
    ai_start = alignment.script_match_text.index('ai')
    assert display_text_for_match_range(
        alignment,
        ai_start,
        ai_start + 2,
    ) == 'AI'


def test_non_ascii_case_expansion_skips_unmappable_line_evidence() -> None:
    raw = _raw(['Strae欢迎', 'ai大会'])
    semantic = build_semantic_utterances_v2(raw)
    script = build_script_snapshot('Straße欢迎AI大会')

    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    assert alignments == ()


def test_unmappable_raw_display_character_does_not_discard_other_line_evidence() -> None:
    raw = _raw(['Straße插曲', '后面完整台本内容'])
    semantic = build_semantic_utterances_v2(raw)
    script = build_script_snapshot('后面完整台本内容')

    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    assert [alignment.script_text for alignment in alignments] == [
        '后面完整台本内容',
    ]


def test_sources_align_independently_then_reconcile_in_raw_chronology() -> None:
    raw = _raw([
        '前面历史台本内容',
        '后面ai内容',
    ])
    semantic = build_semantic_utterances_v2(raw)
    script = build_script_snapshot(
        '',
        source_sections=(
            {
                'source_name': '本次台本.txt',
                'reference_lines': ['后面AI内容'],
            },
            {
                'source_name': '历史台本.txt',
                'reference_lines': [
                    '前面历史台本内容',
                    '后面ai内容',
                ],
            },
        ),
    )

    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    assert [alignment.script_text for alignment in alignments] == [
        '前面历史台本内容',
        '后面AI内容',
    ]
    assert [alignment.raw_match_start for alignment in alignments] == sorted(
        alignment.raw_match_start for alignment in alignments
    )
    assert len({alignment.script_source_hash for alignment in alignments}) == 2
