from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.services.deepseek_subtitle_contract import DeepSeekBoundaryVote
from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.script_correction_candidates import build_script_snapshot
from app.services.script_line_alignment import build_exact_script_line_alignments
from app.services.script_boundary_hints import build_script_boundary_hints
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2
from app.services.subtitle_atoms import build_token_bound_lexical_atoms
from app.services.subtitle_global_planner import (
    CuePlannerProfile,
    SubtitleCuePlanArtifact,
    build_subtitle_global_plan,
)
from app.services.subtitle_legal_gaps import build_subtitle_legal_gaps
from app.services.subtitle_pipeline_v2 import _merge_script_boundary_fallback_votes
from app.services.subtitle_pipeline_v2_schema import canonical_sha256
from tests.fixtures.p1_subtitle_reliability_cases import P1_SCRIPT_FIDELITY_CASES


def _facts(
    words: list[str],
    *,
    language: str = 'zh',
    speakers: list[int] | None = None,
    pauses_ms: list[int] | None = None,
    durations_ms: list[int] | None = None,
    punctuation: list[str] | None = None,
    coarse_shared_time: bool = False,
    missing_time: bool = False,
    confirmed_terms: tuple[str, ...] = (),
):
    speakers = speakers or [0] * len(words)
    pauses_ms = pauses_ms or [80] * max(0, len(words) - 1)
    durations_ms = durations_ms or [120] * len(words)
    punctuation = punctuation or [''] * len(words)
    if (
        len(speakers) != len(words)
        or len(durations_ms) != len(words)
        or len(punctuation) != len(words)
    ):
        raise ValueError('word evidence lengths disagree')
    if len(pauses_ms) != max(0, len(words) - 1):
        raise ValueError('pause evidence length disagrees')

    sentences: list[dict] = []
    if coarse_shared_time and missing_time:
        raise ValueError('coarse and missing timing modes are mutually exclusive')
    if coarse_shared_time:
        sentences.append({
            'speaker_id': speakers[0] if speakers else 0,
            'text': ''.join(words),
            'begin_time': 0,
            'end_time': max(500, len(words) * 200),
            'words': [
                {'text': word, 'punctuation': punctuation[index]}
                for index, word in enumerate(words)
            ],
        })
    elif missing_time:
        sentences.extend(
            {
                'speaker_id': speakers[index],
                'text': word + punctuation[index],
                'words': [{
                    'text': word,
                    'punctuation': punctuation[index],
                }],
            }
            for index, word in enumerate(words)
        )
    else:
        cursor = 0
        for index, word in enumerate(words):
            start = cursor
            end = start + durations_ms[index]
            sentences.append({
                'speaker_id': speakers[index],
                'text': word + punctuation[index],
                'begin_time': start,
                'end_time': end,
                'words': [{
                    'text': word,
                    'punctuation': punctuation[index],
                    'begin_time': start,
                    'end_time': end,
                }],
            })
            if index < len(pauses_ms):
                cursor = end + pauses_ms[index]

    duration_ms = max(
        (int(sentence.get('end_time') or 0) for sentence in sentences),
        default=0,
    )
    raw = build_raw_asr_snapshot(
        {'transcripts': [{'sentences': sentences}]},
        provider_model='fun-asr',
        provider_task_id='task-global-planner',
        language=language,
        request_metadata={},
        preflight_metadata={
            'duration_ms': duration_ms,
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
    return raw, atoms, gaps


def _strict(limit: int, *, language_mode: str = 'auto') -> CuePlannerProfile:
    return CuePlannerProfile(
        mode='strict',
        language_mode=language_mode,
        strict_max_display_units=limit,
    )


def _cue_text(raw, cue, *, english: bool = False) -> str:
    values = [
        raw.tokens[index].text + raw.tokens[index].punctuation
        for index in range(cue.token_start, cue.token_end)
    ]
    return ' '.join(item.strip() for item in values) if english else ''.join(values)


def _vote(gap, *, rating: str = 'preferred', reason: str = 'semantic_boundary'):
    return DeepSeekBoundaryVote(
        gap_id=gap.gap_id,
        left_token_id=gap.left_token_id,
        right_token_id=gap.right_token_id,
        rating=rating,
        reason_code=reason,
        evidence_refs=(),
    )


def test_planner_only_selects_legal_non_hard_gaps_and_conserves_tokens() -> None:
    raw, atoms, gaps = _facts(
        list('甲乙丙丁戊己庚辛'),
        confirmed_terms=('甲乙',),
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        profile=_strict(6),
    )

    assert [token_id for cue in artifact.cues for token_id in cue.token_ids] == [
        token.token_id for token in raw.tokens
    ]
    selected_gap_ids = {
        cue.start_gap_id for cue in artifact.cues[1:]
    }
    gap_by_id = {gap.gap_id: gap for gap in gaps}
    assert all(gap_by_id[gap_id].legal_gap for gap_id in selected_gap_ids)
    hard_gap = next(gap for gap in gaps if gap.reason == 'hard_atom')
    assert hard_gap.gap_id not in selected_gap_ids
    assert any(_cue_text(raw, cue).startswith('甲乙') for cue in artifact.cues)


def test_speaker_evidence_change_is_a_required_boundary() -> None:
    raw, atoms, gaps = _facts(['甲', '乙'], speakers=[0, 1])

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == ['甲', '乙']
    assert artifact.cues[1].start_boundary_kind == 'required_speaker'
    assert artifact.cues[1].start_gap_id == gaps[0].gap_id


def test_speaker_change_remains_required_without_legal_time_evidence() -> None:
    raw, atoms, gaps = _facts(
        ['甲', '乙'],
        speakers=[0, 1],
        missing_time=True,
    )
    assert gaps[0].disposition == 'required'
    assert gaps[0].legal_gap is False

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == ['甲', '乙']
    assert all(cue.start_ms is None for cue in artifact.cues)


def test_same_inputs_produce_stable_ranges_ids_and_artifact_hash() -> None:
    raw, atoms, gaps = _facts(list('甲乙丙丁戊己庚辛壬癸子丑'))
    vote = _vote(gaps[5])

    first = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(vote,),
        profile=_strict(6),
    )
    second = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(vote,),
        profile=_strict(6),
    )

    assert first == second
    assert first.artifact_hash == second.artifact_hash
    assert [cue.cue_id for cue in first.cues] == [cue.cue_id for cue in second.cues]


@pytest.mark.parametrize('length', [18, 19])
def test_smart_mode_does_not_split_only_to_approach_fourteen_chars(
    length: int,
) -> None:
    raw, atoms, gaps = _facts(['字'] * length)

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
    )

    assert len(artifact.cues) == 1
    assert artifact.cues[0].display_char_count == length
    assert artifact.cues[0].strict_overflow_reason is None


def test_smart_mode_uses_soft_length_pressure_for_complete_long_clauses() -> None:
    words = ['我们已经完成核心验证，', '接下来开始不同节目测试。']
    raw, atoms, gaps = _facts(
        words,
        durations_ms=[2200, 2400],
        pauses_ms=[80],
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == words


def test_allowed_semantic_boundary_does_not_force_a_target_length_split() -> None:
    words = ['这是第一层意思', '这是第二层意思']
    raw, atoms, gaps = _facts(words, durations_ms=[1600, 1600])

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[0], rating='allowed'),),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == [''.join(words)]


def test_preferred_semantic_boundary_can_split_below_safety_limit() -> None:
    words = ['这是第一层意思', '这是第二层意思']
    raw, atoms, gaps = _facts(words, durations_ms=[1600, 1600])
    vote = _vote(gaps[0], rating='preferred')

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(vote,),
    )

    assert vote.decision_source == 'ai'
    assert [_cue_text(raw, cue) for cue in artifact.cues] == words


def test_p1_script_fallback_votes_keep_direct_raw_token_timing() -> None:
    case = P1_SCRIPT_FIDELITY_CASES[1]
    raw, atoms, gaps = _facts(
        list(case['words']),
        pauses_ms=[40] * (len(case['words']) - 1),
        durations_ms=[900] * len(case['words']),
    )
    semantic = build_semantic_utterances_v2(raw)
    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=build_script_snapshot(case['script']),
    )
    hints = build_script_boundary_hints(
        raw_snapshot=raw,
        gaps=gaps,
        alignments=alignments,
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=_merge_script_boundary_fallback_votes(hints, gaps),
    )

    cue_texts = [_cue_text(raw, cue) for cue in artifact.cues]
    assert any(
        text.endswith(case['required_boundary_after_text'])
        for text in cue_texts
    )
    for cue in artifact.cues:
        assert cue.start_ms == raw.tokens[cue.token_start].start_ms
        assert cue.end_ms == raw.tokens[cue.token_end - 1].end_ms


def test_strong_pause_outranks_mutually_exclusive_script_fallback() -> None:
    words = [
        '甲乙丙丁戊己',
        '庚辛壬癸子丑',
        '寅卯辰巳午未',
    ]
    raw, atoms, gaps = _facts(
        words,
        pauses_ms=[600, 40],
        durations_ms=[900, 900, 900],
    )
    semantic = build_semantic_utterances_v2(raw)
    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=build_script_snapshot(
            f'{words[0]}{words[1]}\n{words[2]}',
        ),
    )
    hints = build_script_boundary_hints(
        raw_snapshot=raw,
        gaps=gaps,
        alignments=alignments,
    )
    fallback_votes = _merge_script_boundary_fallback_votes(hints, gaps)

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=fallback_votes,
        profile=_strict(12),
    )

    assert len(hints) == 1
    assert fallback_votes[0].decision_source == 'script_fallback'
    assert fallback_votes[0].rating == 'allowed'
    assert artifact.cues[1].start_gap_id == gaps[0].gap_id


def test_smart_mode_preserves_a_complete_phrase_beyond_soft_limit() -> None:
    words = ['高质量的', '物理接触数据', '决定了', '机器人', '能否稳定工作']
    raw, atoms, gaps = _facts(
        words,
        pauses_ms=[40] * (len(words) - 1),
        durations_ms=[800] * len(words),
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=tuple(
            _vote(gap, rating='forbidden', reason='phrase_integrity')
            for gap in gaps
        ),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == [''.join(words)]
    assert artifact.cues[0].display_char_count > 16
    assert artifact.cues[0].strict_overflow_reason is None


@pytest.mark.parametrize('length', [15, 16])
def test_smart_mode_allows_soft_limit_cues_with_a_length_penalty(length: int) -> None:
    raw, atoms, gaps = _facts(['字'] * length)

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
    )

    assert len(artifact.cues) == 1
    assert artifact.cues[0].display_char_count == length
    assert artifact.cues[0].strict_overflow_reason is None


@pytest.mark.parametrize('length', [15, 16])
def test_smart_mode_splits_soft_overflow_at_natural_authorized_vote(
    length: int,
) -> None:
    raw, atoms, gaps = _facts(['字'] * length)
    boundary = length // 2

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[boundary - 1], rating='preferred'),),
    )

    assert [cue.display_char_count for cue in artifact.cues] == [
        boundary,
        length - boundary,
    ]
    assert all(cue.display_char_count <= 14 for cue in artifact.cues)


@pytest.mark.parametrize('length', [15, 16])
def test_allowed_vote_preserves_a_complete_soft_overflow_phrase(length: int) -> None:
    raw, atoms, gaps = _facts(['字'] * length)
    boundary = length // 2

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[boundary - 1], rating='allowed'),),
    )

    assert [cue.display_char_count for cue in artifact.cues] == [length]


def test_smart_mode_records_no_legal_gap_overflow() -> None:
    raw, atoms, gaps = _facts(
        ['甲乙丙丁戊己庚辛壬癸', '子丑寅卯辰巳午未申酉', '戌亥天地玄黄宇宙洪荒'],
        coarse_shared_time=True,
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
    )

    assert len(artifact.cues) == 1
    assert artifact.cues[0].display_char_count == 30
    assert artifact.cues[0].strict_overflow_reason == 'no_legal_gap'


def test_single_long_token_records_unbreakable_atom_overflow() -> None:
    raw, atoms, gaps = _facts(['超长不可拆分专有名词一二三四五六七八九十甲乙丙丁戊己庚辛壬癸'])

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
    )

    assert len(artifact.cues) == 1
    assert artifact.cues[0].strict_overflow_reason == 'unbreakable_atom'


def test_strict_mode_records_unbreakable_atom_overflow() -> None:
    raw, atoms, gaps = _facts(['不可拆分专有名词一二三四'])

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        profile=_strict(6),
    )

    assert artifact.cues[0].display_char_count > 6
    assert artifact.cues[0].strict_overflow_reason == 'unbreakable_atom'


@pytest.mark.parametrize(
    'words',
    [
        ['学习', '一下'],
        ['投下了', '这一票'],
        ['很感人', '的歌'],
        ['有这种', '感触'],
    ],
)
def test_phrase_integrity_atoms_are_never_split_in_strict_mode(words: list[str]) -> None:
    raw, atoms, gaps = _facts(words)
    phrase = ''.join(words)
    assert any(atom.text == phrase and atom.protection == 'hard' for atom in atoms)

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        profile=_strict(6),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == [phrase]
    assert artifact.cues[0].strict_overflow_reason is None


@pytest.mark.parametrize(
    ('words', 'cut_after'),
    [
        (['机器人是', '未来解决养老的终极方案'], 0),
        (['用户的付费能力', '一直是一个瓶颈'], 0),
    ],
)
def test_preferred_votes_preserve_complete_neighboring_phrases(
    words: list[str],
    cut_after: int,
) -> None:
    raw, atoms, gaps = _facts(words, durations_ms=[800] * len(words))

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[cut_after]),),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == words


def test_preferred_identity_boundary_keeps_affiliation_with_long_organization() -> None:
    words = [
        '我', '叫', '张', '三', '来自',
        '耕宇牧星', '北京空间', '科技技术', '有限公司',
    ]
    raw, atoms, gaps = _facts(words)

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[3]),),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == [
        '我叫张三',
        '来自耕宇牧星北京空间科技技术有限公司',
    ]
    assert artifact.cues[1].display_char_count == 18
    assert artifact.cues[1].strict_overflow_reason is None


def test_ai_votes_cannot_split_before_technical_completion_heads() -> None:
    words = [
        '融合',
        '感知',
        '技术',
        '用于',
        '指尖',
        '触觉',
        '传感器',
    ]
    raw, atoms, gaps = _facts(
        words,
        pauses_ms=[40] * (len(words) - 1),
        durations_ms=[800] * len(words),
        punctuation=[*([''] * (len(words) - 1)), '。'],
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[1]), _vote(gaps[5])),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == [
        '融合感知技术用于指尖触觉传感器。',
    ]


def test_smart_mode_allows_one_terminal_punctuation_after_safety_limit() -> None:
    words = ['字'] * 28
    raw, atoms, gaps = _facts(
        words,
        pauses_ms=[40] * (len(words) - 1),
        durations_ms=[500] * len(words),
        punctuation=[*([''] * (len(words) - 1)), '。'],
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=tuple(
            _vote(gap, rating='forbidden', reason='phrase_integrity')
            for gap in gaps
        ),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == ['字' * 28 + '。']
    assert artifact.cues[0].display_char_count == 29
    assert artifact.cues[0].strict_overflow_reason == 'terminal_punctuation'


def test_ai_vote_cannot_strand_a_short_terminal_phrase_tail() -> None:
    words = ['为复杂操作', '提供了', '无限', '可能']
    raw, atoms, gaps = _facts(
        words,
        pauses_ms=[40, 40, 40],
        durations_ms=[800] * len(words),
        punctuation=['', '', '', '。'],
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[2]),),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == [
        '为复杂操作提供了无限可能。',
    ]


def test_enumeration_becomes_independent_only_at_real_legal_pauses() -> None:
    words = [
        '原因有三',
        '第一',
        '生物材料与超声技术',
        '第二',
        '全球仅少数顶尖团队在研发',
        '第三',
    ]
    raw, atoms, gaps = _facts(
        words,
        pauses_ms=[400, 400, 400, 400, 400],
        durations_ms=[800] * len(words),
        punctuation=['，', '，', '；', '，', '；', '。'],
    )
    votes = tuple(
        _vote(gap, reason='enumeration')
        for gap in gaps
        if gap.legal_gap
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=votes,
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == [
        word + punctuation
        for word, punctuation in zip(words, ['，', '，', '；', '，', '；', '。'])
    ]

    coarse_raw, coarse_atoms, coarse_gaps = _facts(
        ['第一', '没有真实停顿'],
        coarse_shared_time=True,
    )
    coarse = build_subtitle_global_plan(
        raw_snapshot=coarse_raw,
        atoms=coarse_atoms,
        gaps=coarse_gaps,
    )
    assert len(coarse.cues) == 1


@pytest.mark.parametrize('limit', [8, 10, 12, 13, 14, 17])
def test_strict_mode_matrix_obeys_limit_when_legal_gaps_exist(limit: int) -> None:
    raw, atoms, gaps = _facts(['字'] * 41)

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        profile=_strict(limit),
    )

    assert all(cue.display_char_count <= limit for cue in artifact.cues)
    assert all(cue.strict_overflow_reason is None for cue in artifact.cues)


def test_strict_mode_only_uses_documented_overflow_reasons() -> None:
    raw, atoms, gaps = _facts(
        ['甲乙丙丁', '戊己庚辛'],
        coarse_shared_time=True,
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        profile=_strict(6),
    )

    assert artifact.cues[0].strict_overflow_reason == 'no_legal_gap'
    assert artifact.cues[0].display_char_count > 6


def test_english_mode_keeps_whole_words_and_normal_spaces() -> None:
    words = 'we build reliable subtitle plans from complete spoken words every single time'.split()
    raw, atoms, gaps = _facts(words, language='en')

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
    )

    rendered = ' '.join(_cue_text(raw, cue, english=True) for cue in artifact.cues)
    assert rendered == ' '.join(words)
    assert all(cue.language_mode == 'english_word' for cue in artifact.cues)
    assert all(cue.display_word_count == cue.token_end - cue.token_start for cue in artifact.cues)


def test_ai_vote_does_not_create_unreadable_short_cues() -> None:
    raw, atoms, gaps = _facts(
        ['其实有点', '略平淡'],
        pauses_ms=[400],
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[0]),),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == ['其实有点略平淡']


def test_ai_vote_keeps_a_complete_520ms_short_reply() -> None:
    raw, atoms, gaps = _facts(
        ['”', '是', '的', '机器人', '如此', '灵', '巧', '手', '同样', '如此'],
        durations_ms=[120, 200, 200, 600, 360, 160, 160, 240, 360, 360],
        pauses_ms=[0, 0, 320, 0, 200, 0, 0, 0, 0],
        punctuation=['', '', '，', '', '，', '', '', '', '', '。'],
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[2]), _vote(gaps[4])),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == [
        '”是的，',
        '机器人如此，',
        '灵巧手同样如此。',
    ]


def test_allowed_votes_do_not_fragment_one_complete_clause() -> None:
    words = ['那么我们', '在这里面', '做了', '一二十年']
    raw, atoms, gaps = _facts(
        words,
        durations_ms=[640] * len(words),
        pauses_ms=[0] * (len(words) - 1),
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=tuple(_vote(gap, rating='allowed') for gap in gaps),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == [''.join(words)]


def test_allowed_vote_does_not_split_subject_from_leading_predicate() -> None:
    words = ['而我们超维传感', '采用压阻加应变计的融合感知技术路线']
    raw, atoms, gaps = _facts(
        words,
        durations_ms=[1600, 2800],
        pauses_ms=[0],
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[0], rating='allowed'),),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == [''.join(words)]


def test_boundary_before_attached_punctuation_is_not_selected() -> None:
    words = ['王兴兴', '脱口而出', '：“通用泛化。']
    raw, atoms, gaps = _facts(
        words,
        durations_ms=[800] * len(words),
        pauses_ms=[40, 40],
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[1], rating='preferred'),),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == [''.join(words)]


def test_allowed_vote_does_not_start_a_cue_with_dependent_conjunction() -> None:
    words = ['有了指尖的微小力反馈', '和整体的握持姿态感知']
    raw, atoms, gaps = _facts(
        words,
        durations_ms=[1700, 1700],
        pauses_ms=[0],
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[0], rating='allowed'),),
    )

    assert [_cue_text(raw, cue) for cue in artifact.cues] == [''.join(words)]


def test_ascii_contraction_is_not_split_across_provider_tokens() -> None:
    words = ['话不多说', 'let', "'", 's', ' go']
    raw, atoms, gaps = _facts(
        words,
        pauses_ms=[80, 0, 0, 0],
        punctuation=['，', '', '', '', '！'],
    )
    votes = (_vote(gaps[0]),)

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=votes,
    )

    assert any(atom.text == "let's" and atom.protection == 'hard' for atom in atoms)
    assert gaps[1].disposition == gaps[2].disposition == 'forbidden'
    cue_texts = [_cue_text(raw, cue) for cue in artifact.cues]
    assert ''.join(cue_texts) == "话不多说，let's go！"
    assert sum("let's" in text for text in cue_texts) == 1


def test_planner_does_not_start_a_cue_with_dependent_particle() -> None:
    words = [
        '然后', '我能', '得到', '一个', '台前', '工作者',
        '的认可', '这', '我觉得', '也是一个', '很大的', 'respect',
    ]
    raw, atoms, gaps = _facts(words, durations_ms=[800] * len(words))

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[5]),),
    )

    cue_texts = [_cue_text(raw, cue) for cue in artifact.cues]
    assert not any(text.lstrip().startswith(('的', '地', '得')) for text in cue_texts)
    assert any('台前工作者的认可' in text for text in cue_texts)
    assert ''.join(cue_texts) == ''.join(words)


def test_self_introduction_keeps_alias_and_chinese_name_together() -> None:
    words = [
        '我觉得', '我', '还是要', '重新', '介绍一下', '自己',
        '大家好', '我', '叫做', 'Ranzer', '叶', '润泽',
        '来自', '湖南', '长沙',
    ]
    raw, atoms, gaps = _facts(
        words,
        durations_ms=[800] * len(words),
        punctuation=[
            '', '', '', '', '', '。', '，', '', '', '，', '', '，', '', '', '。',
        ],
    )

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[5]),),
    )

    assert any(
        atom.source == 'phrase_integrity'
        and atom.protection == 'hard'
        and '大家好，我叫做Ranzer，叶润泽' in atom.text
        for atom in atoms
    )
    assert gaps[9].disposition == 'forbidden'
    assert any(
        '大家好，我叫做Ranzer，叶润泽' in _cue_text(raw, cue)
        for cue in artifact.cues
    )
    assert not any(
        '自己。大家好' in _cue_text(raw, cue)
        for cue in artifact.cues
    )


def test_partial_ai_votes_still_plan_the_entire_tape_with_one_planner() -> None:
    raw, atoms, gaps = _facts(['甲乙', '丙丁', '戊己', '庚辛', '壬癸'])

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[1]),),
        profile=_strict(6),
    )

    assert artifact.profile.profile_version == 'subtitle-global-planner.v7'
    assert [token_id for cue in artifact.cues for token_id in cue.token_ids] == [
        token.token_id for token in raw.tokens
    ]
    assert gaps[1].gap_id in {cue.start_gap_id for cue in artifact.cues[1:]}


def test_model_forbidden_vote_cannot_override_strict_length_safety() -> None:
    raw, atoms, gaps = _facts(['字'] * 12)

    artifact = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=(_vote(gaps[5], rating='forbidden'),),
        profile=_strict(6),
    )

    assert all(cue.display_char_count <= 6 for cue in artifact.cues)
    assert all(cue.strict_overflow_reason is None for cue in artifact.cues)


def test_boundary_vote_input_order_does_not_change_plan_or_hash() -> None:
    raw, atoms, gaps = _facts(['甲乙', '丙丁', '戊己', '庚辛'])
    votes = (_vote(gaps[0]), _vote(gaps[2]))

    first = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=votes,
        profile=_strict(6),
    )
    second = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=tuple(reversed(votes)),
        profile=_strict(6),
    )

    assert first == second


def test_empty_snapshot_produces_stable_empty_artifact() -> None:
    raw, atoms, gaps = _facts([])

    first = build_subtitle_global_plan(raw_snapshot=raw, atoms=atoms, gaps=gaps)
    second = build_subtitle_global_plan(raw_snapshot=raw, atoms=atoms, gaps=gaps)

    assert first == second
    assert first.cues == ()
    assert first.raw_token_count == 0


def test_schema_is_frozen_forbids_extra_fields_and_rejects_hash_tampering() -> None:
    raw, atoms, gaps = _facts(['甲', '乙'])
    artifact = build_subtitle_global_plan(raw_snapshot=raw, atoms=atoms, gaps=gaps)

    with pytest.raises(ValidationError):
        artifact.cues[0].cue_id = 'changed'

    extra = artifact.model_dump(mode='json')
    extra['unexpected'] = True
    with pytest.raises(ValidationError):
        SubtitleCuePlanArtifact.model_validate(extra)

    tampered = deepcopy(artifact.model_dump(mode='json'))
    tampered['artifact_hash'] = '0' * 64
    with pytest.raises(ValidationError, match='artifact_hash'):
        SubtitleCuePlanArtifact.model_validate(tampered)

    cue_tampered = deepcopy(artifact.model_dump(mode='json'))
    cue_tampered['cues'][0]['cue_id'] = 'cue-tampered'
    cue_tampered['artifact_hash'] = canonical_sha256({
        key: value
        for key, value in cue_tampered.items()
        if key != 'artifact_hash'
    })
    with pytest.raises(ValidationError, match='cue_id'):
        SubtitleCuePlanArtifact.model_validate(cue_tampered)


@pytest.mark.parametrize('limit', [5, 31])
def test_strict_profile_rejects_out_of_range_limits(limit: int) -> None:
    with pytest.raises(ValidationError):
        CuePlannerProfile(mode='strict', strict_max_display_units=limit)


def test_planner_does_not_modify_raw_atoms_or_gaps() -> None:
    raw, atoms, gaps = _facts(['甲乙', '丙丁', '戊己'])
    before = (
        raw.model_dump(mode='json'),
        [atom.model_dump(mode='json') for atom in atoms],
        [gap.model_dump(mode='json') for gap in gaps],
    )

    build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        profile=_strict(6),
    )

    assert raw.model_dump(mode='json') == before[0]
    assert [atom.model_dump(mode='json') for atom in atoms] == before[1]
    assert [gap.model_dump(mode='json') for gap in gaps] == before[2]
