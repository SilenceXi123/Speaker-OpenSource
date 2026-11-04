from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.script_correction_candidates import CorrectionCandidate
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2
from app.services.subtitle_atoms import build_token_bound_lexical_atoms
from app.services.subtitle_legal_gaps import SubtitleLegalGap, build_subtitle_legal_gaps


def _sentence(
    words: list[str],
    *,
    speaker: int = 0,
    start: int | None = 0,
    word_spans: list[tuple[int, int]] | None = None,
    coarse_end: int | None = None,
) -> dict:
    provider_words = []
    for index, word in enumerate(words):
        item = {'text': word}
        if word_spans is not None:
            item['begin_time'], item['end_time'] = word_spans[index]
        provider_words.append(item)
    sentence = {
        'speaker_id': speaker,
        'text': ''.join(words),
        'words': provider_words,
    }
    if start is not None:
        sentence['begin_time'] = start
        sentence['end_time'] = coarse_end if coarse_end is not None else start + 500
    return sentence


def _raw(sentences: list[dict]):
    duration = max(
        [
            int(sentence.get('end_time') or 0)
            for sentence in sentences
        ]
        + [0]
    )
    return build_raw_asr_snapshot(
        {'transcripts': [{'sentences': sentences}]},
        provider_model='fun-asr',
        provider_task_id='task-legal-gaps',
        language='zh',
        request_metadata={},
        preflight_metadata={
            'duration_ms': duration,
            'sample_rate_hz': 24000,
            'channel_count': 1,
            'status': 'accepted',
        },
    )


def _artifacts(sentences: list[dict]):
    raw = _raw(sentences)
    semantic = build_semantic_utterances_v2(raw)
    atoms = build_token_bound_lexical_atoms(raw, semantic)
    gaps = build_subtitle_legal_gaps(raw, semantic, atoms=atoms)
    return raw, semantic, atoms, gaps


def test_word_timing_gap_is_legal_and_speaker_change_is_required() -> None:
    _raw_snapshot, semantic, _atoms, gaps = _artifacts([
        _sentence(
            ['甲', '乙'],
            speaker=0,
            word_spans=[(0, 100), (300, 400)],
            coarse_end=400,
        ),
        _sentence(
            ['丙'],
            speaker=1,
            start=500,
            word_spans=[(500, 600)],
            coarse_end=600,
        ),
    ])

    assert len(gaps) == 2
    assert gaps[0].disposition == 'allowed'
    assert gaps[0].legal_gap is True
    assert gaps[0].reason == 'word_timing'
    assert gaps[0].pause_ms == 200
    assert gaps[1].disposition == 'required'
    assert gaps[1].legal_gap is True
    assert gaps[1].reason == 'speaker_change'
    assert gaps[1].utterance_id == semantic.utterances[1].utterance_id


def test_speaker_change_remains_required_without_fabricating_legal_time() -> None:
    _raw_snapshot, _semantic, _atoms, gaps = _artifacts([
        _sentence(['甲'], speaker=0, start=None, word_spans=None),
        _sentence(['乙'], speaker=1, start=None, word_spans=None),
    ])

    assert gaps[0].disposition == 'required'
    assert gaps[0].reason == 'speaker_change'
    assert gaps[0].legal_gap is False
    assert gaps[0].time_evidence == 'missing_time'
    assert gaps[0].pause_ms is None


def test_shared_coarse_span_without_internal_evidence_is_illegal() -> None:
    _raw_snapshot, _semantic, _atoms, gaps = _artifacts([
        _sentence(['甲', '乙'], start=0, word_spans=None, coarse_end=500),
    ])

    assert len(gaps) == 1
    assert gaps[0].disposition == 'forbidden'
    assert gaps[0].legal_gap is False
    assert gaps[0].reason == 'coarse_shared_span'
    assert gaps[0].pause_ms is None


def test_distinct_coarse_segments_with_sortable_time_have_a_legal_gap() -> None:
    _raw_snapshot, _semantic, _atoms, gaps = _artifacts([
        _sentence(['甲'], speaker=0, start=0, word_spans=None, coarse_end=200),
        _sentence(['乙'], speaker=0, start=300, word_spans=None, coarse_end=500),
    ])

    assert gaps[0].disposition == 'allowed'
    assert gaps[0].legal_gap is True
    assert gaps[0].reason == 'sortable_time'
    assert gaps[0].time_evidence == 'sortable_time'
    assert gaps[0].pause_ms == 100


def test_verified_acoustic_pause_makes_gap_legal_and_preserves_evidence() -> None:
    raw = _raw([_sentence(['甲', '乙'], start=0, word_spans=None, coarse_end=500)])
    semantic = build_semantic_utterances_v2(raw)
    atoms = build_token_bound_lexical_atoms(raw, semantic)

    gaps = build_subtitle_legal_gaps(
        raw,
        semantic,
        atoms=atoms,
        verified_pauses_ms={('token-000000', 'token-000001'): 240},
    )

    assert gaps[0].disposition == 'allowed'
    assert gaps[0].legal_gap is True
    assert gaps[0].reason == 'verified_acoustic_pause'
    assert gaps[0].pause_ms == 240
    assert 'verified-pause:240ms' in gaps[0].evidence_refs


def test_verified_pause_cannot_override_conflicting_word_timestamps() -> None:
    raw = _raw([_sentence(
        ['甲', '乙'],
        word_spans=[(0, 200), (100, 300)],
        coarse_end=300,
    )])
    semantic = build_semantic_utterances_v2(raw)
    atoms = build_token_bound_lexical_atoms(raw, semantic)

    gaps = build_subtitle_legal_gaps(
        raw,
        semantic,
        atoms=atoms,
        verified_pauses_ms={('token-000000', 'token-000001'): 240},
    )

    assert gaps[0].legal_gap is False
    assert gaps[0].reason == 'time_overlap'


@pytest.mark.parametrize(
    ('sentence', 'reason'),
    [
        (
            _sentence(
                ['甲', '乙'],
                word_spans=[(0, 200), (100, 300)],
                coarse_end=300,
            ),
            'time_overlap',
        ),
        (_sentence(['甲', '乙'], start=None, word_spans=None), 'missing_time'),
    ],
)
def test_unordered_or_missing_token_time_is_illegal(sentence: dict, reason: str) -> None:
    _raw_snapshot, _semantic, _atoms, gaps = _artifacts([sentence])

    assert gaps[0].disposition == 'forbidden'
    assert gaps[0].legal_gap is False
    assert gaps[0].reason == reason


def test_hard_atom_internal_gap_is_forbidden_even_with_word_timing() -> None:
    _raw_snapshot, _semantic, atoms, gaps = _artifacts([
        _sentence(
            ['行', '业'],
            word_spans=[(0, 100), (100, 200)],
            coarse_end=200,
        ),
    ])

    atom = next(item for item in atoms if item.text == '行业')
    assert atom.protection == 'hard'
    assert gaps[0].disposition == 'forbidden'
    assert gaps[0].legal_gap is False
    assert gaps[0].reason == 'hard_atom'
    assert gaps[0].atom_id == atom.atom_id
    assert gaps[0].time_evidence == 'word_timing'
    assert gaps[0].pause_ms == 0


@pytest.mark.parametrize('protection', ['strong', 'soft'])
def test_non_hard_atom_preserves_legal_time_gap_for_planner_scoring(
    protection: str,
) -> None:
    raw = _raw([
        _sentence(
            ['蓝鲸', '智造'],
            word_spans=[(0, 100), (100, 200)],
            coarse_end=200,
        ),
    ])
    semantic = build_semantic_utterances_v2(raw)
    bundle = {
        'bundle_version': 'runtime_lexicon_bundle.v1',
        'term_set_hash': f'gap-{protection}',
        'terms': [{
            'term_id': f'term-{protection}',
            'display_text': '蓝鲸智造',
            'normalized_term': '蓝鲸智造',
            'protection_level': protection,
        }],
    }
    atoms = build_token_bound_lexical_atoms(
        raw,
        semantic,
        runtime_lexicon_bundle=bundle,
    )
    gaps = build_subtitle_legal_gaps(raw, semantic, atoms=atoms)

    assert gaps[0].legal_gap is True
    assert gaps[0].disposition == 'allowed'
    assert gaps[0].atom_protection == protection


def test_empty_snapshot_has_no_atoms_or_gaps() -> None:
    raw = _raw([])
    semantic = build_semantic_utterances_v2(raw)
    atoms = build_token_bound_lexical_atoms(raw, semantic)

    assert atoms == ()
    assert build_subtitle_legal_gaps(raw, semantic, atoms=atoms) == ()


def test_every_adjacent_token_gap_has_one_stable_utterance_owner() -> None:
    raw = _raw([
        _sentence(
            ['甲', '乙'],
            speaker=0,
            word_spans=[(0, 100), (100, 200)],
            coarse_end=200,
        ),
        _sentence(
            ['丙', '丁'],
            speaker=1,
            start=300,
            word_spans=[(300, 400), (400, 500)],
            coarse_end=500,
        ),
    ])
    semantic = build_semantic_utterances_v2(raw)
    atoms = build_token_bound_lexical_atoms(raw, semantic)

    first = build_subtitle_legal_gaps(raw, semantic, atoms=atoms)
    second = build_subtitle_legal_gaps(raw, semantic, atoms=atoms)

    assert first == second
    assert len(first) == len(raw.tokens) - 1
    assert len({gap.gap_id for gap in first}) == len(first)
    assert all(gap.utterance_id in {item.utterance_id for item in semantic.utterances} for gap in first)
    assert [(gap.left_token_id, gap.right_token_id) for gap in first] == [
        ('token-000000', 'token-000001'),
        ('token-000001', 'token-000002'),
        ('token-000002', 'token-000003'),
    ]


def test_accepted_candidate_rebuilds_planner_gap_as_forbidden() -> None:
    raw = _raw([
        _sentence(
            ['扎记', '村'],
            word_spans=[(0, 100), (100, 200)],
            coarse_end=200,
        ),
    ])
    semantic = build_semantic_utterances_v2(raw)
    correction = CorrectionCandidate(
        candidate_id='correction-chaji-gap',
        run_id='run-gaps',
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
    base_gaps = build_subtitle_legal_gaps(raw, semantic, atoms=base_atoms)
    planner_atoms = build_token_bound_lexical_atoms(
        raw,
        semantic,
        accepted_corrections=(correction,),
    )
    planner_gaps = build_subtitle_legal_gaps(raw, semantic, atoms=planner_atoms)

    assert base_gaps[0].legal_gap is True
    assert planner_gaps[0].legal_gap is False
    assert planner_gaps[0].reason == 'hard_atom'
    assert base_gaps[0].gap_id == planner_gaps[0].gap_id


def test_gap_schema_is_frozen_and_rejects_extra_fields() -> None:
    _raw_snapshot, _semantic, _atoms, gaps = _artifacts([
        _sentence(
            ['甲', '乙'],
            word_spans=[(0, 100), (100, 200)],
            coarse_end=200,
        ),
    ])
    gap = gaps[0]

    with pytest.raises(ValidationError):
        gap.reason = gap.reason

    payload = deepcopy(gap.model_dump(mode='json'))
    payload['unexpected'] = True
    with pytest.raises(ValidationError):
        SubtitleLegalGap.model_validate(payload)
