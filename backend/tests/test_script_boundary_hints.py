import pytest
from pydantic import ValidationError

from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.script_boundary_hints import (
    ScriptBoundaryHint,
    build_script_boundary_hints,
)
from app.services.script_correction_candidates import build_script_snapshot
from app.services.script_line_alignment import build_exact_script_line_alignments
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2
from app.services.subtitle_atoms import build_token_bound_lexical_atoms
from app.services.subtitle_legal_gaps import build_subtitle_legal_gaps


def _facts(words: list[str], *, confirmed_terms: tuple[str, ...] = ()):
    provider_words = [
        {'text': word, 'begin_time': index * 140, 'end_time': index * 140 + 100}
        for index, word in enumerate(words)
    ]
    raw = build_raw_asr_snapshot(
        {'transcripts': [{'sentences': [{
            'speaker_id': 0,
            'begin_time': 0,
            'end_time': len(words) * 140,
            'text': ''.join(words),
            'words': provider_words,
        }]}]},
        provider_model='fun-asr',
        provider_task_id='task-script-boundary-hints',
        language='zh',
        request_metadata={},
        preflight_metadata={
            'duration_ms': len(words) * 140,
            'sample_rate_hz': 24000,
            'channel_count': 1,
            'status': 'accepted',
        },
    )
    semantic = build_semantic_utterances_v2(raw)
    atoms = build_token_bound_lexical_atoms(
        raw,
        semantic,
        confirmed_terms=confirmed_terms,
    )
    gaps = build_subtitle_legal_gaps(raw, semantic, atoms=atoms)
    return raw, semantic, atoms, gaps


def test_punctuation_and_physical_lines_project_only_to_adjacent_legal_gaps() -> None:
    raw, semantic, atoms, gaps = _facts([
        '特别有代表性',
        '它说明ai已经不是',
        '少数人的专属工具',
    ])
    script = build_script_snapshot(
        '特别有代表性。\n'
        '它说明AI已经不是\n'
        '少数人的专属工具'
    )
    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    hints = build_script_boundary_hints(
        raw_snapshot=raw,
        gaps=gaps,
        alignments=alignments,
    )

    assert [(item.left_token_index, item.right_token_index) for item in hints] == [
        (0, 1),
        (1, 2),
    ]
    assert all(item.strength == 'strong' for item in hints)
    assert all(item.script_line_id.startswith('script-line-') for item in hints)


def test_hint_is_rejected_when_target_gap_is_forbidden_by_hard_atom() -> None:
    raw, semantic, atoms, gaps = _facts(
        ['汇聚了2000', '多家', '人工智能企业'],
        confirmed_terms=('2000多家',),
    )
    script = build_script_snapshot('汇聚了2000，多家人工智能企业')
    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    hints = build_script_boundary_hints(
        raw_snapshot=raw,
        gaps=gaps,
        alignments=alignments,
    )

    assert hints == ()


def test_physical_line_end_inside_token_does_not_shift_to_preceding_gap() -> None:
    raw, semantic, atoms, gaps = _facts([
        '特别有代表性',
        '它说明ai已经不是',
        '少数人的专属工具',
    ])
    script = build_script_snapshot(
        '特别有代表性它说明AI\n'
        '已经不是少数人的专属工具'
    )
    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    hints = build_script_boundary_hints(
        raw_snapshot=raw,
        gaps=gaps,
        alignments=alignments,
    )

    assert hints == ()


def test_physical_line_end_requires_aligned_evidence_on_the_right() -> None:
    raw, semantic, atoms, gaps = _facts([
        '第一段完整表达',
        '没有写进台本的原始结尾',
    ])
    script = build_script_snapshot('第一段完整表达')
    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    hints = build_script_boundary_hints(
        raw_snapshot=raw,
        gaps=gaps,
        alignments=alignments,
    )

    assert len(alignments) == 1
    assert hints == ()


def test_synthetic_long_line_chunks_do_not_create_physical_line_hints() -> None:
    first_chunk = '甲' * 160
    second_chunk = '乙' * 12
    raw, semantic, atoms, gaps = _facts([first_chunk, second_chunk])
    script = build_script_snapshot(first_chunk + second_chunk)
    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    hints = build_script_boundary_hints(
        raw_snapshot=raw,
        gaps=gaps,
        alignments=alignments,
    )

    assert [line.line_number for line in script.lines] == [1, 1]
    assert len(alignments) == 2
    assert hints == ()


def test_trailing_punctuation_split_keeps_safe_boundary_projection() -> None:
    first_sentence = '甲' * 90
    second_sentence = '乙' * 90
    raw, semantic, atoms, gaps = _facts([
        first_sentence,
        second_sentence,
    ])
    script = build_script_snapshot(
        f'{first_sentence}。{second_sentence}'
    )
    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    hints = build_script_boundary_hints(
        raw_snapshot=raw,
        gaps=gaps,
        alignments=alignments,
    )

    assert [line.line_number for line in script.lines] == [1, 1]
    assert [(hint.right_token_index, hint.strength) for hint in hints] == [
        (1, 'strong'),
    ]


def test_punctuation_projection_remains_safe_without_a_physical_line_end() -> None:
    raw, semantic, atoms, gaps = _facts([
        '第一段完整表达',
        '第二段完整表达',
    ])
    script = build_script_snapshot('第一段完整表达，第二段完整表达')
    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    hints = build_script_boundary_hints(
        raw_snapshot=raw,
        gaps=gaps,
        alignments=alignments,
    )

    assert [(hint.right_token_index, hint.strength) for hint in hints] == [
        (1, 'weak'),
    ]


def test_alignment_from_different_raw_token_tape_is_rejected() -> None:
    raw, semantic, atoms, gaps = _facts([
        '当前录音里的第一段',
        '当前录音里的第二段',
    ])
    other_raw, other_semantic, _other_atoms, _other_gaps = _facts([
        '完全不同的第一段',
        '完全不同的第二段',
    ])
    other_script = build_script_snapshot(
        '完全不同的第一段\n'
        '完全不同的第二段'
    )
    stale_alignments = build_exact_script_line_alignments(
        raw_snapshot=other_raw,
        semantic_artifact=other_semantic,
        script_snapshot=other_script,
    )

    with pytest.raises(ValueError, match='alignment.*raw'):
        build_script_boundary_hints(
            raw_snapshot=raw,
            gaps=gaps,
            alignments=stale_alignments,
        )


@pytest.mark.parametrize(
    'evidence_refs',
    [
        (
            'script-line-evidence',
            'script-boundary:strong',
            f'script-source:{"0" * 64}',
        ),
        (
            'script-boundary:weak',
            'script-line-evidence',
            f'script-source:{"0" * 64}',
        ),
        (
            'script-boundary:strong',
            f'script-source:{"0" * 64}',
        ),
    ],
)
def test_hint_model_rejects_unsorted_or_incomplete_evidence(
    evidence_refs,
) -> None:
    payload = {
        'hint_id': 'script-boundary-hint-evidence',
        'raw_snapshot_hash': '1' * 64,
        'gap_id': 'gap-evidence',
        'left_token_id': 'token-left',
        'right_token_id': 'token-right',
        'left_token_index': 0,
        'right_token_index': 1,
        'strength': 'strong',
        'script_line_id': 'script-line-evidence',
        'script_source_hash': '0' * 64,
        'evidence_refs': evidence_refs,
    }

    with pytest.raises(ValidationError, match='evidence refs'):
        ScriptBoundaryHint.model_validate(payload)
