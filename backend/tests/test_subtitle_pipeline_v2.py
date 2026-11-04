from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.subtitle_pipeline_run import SubtitlePipelineRun
from app.services.deepseek_subtitle_contract import (
    DeepSeekPlanExecutionResult,
    DeepSeekWindowProfile,
    build_deepseek_plan_windows,
    validate_deepseek_plan,
)
from app.services.raw_asr_snapshot import build_raw_asr_snapshot
from app.services.script_correction_candidates import (
    CorrectionCandidate,
    build_correction_candidates,
    build_script_snapshot,
)
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2
from app.services.subtitle_atoms import build_token_bound_lexical_atoms
from app.services.subtitle_cue_validator import CueValidationIssue, CueValidationReport
from app.services.subtitle_legal_gaps import build_subtitle_legal_gaps
from app.services.subtitle_pipeline_runs import claim_pipeline_run_lease
from app.services.subtitle_pipeline_v2 import (
    SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
    build_evidence_subtitle_segments_v2,
    run_subtitle_pipeline_v2_shadow,
)
from app.services.subtitle_pipeline_v2_schema import canonical_sha256
from app.services import script_correction_candidates as candidate_module
from app.services import subtitle_pipeline_v2 as pipeline_module
from tests.fixtures.p1_subtitle_reliability_cases import P1_SCRIPT_FIDELITY_CASES


def _engine():
    engine = create_engine(
        'sqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _raw(
    words: list[str],
    *,
    word_duration_ms: int = 80,
    word_step_ms: int = 120,
):
    provider_words = []
    cursor = 0
    for word in words:
        provider_words.append({
            'text': word,
            'begin_time': cursor,
            'end_time': cursor + word_duration_ms,
        })
        cursor += word_step_ms
    trailing_gap_ms = word_step_ms - word_duration_ms
    return build_raw_asr_snapshot(
        {'transcripts': [{'sentences': [{
            'speaker_id': 0,
            'begin_time': 0,
            'end_time': max(0, cursor - trailing_gap_ms),
            'text': ''.join(words),
            'words': provider_words,
        }]}]},
        provider_model='fun-asr',
        provider_task_id='task-pipeline-v2',
        language='zh',
        request_metadata={},
        preflight_metadata={
            'duration_ms': max(0, cursor - trailing_gap_ms),
            'sample_rate_hz': 24000,
            'channel_count': 1,
            'status': 'accepted',
        },
    )


def _run(session: Session, raw, run_id: str = 'run-shadow'):
    run = SubtitlePipelineRun(
        id=run_id,
        project_id='project-1',
        job_id=f'job-{run_id}',
        run_sequence=1,
        submission_idempotency_key=f'submit-{run_id}',
        pipeline_version='speaker-v2',
        raw_snapshot_hash=raw.snapshot_hash,
        stage='evidence_matching',
        status='running',
        artifacts={'schema_version': 1},
    )
    session.add(run)
    session.commit()
    now = datetime.now(timezone.utc)
    lease = claim_pipeline_run_lease(
        session,
        run.id,
        owner='worker-1',
        now=now,
        ttl_seconds=120,
    )
    assert lease is not None
    return run, lease


def _facts(
    raw,
    script,
    run_id: str,
    *,
    window_profile: DeepSeekWindowProfile | None = None,
):
    semantic = build_semantic_utterances_v2(raw)
    candidates = build_correction_candidates(
        run_id=run_id,
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )
    atoms = build_token_bound_lexical_atoms(raw, semantic)
    gaps = build_subtitle_legal_gaps(raw, semantic, atoms=atoms)
    windows = build_deepseek_plan_windows(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        atoms=atoms,
        gaps=gaps,
        correction_candidates=candidates,
        **({'profile': window_profile} if window_profile is not None else {}),
    )
    return candidates, windows


def _accepted(window, *, candidate_ids=(), votes=()):
    return {
        'window_id': window.window_id,
        'boundary_votes': list(votes),
        'correction_candidate_ids': list(candidate_ids),
    }


def test_evidence_segments_apply_only_traceable_script_corrections() -> None:
    raw = _raw(['我们', '来到', '扎记村', '现在', '参观'])

    segments, artifact = build_evidence_subtitle_segments_v2(
        raw_snapshot=raw,
        script_snapshot=build_script_snapshot('我们来到查济村现在参观'),
    )

    assert '查济村' in ''.join(segment['text'] for segment in segments)
    assert all(segment['v2FinalCue'] is True for segment in segments)
    assert artifact['raw_token_count'] == 5
    assert len(artifact['corrections']) == 1
    assert artifact['corrections'][0]['source_text'] == '扎记村'
    assert artifact['corrections'][0]['replacement_text'] == '查济村'
    assert artifact['corrections'][0]['token_ids'] == ['token-000002']
    assert artifact['corrections'][0]['start_ms'] == 240
    assert artifact['corrections'][0]['end_ms'] == 320

    unchanged, unchanged_artifact = build_evidence_subtitle_segments_v2(
        raw_snapshot=raw,
        script_snapshot=build_script_snapshot('完全无关的台本内容'),
    )
    assert '扎记村' in ''.join(segment['text'] for segment in unchanged)
    assert unchanged_artifact['corrections'] == []


def test_hard_script_candidate_is_accepted_without_ai_approval() -> None:
    raw = _raw(['现在', '公布', '张', '俊', '豪'])
    semantic = build_semantic_utterances_v2(raw)
    candidates = build_correction_candidates(
        run_id='run-hard-script-autoaccept',
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=build_script_snapshot('第六位是张峻豪'),
        runtime_lexicon_bundle={
            'terms': [{
                'term_id': 'hard-zhang-junhao',
                'display_text': '张峻豪',
                'protection_level': 'hard',
            }],
        },
    )

    accepted_ids, warnings = pipeline_module._accepted_candidate_ids(candidates, {})

    assert [(item.source_text, item.replacement_text) for item in candidates] == [
        ('张俊豪', '张峻豪'),
    ]
    assert accepted_ids == (candidates[0].candidate_id,)
    assert warnings == []


def test_script_display_fidelity_is_autoaccepted_without_ai_and_keeps_timing() -> None:
    raw = _raw(['欢迎', '来到', 'ai', '技术', '盛典'])

    segments, artifact = build_evidence_subtitle_segments_v2(
        raw_snapshot=raw,
        script_snapshot=build_script_snapshot('欢迎来到AI技术盛典'),
    )

    assert ''.join(segment['text'] for segment in segments) == '欢迎来到AI技术盛典'
    assert segments[0]['startMs'] == raw.tokens[0].start_ms
    assert segments[-1]['endMs'] == raw.tokens[-1].end_ms
    assert all(segment['timingEvidence'] == 'direct_raw_word_span' for segment in segments)
    assert artifact['corrections'][0]['source_text'] == 'ai'
    assert artifact['corrections'][0]['replacement_text'] == 'AI'


@pytest.mark.parametrize('case', P1_SCRIPT_FIDELITY_CASES)
def test_p1_script_fidelity_and_boundary_regressions(case: dict) -> None:
    raw = _raw(
        list(case['words']),
        word_duration_ms=900,
        word_step_ms=940,
    )

    segments, artifact = build_evidence_subtitle_segments_v2(
        raw_snapshot=raw,
        script_snapshot=build_script_snapshot(case['script']),
    )

    assert ''.join(segment['text'] for segment in segments) == case['expected_text']
    cue_texts = [segment['text'] for segment in segments]
    assert any(
        text.endswith(case['required_boundary_after_text'])
        for text in cue_texts
    )
    token_by_id = {token.token_id: token for token in raw.tokens}
    for segment in segments:
        first = token_by_id[segment['sourceTokenIds'][0]]
        last = token_by_id[segment['sourceTokenIds'][-1]]
        assert segment['startMs'] == first.start_ms
        assert segment['endMs'] == last.end_ms
        assert segment['timingEvidence'] == 'direct_raw_word_span'
    assert artifact['timing_policy'] == 'direct_raw_word_span'
    assert artifact['script_boundary_hint_count'] >= 1
    assert artifact['script_boundary_hint_accepted_count'] >= 1
    expected_display_count = int('AI' in case['script'])
    assert artifact['script_display_fidelity_candidate_count'] == expected_display_count
    assert artifact['script_display_fidelity_applied_count'] == expected_display_count


@pytest.mark.parametrize('path', ['offline', 'shadow_evidence'])
def test_each_v2_path_builds_exact_script_alignments_once(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    raw = _raw(['欢迎', '来到', 'ai', '技术', '盛典'])
    script = build_script_snapshot('欢迎来到AI技术盛典')
    original = pipeline_module.build_exact_script_line_alignments
    calls = 0

    def counting_alignment_builder(**kwargs):
        nonlocal calls
        calls += 1
        return original(**kwargs)

    monkeypatch.setattr(
        pipeline_module,
        'build_exact_script_line_alignments',
        counting_alignment_builder,
    )
    monkeypatch.setattr(
        candidate_module,
        'build_exact_script_line_alignments',
        counting_alignment_builder,
    )

    if path == 'offline':
        build_evidence_subtitle_segments_v2(
            raw_snapshot=raw,
            script_snapshot=script,
        )
    else:
        pipeline_module._build_evidence(
            run_id='run-count-alignments',
            raw_snapshot=raw,
            script_snapshot=script,
            runtime_lexicon_bundle=None,
            window_profile=None,
        )

    assert calls == 1


@pytest.mark.parametrize('path', ['offline', 'shadow_evidence'])
def test_unmappable_unicode_script_evidence_fails_open_in_each_v2_path(
    path: str,
) -> None:
    raw = _raw(['Strae欢迎', 'ai大会'])
    script = build_script_snapshot('Straße欢迎AI大会')

    if path == 'offline':
        segments, artifact = build_evidence_subtitle_segments_v2(
            raw_snapshot=raw,
            script_snapshot=script,
        )
        assert ''.join(segment['text'] for segment in segments) == 'Strae欢迎ai大会'
        assert artifact['script_display_fidelity_candidate_count'] == 0
        assert artifact['script_boundary_hint_count'] == 0
    else:
        evidence = pipeline_module._build_evidence(
            run_id='run-unmappable-script-evidence',
            raw_snapshot=raw,
            script_snapshot=script,
            runtime_lexicon_bundle=None,
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )
        assert evidence.script_alignments == ()
        assert evidence.script_boundary_hints == ()


def test_offline_metrics_count_script_boundary_candidates_rejected_as_illegal() -> None:
    raw = _raw(['汇聚了2000', '多家', '人工智能企业'])

    _segments, artifact = build_evidence_subtitle_segments_v2(
        raw_snapshot=raw,
        script_snapshot=build_script_snapshot('汇聚了2000，多家人工智能企业'),
        runtime_lexicon_bundle={
            'terms': [{
                'term_id': 'hard-2000-companies',
                'display_text': '2000多家',
                'protection_level': 'hard',
            }],
        },
    )

    assert artifact['script_boundary_hint_count'] == 1
    assert artifact['script_boundary_hint_accepted_count'] == 0
    assert artifact['script_boundary_hint_rejected_illegal_gap_count'] == 1


def test_display_fidelity_candidate_is_in_the_deterministic_accept_set() -> None:
    raw = _raw(['欢迎', '来到', 'ai', '技术', '盛典'])
    semantic = build_semantic_utterances_v2(raw)
    candidates = build_correction_candidates(
        run_id='run-display-autoaccept',
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=build_script_snapshot('欢迎来到AI技术盛典'),
    )

    accepted_ids, warnings = pipeline_module._accepted_candidate_ids(candidates, {})

    assert accepted_ids == (candidates[0].candidate_id,)
    assert warnings == []


@pytest.mark.parametrize(
    ('source_text', 'replacement_text', 'evidence_refs'),
    [
        (
            'ai',
            'ML',
            ('script-display-fidelity:case-space',),
        ),
        (
            'ai,',
            'AI',
            ('script-display-fidelity:case-space',),
        ),
        (
            'ai',
            'AI,',
            ('script-display-fidelity:case-space',),
        ),
        (
            '臺灣ai',
            '台湾AI',
            ('script-display-fidelity:case-space',),
        ),
        (
            'AI',
            'AI',
            ('script-display-fidelity:case-space',),
        ),
        (
            'ai',
            'AI',
            (
                'script-display-fidelity:case-space',
                'script-line:forged',
            ),
        ),
    ],
)
def test_forged_display_fidelity_marker_is_not_autoaccepted(
    source_text: str,
    replacement_text: str,
    evidence_refs: tuple[str, ...],
) -> None:
    candidate = CorrectionCandidate(
        candidate_id='correction-forged-display-fidelity',
        run_id='run-forged-display-fidelity',
        utterance_id='utterance-forged-display-fidelity',
        token_ids=('token-000000',),
        source_text=source_text,
        replacement_text=replacement_text,
        script_line_id='script-line-forged-display-fidelity',
        script_line_order=0,
        script_source_hash='0' * 64,
        phonetic_score=1.0,
        context_score=1.0,
        ranking_score=1.0,
        margin=1.0,
        evidence_refs=evidence_refs,
    )

    accepted_ids, warnings = pipeline_module._accepted_candidate_ids(
        (candidate,),
        {},
    )

    assert accepted_ids == ()
    assert warnings == []


def test_ai_candidate_with_distant_independent_rewrites_is_not_safe() -> None:
    candidate = CorrectionCandidate(
        candidate_id='correction-disjoint-rewrite',
        run_id='run-disjoint-rewrite',
        utterance_id='utterance-disjoint-rewrite',
        token_ids=('token-000000',),
        source_text=(
            '融合感知技术路线通过底层材料创新和多物理量融合感知技术把'
        ),
        replacement_text=(
            '融合感知技术路线通过底层材料创新和多传感器融合感知技术在'
        ),
        script_line_id='script-line-disjoint-rewrite',
        script_line_order=0,
        script_source_hash='0' * 64,
        phonetic_score=0.9,
        context_score=0.9,
        ranking_score=0.9,
        margin=1.0,
    )

    assert candidate.source_text.endswith('多物理量融合感知技术把')
    assert candidate.replacement_text.endswith('多传感器融合感知技术在')
    assert pipeline_module._is_safe_ai_correction_candidate(candidate) is False


def test_evidence_planner_counts_expanded_canonical_name_after_overlay() -> None:
    raw = _raw(['欢迎', 'shark', '来到', '舞台', '马上', '开始'])
    bundle = {
        'terms': [{
            'term_id': 'hard-shark',
            'display_text': 'SHarK米尔艾力',
            'aliases': ['shark'],
            'protection_level': 'hard',
        }],
    }

    segments, artifact = build_evidence_subtitle_segments_v2(
        raw_snapshot=raw,
        script_snapshot=build_script_snapshot(
            '项目强制词库人名——SHarK米尔艾力；别名：shark',
        ),
        runtime_lexicon_bundle=bundle,
    )

    assert artifact['validation']['valid'] is True
    assert artifact['corrections'][0]['replacement_text'] == 'SHarK米尔艾力'
    assert 'SHarK米尔艾力' in ''.join(segment['text'] for segment in segments)


def test_evidence_segments_normalize_spoken_date_without_shifting_raw_timing() -> None:
    raw = _raw(['活动在', '21年的04/27', '举行'])

    segments, artifact = build_evidence_subtitle_segments_v2(
        raw_snapshot=raw,
        script_snapshot=build_script_snapshot(''),
    )

    assert ''.join(segment['text'] for segment in segments) == '活动在21年4月27号举行'
    assert segments[0]['startMs'] == raw.tokens[0].start_ms
    assert segments[-1]['endMs'] == raw.tokens[-1].end_ms
    assert all(segment['timingEvidence'] == 'direct_raw_word_span' for segment in segments)
    assert artifact['display_normalizations'] == [{
        'cue_id': segments[0]['segmentId'],
        'kind': 'spoken_date',
        'source_text': '21年的04/27',
        'replacement_text': '21年4月27号',
    }]


def test_colleague_examples_use_semantic_boundaries_instead_of_length_patches() -> None:
    raw = _raw(
        [
            '我叫',
            '于伟学',
            '来自',
            '耕宇牧星北京空间科技技术有限公司',
        ],
        word_duration_ms=800,
        word_step_ms=840,
    )
    script = build_script_snapshot('')
    engine = _engine()
    with Session(engine) as session:
        run, lease = _run(session, raw, 'run-colleague-clause')
        _candidates, windows = _facts(
            raw,
            script,
            run.id,
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )
        assert len(windows) == 1
        gaps = {gap.right_token_id: gap for gap in windows[0].gaps}
        before_from = gaps[raw.tokens[2].token_id]
        responses = {windows[0].window_id: _accepted(
            windows[0],
            votes=({
                'gap_id': before_from.gap_id,
                'left_token_id': before_from.left_token_id,
                'right_token_id': before_from.right_token_id,
                'rating': 'preferred',
                'reason_code': 'semantic_boundary',
                'evidence_refs': list(before_from.evidence_refs),
            },),
        )}

        result = run_subtitle_pipeline_v2_shadow(
            session,
            lease,
            raw_snapshot=raw,
            script_snapshot=script,
            prepared_responses=responses,
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )

        assert result.stage == 'ready'
        assert not result.validation.has_p0
        assert list(result.rendered_text_by_cue_id.values()) == [
            '我叫于伟学',
            '来自耕宇牧星北京空间科技技术有限公司',
        ]

    phrase = _raw(['让我们掌声有请', '下一组', '创业', '团队', '登场', '现在'])
    with Session(engine) as session:
        run, lease = _run(session, phrase, 'run-colleague-phrase')
        _candidates, windows = _facts(
            phrase,
            script,
            run.id,
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )
        assert len(windows) == 1
        protected_gap = next(
            gap for gap in windows[0].gaps
            if gap.right_token_id == phrase.tokens[3].token_id
        )
        result = run_subtitle_pipeline_v2_shadow(
            session,
            lease,
            raw_snapshot=phrase,
            script_snapshot=script,
            prepared_responses={windows[0].window_id: _accepted(
                windows[0],
                votes=({
                    'gap_id': protected_gap.gap_id,
                    'left_token_id': protected_gap.left_token_id,
                    'right_token_id': protected_gap.right_token_id,
                    'rating': 'forbidden',
                    'reason_code': 'phrase_integrity',
                    'evidence_refs': list(protected_gap.evidence_refs),
                },),
            )},
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )

        assert all('创业' not in text or '创业团队' in text
                   for text in result.rendered_text_by_cue_id.values())


def test_single_pass_window_profile_never_overlaps_semantic_content() -> None:
    raw = _raw(['语义内容'] * 65)
    _candidates, windows = _facts(
        raw,
        build_script_snapshot(''),
        'single-pass-facts',
        window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
    )

    assert len(windows) == 1
    core_token_ids = [
        token.token_id
        for window in windows
        for token in window.tokens
        if window.core_token_start <= token.token_index < window.core_token_end
    ]
    assert core_token_ids == [token.token_id for token in raw.tokens]
    assert all(
        window.window_token_start == window.core_token_start
        and window.window_token_end == window.core_token_end
        for window in windows
    )


def test_runtime_transport_failure_stops_later_provider_calls() -> None:
    raw = _raw(['失败即停'] * 65)
    script = build_script_snapshot('')
    engine = _engine()

    class FailingClient:
        def __init__(self) -> None:
            self.calls = 0

        def plan_window(self, *, window, **_kwargs):
            self.calls += 1
            return DeepSeekPlanExecutionResult(
                window_id=window.window_id,
                status='degraded',
                attempts=1,
                failure_code='transport_failure',
            )

    with Session(engine) as session:
        run, lease = _run(session, raw, 'run-fail-fast')
        client = FailingClient()
        result = run_subtitle_pipeline_v2_shadow(
            session,
            lease,
            raw_snapshot=raw,
            script_snapshot=script,
            client=client,
        )

        assert result.stage == 'ready_with_warnings'
        assert client.calls == 1
        assert session.get(SubtitlePipelineRun, run.id).ai_failed_windows > 1


def test_shadow_uses_only_strong_script_fallback_when_every_window_fails() -> None:
    words = [
        '特别有代表性',
        '它说明ai已经',
        '不是少数人的专属工具',
    ]
    raw = _raw(words, word_duration_ms=900, word_step_ms=940)
    script = build_script_snapshot(
        '特别有代表性\n'
        '它说明ai已经，不是少数人的专属工具'
    )
    engine = _engine()
    with Session(engine) as session:
        run, lease = _run(session, raw, 'run-script-fallback')

        result = run_subtitle_pipeline_v2_shadow(
            session,
            lease,
            raw_snapshot=raw,
            script_snapshot=script,
            prepared_responses={},
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )

        assert result.stage == 'ready_with_warnings'
        assert list(result.rendered_text_by_cue_id.values()) == [
            words[0],
            words[1] + words[2],
        ]
        token_by_id = {token.token_id: token for token in raw.tokens}
        for cue in result.cue_plan.cues:
            assert cue.start_ms == token_by_id[cue.token_ids[0]].start_ms
            assert cue.end_ms == token_by_id[cue.token_ids[-1]].end_ms

        saved = session.get(SubtitlePipelineRun, run.id)
        assert saved is not None
        evidence = saved.artifacts['checkpoints']['evidence_matching']
        assert len(evidence['script_alignments']) == 2
        assert len(evidence['script_boundary_hints']) == 2
        assert [
            hint['strength'] for hint in evidence['script_boundary_hints']
        ] == ['strong', 'weak']
        assert evidence['output_refs']['script_alignments_hash'] == canonical_sha256(
            evidence['script_alignments']
        )
        assert evidence['output_refs']['script_boundary_hints_hash'] == canonical_sha256(
            evidence['script_boundary_hints']
        )
        votes = saved.artifacts['checkpoints']['cue_validating'][
            'accepted_boundary_votes'
        ]
        assert len(votes) == 1
        assert votes[0]['evidence_refs'] == evidence['script_boundary_hints'][0][
            'evidence_refs'
        ]
        assert 'script-boundary:weak' not in votes[0]['evidence_refs']
        assert saved.artifacts['metrics'] == {
            'script_display_fidelity_candidate_count': 0,
            'script_display_fidelity_applied_count': 0,
            'script_boundary_hint_count': 2,
            'script_boundary_hint_accepted_count': 1,
            'script_boundary_hint_rejected_illegal_gap_count': 0,
        }


def test_checkpoint_replay_preserves_script_boundary_provenance_and_metrics() -> None:
    words = ['第一层完整表达', '第二层完整表达']
    raw = _raw(words, word_duration_ms=900, word_step_ms=940)
    script = build_script_snapshot('\n'.join(words))
    engine = _engine()
    with Session(engine) as session:
        first_run, first_lease = _run(session, raw, 'run-script-replay-source')
        first_result = run_subtitle_pipeline_v2_shadow(
            session,
            first_lease,
            raw_snapshot=raw,
            script_snapshot=script,
            prepared_responses={},
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )
        first_saved = session.get(SubtitlePipelineRun, first_run.id)
        assert first_saved is not None
        previous_artifact = deepcopy(first_saved.artifacts)
        assert first_saved.artifacts['metrics'][
            'script_boundary_hint_accepted_count'
        ] == 1

        replay_run, replay_lease = _run(
            session,
            raw,
            'run-script-replay-target',
        )
        replay_result = run_subtitle_pipeline_v2_shadow(
            session,
            replay_lease,
            raw_snapshot=raw,
            script_snapshot=script,
            replay_decisions_from_artifact=previous_artifact,
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )

        replay_saved = session.get(SubtitlePipelineRun, replay_run.id)
        assert replay_saved is not None
        checkpoints = replay_saved.artifacts['checkpoints']
        evidence_checkpoint = checkpoints['evidence_matching']
        ai_checkpoint = checkpoints['ai_planning']
        cue_checkpoint = checkpoints['cue_validating']
        current_hint = evidence_checkpoint['script_boundary_hints'][0]
        replayed_vote = cue_checkpoint['accepted_boundary_votes'][0]

        assert list(replay_result.rendered_text_by_cue_id.values()) == list(
            first_result.rendered_text_by_cue_id.values()
        )
        assert replayed_vote['evidence_refs'] == current_hint['evidence_refs']
        assert ai_checkpoint['accepted_boundary_votes'] == []
        assert replay_saved.artifacts['metrics'][
            'script_boundary_hint_accepted_count'
        ] == 1
        assert cue_checkpoint['input_refs'][
            'script_boundary_hints_hash'
        ] == evidence_checkpoint['output_refs']['script_boundary_hints_hash']
        assert cue_checkpoint['output_refs'][
            'script_evidence_metrics_hash'
        ] == canonical_sha256(replay_saved.artifacts['metrics'])
        assert cue_checkpoint['input_refs'][
            'accepted_boundary_votes_hash'
        ] == canonical_sha256(cue_checkpoint['accepted_boundary_votes'])
        for checkpoint in checkpoints.values():
            assert checkpoint['checkpoint_hash'] == canonical_sha256({
                key: value
                for key, value in checkpoint.items()
                if key != 'checkpoint_hash'
            })


def test_replay_keeps_only_boundary_evidence_allowed_by_current_gap() -> None:
    words = ['第一层完整表达', '第二层完整表达']
    raw = _raw(words, word_duration_ms=900, word_step_ms=940)
    evidence = pipeline_module._build_evidence(
        run_id='run-filter-replay-evidence',
        raw_snapshot=raw,
        script_snapshot=build_script_snapshot('\n'.join(words)),
        runtime_lexicon_bundle=None,
        window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
    )
    hint = evidence.script_boundary_hints[0]
    previous_vote = {
        'gap_id': hint.gap_id,
        'left_token_id': hint.left_token_id,
        'right_token_id': hint.right_token_id,
        'rating': 'preferred',
        'reason_code': 'semantic_boundary',
        'evidence_refs': sorted({
            *hint.evidence_refs,
            'forged:evidence',
            'script-boundary:stale',
            'script-line-stale',
            f"script-source:{'0' * 64}",
        }),
    }

    responses = pipeline_module._build_replay_prepared_responses(
        evidence,
        {
            'checkpoints': {
                'ai_planning': {
                    'accepted_boundary_votes': [previous_vote],
                },
                'cue_validating': {
                    'overlays': [],
                },
            },
        },
    )

    replayed_vote = next(
        vote
        for response in responses.values()
        for vote in response['boundary_votes']
        if vote['gap_id'] == hint.gap_id
    )
    assert replayed_vote['evidence_refs'] == list(hint.evidence_refs)


def test_replay_drops_script_fallback_when_current_script_evidence_disappears() -> None:
    words = ['第一层完整表达', '第二层完整表达']
    raw = _raw(words, word_duration_ms=900, word_step_ms=940)
    engine = _engine()
    with Session(engine) as session:
        first_run, first_lease = _run(
            session,
            raw,
            'run-script-replay-removed-source',
        )
        run_subtitle_pipeline_v2_shadow(
            session,
            first_lease,
            raw_snapshot=raw,
            script_snapshot=build_script_snapshot('\n'.join(words)),
            prepared_responses={},
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )
        previous_artifact = deepcopy(
            session.get(SubtitlePipelineRun, first_run.id).artifacts
        )
        assert previous_artifact['checkpoints']['cue_validating'][
            'accepted_boundary_votes'
        ]

        replay_run, replay_lease = _run(
            session,
            raw,
            'run-script-replay-removed-target',
        )
        run_subtitle_pipeline_v2_shadow(
            session,
            replay_lease,
            raw_snapshot=raw,
            script_snapshot=build_script_snapshot(''),
            replay_decisions_from_artifact=previous_artifact,
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )

        replay_artifact = session.get(
            SubtitlePipelineRun,
            replay_run.id,
        ).artifacts
        assert replay_artifact['checkpoints']['ai_planning'][
            'accepted_boundary_votes'
        ] == []
        assert replay_artifact['checkpoints']['cue_validating'][
            'accepted_boundary_votes'
        ] == []
        assert replay_artifact['metrics']['script_boundary_hint_count'] == 0


def test_replay_regenerates_fallback_from_changed_current_script_evidence() -> None:
    words = ['第一层完整表达', '第二层完整表达']
    raw = _raw(words, word_duration_ms=900, word_step_ms=940)
    original_script = build_script_snapshot('\n'.join(words))
    changed_script = build_script_snapshot(
        f'{words[0]}。\n{words[1]}',
    )
    engine = _engine()
    with Session(engine) as session:
        first_run, first_lease = _run(
            session,
            raw,
            'run-script-replay-changed-source',
        )
        run_subtitle_pipeline_v2_shadow(
            session,
            first_lease,
            raw_snapshot=raw,
            script_snapshot=original_script,
            prepared_responses={},
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )
        previous_artifact = deepcopy(
            session.get(SubtitlePipelineRun, first_run.id).artifacts
        )
        old_vote = previous_artifact['checkpoints']['cue_validating'][
            'accepted_boundary_votes'
        ][0]

        replay_run, replay_lease = _run(
            session,
            raw,
            'run-script-replay-changed-target',
        )
        run_subtitle_pipeline_v2_shadow(
            session,
            replay_lease,
            raw_snapshot=raw,
            script_snapshot=changed_script,
            replay_decisions_from_artifact=previous_artifact,
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )

        replay_artifact = session.get(
            SubtitlePipelineRun,
            replay_run.id,
        ).artifacts
        current_hint = replay_artifact['checkpoints']['evidence_matching'][
            'script_boundary_hints'
        ][0]
        assert replay_artifact['checkpoints']['ai_planning'][
            'accepted_boundary_votes'
        ] == []
        current_vote = replay_artifact['checkpoints']['cue_validating'][
            'accepted_boundary_votes'
        ][0]
        assert current_vote['evidence_refs'] == current_hint['evidence_refs']
        assert current_vote['evidence_refs'] != old_vote['evidence_refs']


def test_ai_vote_wins_over_strong_script_fallback_on_the_same_gap() -> None:
    words = ['第一层完整表达', '第二层完整表达']
    raw = _raw(words, word_duration_ms=900, word_step_ms=940)
    script = build_script_snapshot('\n'.join(words))
    engine = _engine()
    with Session(engine) as session:
        run, lease = _run(session, raw, 'run-script-ai-wins')
        evidence = pipeline_module._build_evidence(
            run_id=run.id,
            raw_snapshot=raw,
            script_snapshot=script,
            runtime_lexicon_bundle=None,
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )
        hint = evidence.script_boundary_hints[0]
        owner = next(
            window
            for window in evidence.windows
            if any(
                gap.gap_id == hint.gap_id and gap.core_owned
                for gap in window.gaps
            )
        )
        response = _accepted(
            owner,
            votes=({
                'gap_id': hint.gap_id,
                'left_token_id': hint.left_token_id,
                'right_token_id': hint.right_token_id,
                'rating': 'forbidden',
                'reason_code': 'phrase_integrity',
                'evidence_refs': list(hint.evidence_refs),
            },),
        )

        result = run_subtitle_pipeline_v2_shadow(
            session,
            lease,
            raw_snapshot=raw,
            script_snapshot=script,
            prepared_responses={owner.window_id: response},
            window_profile=SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE,
        )

        assert list(result.rendered_text_by_cue_id.values()) == [''.join(words)]
        saved = session.get(SubtitlePipelineRun, run.id)
        assert saved is not None
        votes = saved.artifacts['checkpoints']['cue_validating'][
            'accepted_boundary_votes'
        ]
        assert len(votes) == 1
        assert votes[0]['rating'] == 'forbidden'
        assert saved.artifacts['metrics']['script_boundary_hint_accepted_count'] == 1


def test_script_fallback_rechecks_gaps_after_corrections_rebuild_hard_atoms() -> None:
    raw = _raw(['这里就是扎记', '村', '现在继续参观'])
    semantic = build_semantic_utterances_v2(raw)
    script = build_script_snapshot('这里就是扎记\n村现在继续参观')
    base_atoms = build_token_bound_lexical_atoms(raw, semantic)
    base_gaps = build_subtitle_legal_gaps(raw, semantic, atoms=base_atoms)
    alignments = pipeline_module.build_exact_script_line_alignments(
        raw_snapshot=raw,
        semantic_artifact=semantic,
        script_snapshot=script,
    )
    hints = pipeline_module.analyze_script_boundary_hints(
        raw_snapshot=raw,
        gaps=base_gaps,
        alignments=alignments,
    ).hints
    line = script.lines[0]
    correction = CorrectionCandidate(
        candidate_id='correction-crosses-script-boundary',
        run_id='run-final-gap-recheck',
        utterance_id=semantic.utterances[0].utterance_id,
        token_ids=(raw.tokens[0].token_id, raw.tokens[1].token_id),
        source_text='这里就是扎记村',
        replacement_text='这里就是查济村',
        script_line_id=line.line_id,
        script_line_order=line.order_index,
        script_source_hash=script.source_hash,
        phonetic_score=0.9,
        context_score=1.0,
        ranking_score=0.9,
        margin=0.1,
    )
    final_atoms = build_token_bound_lexical_atoms(
        raw,
        semantic,
        accepted_corrections=(correction,),
    )
    final_gaps = build_subtitle_legal_gaps(raw, semantic, atoms=final_atoms)

    assert len(hints) == 1
    assert base_gaps[0].disposition == 'allowed'
    assert final_gaps[0].disposition == 'forbidden'
    assert pipeline_module._merge_script_boundary_fallback_votes(
        hints,
        final_gaps,
    ) == ()


def test_offline_shadow_run_advances_all_stages_and_never_calls_provider() -> None:
    raw = _raw(['我们', '来到', '扎记村', '现在', '留存', '建筑'])
    script = build_script_snapshot('我们来到查济村现在留存建筑')
    engine = _engine()
    with Session(engine) as session:
        run, lease = _run(session, raw)
        candidates, windows = _facts(raw, script, run.id)
        assert len(candidates) == 1
        responses = {
            window.window_id: _accepted(
                window,
                candidate_ids=(candidates[0].candidate_id,),
            )
            for window in windows
        }

        result = run_subtitle_pipeline_v2_shadow(
            session,
            lease,
            raw_snapshot=raw,
            script_snapshot=script,
            prepared_responses=responses,
            client=pytest.fail,
        )

        session.expire_all()
        saved = session.get(SubtitlePipelineRun, run.id)
        assert saved is not None
        assert result.stage == saved.stage == 'ready'
        assert saved.lease_owner is None
        assert saved.evidence_completed_at is not None
        assert saved.ai_started_at is not None
        assert saved.ai_completed_at is not None
        assert saved.validation_started_at is not None
        assert saved.validation_completed_at is not None
        assert set(saved.artifacts['checkpoints']) == {
            'evidence_matching',
            'ai_planning',
            'cue_validating',
        }
        for checkpoint in saved.artifacts['checkpoints'].values():
            assert checkpoint['input_refs']
            assert checkpoint['output_refs']
            assert checkpoint['checkpoint_hash'] == canonical_sha256({
                key: value
                for key, value in checkpoint.items()
                if key != 'checkpoint_hash'
            })
        assert saved.artifact_hash == canonical_sha256({
            key: value
            for key, value in saved.artifacts.items()
            if key != 'content_hash'
        })
        assert saved.artifacts['content_hash'] == saved.artifact_hash
        assert '查济村' in ''.join(result.rendered_text_by_cue_id.values())
        assert raw.snapshot_hash == saved.artifacts['raw_snapshot_hash']
        assert 'tokens' not in saved.artifacts


def test_partial_and_total_window_failures_degrade_without_bypassing_planner() -> None:
    raw = _raw(['甲'] * 65)
    script = build_script_snapshot('')
    engine = _engine()
    with Session(engine) as session:
        run, lease = _run(session, raw, 'run-partial')
        _candidates, windows = _facts(raw, script, run.id)
        assert len(windows) == 2
        legal_gap = next(gap for gap in windows[0].gaps if gap.legal_gap and gap.core_owned)
        vote = {
            'gap_id': legal_gap.gap_id,
            'left_token_id': legal_gap.left_token_id,
            'right_token_id': legal_gap.right_token_id,
            'rating': 'preferred',
            'reason_code': 'semantic_boundary',
            'evidence_refs': list(legal_gap.evidence_refs),
        }

        result = run_subtitle_pipeline_v2_shadow(
            session,
            lease,
            raw_snapshot=raw,
            script_snapshot=script,
            prepared_responses={
                windows[0].window_id: _accepted(windows[0], votes=(vote,)),
            },
        )

        assert result.stage == 'ready_with_warnings'
        assert result.cue_plan.cues
        saved = session.get(SubtitlePipelineRun, run.id)
        assert saved is not None
        assert saved.artifacts['checkpoints']['cue_validating'][
            'accepted_boundary_votes'
        ][0]['gap_id'] == legal_gap.gap_id
        assert saved.ai_completed_windows == 1
        assert saved.ai_failed_windows == 1
        assert saved.ai_degraded_windows == 0

    with Session(engine) as session:
        run, lease = _run(session, raw, 'run-total')
        result = run_subtitle_pipeline_v2_shadow(
            session,
            lease,
            raw_snapshot=raw,
            script_snapshot=script,
            prepared_responses={},
        )
        assert result.stage == 'ready_with_warnings'
        assert result.cue_plan.cues
        assert session.get(SubtitlePipelineRun, run.id).ai_failed_windows == 2


def test_p0_validation_finishes_run_as_failed(monkeypatch) -> None:
    raw = _raw(['安全', '事实'])
    script = build_script_snapshot('')
    engine = _engine()
    with Session(engine) as session:
        run, lease = _run(session, raw, 'run-p0')
        monkeypatch.setattr(
            'app.services.subtitle_pipeline_v2.validate_subtitle_cue_plan',
            lambda **_kwargs: CueValidationReport(issues=[CueValidationIssue(
                code='forced_test_failure',
                severity='P0',
                message='forced P0',
            )]),
        )

        result = run_subtitle_pipeline_v2_shadow(
            session,
            lease,
            raw_snapshot=raw,
            script_snapshot=script,
            prepared_responses={},
        )

        assert result.stage == 'failed'
        saved = session.get(SubtitlePipelineRun, run.id)
        assert saved is not None
        assert saved.validator_p0_count == 1
        assert saved.error_code == 'cue_validation_p0'


class _InterruptingClient:
    def __init__(self, accepted_window_id: str) -> None:
        self.accepted_window_id = accepted_window_id
        self.calls: list[str] = []

    def plan_window(self, *, window, **_kwargs):
        self.calls.append(window.window_id)
        if window.window_id != self.accepted_window_id:
            raise KeyboardInterrupt('simulated process stop')
        validation = validate_deepseek_plan(window, _accepted(window))
        return DeepSeekPlanExecutionResult(
            window_id=window.window_id,
            status='accepted',
            attempts=1,
            validation=validation,
        )


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def plan_window(self, *, window, **_kwargs):
        self.calls.append(window.window_id)
        validation = validate_deepseek_plan(window, _accepted(window))
        return DeepSeekPlanExecutionResult(
            window_id=window.window_id,
            status='accepted',
            attempts=1,
            validation=validation,
        )


def test_restart_skips_validated_window_checkpoint_and_runs_only_unfinished() -> None:
    raw = _raw(['乙'] * 65)
    script = build_script_snapshot('')
    engine = _engine()
    with Session(engine) as session:
        run, lease = _run(session, raw, 'run-resume')
        _candidates, windows = _facts(raw, script, run.id)
        first_client = _InterruptingClient(windows[0].window_id)

        with pytest.raises(KeyboardInterrupt):
            run_subtitle_pipeline_v2_shadow(
                session,
                lease,
                raw_snapshot=raw,
                script_snapshot=script,
                client=first_client,
            )
        assert first_client.calls == [windows[0].window_id, windows[1].window_id]
        session.expire_all()
        checkpoint = session.get(SubtitlePipelineRun, run.id).artifacts[
            'checkpoints'
        ]['ai_planning']
        assert list(checkpoint['window_results']) == [windows[0].window_id]

        takeover = claim_pipeline_run_lease(
            session,
            run.id,
            owner='worker-2',
            now=datetime.now(timezone.utc) + timedelta(seconds=121),
            ttl_seconds=120,
        )
        assert takeover is not None
        second_client = _RecordingClient()
        result = run_subtitle_pipeline_v2_shadow(
            session,
            takeover,
            raw_snapshot=raw,
            script_snapshot=script,
            client=second_client,
        )

        assert result.stage in {'ready', 'ready_with_warnings'}
        assert second_client.calls == [windows[1].window_id]
