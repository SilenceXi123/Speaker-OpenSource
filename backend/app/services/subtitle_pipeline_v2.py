from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import re
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.services.deepseek_subtitle_contract import (
    DeepSeekBoundaryVote,
    DeepSeekPlanExecutionResult,
    DeepSeekPlanWindow,
    DeepSeekWindowProfile,
    build_deepseek_plan_windows,
    run_deepseek_plan_window,
)
from app.services.script_correction_candidates import (
    CorrectionCandidate,
    SCRIPT_DISPLAY_FIDELITY_EVIDENCE_REF,
    ScriptSnapshot,
    build_correction_candidates,
    is_script_display_fidelity_change,
)
from app.services.script_boundary_hints import (
    ScriptBoundaryHint,
    analyze_script_boundary_hints,
)
from app.services.script_line_alignment import (
    ExactScriptLineAlignment,
    build_exact_script_line_alignments,
)
from app.services.semantic_utterances_v2 import build_semantic_utterances_v2
from app.services.subtitle_atoms import (
    TokenBoundLexicalAtom,
    build_token_bound_lexical_atoms,
)
from app.services.subtitle_cue_validator import (
    CueValidationReport,
    validate_subtitle_cue_plan,
)
from app.services.subtitle_global_planner import (
    DEFAULT_CUE_PLANNER_PROFILE,
    CuePlannerProfile,
    SubtitleCuePlanArtifact,
    build_subtitle_global_plan,
)
from app.services.subtitle_legal_gaps import SubtitleLegalGap, build_subtitle_legal_gaps
if TYPE_CHECKING:
    from app.services.subtitle_pipeline_runs import PipelineRunLease
from app.services.subtitle_pipeline_v2_schema import (
    RawAsrSnapshot,
    SemanticUtteranceArtifact,
    canonical_sha256,
)
from app.services.subtitle_token_overlays import (
    AuthorizedTextOverlay,
    FillerTokenMask,
    apply_authorized_overlays,
    render_token_span_text,
)


PIPELINE_V2_RUN_SCHEMA_VERSION = 'subtitle-pipeline-v2-run.v1'
FINAL_CUE_ARTIFACT_SCHEMA_VERSION = 'subtitle-cue-artifact.v1'
_ACTIVE_STAGES = frozenset({'evidence_matching', 'ai_planning', 'cue_validating'})
SINGLE_PASS_DEEPSEEK_WINDOW_PROFILE = DeepSeekWindowProfile(
    profile_version='deepseek-single-pass.v1',
    core_token_limit=256,
    overlap_tokens=0,
    combine_semantic_utterances=True,
)
_SPOKEN_DATE_RE = re.compile(
    r'(?<!\d)(?P<year>\d{2,4})年(?:的)?'
    r'(?P<month>0?[1-9]|1[0-2])/(?P<day>0?[1-9]|[12]\d|3[01])(?!\d)'
)


@dataclass(frozen=True)
class SubtitlePipelineV2Result:
    stage: str
    cue_plan: SubtitleCuePlanArtifact
    validation: CueValidationReport
    rendered_text_by_cue_id: dict[str, str]


@dataclass(frozen=True)
class _EvidenceFacts:
    semantic: SemanticUtteranceArtifact
    script_alignments: tuple[ExactScriptLineAlignment, ...]
    candidates: tuple[CorrectionCandidate, ...]
    base_atoms: tuple[TokenBoundLexicalAtom, ...]
    base_gaps: tuple[SubtitleLegalGap, ...]
    script_boundary_hints: tuple[ScriptBoundaryHint, ...]
    script_boundary_hint_candidate_count: int
    script_boundary_hint_rejected_illegal_gap_count: int
    windows: tuple[DeepSeekPlanWindow, ...]
    checkpoint: dict[str, Any]


def build_evidence_subtitle_segments_v2(
    *,
    raw_snapshot: RawAsrSnapshot,
    script_snapshot: ScriptSnapshot,
    runtime_lexicon_bundle: Mapping[str, Any] | None = None,
    cue_profile: CuePlannerProfile = DEFAULT_CUE_PLANNER_PROFILE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build final legacy-compatible segments without a provider call."""
    semantic = build_semantic_utterances_v2(raw_snapshot)
    rejected_corrections: list[dict[str, Any]] = []
    script_alignments = build_exact_script_line_alignments(
        raw_snapshot=raw_snapshot,
        semantic_artifact=semantic,
        script_snapshot=script_snapshot,
    )
    candidates = build_correction_candidates(
        run_id=f'evidence-{raw_snapshot.snapshot_hash}',
        rejected_corrections=rejected_corrections,
        raw_snapshot=raw_snapshot,
        semantic_artifact=semantic,
        script_snapshot=script_snapshot,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        script_alignments=script_alignments,
    )
    base_atoms = build_token_bound_lexical_atoms(
        raw_snapshot,
        semantic,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    base_gaps = build_subtitle_legal_gaps(
        raw_snapshot,
        semantic,
        atoms=base_atoms,
    )
    script_boundary_analysis = analyze_script_boundary_hints(
        raw_snapshot=raw_snapshot,
        gaps=base_gaps,
        alignments=script_alignments,
    )
    accepted_candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    overlays = apply_authorized_overlays(
        raw_snapshot=raw_snapshot,
        correction_candidates=candidates,
        accepted_candidate_ids=accepted_candidate_ids,
    )
    atoms = build_token_bound_lexical_atoms(
        raw_snapshot,
        semantic,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        accepted_corrections=candidates,
    )
    gaps = build_subtitle_legal_gaps(
        raw_snapshot,
        semantic,
        atoms=atoms,
    )
    accepted_votes = _merge_script_boundary_fallback_votes(
        script_boundary_analysis.hints,
        gaps,
    )
    cue_plan = build_subtitle_global_plan(
        raw_snapshot=raw_snapshot,
        atoms=atoms,
        gaps=gaps,
        boundary_votes=accepted_votes,
        profile=cue_profile,
    )
    rendered = {
        cue.cue_id: render_token_span_text(
            raw_snapshot=raw_snapshot,
            token_start=cue.token_start,
            token_end=cue.token_end,
            overlays=overlays,
            masks=(),
        )
        for cue in cue_plan.cues
    }
    validation = validate_subtitle_cue_plan(
        raw_snapshot=raw_snapshot,
        overlays=overlays,
        masks=(),
        atoms=atoms,
        gaps=gaps,
        cue_plan=cue_plan,
        rendered_text_by_cue_id=rendered,
    )
    if any(issue.severity == 'P0' for issue in validation.issues):
        raise ValueError('evidence subtitle plan failed fact-conservation validation')

    segments, display_normalizations = build_legacy_segments_from_v2_cues(
        raw_snapshot=raw_snapshot,
        cues=cue_plan.cues,
        rendered_text_by_cue_id=rendered,
    )
    script_metrics = _script_evidence_metrics(
        candidates=candidates,
        overlays=overlays,
        hints=script_boundary_analysis.hints,
        accepted_votes=accepted_votes,
        hint_candidate_count=script_boundary_analysis.candidate_count,
        rejected_illegal_gap_count=(
            script_boundary_analysis.rejected_illegal_gap_count
        ),
    )
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    artifact = {
        'schema_version': 'evidence-subtitle.v1',
        'raw_snapshot_hash': raw_snapshot.snapshot_hash,
        'script_snapshot_hash': script_snapshot.snapshot_hash,
        'cue_plan_hash': cue_plan.artifact_hash,
        'raw_token_count': len(raw_snapshot.tokens),
        'cue_count': len(cue_plan.cues),
        'timing_policy': 'apple_attributed_run' if raw_snapshot.provider_model == 'apple_speech' else 'direct_raw_word_span',
        **script_metrics,
        'display_normalizations': display_normalizations,
        'rejected_corrections': rejected_corrections,
        'corrections': correction_history_with_timing(raw_snapshot, [
            {
                'candidate_id': overlay.candidate_id,
                'token_ids': list(overlay.token_ids),
                'source_text': overlay.source_text,
                'replacement_text': overlay.replacement_text,
                'script_line_id': candidate_by_id[overlay.candidate_id].script_line_id,
                'phonetic_score': candidate_by_id[overlay.candidate_id].phonetic_score,
                'context_score': candidate_by_id[overlay.candidate_id].context_score,
                'margin': candidate_by_id[overlay.candidate_id].margin,
            }
            for overlay in overlays
        ]),
        'validation': validation.to_dict(),
    }
    return segments, artifact


def correction_history_with_timing(
    raw_snapshot: RawAsrSnapshot,
    corrections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Locate against the actual V2 snapshot, never the separately numbered UI snapshot."""
    token_positions = {token.token_id: index for index, token in enumerate(raw_snapshot.tokens)}
    result = []
    for correction in corrections:
        item = dict(correction)
        token_ids = item.get('token_ids') or []
        indexes = [token_positions.get(token_id) for token_id in token_ids]
        if indexes and all(index is not None for index in indexes):
            first, last = indexes[0], indexes[-1]
            tokens = raw_snapshot.tokens[first:last + 1]
            if (
                indexes == list(range(first, last + 1))
                and item.get('raw_snapshot_hash', raw_snapshot.snapshot_hash) == raw_snapshot.snapshot_hash
                and ''.join(token.text for token in tokens) == item.get('source_text')
            ):
                item['start_ms'] = tokens[0].start_ms
                item['end_ms'] = tokens[-1].end_ms
        result.append(item)
    return result


def build_legacy_segments_from_v2_cues(
    *,
    raw_snapshot: RawAsrSnapshot,
    cues: Sequence[Any],
    rendered_text_by_cue_id: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    segments: list[dict[str, Any]] = []
    display_normalizations: list[dict[str, str]] = []
    for cue in cues:
        if cue.start_ms is None or cue.end_ms is None or cue.end_ms <= cue.start_ms:
            raise ValueError('evidence subtitle cue requires direct raw token timing')
        source_text = rendered_text_by_cue_id[cue.cue_id]
        text, cue_normalizations = normalize_evidence_display_text(source_text)
        display_normalizations.extend({
            'cue_id': cue.cue_id,
            **item,
        } for item in cue_normalizations)
        tokens = raw_snapshot.tokens[cue.token_start:cue.token_end]
        segments.append({
            'segmentId': cue.cue_id,
            'speakerKey': cue.speaker_key or 'speaker_0',
            'speakerId': cue.speaker_id,
            'startMs': cue.start_ms,
            'endMs': cue.end_ms,
            'text': text,
            'words': [
                {
                    'startMs': token.start_ms,
                    'endMs': token.end_ms,
                    'text': token.text,
                    'punctuation': token.punctuation,
                }
                for token in tokens
            ],
            'subtitleParts': [text],
            'subtitlePartRanges': [{
                'startMs': cue.start_ms,
                'endMs': cue.end_ms,
            }],
            'sourceTokenIds': list(cue.token_ids),
            'timingEvidence': 'apple_attributed_run' if raw_snapshot.provider_model == 'apple_speech' else 'direct_raw_word_span',
            'v2FinalCue': True,
        })
    return segments, display_normalizations


def normalize_evidence_display_text(
    text: str,
) -> tuple[str, list[dict[str, str]]]:
    changes: list[dict[str, str]] = []

    def replace_date(match: re.Match[str]) -> str:
        replacement = (
            f"{match.group('year')}年{int(match.group('month'))}月"
            f"{int(match.group('day'))}号"
        )
        changes.append({
            'kind': 'spoken_date',
            'source_text': match.group(0),
            'replacement_text': replacement,
        })
        return replacement

    return _SPOKEN_DATE_RE.sub(replace_date, str(text or '')), changes


def run_subtitle_pipeline_v2_shadow(
    session: Session,
    lease: PipelineRunLease,
    *,
    raw_snapshot: RawAsrSnapshot,
    script_snapshot: ScriptSnapshot,
    runtime_lexicon_bundle: Mapping[str, Any] | None = None,
    prepared_responses: Mapping[str, Mapping[str, Any] | str] | None = None,
    replay_decisions_from_artifact: Mapping[str, Any] | None = None,
    client: Any | None = None,
    cue_profile: CuePlannerProfile = DEFAULT_CUE_PLANNER_PROFILE,
    window_profile: DeepSeekWindowProfile | None = None,
) -> SubtitlePipelineV2Result:
    from app.services.subtitle_pipeline_runs import assert_pipeline_run_lease, transition_pipeline_run_with_lease

    if prepared_responses is not None and replay_decisions_from_artifact is not None:
        raise ValueError('prepared responses and replay decisions are mutually exclusive')
    run = assert_pipeline_run_lease(session, lease)
    if run.stage not in _ACTIVE_STAGES:
        raise RuntimeError(f'V2 shadow runner cannot start from stage {run.stage}')
    if run.pipeline_version != 'speaker-v2':
        raise ValueError('V2 shadow runner requires a speaker-v2 run')
    if run.raw_snapshot_hash != raw_snapshot.snapshot_hash:
        raise ValueError('pipeline run references a different raw snapshot')

    artifact = _initial_artifact(
        run_id=run.id,
        raw_snapshot=raw_snapshot,
        script_snapshot=script_snapshot,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        existing=run.artifacts,
    )
    evidence = _build_evidence(
        run_id=run.id,
        raw_snapshot=raw_snapshot,
        script_snapshot=script_snapshot,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        window_profile=window_profile,
    )
    if replay_decisions_from_artifact is not None:
        prepared_responses = _build_replay_prepared_responses(
            evidence,
            replay_decisions_from_artifact,
        )
    artifact['checkpoints']['evidence_matching'] = evidence.checkpoint
    _save_checkpoint(session, lease, artifact)
    if run.stage == 'evidence_matching':
        run = transition_pipeline_run_with_lease(session, lease, 'ai_planning')

    window_results = _load_window_results(
        artifact,
        windows_hash=evidence.checkpoint['output_refs']['windows_hash'],
        windows=evidence.windows,
    )
    provider_unavailable = any(
        result.status == 'degraded' and result.failure_code == 'transport_failure'
        for result in window_results.values()
    )
    warnings: list[str] = []
    for window in evidence.windows:
        if window.window_id in window_results:
            continue
        if provider_unavailable and prepared_responses is None:
            result = DeepSeekPlanExecutionResult(
                window_id=window.window_id,
                status='degraded',
                attempts=1,
                failure_code='transport_failure',
            )
        else:
            result = _run_window(
                window,
                project_id=run.project_id,
                job_id=run.job_id,
                lease=lease,
                prepared_responses=prepared_responses,
                client=client,
            )
            provider_unavailable = (
                result.status == 'degraded'
                and result.failure_code == 'transport_failure'
            )
        window_results[window.window_id] = result
        ai_checkpoint, warnings = _ai_checkpoint(
            evidence,
            window_results,
        )
        artifact['checkpoints']['ai_planning'] = ai_checkpoint
        _save_checkpoint(
            session,
            lease,
            artifact,
            metrics=_ai_metrics(evidence.windows, window_results, warnings),
        )

    ai_checkpoint, warnings = _ai_checkpoint(evidence, window_results)
    artifact['checkpoints']['ai_planning'] = ai_checkpoint
    _save_checkpoint(
        session,
        lease,
        artifact,
        metrics=_ai_metrics(evidence.windows, window_results, warnings),
    )
    run = assert_pipeline_run_lease(session, lease)
    if run.stage == 'ai_planning':
        run = transition_pipeline_run_with_lease(session, lease, 'cue_validating')

    accepted_candidate_ids, candidate_warnings = _accepted_candidate_ids(
        evidence.candidates,
        window_results,
    )
    warnings.extend(candidate_warnings)
    accepted_candidate_id_set = set(accepted_candidate_ids)
    accepted_candidates = tuple(
        candidate
        for candidate in evidence.candidates
        if candidate.candidate_id in accepted_candidate_id_set
    )
    overlays = apply_authorized_overlays(
        raw_snapshot=raw_snapshot,
        correction_candidates=evidence.candidates,
        accepted_candidate_ids=accepted_candidate_ids,
    )
    final_atoms = build_token_bound_lexical_atoms(
        raw_snapshot,
        evidence.semantic,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        accepted_corrections=accepted_candidates,
    )
    final_gaps = build_subtitle_legal_gaps(
        raw_snapshot,
        evidence.semantic,
        atoms=final_atoms,
    )
    ai_votes = _legal_boundary_votes(window_results, final_gaps)
    accepted_votes = _merge_script_boundary_fallback_votes(
        evidence.script_boundary_hints,
        final_gaps,
        existing_votes=ai_votes,
    )
    # Keep every spoken token in the automatic path. Filler removal remains an
    # explicit user edit instead of becoming an invisible V2 side effect.
    masks: tuple[FillerTokenMask, ...] = ()
    cue_plan = build_subtitle_global_plan(
        raw_snapshot=raw_snapshot,
        atoms=final_atoms,
        gaps=final_gaps,
        boundary_votes=accepted_votes,
        profile=cue_profile,
    )
    rendered = {
        cue.cue_id: render_token_span_text(
            raw_snapshot=raw_snapshot,
            token_start=cue.token_start,
            token_end=cue.token_end,
            overlays=overlays,
            masks=masks,
        )
        for cue in cue_plan.cues
    }
    validation = validate_subtitle_cue_plan(
        raw_snapshot=raw_snapshot,
        overlays=overlays,
        masks=masks,
        atoms=final_atoms,
        gaps=final_gaps,
        cue_plan=cue_plan,
        rendered_text_by_cue_id=rendered,
    )
    issue_warnings = [
        f'{issue.severity}:{issue.code}'
        for issue in validation.issues
        if issue.severity != 'P0'
    ]
    warnings = _unique((*warnings, *issue_warnings))
    script_metrics = _script_evidence_metrics(
        candidates=evidence.candidates,
        overlays=overlays,
        hints=evidence.script_boundary_hints,
        accepted_votes=accepted_votes,
        hint_candidate_count=evidence.script_boundary_hint_candidate_count,
        rejected_illegal_gap_count=(
            evidence.script_boundary_hint_rejected_illegal_gap_count
        ),
    )
    final_cue_artifact = _final_cue_artifact(
        run_id=run.id,
        raw_snapshot=raw_snapshot,
        cue_plan=cue_plan,
        overlays=overlays,
        masks=masks,
        rendered=rendered,
        validation=validation,
        metrics=script_metrics,
    )
    cue_checkpoint = _checkpoint(
        input_refs={
            'raw_snapshot_hash': raw_snapshot.snapshot_hash,
            'ai_checkpoint_hash': ai_checkpoint['checkpoint_hash'],
            'final_atoms_hash': canonical_sha256(_models(final_atoms)),
            'final_gaps_hash': canonical_sha256(_models(final_gaps)),
            'accepted_boundary_votes_hash': canonical_sha256(_models(accepted_votes)),
            'script_boundary_hints_hash': evidence.checkpoint['output_refs'][
                'script_boundary_hints_hash'
            ],
        },
        output_refs={
            'cue_plan_hash': cue_plan.artifact_hash,
            'validation_hash': canonical_sha256(validation.to_dict()),
            'final_cue_artifact_hash': final_cue_artifact['artifact_hash'],
            'script_evidence_metrics_hash': canonical_sha256(script_metrics),
        },
        values={
            'final_atoms': _models(final_atoms),
            'final_gaps': _models(final_gaps),
            'overlays': _models(overlays),
            'rejected_corrections': evidence.checkpoint.get('rejected_corrections', []),
            'masks': _models(masks),
            'accepted_boundary_votes': _models(accepted_votes),
            'cue_plan': cue_plan.model_dump(mode='json'),
            'validation': validation.to_dict(),
            **script_metrics,
        },
    )
    artifact['checkpoints']['cue_validating'] = cue_checkpoint
    artifact['final_cue_artifact'] = final_cue_artifact
    artifact['metrics'] = script_metrics
    p0_count = sum(issue.severity == 'P0' for issue in validation.issues)
    _save_checkpoint(
        session,
        lease,
        artifact,
        metrics={
            **_ai_metrics(evidence.windows, window_results, warnings),
            'validator_phase': 'completed',
            'validator_issue_count': len(validation.issues),
            'validator_p0_count': p0_count,
            'warnings': warnings,
        },
    )

    terminal_stage = 'failed' if p0_count else (
        'ready_with_warnings' if warnings else 'ready'
    )
    transition_pipeline_run_with_lease(
        session,
        lease,
        terminal_stage,
        error_code='cue_validation_p0' if p0_count else None,
        error_summary='V2 cue validation reported P0 issues' if p0_count else None,
    )
    return SubtitlePipelineV2Result(
        stage=terminal_stage,
        cue_plan=cue_plan,
        validation=validation,
        rendered_text_by_cue_id=rendered,
    )


def _build_replay_prepared_responses(
    evidence: _EvidenceFacts,
    previous_artifact: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Replay only previously accepted decisions against current evidence IDs."""
    checkpoints = (
        previous_artifact.get('checkpoints', {})
        if isinstance(previous_artifact, Mapping)
        else {}
    )
    ai_checkpoint = checkpoints.get('ai_planning', {})
    cue_checkpoint = checkpoints.get('cue_validating', {})
    previous_votes: dict[str, DeepSeekBoundaryVote] = {}
    for value in ai_checkpoint.get('accepted_boundary_votes') or []:
        try:
            vote = DeepSeekBoundaryVote.model_validate(value)
        except (TypeError, ValueError):
            continue
        previous_votes[vote.gap_id] = vote

    previous_corrections = {
        (
            tuple(str(item) for item in (value.get('token_ids') or [])),
            str(value.get('source_text') or ''),
            str(value.get('replacement_text') or ''),
        )
        for value in (cue_checkpoint.get('overlays') or [])
        if isinstance(value, Mapping)
    }
    replay_candidate_ids = {
        candidate.candidate_id
        for candidate in evidence.candidates
        if (
            candidate.lexicon_protection_level == 'hard'
            or (
                candidate.token_ids,
                candidate.source_text,
                candidate.replacement_text,
            ) in previous_corrections
        )
    }

    responses: dict[str, dict[str, Any]] = {}
    for window in evidence.windows:
        votes: list[dict[str, Any]] = []
        for gap in window.gaps:
            previous = previous_votes.get(gap.gap_id)
            if (
                previous is None
                or not gap.core_owned
                or not gap.legal_gap
                or gap.disposition != 'allowed'
                or gap.atom_protection == 'hard'
            ):
                continue
            allowed_evidence_refs = {
                *gap.evidence_refs,
                *gap.script_boundary_evidence_refs,
            }
            votes.append({
                'gap_id': gap.gap_id,
                'left_token_id': gap.left_token_id,
                'right_token_id': gap.right_token_id,
                'rating': previous.rating,
                'reason_code': previous.reason_code,
                'evidence_refs': sorted(
                    set(previous.evidence_refs).intersection(
                        allowed_evidence_refs,
                    )
                ),
            })
        responses[window.window_id] = {
            'window_id': window.window_id,
            'boundary_votes': votes,
            'correction_candidate_ids': [
                candidate.candidate_id
                for candidate in window.correction_candidates
                if candidate.core_owned and candidate.candidate_id in replay_candidate_ids
            ],
        }
    return responses


def _build_evidence(
    *,
    run_id: str,
    raw_snapshot: RawAsrSnapshot,
    script_snapshot: ScriptSnapshot,
    runtime_lexicon_bundle: Mapping[str, Any] | None,
    window_profile: DeepSeekWindowProfile | None,
) -> _EvidenceFacts:
    semantic = build_semantic_utterances_v2(raw_snapshot)
    rejected_corrections: list[dict[str, Any]] = []
    script_alignments = build_exact_script_line_alignments(
        raw_snapshot=raw_snapshot,
        semantic_artifact=semantic,
        script_snapshot=script_snapshot,
    )
    candidates = build_correction_candidates(
        run_id=run_id,
        raw_snapshot=raw_snapshot,
        semantic_artifact=semantic,
        rejected_corrections=rejected_corrections,
        script_snapshot=script_snapshot,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        script_alignments=script_alignments,
    )
    base_atoms = build_token_bound_lexical_atoms(
        raw_snapshot,
        semantic,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    base_gaps = build_subtitle_legal_gaps(
        raw_snapshot,
        semantic,
        atoms=base_atoms,
    )
    script_boundary_analysis = analyze_script_boundary_hints(
        raw_snapshot=raw_snapshot,
        gaps=base_gaps,
        alignments=script_alignments,
    )
    windows = build_deepseek_plan_windows(
        raw_snapshot=raw_snapshot,
        semantic_artifact=semantic,
        atoms=base_atoms,
        gaps=base_gaps,
        correction_candidates=candidates,
        script_boundary_hints=script_boundary_analysis.hints,
        **({'profile': window_profile} if window_profile is not None else {}),
    )
    script_alignment_values = [asdict(item) for item in script_alignments]
    script_boundary_hint_values = _models(script_boundary_analysis.hints)
    values = {
        'semantic_artifact': semantic.model_dump(mode='json'),
        'script_alignments': script_alignment_values,
        'correction_candidates': _models(candidates),
        'rejected_corrections': rejected_corrections,
        'base_atoms': _models(base_atoms),
        'base_gaps': _models(base_gaps),
        'script_boundary_hints': script_boundary_hint_values,
        'script_boundary_hint_candidate_count': (
            script_boundary_analysis.candidate_count
        ),
        'script_boundary_hint_rejected_illegal_gap_count': (
            script_boundary_analysis.rejected_illegal_gap_count
        ),
    }
    output_refs = {
        'semantic_artifact_hash': semantic.artifact_hash,
        'script_alignments_hash': canonical_sha256(script_alignment_values),
        'correction_candidates_hash': canonical_sha256(values['correction_candidates']),
        'base_atoms_hash': canonical_sha256(values['base_atoms']),
        'base_gaps_hash': canonical_sha256(values['base_gaps']),
        'script_boundary_hints_hash': canonical_sha256(
            script_boundary_hint_values
        ),
        'windows_hash': canonical_sha256(_models(windows)),
    }
    return _EvidenceFacts(
        semantic=semantic,
        script_alignments=script_alignments,
        candidates=candidates,
        base_atoms=base_atoms,
        base_gaps=base_gaps,
        script_boundary_hints=script_boundary_analysis.hints,
        script_boundary_hint_candidate_count=(
            script_boundary_analysis.candidate_count
        ),
        script_boundary_hint_rejected_illegal_gap_count=(
            script_boundary_analysis.rejected_illegal_gap_count
        ),
        windows=windows,
        checkpoint=_checkpoint(
            input_refs={
                'raw_snapshot_hash': raw_snapshot.snapshot_hash,
                'script_snapshot_hash': script_snapshot.snapshot_hash,
                'runtime_lexicon_hash': canonical_sha256(runtime_lexicon_bundle or {}),
            },
            output_refs=output_refs,
            values=values,
        ),
    )


def _run_window(
    window: DeepSeekPlanWindow,
    *,
    project_id: str,
    job_id: str,
    lease: PipelineRunLease,
    prepared_responses: Mapping[str, Mapping[str, Any] | str] | None,
    client: Any | None,
) -> DeepSeekPlanExecutionResult:
    if prepared_responses is not None:
        response = prepared_responses.get(window.window_id)
        if response is None:
            return DeepSeekPlanExecutionResult(
                window_id=window.window_id,
                status='degraded',
                attempts=1,
                failure_code='transport_failure',
            )
        return run_deepseek_plan_window(window, lambda _window: response)
    if client is None:
        from app.services.deepseek_subtitle_client import DeepSeekSubtitlePlannerClient
        client = DeepSeekSubtitlePlannerClient()
    planner_client = client
    try:
        return planner_client.plan_window(
            project_id=project_id,
            job_id=job_id,
            lease=lease,
            window=window,
        )
    except Exception:
        return DeepSeekPlanExecutionResult(
            window_id=window.window_id,
            status='degraded',
            attempts=1,
            failure_code='transport_failure',
        )


def _ai_checkpoint(
    evidence: _EvidenceFacts,
    window_results: Mapping[str, DeepSeekPlanExecutionResult],
) -> tuple[dict[str, Any], list[str]]:
    ordered_results = {
        window.window_id: window_results[window.window_id].model_dump(mode='json')
        for window in evidence.windows
        if window.window_id in window_results
    }
    candidate_ids, candidate_warnings = _accepted_candidate_ids(
        evidence.candidates,
        window_results,
    )
    votes = _all_boundary_votes(window_results)
    warnings = list(candidate_warnings)
    for window in evidence.windows:
        result = window_results.get(window.window_id)
        if result is None:
            continue
        if result.status == 'degraded':
            warnings.append(
                f'window {window.window_id} degraded: {result.failure_code}'
            )
        elif result.status == 'accepted_with_rejections':
            warnings.append(f'window {window.window_id} accepted with rejections')
    values = {
        'window_results': ordered_results,
        'accepted_candidate_ids': list(candidate_ids),
        'accepted_boundary_votes': _models(votes),
    }
    return _checkpoint(
        input_refs={
            'windows_hash': evidence.checkpoint['output_refs']['windows_hash'],
            'evidence_checkpoint_hash': evidence.checkpoint['checkpoint_hash'],
        },
        output_refs={
            'window_results_hash': canonical_sha256(ordered_results),
            'accepted_candidate_ids_hash': canonical_sha256(list(candidate_ids)),
            'accepted_boundary_votes_hash': canonical_sha256(values['accepted_boundary_votes']),
        },
        values=values,
    ), _unique(warnings)


def _load_window_results(
    artifact: Mapping[str, Any],
    *,
    windows_hash: str,
    windows: Sequence[DeepSeekPlanWindow],
) -> dict[str, DeepSeekPlanExecutionResult]:
    checkpoint = artifact.get('checkpoints', {}).get('ai_planning')
    if not _valid_checkpoint(checkpoint):
        return {}
    if checkpoint.get('input_refs', {}).get('windows_hash') != windows_hash:
        return {}
    known_ids = {window.window_id for window in windows}
    results: dict[str, DeepSeekPlanExecutionResult] = {}
    for window_id, value in checkpoint.get('window_results', {}).items():
        try:
            result = DeepSeekPlanExecutionResult.model_validate(value)
        except (TypeError, ValueError):
            continue
        if window_id in known_ids and result.window_id == window_id:
            results[window_id] = result
    return results


def _accepted_candidate_ids(
    candidates: Sequence[CorrectionCandidate],
    window_results: Mapping[str, DeepSeekPlanExecutionResult],
) -> tuple[tuple[str, ...], list[str]]:
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    known = set(candidate_by_id)
    accepted: list[str] = [
        candidate.candidate_id
        for candidate in candidates
        if (
            candidate.lexicon_protection_level == 'hard'
            or is_script_display_fidelity_change(
                source_text=candidate.source_text,
                replacement_text=candidate.replacement_text,
                evidence_refs=candidate.evidence_refs,
            )
        )
    ]
    warnings: list[str] = []
    for result in window_results.values():
        if result.validation is None:
            continue
        for candidate_id in result.validation.correction_candidate_ids:
            if candidate_id not in known:
                warnings.append(f'unknown accepted candidate ignored: {candidate_id}')
            elif not _is_safe_ai_correction_candidate(candidate_by_id[candidate_id]):
                warning = f'disjoint multi-edit candidate ignored: {candidate_id}'
                if warning not in warnings:
                    warnings.append(warning)
            elif candidate_id not in accepted:
                accepted.append(candidate_id)
    return tuple(accepted), warnings


def _is_safe_ai_correction_candidate(candidate: CorrectionCandidate) -> bool:
    if candidate.lexicon_protection_level == 'hard':
        return True
    source = re.sub(r'[^\u3400-\u9fffA-Za-z0-9]+', '', candidate.source_text).lower()
    replacement = re.sub(
        r'[^\u3400-\u9fffA-Za-z0-9]+',
        '',
        candidate.replacement_text,
    ).lower()
    edit_groups = [
        opcode
        for opcode in SequenceMatcher(None, source, replacement, autojunk=False).get_opcodes()
        if opcode[0] != 'equal'
    ]
    return len(edit_groups) <= 1


def _all_boundary_votes(
    window_results: Mapping[str, DeepSeekPlanExecutionResult],
) -> tuple[DeepSeekBoundaryVote, ...]:
    votes: list[DeepSeekBoundaryVote] = []
    seen: set[str] = set()
    for result in window_results.values():
        if result.validation is None:
            continue
        for vote in result.validation.boundary_votes:
            if vote.gap_id not in seen:
                seen.add(vote.gap_id)
                votes.append(vote)
    return tuple(votes)


def _legal_boundary_votes(
    window_results: Mapping[str, DeepSeekPlanExecutionResult],
    gaps: Sequence[SubtitleLegalGap],
) -> tuple[DeepSeekBoundaryVote, ...]:
    gap_by_id = {gap.gap_id: gap for gap in gaps}
    return tuple(
        vote
        for vote in _all_boundary_votes(window_results)
        if (
            (gap := gap_by_id.get(vote.gap_id)) is not None
            and gap.disposition == 'allowed'
            and gap.legal_gap
            and gap.atom_protection != 'hard'
            and gap.left_token_id == vote.left_token_id
            and gap.right_token_id == vote.right_token_id
        )
    )


def _merge_script_boundary_fallback_votes(
    hints: Sequence[ScriptBoundaryHint],
    gaps: Sequence[SubtitleLegalGap],
    existing_votes: Sequence[DeepSeekBoundaryVote] = (),
) -> tuple[DeepSeekBoundaryVote, ...]:
    votes = list(existing_votes)
    voted_gap_ids = {vote.gap_id for vote in existing_votes}
    gap_by_id = {gap.gap_id: gap for gap in gaps}
    for hint in hints:
        if hint.strength != 'strong' or hint.gap_id in voted_gap_ids:
            continue
        gap = gap_by_id.get(hint.gap_id)
        if (
            gap is None
            or gap.disposition != 'allowed'
            or not gap.legal_gap
            or gap.atom_protection == 'hard'
            or gap.left_token_id != hint.left_token_id
            or gap.right_token_id != hint.right_token_id
            or gap.left_token_index != hint.left_token_index
            or gap.right_token_index != hint.right_token_index
        ):
            continue
        votes.append(DeepSeekBoundaryVote(
            gap_id=gap.gap_id,
            left_token_id=gap.left_token_id,
            right_token_id=gap.right_token_id,
            rating='allowed',
            reason_code='semantic_boundary',
            evidence_refs=hint.evidence_refs,
            decision_source='script_fallback',
        ))
        voted_gap_ids.add(gap.gap_id)
    return tuple(votes)


def _script_evidence_metrics(
    *,
    candidates: Sequence[CorrectionCandidate],
    overlays: Sequence[AuthorizedTextOverlay],
    hints: Sequence[ScriptBoundaryHint],
    accepted_votes: Sequence[DeepSeekBoundaryVote],
    hint_candidate_count: int,
    rejected_illegal_gap_count: int,
) -> dict[str, int]:
    display_candidate_ids = {
        candidate.candidate_id
        for candidate in candidates
        if candidate.evidence_refs == (SCRIPT_DISPLAY_FIDELITY_EVIDENCE_REF,)
    }
    hint_evidence_refs = {
        evidence_ref
        for hint in hints
        for evidence_ref in (hint.hint_id, *hint.evidence_refs)
    }
    return {
        'script_display_fidelity_candidate_count': len(display_candidate_ids),
        'script_display_fidelity_applied_count': sum(
            overlay.candidate_id in display_candidate_ids
            for overlay in overlays
        ),
        'script_boundary_hint_count': int(hint_candidate_count),
        'script_boundary_hint_accepted_count': sum(
            bool(hint_evidence_refs.intersection(vote.evidence_refs))
            for vote in accepted_votes
        ),
        'script_boundary_hint_rejected_illegal_gap_count': int(
            rejected_illegal_gap_count
        ),
    }


def _ai_metrics(
    windows: Sequence[DeepSeekPlanWindow],
    window_results: Mapping[str, DeepSeekPlanExecutionResult],
    warnings: Sequence[str],
) -> dict[str, Any]:
    results = list(window_results.values())
    return {
        'ai_total_windows': len(windows),
        'ai_completed_windows': sum(result.status != 'degraded' for result in results),
        'ai_failed_windows': sum(result.status == 'degraded' for result in results),
        'ai_degraded_windows': sum(
            result.status == 'accepted_with_rejections' for result in results
        ),
        'warnings': list(warnings),
    }


def _initial_artifact(
    *,
    run_id: str,
    raw_snapshot: RawAsrSnapshot,
    script_snapshot: ScriptSnapshot,
    runtime_lexicon_bundle: Mapping[str, Any] | None,
    existing: Mapping[str, Any] | None,
) -> dict[str, Any]:
    artifact = deepcopy(dict(existing or {}))
    if artifact.get('pipeline_schema_version') not in {
        None,
        PIPELINE_V2_RUN_SCHEMA_VERSION,
    }:
        raise ValueError('pipeline artifact schema version mismatch')
    input_refs = {
        'raw_snapshot_hash': raw_snapshot.snapshot_hash,
        'script_snapshot_hash': script_snapshot.snapshot_hash,
        'script_source_hash': script_snapshot.source_hash,
        'runtime_lexicon_hash': canonical_sha256(runtime_lexicon_bundle or {}),
    }
    existing_refs = artifact.get('input_refs')
    if existing_refs not in (None, input_refs):
        raise ValueError('pipeline artifact input references changed during recovery')
    artifact.update({
        'schema_version': 1,
        'pipeline_schema_version': PIPELINE_V2_RUN_SCHEMA_VERSION,
        'run_id': run_id,
        'raw_snapshot_hash': raw_snapshot.snapshot_hash,
        'input_refs': input_refs,
        'checkpoints': dict(artifact.get('checkpoints') or {}),
    })
    artifact.pop('content_hash', None)
    return artifact


def _final_cue_artifact(
    *,
    run_id: str,
    raw_snapshot: RawAsrSnapshot,
    cue_plan: SubtitleCuePlanArtifact,
    overlays: Sequence[AuthorizedTextOverlay],
    masks: Sequence[FillerTokenMask],
    rendered: Mapping[str, str],
    validation: CueValidationReport,
    metrics: Mapping[str, int],
) -> dict[str, Any]:
    content = {
        'schema_version': FINAL_CUE_ARTIFACT_SCHEMA_VERSION,
        'run_id': run_id,
        'raw_snapshot_hash': raw_snapshot.snapshot_hash,
        'cue_plan_hash': cue_plan.artifact_hash,
        'overlay_ids': [overlay.overlay_id for overlay in overlays],
        'mask_ids': [mask.mask_id for mask in masks],
        'cues': [
            {
                'cue_id': cue.cue_id,
                'token_ids': list(cue.token_ids),
                'start_ms': cue.start_ms,
                'end_ms': cue.end_ms,
                'text': rendered[cue.cue_id],
            }
            for cue in cue_plan.cues
        ],
        'validation': validation.to_dict(),
        'metrics': dict(metrics),
    }
    return {**content, 'artifact_hash': canonical_sha256(content)}


def _checkpoint(
    *,
    input_refs: Mapping[str, Any],
    output_refs: Mapping[str, Any],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    content = {
        'input_refs': dict(input_refs),
        'output_refs': dict(output_refs),
        **dict(values),
    }
    return {**content, 'checkpoint_hash': canonical_sha256(content)}


def _valid_checkpoint(value: Any) -> bool:
    if not isinstance(value, Mapping) or not isinstance(value.get('checkpoint_hash'), str):
        return False
    content = {key: item for key, item in value.items() if key != 'checkpoint_hash'}
    return canonical_sha256(content) == value['checkpoint_hash']


def _save_checkpoint(
    session: Session,
    lease: PipelineRunLease,
    artifact: dict[str, Any],
    *,
    metrics: Mapping[str, Any] | None = None,
) -> None:
    from app.services.subtitle_pipeline_runs import checkpoint_pipeline_run_with_lease

    artifact.pop('content_hash', None)
    content_hash = canonical_sha256(artifact)
    artifact['content_hash'] = content_hash
    checkpoint_pipeline_run_with_lease(
        session,
        lease,
        artifacts=deepcopy(artifact),
        artifact_hash=content_hash,
        artifact_refs={
            'raw_snapshot_hash': artifact['raw_snapshot_hash'],
            'script_snapshot_hash': artifact['input_refs']['script_snapshot_hash'],
            **(
                {
                    'final_cue_artifact_hash': artifact['final_cue_artifact'][
                        'artifact_hash'
                    ],
                }
                if 'final_cue_artifact' in artifact
                else {}
            ),
        },
        metrics=dict(metrics or {}),
    )


def _models(values: Sequence[Any]) -> list[dict[str, Any]]:
    return [value.model_dump(mode='json') for value in values]


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))
