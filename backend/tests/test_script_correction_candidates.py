from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.services import script_correction_candidates as candidate_module
from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.script_correction_candidates import (
    ScriptSnapshot,
    build_correction_candidates,
    build_script_snapshot,
)
from app.services.script_line_alignment import build_exact_script_line_alignments
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2


def _raw_snapshot(sentences: list[dict]):
    return build_raw_asr_snapshot(
        {'transcripts': [{'sentences': sentences}]},
        provider_model='fun-asr',
        provider_task_id='task-script-candidates',
        language='zh',
        request_metadata={},
        preflight_metadata={
            'duration_ms': max([int(item.get('end_time') or 0) for item in sentences] or [0]),
            'sample_rate_hz': 24000,
            'channel_count': 1,
            'status': 'accepted',
        },
    )


def _sentence(words: list[str], *, speaker: int = 0, start: int = 0) -> dict:
    provider_words = []
    cursor = start
    for word in words:
        provider_words.append({'text': word, 'begin_time': cursor, 'end_time': cursor + 100})
        cursor += 100
    return {
        'speaker_id': speaker,
        'begin_time': start,
        'end_time': cursor,
        'text': ''.join(words),
        'words': provider_words,
    }


def _candidates(sentences: list[dict], script: str, *, bundle: dict | None = None):
    raw = _raw_snapshot(sentences)
    semantic = build_semantic_utterances_v2(raw)
    script_snapshot = build_script_snapshot(script)
    return build_correction_candidates(
        run_id='run-script-candidates',
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script_snapshot,
        runtime_lexicon_bundle=bundle,
    )


@pytest.mark.parametrize(('source', 'replacement'), [
    ('十五元', '五十元'), ('300元', '3000元'), ('3到4厘米', '三四厘米'),
])
def test_learned_candidates_cannot_override_numeric_facts(source, replacement):
    candidates = _candidates([_sentence([source])], '', bundle={'learned_corrections': [{
        'source_text': source, 'replacement_text': replacement, 'term_id': 'learned-1',
        'scope_type': 'project', 'confidence': 1.0, 'evidence_count': 3, 'project_count': 1,
    }]})
    assert candidates == ()


@pytest.mark.parametrize(
    ('source', 'source_name', 'expected_kind'),
    [
        (b'\xe7\xac\xac\xe4\xb8\x80\xe8\xa1\x8c\n\xe7\xac\xac\xe4\xba\x8c\xe8\xa1\x8c', 'script.txt', 'txt'),
        (b'# \xe6\xa0\x87\xe9\xa2\x98\n\xe5\x86\x85\xe5\xae\xb9', 'script.md', 'md'),
        (b'1\n00:00:01,000 --> 00:00:02,000\n\xe5\x94\xb1\xe8\xaf\x8d\xe4\xb8\x80\n', 'script.srt', 'srt'),
        ('直接粘贴\n第二段', None, 'pasted'),
    ],
)
def test_script_snapshot_preserves_order_lines_paragraphs_and_source_hash(
    source: str | bytes,
    source_name: str | None,
    expected_kind: str,
) -> None:
    first = build_script_snapshot(source, source_name=source_name)
    second = build_script_snapshot(source, source_name=source_name)

    assert first.source_kind == expected_kind
    assert first.extraction_status == 'ready'
    assert [line.order_index for line in first.lines] == list(range(len(first.lines)))
    assert all(line.line_number >= 1 for line in first.lines)
    assert first.source_hash == second.source_hash
    assert first.snapshot_hash == second.snapshot_hash
    assert first == second


def test_script_snapshot_keeps_original_text_and_builds_simplified_match_view() -> None:
    snapshot = build_script_snapshot('臺灣原文\n\n第二段')

    assert [line.text for line in snapshot.lines] == ['臺灣原文', '第二段']
    assert [line.match_text for line in snapshot.lines] == ['台湾原文', '第二段']
    assert [line.paragraph_index for line in snapshot.lines] == [0, 1]
    assert [line.line_number for line in snapshot.lines] == [1, 3]


def test_script_snapshot_sanitizes_source_name_and_chunks_long_physical_lines() -> None:
    snapshot = build_script_snapshot(
        ('甲' * 161).encode(),
        source_name='/private/台本/episode.txt',
    )

    assert snapshot.source_name == 'episode.txt'
    assert [len(line.text) for line in snapshot.lines] == [160, 1]
    assert [line.line_number for line in snapshot.lines] == [1, 1]
    assert [line.paragraph_index for line in snapshot.lines] == [0, 0]
    assert [line.order_index for line in snapshot.lines] == [0, 1]


def test_script_snapshot_is_frozen_strict_and_rejects_hash_tampering() -> None:
    snapshot = build_script_snapshot('第一行')

    with pytest.raises(ValidationError):
        snapshot.source_hash = snapshot.source_hash

    with_extra = snapshot.model_dump(mode='json')
    with_extra['unexpected'] = True
    with pytest.raises(ValidationError):
        ScriptSnapshot.model_validate(with_extra)

    tampered = deepcopy(snapshot.model_dump(mode='json'))
    tampered['lines'][0]['text'] = '篡改'
    with pytest.raises(ValidationError, match='snapshot_hash'):
        ScriptSnapshot.model_validate(tampered)


def test_evidence_backed_candidate_corrects_zha_ji_village_from_script_line() -> None:
    candidates = _candidates(
        [_sentence(['我们', '来到', '扎记村', '现在', '留存', '建筑'])],
        '我们来到查济村现在留存建筑',
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_text == '扎记村'
    assert candidate.replacement_text == '查济村'
    assert candidate.token_ids == ('token-000002',)
    assert candidate.phonetic_score >= 0.88
    assert candidate.context_score >= 0.70
    assert candidate.replacement_text in '我们来到查济村现在留存建筑'
    assert candidate.candidate_id.startswith('correction-')


def test_exact_long_line_reacquires_later_script_region_before_correction() -> None:
    prefix = '\n'.join(f'无关流程第{index}行' for index in range(20))
    candidates = _candidates(
        [
            _sentence(['现在', '进入', '正式', '环节']),
            _sentence(
                ['我们', '来到', '扎记村', '现在', '参观'],
                speaker=1,
                start=500,
            ),
        ],
        f'{prefix}\n现在进入正式环节\n我们来到查济村现在参观',
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('扎记村', '查济村'),
    ]


def test_candidate_requires_local_phonetic_support_not_only_long_context() -> None:
    candidates = _candidates(
        [_sentence([
            '除', '4', '名', '名人', '堂', '成员', '外', '其余', '14', '位',
            'rapper', '需', '组成', '7', '队', '双', '人', '组合',
        ])],
        '除4位名人堂成员外其余14位rapper需组成7对双人组合',
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('7队', '7对'),
    ]


def test_script_candidate_does_not_insert_words_missing_from_asr() -> None:
    candidates = _candidates(
        [_sentence([
            '组队', '选择', '权', '从', '参与', '名人', '堂', '挑战', '的',
            '5', '位', 'rapper', '开始',
        ])],
        '组队选择权从参与名人堂挑战赛的5位rapper开始',
    )

    assert candidates == ()


def test_script_term_corrects_same_length_homophone_without_local_line_context() -> None:
    candidates = _candidates(
        [_sentence(['那么', '接下来', '我们', '巅峰', '财', '团', '的', '成员'])],
        '每个人表演结束后由巅峰裁团投票决定最终成绩',
        bundle={
            'terms': [{
                'term_id': 'term-peak-jury',
                'display_text': '巅峰裁团',
                'protection_level': 'strong',
            }],
        },
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('巅峰财团', '巅峰裁团'),
    ]


def test_script_term_corrects_short_homophone_without_local_line_context() -> None:
    candidates = _candidates(
        [_sentence(['现在', '公布', '张', '俊', '豪', '的', '选择'])],
        '第六位是张峻豪',
        bundle={
            'terms': [{
                'term_id': 'term-zhang-junhao',
                'display_text': '张峻豪',
                'protection_level': 'strong',
            }],
        },
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('张俊豪', '张峻豪'),
    ]


def test_ranked_script_person_corrects_latin_spelling_and_multiple_homophones() -> None:
    candidates = _candidates(
        [_sentence(['现在', '公布', 'miss', 'co', '葛', '中', '山'])],
        '第五位是Miss Ko葛仲珊',
        bundle={
            'terms': [
                {
                    'term_id': 'term-miss-ko',
                    'display_text': 'Miss Ko',
                    'protection_level': 'strong',
                },
                {
                    'term_id': 'term-ge-zhongshan',
                    'display_text': '葛仲珊',
                    'protection_level': 'strong',
                },
            ],
        },
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('missco', 'Miss Ko'),
        ('葛中山', '葛仲珊'),
    ]


def test_intro_roster_corrects_nearby_latin_spelling_with_length_difference() -> None:
    candidates = _candidates(
        [_sentence(['欢迎', 'repeater', '回到', '现场'])],
        '有请首轮名人堂成员——艾热、rapeter、功夫胖',
        bundle={
            'terms': [{
                'term_id': 'term-rapeter',
                'display_text': 'rapeter',
                'protection_level': 'hard',
            }],
        },
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('repeater', 'rapeter'),
    ]
    assert candidates[0].lexicon_protection_level == 'hard'


def test_multiple_homophones_still_require_ranked_person_evidence() -> None:
    candidates = _candidates(
        [_sentence(['葛', '中', '山', '来到', '现场'])],
        '欢迎葛仲珊来到现场',
        bundle={
            'terms': [{
                'term_id': 'term-ge-zhongshan',
                'display_text': '葛仲珊',
                'protection_level': 'strong',
            }],
        },
    )

    assert candidates == ()


def test_script_term_can_recur_after_script_cursor_passes_its_evidence() -> None:
    candidates = _candidates(
        [
            _sentence(['现在', '进入', '下一', '环节']),
            _sentence(['再次', '欢迎', '张', '俊', '豪'], start=500),
        ],
        '第六位是张峻豪\n现在进入下一环节',
        bundle={
            'terms': [{
                'term_id': 'term-zhang-junhao',
                'display_text': '张峻豪',
                'protection_level': 'strong',
            }],
        },
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('张俊豪', '张峻豪'),
    ]


def test_gold_runtime_alias_expands_short_name_to_canonical_display() -> None:
    candidates = _candidates(
        [_sentence(['欢迎', 'shark', '来到', '舞台'])],
        '人工金标规范人名——SHarK米尔艾力；别名：SHarK、米尔艾力',
        bundle={
            'terms': [{
                'term_id': 'gold-shark',
                'normalized_term': 'SHarK米尔艾力',
                'display_text': 'SHarK米尔艾力',
                'aliases': ['SHarK', '米尔艾力'],
                'protection_level': 'hard',
            }],
        },
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('shark', 'SHarK米尔艾力'),
    ]


def test_database_runtime_alias_uses_canonical_display_for_correction() -> None:
    candidates = _candidates(
        [_sentence(['欢迎', '忍者', '来到', '舞台'])],
        '人工金标规范人名——RANZER叶润泽；别名：忍者',
        bundle={
            'terms': [{
                'term_id': 'gold-ranzer',
                'normalized_term': 'RANZER叶润泽',
                'display_text': 'RANZER叶润泽',
                'aliases': ['忍者'],
                'protection_level': 'hard',
            }],
        },
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('忍者', 'RANZER叶润泽'),
    ]


def test_runtime_alias_prefers_complete_fragmented_name_over_short_prefix() -> None:
    candidates = _candidates(
        [_sentence(['欢迎', 'r', 'ans', 'er', '来到', '舞台'])],
        '项目强制词库人名——RANZER叶润泽；别名：rans、ranser',
        bundle={
            'terms': [{
                'term_id': 'gold-ranzer',
                'display_text': 'RANZER叶润泽',
                'aliases': ['rans', 'ranser'],
                'protection_level': 'hard',
            }],
        },
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('ranser', 'RANZER叶润泽'),
    ]


def test_runtime_alias_coalesces_adjacent_latin_and_cjk_names() -> None:
    candidates = _candidates(
        [_sentence(['我叫', 'R', 'enz', 'r', '叶', '润', '泽', '来自', '长沙'])],
        '项目强制词库人名——RANZER叶润泽；别名：Renzr、叶润泽',
        bundle={
            'terms': [{
                'term_id': 'gold-ranzer',
                'display_text': 'RANZER叶润泽',
                'aliases': ['Renzr', '叶润泽'],
                'protection_level': 'hard',
            }],
        },
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('renzr叶润泽', 'RANZER叶润泽'),
    ]


def test_candidate_id_and_scores_are_stable_for_identical_inputs() -> None:
    sentences = [_sentence(['我们', '来到', '扎记村', '现在', '留存', '建筑'])]

    first = _candidates(sentences, '我们来到查济村现在留存建筑')
    second = _candidates(deepcopy(sentences), '我们来到查济村现在留存建筑')

    assert first == second


def test_empty_run_id_is_rejected_even_when_script_has_no_candidates() -> None:
    raw = _raw_snapshot([_sentence(['原文'])])
    semantic = build_semantic_utterances_v2(raw)

    with pytest.raises(ValueError, match='run_id'):
        build_correction_candidates(
            run_id=' ',
            raw_snapshot=raw,
            semantic_artifact=semantic,
            script_snapshot=build_script_snapshot(''),
        )


@pytest.mark.parametrize(
    ('words', 'source_text', 'replacement_text', 'rule_id'),
    [
        (
            ['他', '说', '的', '很', '清楚'],
            '说的很清楚',
            '说得很清楚',
            'complement-de',
        ),
        (
            ['请', '认真', '得', '回答', '问题'],
            '认真得回答',
            '认真地回答',
            'adverbial-di',
        ),
        (
            ['这是', '美丽', '地', '风景'],
            '美丽地风景',
            '美丽的风景',
            'attributive-de',
        ),
    ],
)
def test_grammar_particle_candidates_work_without_a_script(
    words: list[str],
    source_text: str,
    replacement_text: str,
    rule_id: str,
) -> None:
    candidates = _candidates([_sentence(words)], '')

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_text == source_text
    assert candidate.replacement_text == replacement_text
    assert candidate.candidate_type == 'grammar'
    assert candidate.lexicon_term_id is None
    assert f'grammar-rule:{rule_id}' in candidate.evidence_refs


@pytest.mark.parametrize(
    'words',
    [
        ['我', '觉得', '这个', '目的地', '很好'],
        ['他', '说', '得', '很', '清楚'],
        ['请', '认真', '地', '回答', '问题'],
        ['这是', '美丽', '的', '风景'],
    ],
)
def test_grammar_particle_candidates_keep_fixed_and_correct_expressions(
    words: list[str],
) -> None:
    assert _candidates([_sentence(words)], '') == ()


@pytest.mark.parametrize(
    ('words', 'script'),
    [
        (['今天', '来到', '这里'], '安徽省宣城市'),
        (['甲乙', '扎记村', '丙丁'], '完全不同查济村另一个语境'),
        (['我们', '来到', '火车站', '现在', '参观'], '我们来到查济村现在参观'),
        (['扎', '无关', '记村', '现在'], '查济村现在'),
    ],
)
def test_hard_gates_reject_unsupported_phonetic_context_and_noncontiguous_matches(
    words: list[str],
    script: str,
) -> None:
    assert _candidates([_sentence(words)], script) == ()


def test_candidate_span_never_crosses_speaker_boundaries() -> None:
    candidates = _candidates(
        [
            _sentence(['我们', '来到', '扎记'], speaker=0, start=0),
            _sentence(['村', '现在', '参观'], speaker=1, start=300),
        ],
        '我们来到查济村现在参观',
    )

    assert candidates == ()


def test_script_order_must_be_monotonic_across_utterances() -> None:
    candidates = _candidates(
        [
            _sentence(['我们', '来到', '扎记村', '现在'], speaker=0, start=0),
            _sentence(['接着', '参观', '原历馆', '展览'], speaker=1, start=400),
        ],
        '接着参观园林馆展览\n我们来到查济村现在',
    )

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('扎记村', '查济村'),
    ]


def test_same_span_conflict_is_rejected_when_top_margin_is_too_small() -> None:
    candidates = _candidates(
        [_sentence(['我们', '来到', '扎记村', '现在'])],
        '我们来到查济村现在\n我们来到茶济村现在',
    )

    assert candidates == ()


def test_runtime_lexicon_can_rank_but_cannot_bypass_context_gate() -> None:
    bundle = {
        'bundle_version': 'runtime_lexicon_bundle.v1',
        'terms': [{
            'term_id': 'term-chaji',
            'display_text': '查济村',
            'normalized_term': '查济村',
            'protection_level': 'hard',
        }],
    }

    candidates = _candidates(
        [_sentence(['甲乙', '扎记村', '丙丁'])],
        '完全不同查济村另一个语境',
        bundle=bundle,
    )

    assert candidates == ()


def test_large_unrelated_script_does_not_expand_into_phonetic_cartesian_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentences = []
    cursor = 0
    for index in range(40):
        sentences.append(_sentence(
            ['我们', '正在', '讨论', f'现场内容{index}', '接下来', '继续'],
            speaker=index % 2,
            start=cursor,
        ))
        cursor += 700
    script = '\n'.join(
        f'台本流程第{index}行：嘉宾按照既定顺序完成舞台准备和灯光确认。'
        for index in range(160)
    )
    phonetic_calls = 0
    original = candidate_module._phonetic_similarity

    def count_phonetic_calls(left: str, right: str) -> float:
        nonlocal phonetic_calls
        phonetic_calls += 1
        return original(left, right)

    monkeypatch.setattr(
        candidate_module,
        '_phonetic_similarity',
        count_phonetic_calls,
    )

    assert _candidates(sentences, script) == ()
    assert phonetic_calls == 0


def test_large_runtime_lexicon_is_indexed_before_span_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentences = []
    cursor = 0
    for index in range(40):
        sentences.append(_sentence(
            ['我们', '正在', '讨论', f'现场内容{index}', '接下来', '继续'],
            speaker=index % 2,
            start=cursor,
        ))
        cursor += 700
    terms = [f'候选专名{index:03d}' for index in range(120)]
    script = '\n'.join(f'第{index + 1}位是{term}' for index, term in enumerate(terms))
    bundle = {
        'terms': [
            {
                'term_id': f'term-{index}',
                'display_text': term,
                'protection_level': 'strong',
            }
            for index, term in enumerate(terms)
        ],
    }
    phonetic_calls = 0
    original = candidate_module._phonetic_similarity

    def count_phonetic_calls(left: str, right: str) -> float:
        nonlocal phonetic_calls
        phonetic_calls += 1
        return original(left, right)

    monkeypatch.setattr(
        candidate_module,
        '_phonetic_similarity',
        count_phonetic_calls,
    )

    assert _candidates(sentences, script, bundle=bundle) == ()
    assert phonetic_calls == 0


@pytest.mark.parametrize(
    ('raw_term', 'script_term', 'expected'),
    [
        ('ai', 'AI', 'AI'),
        ('AI', 'ai', 'ai'),
        ('deepseekr1', 'DeepSeek R1', 'DeepSeek R1'),
    ],
)
def test_exact_script_alignment_emits_display_only_candidate(
    raw_term: str,
    script_term: str,
    expected: str,
) -> None:
    candidates = _candidates(
        [_sentence(['欢迎', '来到', raw_term, '技术', '盛典'])],
        f'欢迎来到{script_term}技术盛典',
    )

    display_candidates = [
        item
        for item in candidates
        if 'script-display-fidelity:case-space' in item.evidence_refs
    ]
    assert len(display_candidates) == 1
    assert display_candidates[0].source_text == raw_term
    assert display_candidates[0].replacement_text == expected
    assert display_candidates[0].token_ids == ('token-000002',)


def test_precomputed_alignments_avoid_internal_recomputation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_snapshot([_sentence(['欢迎', '来到', 'ai', '技术', '盛典'])])
    semantic = build_semantic_utterances_v2(raw)
    script = build_script_snapshot('欢迎来到AI技术盛典')
    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )
    monkeypatch.setattr(
        candidate_module,
        'build_exact_script_line_alignments',
        pytest.fail,
    )

    candidates = build_correction_candidates(
        run_id='run-precomputed-alignments',
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
        script_alignments=alignments,
    )

    assert candidates[0].source_text == 'ai'
    assert candidates[0].replacement_text == 'AI'


def test_precomputed_alignments_reject_different_raw_tokenization() -> None:
    script = build_script_snapshot('欢迎来到AI技术盛典')
    source_raw = _raw_snapshot([
        _sentence(['欢迎来到', 'ai', '技术盛典']),
    ])
    source_alignments = build_exact_script_line_alignments(
        raw_snapshot=source_raw,
        semantic_artifact=build_semantic_utterances_v2(source_raw),
        script_snapshot=script,
    )
    current_raw = _raw_snapshot([
        _sentence(['欢迎', '来到ai', '技术盛典']),
    ])

    with pytest.raises(ValueError, match='precomputed script alignment'):
        build_correction_candidates(
            run_id='run-stale-precomputed-alignments',
            raw_snapshot=current_raw,
            semantic_artifact=build_semantic_utterances_v2(current_raw),
            script_snapshot=script,
            script_alignments=source_alignments,
        )


@pytest.mark.parametrize('use_precomputed_alignments', [False, True])
@pytest.mark.parametrize(
    ('words', 'expected_display_changes'),
    [
        (
            ['前缀欢迎来到', 'ai', '技术盛典'],
            [('ai', 'AI', ('token-000001',))],
        ),
        (
            ['欢迎来到', 'ai', '技术盛典后缀'],
            [('ai', 'AI', ('token-000001',))],
        ),
        (
            ['前缀欢迎来到ai技术盛典后缀'],
            [],
        ),
    ],
    ids=['partial-start', 'partial-end', 'partial-both'],
)
def test_partial_token_exact_alignment_uses_only_fully_contained_tokens(
    words: list[str],
    expected_display_changes: list[tuple[str, str, tuple[str, ...]]],
    use_precomputed_alignments: bool,
) -> None:
    raw = _raw_snapshot([_sentence(words)])
    semantic = build_semantic_utterances_v2(raw)
    script = build_script_snapshot('欢迎来到AI技术盛典')
    alignments = build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )

    candidates = build_correction_candidates(
        run_id='run-partial-token-alignment',
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
        script_alignments=(
            alignments if use_precomputed_alignments else None
        ),
    )

    assert [
        (item.source_text, item.replacement_text, item.token_ids)
        for item in candidates
        if 'script-display-fidelity:case-space' in item.evidence_refs
    ] == expected_display_changes


def test_display_fidelity_does_not_inject_script_punctuation_or_missing_words() -> None:
    candidates = _candidates(
        [_sentence(['欢迎', '来到', 'ai', '盛典'])],
        '欢迎来到AI，创新盛典',
    )

    assert all(
        'script-display-fidelity:case-space' not in item.evidence_refs
        for item in candidates
    )


@pytest.mark.parametrize(
    ('raw_term', 'script_term'),
    [
        ('ai,', 'AI'),
        ('臺灣ai', '台湾AI'),
    ],
)
def test_display_fidelity_token_expansion_preserves_non_display_characters(
    raw_term: str,
    script_term: str,
) -> None:
    candidates = _candidates(
        [_sentence(['欢迎', '来到', raw_term, '技术', '盛典'])],
        f'欢迎来到{script_term}技术盛典',
    )

    assert all(
        'script-display-fidelity:case-space' not in item.evidence_refs
        for item in candidates
    )
