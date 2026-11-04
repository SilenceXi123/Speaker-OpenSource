from __future__ import annotations

from copy import deepcopy

import httpx
import pytest
from pydantic import ValidationError

from app.services.deepseek_subtitle_contract import (
    DeepSeekPlanWindow,
    DeepSeekWindowProfile,
    build_deepseek_plan_windows,
    run_deepseek_plan_window,
    validate_deepseek_plan,
)
from app.services.deepseek_subtitle_client import _compact_window_payload
from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.script_boundary_hints import ScriptBoundaryHint
from app.services.script_correction_candidates import CorrectionCandidate
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2
from app.services.subtitle_atoms import build_token_bound_lexical_atoms
from app.services.subtitle_legal_gaps import build_subtitle_legal_gaps


def _sentence(
    words: list[str],
    *,
    speaker: int = 0,
    start: int = 0,
) -> dict:
    provider_words = []
    cursor = start
    for word in words:
        provider_words.append({
            'text': word,
            'begin_time': cursor,
            'end_time': cursor + 100,
        })
        cursor += 180
    return {
        'speaker_id': speaker,
        'begin_time': start,
        'end_time': max(start, cursor - 80),
        'text': ''.join(words),
        'words': provider_words,
    }


def _pipeline(
    sentences: list[dict],
    *,
    candidates: tuple[CorrectionCandidate, ...] = (),
    core_token_limit: int = 2,
    overlap_tokens: int = 1,
):
    raw = build_raw_asr_snapshot(
        {'transcripts': [{'sentences': sentences}]},
        provider_model='fun-asr',
        provider_task_id='task-deepseek-contract',
        language='zh',
        request_metadata={},
        preflight_metadata={
            'duration_ms': max(
                [int(sentence.get('end_time') or 0) for sentence in sentences] or [0]
            ),
            'sample_rate_hz': 24000,
            'channel_count': 1,
            'status': 'accepted',
        },
    )
    semantic = build_semantic_utterances_v2(raw)
    atoms = build_token_bound_lexical_atoms(raw, semantic)
    gaps = build_subtitle_legal_gaps(raw, semantic, atoms=atoms)
    windows = build_deepseek_plan_windows(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        atoms=atoms,
        gaps=gaps,
        correction_candidates=candidates,
        profile=DeepSeekWindowProfile(
            profile_version='test-window.v1',
            core_token_limit=core_token_limit,
            overlap_tokens=overlap_tokens,
        ),
    )
    return raw, semantic, atoms, gaps, windows


def _ordinary_windows():
    return _pipeline([_sentence(['甲', '说', '乙', '讲', '丙', '答'])])[-1]


def _response(window, *, votes: list[dict] | None = None, candidates=None) -> dict:
    return {
        'window_id': window.window_id,
        'boundary_votes': votes or [],
        'correction_candidate_ids': candidates or [],
    }


def _vote(gap, **changes) -> dict:
    payload = {
        'gap_id': gap.gap_id,
        'left_token_id': gap.left_token_id,
        'right_token_id': gap.right_token_id,
        'rating': 'preferred',
        'reason_code': 'pause',
        'evidence_refs': list(gap.evidence_refs),
    }
    payload.update(changes)
    return payload


def _script_boundary_hint(raw, gap, **changes) -> ScriptBoundaryHint:
    payload = {
        'hint_id': f'script-boundary-hint-{gap.right_token_index}',
        'raw_snapshot_hash': raw.snapshot_hash,
        'gap_id': gap.gap_id,
        'left_token_id': gap.left_token_id,
        'right_token_id': gap.right_token_id,
        'left_token_index': gap.left_token_index,
        'right_token_index': gap.right_token_index,
        'strength': 'strong',
        'script_line_id': 'script-line-boundary-evidence',
        'script_source_hash': '0' * 64,
        'evidence_refs': (
            'script-boundary:strong',
            'script-line-boundary-evidence',
            f'script-source:{"0" * 64}',
        ),
    }
    payload.update(changes)
    return ScriptBoundaryHint.model_validate(payload)


def test_legal_response_returns_only_authorized_votes_and_candidates() -> None:
    raw, semantic, atoms, gaps, _windows = _pipeline([
        _sentence(['甲', '说', '乙', '讲'])
    ])
    candidate = CorrectionCandidate(
        candidate_id='correction-authorized',
        run_id='run-contract',
        utterance_id=semantic.utterances[0].utterance_id,
        token_ids=('token-000002',),
        source_text='乙',
        replacement_text='已',
        script_line_id='script-line-evidence',
        script_line_order=0,
        script_source_hash='0' * 64,
        phonetic_score=0.91,
        context_score=0.90,
        ranking_score=0.90,
        margin=0.20,
    )
    windows = build_deepseek_plan_windows(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        atoms=atoms,
        gaps=gaps,
        correction_candidates=(candidate,),
        profile=DeepSeekWindowProfile(
            profile_version='test-window.v1',
            core_token_limit=2,
            overlap_tokens=1,
        ),
    )
    window = windows[1]
    owned_gap = next(gap for gap in window.gaps if gap.core_owned)

    result = validate_deepseek_plan(
        window,
        _response(
            window,
            votes=[_vote(owned_gap)],
            candidates=[candidate.candidate_id],
        ),
    )

    assert result.status == 'accepted'
    assert [vote.gap_id for vote in result.boundary_votes] == [owned_gap.gap_id]
    assert result.correction_candidate_ids == (candidate.candidate_id,)
    assert result.rejections == ()


def test_provider_cannot_claim_internal_script_fallback_origin() -> None:
    _raw, _semantic, _atoms, _gaps, windows = _pipeline([
        _sentence(['第一层表达', '第二层表达'])
    ])
    window = windows[0]
    owned_gap = next(gap for gap in window.gaps if gap.core_owned)
    vote = _vote(owned_gap)
    vote['decision_source'] = 'script_fallback'

    result = validate_deepseek_plan(
        window,
        _response(window, votes=[vote]),
    )

    assert result.status == 'accepted'
    assert result.boundary_votes[0].decision_source == 'ai'


@pytest.mark.parametrize(
    'rating',
    ['preferred', 'allowed', 'discouraged', 'forbidden'],
)
def test_all_formal_boundary_ratings_are_accepted(rating) -> None:
    window = _ordinary_windows()[0]
    gap = next(item for item in window.gaps if item.core_owned)

    result = validate_deepseek_plan(
        window,
        _response(window, votes=[_vote(gap, rating=rating)]),
    )

    assert result.status == 'accepted'
    assert result.boundary_votes[0].rating == rating


@pytest.mark.parametrize(
    'mutation',
    [
        lambda payload: payload.pop('correction_candidate_ids'),
        lambda payload: payload.update({'unexpected': True}),
        lambda payload: payload.update({'text': '自由改写'}),
        lambda payload: payload.update({'start_ms': 100}),
        lambda payload: payload.update({'final_subtitle': '成品字幕'}),
    ],
)
def test_missing_extra_and_free_form_output_is_a_structured_contract_rejection(
    mutation,
) -> None:
    window = _ordinary_windows()[0]
    payload = _response(window)
    mutation(payload)

    result = validate_deepseek_plan(window, payload)

    assert result.status == 'rejected'
    assert result.boundary_votes == ()
    assert result.correction_candidate_ids == ()
    assert [item.reason_code for item in result.rejections] == [
        'response_contract_invalid'
    ]


def test_invalid_json_is_rejected_without_echoing_the_response() -> None:
    window = _ordinary_windows()[0]

    result = validate_deepseek_plan(window, '{"final_subtitle":"secret"')

    assert result.status == 'rejected'
    assert result.rejections[0].reason_code == 'response_contract_invalid'
    assert 'secret' not in repr(result)


def test_unknown_and_duplicate_gap_votes_are_rejected_per_item() -> None:
    window = _ordinary_windows()[0]
    gap = next(item for item in window.gaps if item.core_owned)

    result = validate_deepseek_plan(
        window,
        _response(window, votes=[
            _vote(gap),
            _vote(gap),
            _vote(
                gap,
                gap_id='gap-not-in-window',
                left_token_id='token-x',
                right_token_id='token-y',
            ),
        ]),
    )

    assert [item.gap_id for item in result.boundary_votes] == [gap.gap_id]
    assert [item.reason_code for item in result.rejections] == [
        'duplicate_gap_vote',
        'unknown_gap',
    ]


@pytest.mark.parametrize(
    'changes',
    [
        {'left_token_id': 'token-unknown'},
        {'right_token_id': 'token-unknown'},
        {
            'left_token_id': 'token-000001',
            'right_token_id': 'token-000000',
        },
    ],
)
def test_gap_token_mismatch_reverse_or_unknown_token_rejects_the_whole_vote(
    changes,
) -> None:
    window = _ordinary_windows()[0]
    gap = next(item for item in window.gaps if item.core_owned)

    result = validate_deepseek_plan(
        window,
        _response(window, votes=[_vote(gap, **changes)]),
    )

    assert result.boundary_votes == ()
    assert result.rejections[0].reason_code == 'gap_token_mismatch'


def test_cross_speaker_gap_cannot_receive_a_vote_even_in_its_core_window() -> None:
    _raw, _semantic, _atoms, gaps, windows = _pipeline([
        _sentence(['甲'], speaker=0, start=0),
        _sentence(['乙'], speaker=1, start=400),
    ])
    speaker_gap = next(item for item in gaps if item.disposition == 'required')
    window = next(
        item for item in windows
        if any(
            gap.gap_id == speaker_gap.gap_id and gap.core_owned
            for gap in item.gaps
        )
    )
    window_gap = next(item for item in window.gaps if item.gap_id == speaker_gap.gap_id)

    result = validate_deepseek_plan(
        window,
        _response(window, votes=[_vote(window_gap)]),
    )

    assert window_gap.core_owned is True
    assert result.rejections[0].reason_code == 'speaker_boundary_required'


def test_hard_atom_internal_gap_cannot_receive_a_vote() -> None:
    _raw, _semantic, _atoms, _gaps, windows = _pipeline([
        _sentence(['行', '业'])
    ])
    window = windows[0]
    hard_gap = next(item for item in window.gaps if item.atom_protection == 'hard')

    result = validate_deepseek_plan(
        window,
        _response(window, votes=[_vote(hard_gap)]),
    )

    assert hard_gap.core_owned is True
    assert result.rejections[0].reason_code == 'hard_atom_forbidden'


def test_unknown_duplicate_and_overlap_candidate_ids_are_rejected() -> None:
    raw, semantic, atoms, gaps, _windows = _pipeline([
        _sentence(['甲', '说', '乙', '讲'])
    ])
    candidate = CorrectionCandidate(
        candidate_id='correction-window-owner',
        run_id='run-contract',
        utterance_id=semantic.utterances[0].utterance_id,
        token_ids=('token-000002',),
        source_text='乙',
        replacement_text='已',
        script_line_id='script-line-evidence',
        script_line_order=0,
        script_source_hash='0' * 64,
        phonetic_score=0.91,
        context_score=0.90,
        ranking_score=0.90,
        margin=0.20,
    )
    windows = build_deepseek_plan_windows(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        atoms=atoms,
        gaps=gaps,
        correction_candidates=(candidate,),
        profile=DeepSeekWindowProfile(
            profile_version='test-window.v1',
            core_token_limit=2,
            overlap_tokens=1,
        ),
    )
    overlap_window, owner_window = windows

    overlap_result = validate_deepseek_plan(
        overlap_window,
        _response(overlap_window, candidates=[candidate.candidate_id]),
    )
    owner_result = validate_deepseek_plan(
        owner_window,
        _response(owner_window, candidates=[
            candidate.candidate_id,
            candidate.candidate_id,
            'correction-unknown',
        ]),
    )

    assert overlap_result.rejections[0].reason_code == 'candidate_overlap_read_only'
    assert owner_result.correction_candidate_ids == (candidate.candidate_id,)
    assert [item.reason_code for item in owner_result.rejections] == [
        'duplicate_candidate',
        'unknown_candidate',
    ]


def test_overlap_context_has_exactly_one_core_owner_for_each_voteable_gap() -> None:
    windows = _ordinary_windows()
    appearances: dict[str, list[bool]] = {}
    for window in windows:
        for gap in window.gaps:
            appearances.setdefault(gap.gap_id, []).append(gap.core_owned)

    assert any(len(owners) > 1 for owners in appearances.values())
    assert all(sum(owners) == 1 for owners in appearances.values())


def test_overlap_gap_vote_is_rejected_as_read_only() -> None:
    window = _ordinary_windows()[0]
    overlap_gap = next(gap for gap in window.gaps if not gap.core_owned)

    result = validate_deepseek_plan(
        window,
        _response(window, votes=[_vote(overlap_gap)]),
    )

    assert result.boundary_votes == ()
    assert result.rejections[0].reason_code == 'overlap_read_only'


@pytest.mark.parametrize(
    'field,value',
    [
        ('full_script', '完整台本'),
        ('final_subtitle', '最终字幕'),
        ('absolute_file_path', '/private/episode.wav'),
        ('oss_url', 'https://bucket.oss-cn.example.invalid/file.wav'),
        ('api_key', 'sk-sensitive'),
        ('logs', ['unrelated']),
    ],
)
def test_request_schema_rejects_forbidden_extra_input_fields(field, value) -> None:
    window = _ordinary_windows()[0]
    payload = window.model_dump(mode='json')
    payload[field] = value

    with pytest.raises(ValidationError):
        DeepSeekPlanWindow.model_validate(payload)


@pytest.mark.parametrize(
    'unsafe_text',
    [
        '/private/episode.wav',
        '音频在 /private/episode.wav',
        '路径：/private/episode.wav',
        'C:\\private\\episode.wav',
        '打开 C:\\private\\episode.wav',
        '打开C:\\private\\episode.wav',
        '共享 \\\\server\\private\\episode.wav',
        '路径（\\\\server\\private\\episode.wav',
        '共享 //server/private/episode.wav',
        '路径是/Applications/Xcode.app',
        '路径是/usr/local/bin/tool',
        '路径是/data/private.wav',
        '路径是/media/private.wav',
        '路径是/www/wwwroot/audio.wav',
        '路径是/custom-root/secret.wav',
        '路径是/secret.wav',
        '路径为/secret',
        '文件位于/secret.wav',
        '文件在/secret.wav',
        '文件在/不公开/secret.wav',
        '路径是/否决/secret.wav',
        '参考https://example.invalid/docs，路径是/usr/local/bin/tool',
        '参考https://example.invalid/docs;/usr/local/bin/tool',
        '链接 file:///private/episode.wav',
        'https://bucket.oss-cn.example.invalid/episode.wav',
        '//bucket.oss-cn-beijing.aliyuncs.com/file.wav',
        'https://oss.example.com/result.json',
        'https://oss.example.com/result.json?OSSAccessKeyId=secret&Signature=signed',
        'https://cdn.example.com/file.wav?X-OSS-Signature=secret',
        'Bearer sk-sensitive-value',
        'DASHSCOPE_API_KEY=secret-value',
        'ANTHROPIC_API_KEY=secret-value',
        'ANTHROPIC_API_KEY="secret-value"',
        "DASHSCOPE_API_KEY='secret-value'",
        'API KEY: secret-value',
        '配置{"api_key": "secret-value"}',
        '//files.corp.local/share/secret.wav',
        '//192.168.1.2/share/secret.wav',
    ],
)
def test_request_schema_rejects_sensitive_content_hidden_in_allowed_text_fields(
    unsafe_text,
) -> None:
    window = _ordinary_windows()[0]
    payload = window.model_dump(mode='json')
    payload['tokens'][0]['text'] = unsafe_text

    with pytest.raises(ValidationError, match='unsafe content'):
        DeepSeekPlanWindow.model_validate(payload)


@pytest.mark.parametrize(
    'safe_text',
    [
        '比例 1/2 和/或',
        '访问 https://example.invalid/path',
        '访问 https://example.invalid/home/docs',
        '访问 https://example.invalid/private/episode.wav',
        '访问 //cdn.example.invalid/assets/episode.wav',
        '访问 https://cross-site.example.com/docs',
        '接口 api/data/items',
        '操作 打开/关闭，文件/目录',
        '音频是/否完整',
        '文件是/否有效',
        '文件在/不在目录中',
        '请选择路径/文件名',
        '路径是/否有效',
        './data/input.wav',
        '../media/audio.wav',
    ],
)
def test_request_schema_does_not_treat_ordinary_slashes_as_local_paths(
    safe_text,
) -> None:
    window = _ordinary_windows()[0]
    payload = window.model_dump(mode='json')
    payload['tokens'][0]['text'] = safe_text

    validated = DeepSeekPlanWindow.model_validate(payload)

    assert validated.tokens[0].text == safe_text


def test_request_schema_rejects_tampered_gap_token_binding() -> None:
    window = _ordinary_windows()[0]
    payload = window.model_dump(mode='json')
    payload['gaps'][0]['right_token_id'] = payload['tokens'][-1]['token_id']

    with pytest.raises(ValidationError, match='gap token IDs'):
        DeepSeekPlanWindow.model_validate(payload)


def test_window_build_is_stable_and_request_models_are_frozen() -> None:
    first = _ordinary_windows()
    second = _ordinary_windows()

    assert first == second
    assert [item.window_id for item in first] == [item.window_id for item in second]
    with pytest.raises(ValidationError):
        first[0].window_id = first[0].window_id

    with pytest.raises(ValidationError):
        DeepSeekWindowProfile(
            profile_version='invalid-window.v1',
            core_token_limit=2,
            overlap_tokens=-1,
        )


@pytest.mark.parametrize(
    'error_type',
    [TimeoutError, ConnectionError, httpx.ReadTimeout, httpx.ConnectError],
)
def test_transport_timeout_or_connection_error_does_not_retry(error_type) -> None:
    window = _ordinary_windows()[0]
    calls = 0

    def transport(_window):
        nonlocal calls
        calls += 1
        raise error_type('sensitive transport detail')

    result = run_deepseek_plan_window(window, transport)

    assert calls == 1
    assert result.attempts == 1
    assert result.status == 'degraded'
    assert result.failure_code == 'transport_failure'
    assert 'prompt and response' not in repr(result)


def test_json_or_contract_failure_does_not_call_model_a_second_time() -> None:
    window = _ordinary_windows()[0]
    calls = 0

    def transport(_window):
        nonlocal calls
        calls += 1
        return '{"final_subtitle":"overreach"}'

    result = run_deepseek_plan_window(window, transport)

    assert calls == 1
    assert result.attempts == 1
    assert result.status == 'degraded'
    assert result.failure_code == 'response_contract_invalid'


def test_window_id_mismatch_is_a_structured_rejection() -> None:
    window = _ordinary_windows()[0]
    payload = _response(window)
    payload['window_id'] = 'window-other'

    result = validate_deepseek_plan(window, payload)

    assert result.status == 'rejected'
    assert result.rejections[0].reason_code == 'window_id_mismatch'


def test_request_does_not_mutate_raw_artifacts() -> None:
    raw, semantic, atoms, gaps, _windows = _pipeline([
        _sentence(['甲', '说', '乙'])
    ])
    before = deepcopy((raw, semantic, atoms, gaps))

    build_deepseek_plan_windows(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        atoms=atoms,
        gaps=gaps,
    )

    assert (raw, semantic, atoms, gaps) == before


def test_mismatched_script_boundary_hint_is_rejected_before_window_planning() -> None:
    raw, semantic, atoms, gaps, _windows = _pipeline([
        _sentence(['甲', '说', '乙'])
    ])
    hint = _script_boundary_hint(
        raw,
        gaps[0],
        left_token_id='token-mismatched',
    )

    with pytest.raises(ValueError, match='hint.*token'):
        build_deepseek_plan_windows(
            raw_snapshot=raw,
            semantic_artifact=semantic,
            atoms=atoms,
            gaps=gaps,
            script_boundary_hints=(hint,),
        )


def test_unsafe_script_boundary_hint_is_rejected_before_window_planning() -> None:
    raw, semantic, atoms, gaps, _windows = _pipeline([
        _sentence(['行', '业'])
    ])
    hard_gap = next(gap for gap in gaps if gap.atom_protection == 'hard')
    hint = _script_boundary_hint(raw, hard_gap)

    with pytest.raises(ValueError, match='safe legal gap'):
        build_deepseek_plan_windows(
            raw_snapshot=raw,
            semantic_artifact=semantic,
            atoms=atoms,
            gaps=gaps,
            script_boundary_hints=(hint,),
        )


def test_only_core_owner_exposes_script_hint_and_accepts_its_evidence() -> None:
    raw, semantic, atoms, gaps, _windows = _pipeline([
        _sentence(['甲', '说', '乙', '讲'])
    ])
    target_gap = gaps[1]
    hint = _script_boundary_hint(raw, target_gap)

    windows = build_deepseek_plan_windows(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        atoms=atoms,
        gaps=gaps,
        script_boundary_hints=(hint,),
        profile=DeepSeekWindowProfile(
            profile_version='test-window.v1',
            core_token_limit=2,
            overlap_tokens=1,
        ),
    )
    appearances = [
        (window, gap)
        for window in windows
        for gap in window.gaps
        if gap.gap_id == target_gap.gap_id
    ]

    assert len(appearances) == 2
    assert [
        (gap.core_owned, gap.script_boundary_strength)
        for _window, gap in appearances
    ] == [(False, None), (True, 'strong')]
    owner_window, owner_gap = next(
        (window, gap) for window, gap in appearances if gap.core_owned
    )
    result = validate_deepseek_plan(
        owner_window,
        _response(
            owner_window,
            votes=[_vote(owner_gap, evidence_refs=list(hint.evidence_refs))],
        ),
    )
    assert result.status == 'accepted'
    assert result.boundary_votes[0].evidence_refs == hint.evidence_refs


def test_production_256_token_seam_has_one_owner_and_serializes_script_hint() -> None:
    words = ['甲乙丙丁'] * 257
    raw, semantic, atoms, gaps, _windows = _pipeline(
        [_sentence(words)],
        core_token_limit=256,
        overlap_tokens=0,
    )
    seam_gap = next(gap for gap in gaps if gap.right_token_index == 256)
    hint = _script_boundary_hint(raw, seam_gap)

    windows = build_deepseek_plan_windows(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        atoms=atoms,
        gaps=gaps,
        script_boundary_hints=(hint,),
        profile=DeepSeekWindowProfile(
            profile_version='deepseek-single-pass.v1',
            core_token_limit=256,
            overlap_tokens=0,
            combine_semantic_utterances=True,
        ),
    )
    owner_count_by_gap_id = {
        gap.gap_id: sum(
            window_gap.gap_id == gap.gap_id and window_gap.core_owned
            for window in windows
            for window_gap in window.gaps
        )
        for gap in gaps
    }

    assert set(owner_count_by_gap_id.values()) == {1}
    owner_window, owner_gap = next(
        (window, window_gap)
        for window in windows
        for window_gap in window.gaps
        if window_gap.gap_id == seam_gap.gap_id
        and window_gap.core_owned
    )
    assert owner_gap.script_boundary_strength == 'strong'
    compact = _compact_window_payload(owner_window)
    left_token = next(
        token
        for token in compact['tokens']
        if token['index'] == 255
    )
    assert left_token['script_boundary_after'] == 'strong'


@pytest.mark.parametrize(
    'gap_changes',
    [
        {'script_boundary_strength': 'strong'},
        {'script_boundary_evidence_refs': ('script-boundary:strong',)},
        {
            'script_boundary_strength': 'strong',
            'script_boundary_evidence_refs': (
                'script-line-z',
                'script-boundary:strong',
                'script-boundary:strong',
            ),
        },
    ],
)
def test_window_gap_rejects_unpaired_or_unsorted_script_hint_fields(
    gap_changes,
) -> None:
    window = _ordinary_windows()[0]
    payload = window.model_dump(mode='json')
    payload['gaps'][0].update(gap_changes)

    with pytest.raises(ValidationError, match='script boundary'):
        DeepSeekPlanWindow.model_validate(payload)


@pytest.mark.parametrize(
    'gap_changes',
    [
        {'disposition': 'required'},
        {'disposition': 'forbidden'},
        {'legal_gap': False},
        {
            'reason': 'hard_atom',
            'atom_id': 'atom-hard-direct',
            'atom_protection': 'hard',
        },
    ],
    ids=['required', 'forbidden', 'illegal', 'hard-atom'],
)
def test_window_rejects_script_hint_on_unsafe_direct_gap(gap_changes) -> None:
    window = _ordinary_windows()[0]
    payload = window.model_dump(mode='json')
    payload['gaps'][0].update({
        'script_boundary_strength': 'strong',
        'script_boundary_evidence_refs': (
            'script-boundary:strong',
            'script-line-boundary-evidence',
        ),
        **gap_changes,
    })

    with pytest.raises(ValidationError, match='safe legal gap'):
        DeepSeekPlanWindow.model_validate(payload)


def test_window_rejects_script_hint_on_read_only_overlap_gap() -> None:
    window = next(
        item
        for item in _ordinary_windows()
        if any(not gap.core_owned for gap in item.gaps)
    )
    payload = window.model_dump(mode='json')
    overlap_gap = next(gap for gap in payload['gaps'] if not gap['core_owned'])
    overlap_gap.update({
        'script_boundary_strength': 'strong',
        'script_boundary_evidence_refs': (
            'script-boundary:strong',
            'script-line-boundary-evidence',
        ),
    })

    with pytest.raises(ValidationError, match='core-owned'):
        DeepSeekPlanWindow.model_validate(payload)
