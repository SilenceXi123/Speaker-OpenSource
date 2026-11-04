from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.script_correction_candidates import CorrectionCandidate
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2
from app.services.subtitle_atoms import (
    build_token_bound_lexical_atoms,
    split_text_by_atom_length,
)


def test_repeated_text_reuses_analysis_without_sharing_mutable_results(monkeypatch) -> None:
    from app.services import subtitle_atoms as atoms

    analyze = atoms.resolve_linguistic_spans
    calls = []

    def traced(text, **kwargs):
        calls.append((text, kwargs))
        return analyze(text, **kwargs)

    monkeypatch.setattr(atoms, 'resolve_linguistic_spans', traced)
    text = '来自星河数智有限公司的朋友'
    first = atoms.build_lexical_atoms(text, protected_terms=('星河数智有限公司',))
    expected = list(first)
    first.clear()
    for _ in range(20):
        assert atoms.build_lexical_atoms(text, protected_terms=('星河数智有限公司',)) == expected
    assert len(calls) == 1
    atoms.build_lexical_atoms(text, protected_terms=('星河数智',))
    assert len(calls) == 2


def test_atom_analysis_follows_runtime_term_changes() -> None:
    from app.services.subtitle_atoms import build_lexical_atoms

    bundle = {'term_set_hash': 'atom-cache-before', 'terms': []}
    text = '星河数智体验馆'
    before = build_lexical_atoms(text, runtime_lexicon_bundle=bundle)
    bundle['term_set_hash'] = 'atom-cache-after'
    bundle['terms'].append({'normalized_term': text, 'protection_level': 'hard'})
    after = build_lexical_atoms(text, runtime_lexicon_bundle=bundle)
    assert any(atom.text == text and atom.kind == 'runtime_lexicon_hard' for atom in after)
    assert before != after


def _snapshot(words: list[str]):
    provider_words = []
    cursor = 0
    for word in words:
        provider_words.append({
            'text': word,
            'begin_time': cursor,
            'end_time': cursor + 100,
        })
        cursor += 100
    return build_raw_asr_snapshot(
        {'transcripts': [{'sentences': [{
            'speaker_id': 0,
            'begin_time': 0,
            'end_time': cursor,
            'text': ''.join(words),
            'words': provider_words,
        }]}]},
        provider_model='fun-asr',
        provider_task_id='task-token-atoms',
        language='zh',
        request_metadata={},
        preflight_metadata={
            'duration_ms': cursor,
            'sample_rate_hz': 24000,
            'channel_count': 1,
            'status': 'accepted',
        },
    )


def _token_atoms(
    words: list[str],
    *,
    runtime_lexicon_bundle: dict | None = None,
    confirmed_terms: tuple[str, ...] = (),
    accepted_corrections: tuple[CorrectionCandidate, ...] = (),
):
    raw = _snapshot(words)
    semantic = build_semantic_utterances_v2(raw)
    atoms = build_token_bound_lexical_atoms(
        raw,
        semantic,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        confirmed_terms=confirmed_terms,
        accepted_corrections=accepted_corrections,
    )
    return raw, semantic, atoms


def test_split_text_by_atom_length_keeps_protected_terms_whole() -> None:
    chunks = split_text_by_atom_length(
        '是的像亚洲青年赛艇锦标赛和沈阳赛艇国际公开赛呢',
        14,
        protected_terms=('亚洲青年赛艇锦标赛', '沈阳赛艇国际公开赛'),
    )

    assert ''.join(chunks) == '是的像亚洲青年赛艇锦标赛和沈阳赛艇国际公开赛呢'
    assert any('亚洲青年赛艇锦标赛' in chunk for chunk in chunks)
    assert any('沈阳赛艇国际公开赛' in chunk for chunk in chunks)
    assert not any(
        chunks[index].endswith('沈') and chunks[index + 1].startswith('阳')
        for index in range(len(chunks) - 1)
    )


def test_split_text_by_atom_length_keeps_long_english_word_whole() -> None:
    chunks = split_text_by_atom_length(
        'Supercalifragilisticexpialidocious就是一个完整英文单词',
        14,
    )

    assert ''.join(chunks) == 'Supercalifragilisticexpialidocious就是一个完整英文单词'
    assert chunks[0] == 'Supercalifragilisticexpialidocious'
    assert not any(
        'Supercalifragilisticexpialidocious'.startswith(chunk)
        and chunk != 'Supercalifragilisticexpialidocious'
        for chunk in chunks
    )


def test_split_text_by_atom_length_keeps_reduplicated_chinese_word_whole() -> None:
    chunks = split_text_by_atom_length(
        '这样非正式的会谈当中慢慢地产生的',
        14,
        protected_terms=('非正式', '会谈当中'),
    )

    assert ''.join(chunks) == '这样非正式的会谈当中慢慢地产生的'
    assert not any(
        chunks[index].endswith('慢') and chunks[index + 1].startswith('慢')
        for index in range(len(chunks) - 1)
    )


def test_split_text_by_atom_length_keeps_corporate_name_whole() -> None:
    chunks = split_text_by_atom_length(
        '还有元力无限智能科技杭州有限公司联合创始人刘洋',
        14,
    )

    assert ''.join(chunks) == '还有元力无限智能科技杭州有限公司联合创始人刘洋'
    assert any('元力无限智能科技杭州有限公司' in chunk for chunk in chunks)
    assert not any(
        chunks[index].endswith('有限公') and chunks[index + 1].startswith('司')
        for index in range(len(chunks) - 1)
    )


def test_split_text_by_atom_length_keeps_common_chinese_words_whole() -> None:
    chunks = split_text_by_atom_length(
        '其实是我们希望用最聪明的大脑理解创业的魅力所在',
        14,
    )

    assert ''.join(chunks) == '其实是我们希望用最聪明的大脑理解创业的魅力所在'
    assert not any(
        (chunks[index].endswith('聪') and chunks[index + 1].startswith('明'))
        or (chunks[index].endswith('创') and chunks[index + 1].startswith('业'))
        for index in range(len(chunks) - 1)
    )


@pytest.mark.parametrize(
    ('words', 'expected_text'),
    [
        (['行', '业'], '行业'),
        (['慢', '慢'], '慢慢'),
        (['璀', '璨'], '璀璨'),
        (['人工', '智能'], '人工智能'),
        (['力', '控'], '力控'),
        (['北京元空', '智能科技', '有限公司'], '北京元空智能科技有限公司'),
        (['原力', '无限'], '原力无限'),
        (['学习', '一下'], '学习一下'),
        (['投下了', '这一票'], '投下了这一票'),
        (['OpenAI'], 'OpenAI'),
        (['U.S.A.'], 'U.S.A.'),
        (['AI+'], 'AI+'),
    ],
)
def test_token_bound_atoms_keep_required_terms_whole(
    words: list[str],
    expected_text: str,
) -> None:
    _raw, _semantic, atoms = _token_atoms(words)

    atom = next(item for item in atoms if item.text == expected_text)
    assert atom.protection == 'hard'
    assert atom.token_end > atom.token_start
    assert atom.token_ids == tuple(
        f'token-{index:06d}' for index in range(len(words))
    )


def test_confirmed_name_atom_includes_adjacent_role_title() -> None:
    _raw, _semantic, atoms = _token_atoms(
        ['工作人员', '李丹', '发言'],
        confirmed_terms=('李丹',),
    )

    atom = next(item for item in atoms if item.text == '工作人员李丹')
    assert atom.source == 'confirmed_name'
    assert atom.protection == 'hard'
    assert atom.token_ids == ('token-000000', 'token-000001')


def test_token_bound_atom_text_matches_the_complete_bound_token() -> None:
    _raw, _semantic, atoms = _token_atoms(['在行业里'])

    atom = atoms[0]
    assert atom.token_ids == ('token-000000',)
    assert atom.text == '在行业里'


def test_runtime_lexicon_levels_are_preserved_on_token_ranges() -> None:
    bundle = {
        'bundle_version': 'runtime_lexicon_bundle.v1',
        'term_set_hash': 'token-atom-levels',
        'terms': [
            {'term_id': 'hard-id', 'display_text': '硬词条', 'normalized_term': '硬词条', 'protection_level': 'hard'},
            {'term_id': 'strong-id', 'display_text': '强词条', 'normalized_term': '强词条', 'protection_level': 'strong'},
            {'term_id': 'soft-id', 'display_text': '软词条', 'normalized_term': '软词条', 'protection_level': 'soft'},
        ],
    }
    _raw, _semantic, atoms = _token_atoms(
        ['硬词', '条', '强词', '条', '软词', '条'],
        runtime_lexicon_bundle=bundle,
    )

    by_text = {item.text: item for item in atoms}
    assert by_text['硬词条'].protection == 'hard'
    assert by_text['强词条'].protection == 'strong'
    assert by_text['软词条'].protection == 'soft'
    assert by_text['硬词条'].evidence_refs == ('hard-id',)


def test_numeric_date_unit_enumeration_and_reduplication_are_token_bound() -> None:
    _raw, _semantic, atoms = _token_atoms(
        ['第', '一', '2026', '年', '7', '月', '18', '日', '3.5', '公里', '慢', '慢'],
    )

    by_source = {item.source: item for item in atoms}
    assert by_source['enumeration'].text == '第一'
    assert any(item.source == 'number_date_unit' and item.text == '2026年7月18日' for item in atoms)
    assert any(item.source == 'number_date_unit' and item.text == '3.5公里' for item in atoms)
    assert any(item.source == 'cjk_reduplication' and item.text == '慢慢' for item in atoms)


@pytest.mark.parametrize(
    'words',
    [['2026', '年'], ['3', 'mm'], ['50', '%'], ['50', '万次']],
)
def test_short_number_unit_expressions_remain_whole(words: list[str]) -> None:
    _raw, _semantic, atoms = _token_atoms(words)

    assert any(
        item.source == 'number_date_unit'
        and item.text == ''.join(words)
        and len(item.token_ids) == len(words)
        for item in atoms
    )


def test_per_job_jieba_span_is_exposed_as_soft_token_atom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTokenizer:
        def cut(self, _text: str, *, cut_all: bool = False):
            assert cut_all is False
            return ['量子飞轮']

    monkeypatch.setattr(
        'app.services.subtitle_linguistic_spans.build_jieba_tokenizer',
        lambda _bundle: FakeTokenizer(),
    )
    bundle = {
        'bundle_version': 'runtime_lexicon_bundle.v1',
        'term_set_hash': 'per-job-cws',
        'terms': [],
    }

    _raw, _semantic, atoms = _token_atoms(
        ['量子', '飞轮'],
        runtime_lexicon_bundle=bundle,
    )

    atom = next(item for item in atoms if item.text == '量子飞轮')
    assert atom.source == 'jieba'
    assert atom.protection == 'soft'


def test_only_accepted_correction_becomes_temporary_hard_atom() -> None:
    raw = _snapshot(['扎记', '村'])
    semantic = build_semantic_utterances_v2(raw)
    correction = CorrectionCandidate(
        candidate_id='correction-chaji',
        run_id='run-atoms',
        utterance_id=semantic.utterances[0].utterance_id,
        token_ids=('token-000000', 'token-000001'),
        source_text='扎记村',
        replacement_text='查济村',
        script_line_id='script-line-chaji',
        script_line_order=0,
        script_source_hash='0' * 64,
        phonetic_score=0.9,
        context_score=0.9,
        ranking_score=0.9,
        margin=1.0,
    )

    base_atoms = build_token_bound_lexical_atoms(raw, semantic)
    planner_atoms = build_token_bound_lexical_atoms(
        raw,
        semantic,
        accepted_corrections=(correction,),
    )

    assert not any(item.source == 'accepted_script_correction' for item in base_atoms)
    overlay = next(item for item in planner_atoms if item.source == 'accepted_script_correction')
    assert overlay.text == '查济村'
    assert overlay.token_ids == ('token-000000', 'token-000001')
    assert overlay.protection == 'hard'
    assert overlay.evidence_refs == ('correction-chaji',)

    forged = correction.model_copy(update={'source_text': '不对应原始词'})
    with pytest.raises(ValueError, match='source text'):
        build_token_bound_lexical_atoms(
            raw,
            semantic,
            accepted_corrections=(forged,),
        )


def test_contextual_script_correction_does_not_become_one_long_hard_atom() -> None:
    words = [
        '融合感知技术路线',
        '通过',
        '底层',
        '材料',
        '创新',
        '和',
        '多',
        '物理量',
        '融合',
        '感知',
        '技术',
        '把',
        '位置信息',
    ]
    raw = _snapshot(words)
    semantic = build_semantic_utterances_v2(raw)
    correction = CorrectionCandidate(
        candidate_id='correction-technology-route',
        run_id='run-atoms',
        utterance_id=semantic.utterances[0].utterance_id,
        token_ids=tuple(token.token_id for token in raw.tokens[:12]),
        source_text=''.join(words[:12]),
        replacement_text=(
            '融合感知技术路线通过底层材料创新和多'
            '传感器融合感知技术在'
        ),
        script_line_id='script-line-technology-route',
        script_line_order=0,
        script_source_hash='0' * 64,
        phonetic_score=0.9,
        context_score=0.9,
        ranking_score=0.9,
        margin=1.0,
    )

    atoms = build_token_bound_lexical_atoms(
        raw,
        semantic,
        accepted_corrections=(correction,),
    )

    overlays = [item for item in atoms if item.source == 'accepted_script_correction']
    assert [(item.token_ids, item.text) for item in overlays] == [
        (tuple(token.token_id for token in raw.tokens[6:8]), '多传感器'),
        ((raw.tokens[11].token_id,), '在'),
    ]
    assert not any(
        item.source == 'accepted_script_correction'
        and item.token_start == 0
        and item.token_end == 12
        for item in atoms
    )


def test_symmetric_pair_term_becomes_one_hard_atom() -> None:
    _raw, _semantic, atoms = _token_atoms(['空', '对', '空', '自动', '拦截'])

    atom = next(item for item in atoms if item.text == '空对空')
    assert atom.token_ids == ('token-000000', 'token-000001', 'token-000002')
    assert atom.source == 'phrase_integrity'
    assert atom.protection == 'hard'


def test_token_bound_atom_schema_is_frozen_strict_and_stable() -> None:
    raw, semantic, first = _token_atoms(['行', '业'])
    second = build_token_bound_lexical_atoms(raw, semantic)

    assert first == second
    atom = first[0]
    with pytest.raises(ValidationError):
        atom.text = atom.text

    payload = atom.model_dump(mode='json')
    payload['unexpected'] = True
    with pytest.raises(ValidationError):
        type(atom).model_validate(payload)

    wrong_protection = atom.model_dump(mode='json')
    wrong_protection['protection'] = 'soft'
    with pytest.raises(ValidationError, match='hard protection'):
        type(atom).model_validate(wrong_protection)
