from __future__ import annotations

from copy import deepcopy

import pytest

from app.schemas.subtitle import SubtitleCue
from app.services.deepseek_subtitle_contract import DeepSeekBoundaryVote
from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.script_correction_candidates import CorrectionCandidate
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2
from app.services.subtitle_atoms import build_token_bound_lexical_atoms
from app.services.subtitle_cue_validator import (
    validate_subtitle_cue_plan,
    validate_subtitle_cues,
)
from app.services.subtitle_global_planner import (
    CuePlannerProfile,
    build_subtitle_global_plan,
)
from app.services.subtitle_legal_gaps import build_subtitle_legal_gaps
from app.services.subtitle_token_overlays import (
    apply_authorized_overlays,
    build_filler_masks,
    render_token_span_text,
)


def _cue(index: int, start_ms: int, end_ms: int, text: str) -> SubtitleCue:
    return SubtitleCue(
        index=index,
        start_ms=start_ms,
        end_ms=end_ms,
        speaker_key='speaker_0',
        speaker_name='主持人',
        full_text=f'主持人：{text}',
        clean_text=text,
    )


def test_validate_subtitle_cues_reports_protected_term_split() -> None:
    report = validate_subtitle_cues(
        [
            _cue(1, 19000, 22000, '这里就是沈阳的国际赛艇'),
            _cue(2, 22020, 22800, '中心哦'),
        ],
        protected_terms=('国际赛艇中心',),
    )

    assert not report.valid
    assert report.has_p0
    assert any(
        issue.code == 'protected_term_split'
        and issue.left_index == 1
        and issue.right_index == 2
        and issue.term == '国际赛艇中心'
        for issue in report.issues
    )


def test_validate_subtitle_cues_reports_single_cjk_fragment() -> None:
    report = validate_subtitle_cues([
        _cue(1, 0, 300, '璀'),
        _cue(2, 300, 800, '璨的绿色明珠'),
    ])

    assert not report.valid
    assert any(issue.code == 'isolated_single_cjk_cue' for issue in report.issues)


@pytest.mark.parametrize('particle', ['哦', '呜'])
def test_validate_subtitle_cues_does_not_block_standalone_filler_particle(
    particle: str,
) -> None:
    report = validate_subtitle_cues([
        _cue(1, 0, 300, particle),
        _cue(2, 300, 800, '欢迎大家'),
    ])

    assert not any(issue.code == 'isolated_single_cjk_cue' for issue in report.issues)


@pytest.mark.parametrize(
    ('left', 'right'),
    [
        ('把时间留给我们的智库团', '李总我看到'),
        ('大家都在考虑飞机怎么飞得更高', '飞得更久'),
        ('为了让网张得更开', '网的抗风性更低'),
    ],
)
def test_validate_subtitle_cues_does_not_infer_false_cross_cue_terms(
    left: str,
    right: str,
) -> None:
    report = validate_subtitle_cues([
        _cue(1, 0, 1000, left),
        _cue(2, 1000, 2000, right),
    ])

    assert not any(issue.code == 'lexical_atom_split' for issue in report.issues)


def test_validate_subtitle_cues_reports_corporate_name_atom_split() -> None:
    report = validate_subtitle_cues([
        _cue(1, 0, 1200, '北京元空智能科技有限公'),
        _cue(2, 1200, 2200, '司创始人彭大为'),
    ])

    assert not report.valid
    assert any(
        issue.code == 'lexical_atom_split' and issue.term == '北京元空智能科技有限公司'
        for issue in report.issues
    )


def _v2_facts(
    words: list[str],
    *,
    speakers: list[int] | None = None,
    punctuation: list[str] | None = None,
    language: str = 'zh',
    coarse_shared_time: bool = False,
    confirmed_terms: tuple[str, ...] = (),
    accepted_corrections: tuple[CorrectionCandidate, ...] = (),
    strict_limit: int | None = None,
    forbid_all_boundaries: bool = False,
):
    speakers = speakers or [0] * len(words)
    punctuation = punctuation or [''] * len(words)
    if len(speakers) != len(words) or len(punctuation) != len(words):
        raise ValueError('word evidence lengths disagree')

    if coarse_shared_time:
        sentences = [{
            'speaker_id': speakers[0] if speakers else 0,
            'text': ''.join(words),
            'begin_time': 0,
            'end_time': max(500, len(words) * 100),
            'words': [
                {'text': word, 'punctuation': punctuation[index]}
                for index, word in enumerate(words)
            ],
        }] if words else []
    else:
        sentences = []
        cursor = 0
        for index, word in enumerate(words):
            sentences.append({
                'speaker_id': speakers[index],
                'text': word + punctuation[index],
                'begin_time': cursor,
                'end_time': cursor + 100,
                'words': [{
                    'text': word,
                    'punctuation': punctuation[index],
                    'begin_time': cursor,
                    'end_time': cursor + 100,
                }],
            })
            cursor += 200

    duration_ms = max(
        (int(sentence.get('end_time') or 0) for sentence in sentences),
        default=0,
    )
    raw = build_raw_asr_snapshot(
        {'transcripts': [{'sentences': sentences}]},
        provider_model='fun-asr',
        provider_task_id='task-v2-cue-validator',
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
        accepted_corrections=accepted_corrections,
    )
    gaps = build_subtitle_legal_gaps(raw, semantic, atoms=atoms)
    profile = (
        CuePlannerProfile(mode='strict', strict_max_display_units=strict_limit)
        if strict_limit is not None
        else CuePlannerProfile()
    )
    plan = build_subtitle_global_plan(
        raw_snapshot=raw,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=tuple(
            DeepSeekBoundaryVote(
                gap_id=gap.gap_id,
                left_token_id=gap.left_token_id,
                right_token_id=gap.right_token_id,
                rating='forbidden',
                reason_code='phrase_integrity',
                evidence_refs=(),
            )
            for gap in gaps
        ) if forbid_all_boundaries else (),
        profile=profile,
    )
    masks = build_filler_masks(raw_snapshot=raw)
    rendered = {
        cue.cue_id: render_token_span_text(
            raw_snapshot=raw,
            token_start=cue.token_start,
            token_end=cue.token_end,
            masks=masks,
        )
        for cue in plan.cues
    }
    return raw, semantic, atoms, gaps, plan, masks, rendered


def _candidate(raw, semantic, replacement: str) -> CorrectionCandidate:
    token_ids = tuple(token.token_id for token in raw.tokens)
    return CorrectionCandidate(
        candidate_id='correction-validator-authorized',
        run_id='run-v2-cue-validator',
        utterance_id=semantic.utterances[0].utterance_id,
        token_ids=token_ids,
        source_text=''.join(token.text for token in raw.tokens),
        replacement_text=replacement,
        script_line_id='script-line-validator',
        script_line_order=0,
        script_source_hash='0' * 64,
        phonetic_score=0.95,
        context_score=0.9,
        ranking_score=0.93,
        margin=0.2,
    )


def _rendered(raw, plan, overlays=(), masks=()):
    return {
        cue.cue_id: render_token_span_text(
            raw_snapshot=raw,
            token_start=cue.token_start,
            token_end=cue.token_end,
            overlays=overlays,
            masks=masks,
        )
        for cue in plan.cues
    }


def test_v2_validator_accepts_conserved_plan_and_does_not_mutate_facts() -> None:
    raw, _semantic, atoms, gaps, plan, masks, rendered = _v2_facts(
        ['甲', '乙', '丙'],
    )
    before = deepcopy(raw.model_dump(mode='json'))

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=(),
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=plan,
        rendered_text_by_cue_id=rendered,
    )

    assert report.valid
    assert raw.model_dump(mode='json') == before
    assert not any(issue.severity == 'P0' for issue in report.issues)


def test_v2_validator_rejects_tampered_raw_snapshot_hash_chain() -> None:
    raw, _semantic, atoms, gaps, plan, masks, rendered = _v2_facts(['甲'])
    tampered_token = raw.tokens[0].model_copy(update={'text': '乙'})
    tampered_raw = raw.model_copy(update={'tokens': (tampered_token,)})

    report = validate_subtitle_cue_plan(
        raw_snapshot=tampered_raw,
        overlays=(),
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=plan,
        rendered_text_by_cue_id=rendered,
    )

    assert any(
        issue.code == 'invalid_raw_snapshot' and issue.severity == 'P0'
        for issue in report.issues
    )


def test_v2_validator_rejects_tampered_atom_and_gap_fact_tapes() -> None:
    raw, _semantic, atoms, gaps, plan, masks, rendered = _v2_facts(['甲', '乙'])
    tampered_atom = atoms[0].model_copy(update={
        'token_ids': (raw.tokens[1].token_id,),
    })
    tampered_gap = gaps[0].model_copy(update={
        'left_token_id': raw.tokens[1].token_id,
    })

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=(),
        masks=masks,
        atoms=(tampered_atom, *atoms[1:]),
        gaps=(tampered_gap,),
        cue_plan=plan,
        rendered_text_by_cue_id=rendered,
    )

    issue_codes = {issue.code for issue in report.issues if issue.severity == 'P0'}
    assert {'atom_tape_mismatch', 'invalid_atom_id'} <= issue_codes
    assert {'gap_tape_mismatch', 'invalid_gap_id'} <= issue_codes


@pytest.mark.parametrize(
    ('words', 'protected', 'expected_term'),
    [
        (['国际', '赛艇中心'], '国际赛艇中心', '国际赛艇中心'),
        (['Open', 'AI'], 'OpenAI', 'OpenAI'),
        (['20', '26'], '2026', '2026'),
    ],
)
def test_v2_validator_blocks_hard_protected_english_and_number_atom_splits(
    words: list[str],
    protected: str,
    expected_term: str,
) -> None:
    raw, _semantic, atoms, gaps, plan, masks, _rendered_map = _v2_facts(
        words,
        confirmed_terms=(protected,),
    )
    original = plan.cues[0]
    left = original.model_copy(update={
        'cue_id': 'tampered-left',
        'token_start': 0,
        'token_end': 1,
        'token_ids': (raw.tokens[0].token_id,),
        'display_unit_count': len(raw.tokens[0].text),
        'display_char_count': len(raw.tokens[0].text),
        'display_word_count': 1,
        'start_ms': raw.tokens[0].start_ms,
        'end_ms': raw.tokens[0].end_ms,
        'duration_ms': raw.tokens[0].end_ms - raw.tokens[0].start_ms,
        'strict_overflow_reason': None,
    })
    right = original.model_copy(update={
        'cue_id': 'tampered-right',
        'token_start': 1,
        'token_end': 2,
        'token_ids': (raw.tokens[1].token_id,),
        'start_gap_id': gaps[0].gap_id,
        'start_boundary_kind': 'planned_legal_gap',
        'display_unit_count': len(raw.tokens[1].text),
        'display_char_count': len(raw.tokens[1].text),
        'display_word_count': 1,
        'start_ms': raw.tokens[1].start_ms,
        'end_ms': raw.tokens[1].end_ms,
        'duration_ms': raw.tokens[1].end_ms - raw.tokens[1].start_ms,
        'strict_overflow_reason': None,
    })
    tampered = plan.model_copy(update={'cues': (left, right)})

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=(),
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=tampered,
    )

    assert any(
        issue.code == 'hard_atom_split'
        and issue.severity == 'P0'
        and issue.term == expected_term
        for issue in report.issues
    )


@pytest.mark.parametrize(
    ('token_ids', 'expected_code'),
    [
        (('token-000000', 'token-000001'), 'token_loss'),
        (('token-000000', 'token-000000', 'token-000002'), 'token_duplicate'),
        (('token-000001', 'token-000000', 'token-000002'), 'token_reorder'),
    ],
)
def test_v2_validator_blocks_token_loss_duplicate_and_reorder(
    token_ids: tuple[str, ...],
    expected_code: str,
) -> None:
    raw, _semantic, atoms, gaps, plan, masks, _rendered_map = _v2_facts(
        ['甲', '乙', '丙'],
    )
    bad_cue = plan.cues[0].model_copy(update={'token_ids': token_ids})
    tampered = plan.model_copy(update={'cues': (bad_cue,)})

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=(),
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=tampered,
    )

    assert any(
        issue.code == expected_code and issue.severity == 'P0'
        for issue in report.issues
    )


def test_v2_validator_blocks_cross_speaker_cue() -> None:
    raw, _semantic, atoms, gaps, plan, masks, _rendered_map = _v2_facts(
        ['甲', '乙'],
        speakers=[0, 1],
    )
    first = plan.cues[0]
    combined = first.model_copy(update={
        'cue_id': 'tampered-cross-speaker',
        'token_end': 2,
        'token_ids': tuple(token.token_id for token in raw.tokens),
        'display_unit_count': 2,
        'display_char_count': 2,
        'end_ms': raw.tokens[-1].end_ms,
        'duration_ms': raw.tokens[-1].end_ms - raw.tokens[0].start_ms,
    })
    tampered = plan.model_copy(update={'cues': (combined,)})

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=(),
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=tampered,
    )

    assert any(issue.code == 'cross_speaker_cue' for issue in report.issues)


def test_v2_validator_requires_overlay_to_match_an_accepted_correction_atom() -> None:
    raw, semantic, atoms, gaps, plan, masks, _rendered_map = _v2_facts(['安', '徽'])
    candidate = _candidate(raw, semantic, '安徽省')
    overlays = apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(candidate,),
        accepted_candidate_ids=(candidate.candidate_id,),
    )

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=overlays,
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=plan,
        rendered_text_by_cue_id=_rendered(raw, plan, overlays, masks),
    )

    assert any(
        issue.code == 'unauthorized_text_insert' and issue.severity == 'P0'
        for issue in report.issues
    )


def test_v2_validator_accepts_overlay_backed_by_accepted_correction_atom() -> None:
    raw, semantic, _atoms, _gaps, _plan, masks, _rendered_map = _v2_facts(['扎记', '村'])
    candidate = _candidate(raw, semantic, '查济村')
    semantic = build_semantic_utterances_v2(raw)
    atoms = build_token_bound_lexical_atoms(
        raw,
        semantic,
        accepted_corrections=(candidate,),
    )
    gaps = build_subtitle_legal_gaps(raw, semantic, atoms=atoms)
    plan = build_subtitle_global_plan(raw_snapshot=raw, atoms=atoms, gaps=gaps)
    overlays = apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(candidate,),
        accepted_candidate_ids=(candidate.candidate_id,),
    )

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=overlays,
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=plan,
        rendered_text_by_cue_id=_rendered(raw, plan, overlays, masks),
    )

    assert report.valid
    assert not any(issue.code == 'unauthorized_text_insert' for issue in report.issues)


def test_v2_validator_detects_direct_timing_drift_and_overlap() -> None:
    raw, _semantic, atoms, gaps, plan, masks, _rendered_map = _v2_facts(
        ['甲', '乙'],
        speakers=[0, 1],
    )
    second = plan.cues[1].model_copy(update={
        'start_ms': 50,
        'duration_ms': plan.cues[1].end_ms - 50,
    })
    tampered = plan.model_copy(update={'cues': (plan.cues[0], second)})

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=(),
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=tampered,
    )

    assert any(issue.code == 'cumulative_timing_drift' for issue in report.issues)
    assert any(issue.code == 'overlapping_time' for issue in report.issues)


def test_v2_validator_blocks_avoidable_strict_overflow() -> None:
    raw, _semantic, atoms, gaps, plan, masks, _rendered_map = _v2_facts(
        list('甲乙丙丁戊己庚辛'),
        strict_limit=6,
    )
    first = plan.cues[0]
    combined = first.model_copy(update={
        'cue_id': 'tampered-strict-overflow',
        'token_end': len(raw.tokens),
        'token_ids': tuple(token.token_id for token in raw.tokens),
        'display_unit_count': 8,
        'display_char_count': 8,
        'end_ms': raw.tokens[-1].end_ms,
        'duration_ms': raw.tokens[-1].end_ms - raw.tokens[0].start_ms,
        'strict_overflow_reason': 'no_legal_gap',
    })
    tampered = plan.model_copy(update={'cues': (combined,)})

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=(),
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=tampered,
    )

    assert any(
        issue.code == 'avoidable_strict_overflow' and issue.severity == 'P0'
        for issue in report.issues
    )


@pytest.mark.parametrize(
    ('words', 'coarse_shared_time', 'expected_reason'),
    [
        (list('甲乙丙丁戊己庚辛'), True, 'no_legal_gap'),
        (['不可拆分专有名词一二三四'], False, 'unbreakable_atom'),
    ],
)
def test_v2_validator_keeps_explainable_unavoidable_overflow_as_non_p0(
    words: list[str],
    coarse_shared_time: bool,
    expected_reason: str,
) -> None:
    raw, _semantic, atoms, gaps, plan, masks, rendered = _v2_facts(
        words,
        coarse_shared_time=coarse_shared_time,
        strict_limit=6,
    )

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=(),
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=plan,
        rendered_text_by_cue_id=rendered,
    )

    assert report.valid
    assert any(
        issue.code == 'unavoidable_overflow'
        and issue.severity == 'P1'
        and expected_reason in issue.message
        for issue in report.issues
    )


def test_v2_validator_allows_one_terminal_punctuation_after_smart_safety_limit() -> None:
    words = ['字'] * 28
    raw, _semantic, atoms, gaps, plan, masks, rendered = _v2_facts(
        words,
        punctuation=[*([''] * (len(words) - 1)), '。'],
        forbid_all_boundaries=True,
    )

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=(),
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=plan,
        rendered_text_by_cue_id=rendered,
    )

    assert report.valid
    assert plan.cues[0].strict_overflow_reason == 'terminal_punctuation'
    assert any(
        issue.code == 'unavoidable_overflow'
        and issue.severity == 'P1'
        and 'terminal_punctuation' in issue.message
        for issue in report.issues
    )


@pytest.mark.parametrize('text', ['甲', '哦'])
def test_v2_validator_does_not_make_legal_single_token_cues_p0(text: str) -> None:
    raw, _semantic, atoms, gaps, plan, masks, rendered = _v2_facts([text])

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=(),
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=plan,
        rendered_text_by_cue_id=rendered,
    )

    assert report.valid
    assert not report.has_p0


def test_v2_validator_blocks_traditional_text_leaking_into_final_render() -> None:
    raw, _semantic, atoms, gaps, plan, masks, _rendered_map = _v2_facts(['臺灣'])
    leaked = {plan.cues[0].cue_id: '臺灣'}

    report = validate_subtitle_cue_plan(
        raw_snapshot=raw,
        overlays=(),
        masks=masks,
        atoms=atoms,
        gaps=gaps,
        cue_plan=plan,
        rendered_text_by_cue_id=leaked,
    )

    assert any(
        issue.code == 'traditional_chinese_output' and issue.severity == 'P0'
        for issue in report.issues
    )
