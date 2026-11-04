from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.script_correction_candidates import CorrectionCandidate
from app.services.subtitle_pipeline_v2_schema import canonical_sha256
from app.services.subtitle_token_overlays import (
    AuthorizedTextOverlay,
    FillerTokenMask,
    apply_authorized_overlays,
    build_filler_masks,
    render_token_span_text,
)


def _snapshot(sentences: list[dict], *, language: str = 'zh'):
    cursor = 0
    provider_sentences: list[dict] = []
    for sentence in sentences:
        words: list[dict] = []
        for item in sentence['words']:
            if isinstance(item, str):
                text, punctuation = item, ''
            else:
                text, punctuation = item
            words.append({
                'text': text,
                'punctuation': punctuation,
                'begin_time': cursor,
                'end_time': cursor + 100,
            })
            cursor += 120
        provider_sentences.append({
            'speaker_id': sentence.get('speaker', 0),
            'begin_time': words[0]['begin_time'] if words else cursor,
            'end_time': words[-1]['end_time'] if words else cursor,
            'text': ''.join(item['text'] + item['punctuation'] for item in words),
            'words': words,
        })
        cursor += 200
    return build_raw_asr_snapshot(
        {'transcripts': [{'sentences': provider_sentences}]},
        provider_model='fun-asr',
        provider_task_id='task-token-overlays',
        language=language,
        request_metadata={},
        preflight_metadata={
            'duration_ms': cursor,
            'sample_rate_hz': 24000,
            'channel_count': 1,
            'status': 'accepted',
        },
    )


def _candidate(
    raw,
    start: int,
    end: int,
    replacement: str,
    *,
    candidate_id: str = 'correction-authorized',
    source_text: str | None = None,
) -> CorrectionCandidate:
    tokens = raw.tokens[start:end]
    return CorrectionCandidate(
        candidate_id=candidate_id,
        run_id='run-token-overlays',
        utterance_id='utterance-token-overlays',
        token_ids=tuple(token.token_id for token in tokens),
        source_text=source_text or ''.join(token.text for token in tokens),
        replacement_text=replacement,
        script_line_id='script-line-authorized',
        script_line_order=0,
        script_source_hash='0' * 64,
        phonetic_score=0.95,
        context_score=0.9,
        ranking_score=0.93,
        margin=0.2,
    )


@pytest.mark.parametrize(('source', 'replacement'), [
    ('十五元', '五十元'), ('300元', '3000元'), ('3到4厘米', '三四厘米'),
])
def test_even_selected_correction_cannot_change_numeric_facts(source, replacement):
    raw = _snapshot([{'words': [source]}])
    candidate = _candidate(raw, 0, 1, replacement)
    with pytest.raises(ValueError, match='numeric_fact_change'):
        apply_authorized_overlays(raw_snapshot=raw, correction_candidates=(candidate,), accepted_candidate_ids=(candidate.candidate_id,))


def test_authorized_overlay_binds_complete_token_span_without_mutating_raw() -> None:
    raw = _snapshot([{'words': ['扎记', ('村', '，'), '今天']}])
    before = deepcopy(raw.model_dump(mode='json'))
    candidate = _candidate(raw, 0, 2, '查济村')

    overlays = apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(candidate,),
        accepted_candidate_ids=(candidate.candidate_id,),
    )

    assert len(overlays) == 1
    overlay = overlays[0]
    assert overlay.candidate_id == candidate.candidate_id
    assert (overlay.token_start, overlay.token_end) == (0, 2)
    assert overlay.token_ids == ('token-000000', 'token-000001')
    assert overlay.source_text == '扎记村'
    assert overlay.replacement_text == '查济村'
    assert render_token_span_text(
        raw_snapshot=raw,
        token_start=0,
        token_end=3,
        overlays=overlays,
    ) == '查济村，今天'
    assert raw.model_dump(mode='json') == before
    assert [(token.start_ms, token.end_ms) for token in raw.tokens] == [
        (0, 100),
        (120, 220),
        (240, 340),
    ]


def test_case_only_candidate_keeps_overlay_on_the_changed_token() -> None:
    raw = _snapshot([{'words': ['欢迎', '来到', 'ai', '技术', '盛典']}])
    before_timing = [
        (token.start_ms, token.end_ms)
        for token in raw.tokens
    ]
    candidate = _candidate(raw, 2, 3, 'AI')

    overlays = apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(candidate,),
        accepted_candidate_ids=(candidate.candidate_id,),
    )

    assert [
        (
            item.token_start,
            item.token_end,
            item.token_ids,
            item.source_text,
            item.replacement_text,
        )
        for item in overlays
    ] == [
        (2, 3, ('token-000002',), 'ai', 'AI'),
    ]
    assert render_token_span_text(
        raw_snapshot=raw,
        token_start=0,
        token_end=5,
        overlays=overlays,
    ) == '欢迎来到AI技术盛典'
    assert [
        (token.start_ms, token.end_ms)
        for token in raw.tokens
    ] == before_timing


def test_contextual_correction_protects_only_the_changed_token_core() -> None:
    raw = _snapshot([{'words': [
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
    ]}])
    candidate = _candidate(
        raw,
        0,
        12,
        '融合感知技术路线通过底层材料创新和多传感器融合感知技术在',
    )

    overlays = apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(candidate,),
        accepted_candidate_ids=(candidate.candidate_id,),
    )

    assert len(overlays) == 2
    assert [
        (item.token_start, item.token_end, item.source_text, item.replacement_text)
        for item in overlays
    ] == [
        (6, 8, '多物理量', '多传感器'),
        (11, 12, '把', '在'),
    ]
    assert render_token_span_text(
        raw_snapshot=raw,
        token_start=0,
        token_end=6,
        overlays=overlays,
    ) == '融合感知技术路线通过底层材料创新和'
    assert render_token_span_text(
        raw_snapshot=raw,
        token_start=6,
        token_end=13,
        overlays=overlays,
    ) == '多传感器融合感知技术在位置信息'


def test_unselected_or_unknown_candidate_cannot_add_text() -> None:
    raw = _snapshot([{'words': ['安徽']}])
    candidate = _candidate(raw, 0, 1, '安徽省')

    assert apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(candidate,),
        accepted_candidate_ids=(),
    ) == ()
    assert render_token_span_text(
        raw_snapshot=raw,
        token_start=0,
        token_end=1,
    ) == '安徽'

    with pytest.raises(ValueError, match='unknown correction candidate'):
        apply_authorized_overlays(
            raw_snapshot=raw,
            correction_candidates=(candidate,),
            accepted_candidate_ids=('correction-not-local-evidence',),
        )


def test_authorized_replacement_may_have_a_different_character_count() -> None:
    raw = _snapshot([{'words': ['安徽']}])
    candidate = _candidate(raw, 0, 1, '安徽省')
    overlays = apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(candidate,),
        accepted_candidate_ids=(candidate.candidate_id,),
    )

    assert render_token_span_text(
        raw_snapshot=raw,
        token_start=0,
        token_end=1,
        overlays=overlays,
    ) == '安徽省'
    assert overlays[0].token_ids == (raw.tokens[0].token_id,)


def test_authorized_overlay_collapses_duplicate_internal_punctuation() -> None:
    raw = _snapshot([{'words': [('Renzr', '，'), ('叶润泽', '，'), '来自长沙']}])
    candidate = _candidate(raw, 0, 2, 'RANZER叶润泽')
    overlays = apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(candidate,),
        accepted_candidate_ids=(candidate.candidate_id,),
    )

    assert render_token_span_text(
        raw_snapshot=raw,
        token_start=0,
        token_end=3,
        overlays=overlays,
    ) == 'RANZER叶润泽，来自长沙'


@pytest.mark.parametrize(
    ('raw', 'candidate', 'message'),
    [
        (
            _snapshot([{'words': ['甲', '乙', '丙']}]),
            lambda raw: _candidate(raw, 0, 2, '甲乙', source_text='不一致'),
            'source text',
        ),
        (
            _snapshot([{'words': ['甲']}, {'speaker': 1, 'words': ['乙']}]),
            lambda raw: _candidate(raw, 0, 2, '甲乙'),
            'speaker boundary',
        ),
    ],
)
def test_overlay_rejects_forged_source_or_cross_speaker_span(
    raw,
    candidate,
    message: str,
) -> None:
    item = candidate(raw)
    with pytest.raises(ValueError, match=message):
        apply_authorized_overlays(
            raw_snapshot=raw,
            correction_candidates=(item,),
            accepted_candidate_ids=(item.candidate_id,),
        )


def test_overlay_rejects_non_contiguous_and_overlapping_candidates() -> None:
    raw = _snapshot([{'words': ['甲', '乙', '丙']}])
    non_contiguous = _candidate(raw, 0, 2, '甲丙').model_copy(
        update={'token_ids': ('token-000000', 'token-000002')},
    )
    with pytest.raises(ValueError, match='contiguous'):
        apply_authorized_overlays(
            raw_snapshot=raw,
            correction_candidates=(non_contiguous,),
            accepted_candidate_ids=(non_contiguous.candidate_id,),
        )

    first = _candidate(raw, 0, 2, '甲乙丁', candidate_id='correction-first')
    second = _candidate(raw, 1, 3, '乙丙戊', candidate_id='correction-second')
    with pytest.raises(ValueError, match='overlap'):
        apply_authorized_overlays(
            raw_snapshot=raw,
            correction_candidates=(first, second),
            accepted_candidate_ids=(first.candidate_id, second.candidate_id),
        )


def test_filler_masks_are_conservative_explainable_and_reversible() -> None:
    raw = _snapshot([
        {'words': ['嗯']},
        {'words': [('呃', '，'), '我们', '继续']},
        {'words': ['这', '呢', '件', '事']},
        {'words': ['嘛']},
    ])

    masks = build_filler_masks(raw_snapshot=raw)

    assert [mask.source_text for mask in masks] == ['嗯', '呃，', '嘛']
    assert [mask.reason for mask in masks] == [
        'standalone_filler',
        'discourse_filler',
        'standalone_filler',
    ]
    assert all(mask.reversible is True for mask in masks)
    assert all(mask.evidence_refs for mask in masks)
    assert raw.tokens[5].text == '呢'


def test_mask_only_hides_rendered_token_and_preserves_later_timing() -> None:
    raw = _snapshot([{'words': [('呃', '，'), '今天', '开始']}])
    before = deepcopy(raw.model_dump(mode='json'))
    later_time = (raw.tokens[1].start_ms, raw.tokens[1].end_ms)
    masks = build_filler_masks(raw_snapshot=raw)

    assert render_token_span_text(
        raw_snapshot=raw,
        token_start=0,
        token_end=3,
        masks=masks,
    ) == '今天开始'
    assert render_token_span_text(
        raw_snapshot=raw,
        token_start=0,
        token_end=3,
    ) == '呃，今天开始'
    assert (raw.tokens[1].start_ms, raw.tokens[1].end_ms) == later_time
    assert raw.model_dump(mode='json') == before


def test_render_rejects_a_mask_without_conservative_filler_evidence() -> None:
    raw = _snapshot([{'words': ['这', '呢', '件', '事']}])
    token = raw.tokens[1]
    values = {
        'raw_snapshot_hash': raw.snapshot_hash,
        'token_index': 1,
        'token_id': token.token_id,
        'source_text': token.text + token.punctuation,
        'reason': 'standalone_filler',
        'evidence_refs': (f'segment_singleton:{token.segment_id}',),
    }
    forged = FillerTokenMask(
        mask_id=f'mask-{canonical_sha256(values)}',
        **values,
    )

    with pytest.raises(ValueError, match='not authorized by raw filler evidence'):
        render_token_span_text(
            raw_snapshot=raw,
            token_start=0,
            token_end=4,
            masks=(forged,),
        )


def test_render_rejects_cut_overlay_and_overlay_mask_conflict() -> None:
    raw = _snapshot([{'words': [('呃', '，'), '扎记', '村']}])
    candidate = _candidate(raw, 1, 3, '查济镇')
    overlays = apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(candidate,),
        accepted_candidate_ids=(candidate.candidate_id,),
    )
    masks = build_filler_masks(raw_snapshot=raw)

    with pytest.raises(ValueError, match='cuts an authorized overlay'):
        render_token_span_text(
            raw_snapshot=raw,
            token_start=0,
            token_end=2,
            overlays=overlays,
            masks=masks,
        )

    conflicting_candidate = _candidate(
        raw,
        0,
        2,
        '查济',
        candidate_id='correction-conflicts-with-mask',
    )
    conflicting_overlays = apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(conflicting_candidate,),
        accepted_candidate_ids=(conflicting_candidate.candidate_id,),
    )
    with pytest.raises(ValueError, match='overlap'):
        render_token_span_text(
            raw_snapshot=raw,
            token_start=0,
            token_end=3,
            overlays=conflicting_overlays,
            masks=masks,
        )


def test_render_converts_only_chinese_and_preserves_english_spacing() -> None:
    raw = _snapshot(
        [{'words': [('臺灣 ', ''), ('AI Model ', ''), ('2026', '，'), ('OK', '!')]}],
        language='mixed',
    )

    assert render_token_span_text(
        raw_snapshot=raw,
        token_start=0,
        token_end=4,
    ) == '台湾 AI Model 2026，OK!'


def test_overlay_and_mask_models_are_frozen_strict_content_addressed_and_stable() -> None:
    raw = _snapshot([{'words': ['嗯']}, {'words': ['扎记', '村']}])
    candidate = _candidate(raw, 1, 3, '查济村')
    first_overlays = apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(candidate,),
        accepted_candidate_ids=(candidate.candidate_id,),
    )
    second_overlays = apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(candidate,),
        accepted_candidate_ids=(candidate.candidate_id,),
    )
    first_masks = build_filler_masks(raw_snapshot=raw)
    second_masks = build_filler_masks(raw_snapshot=raw)

    assert first_overlays == second_overlays
    assert first_masks == second_masks

    overlay = first_overlays[0]
    mask = first_masks[0]
    with pytest.raises(ValidationError):
        overlay.replacement_text = '篡改'
    with pytest.raises(ValidationError):
        mask.reason = '篡改'

    overlay_extra = overlay.model_dump(mode='json')
    overlay_extra['unexpected'] = True
    with pytest.raises(ValidationError):
        AuthorizedTextOverlay.model_validate(overlay_extra)

    mask_extra = mask.model_dump(mode='json')
    mask_extra['unexpected'] = True
    with pytest.raises(ValidationError):
        FillerTokenMask.model_validate(mask_extra)

    tampered_overlay = overlay.model_dump(mode='json')
    tampered_overlay['replacement_text'] = '篡改'
    with pytest.raises(ValidationError, match='overlay_id'):
        AuthorizedTextOverlay.model_validate(tampered_overlay)

    tampered_mask = mask.model_dump(mode='json')
    tampered_mask['reason'] = 'discourse_filler'
    with pytest.raises(ValidationError, match='mask_id'):
        FillerTokenMask.model_validate(tampered_mask)


def test_empty_snapshot_has_no_overlays_or_masks() -> None:
    raw = _snapshot([])

    assert apply_authorized_overlays(
        raw_snapshot=raw,
        correction_candidates=(),
        accepted_candidate_ids=(),
    ) == ()
    assert build_filler_masks(raw_snapshot=raw) == ()
