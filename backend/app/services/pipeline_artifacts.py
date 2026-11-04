from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import TYPE_CHECKING, Any, Iterable, Optional

if TYPE_CHECKING:
    from app.models.job import Job
from app.schemas.subtitle import SubtitleCue
from app.services.pipeline_artifact_schema import PIPELINE_ARTIFACT_SCHEMA_VERSION
from app.services.subtitle_atoms import build_lexical_atoms
from app.services.subtitle_cue_validator import CueValidationReport, validate_subtitle_cues
from app.services.subtitle_length_profiles import job_subtitle_length_profile
from app.services.subtitle_protected_terms import normalize_runtime_lexicon_bundle
from app.services.subtitles import build_subtitle_cues, collect_subtitle_protected_terms


MAX_SNAPSHOT_ITEMS = 500


def build_pipeline_artifacts(
    job: Job,
    *,
    raw_segments: list[dict[str, Any]],
    semantic_utterances: list[dict[str, Any]],
    normalized_segments: list[dict[str, Any]],
    refreshed_by: str = 'unknown',
    user_edit_patches: Optional[list[dict[str, Any]]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    pipeline = str(getattr(job, 'kind', None) or 'transcription')
    runtime_bundle = _resolve_runtime_lexicon_bundle(job, runtime_lexicon_bundle)
    subtitle_length_profile = job_subtitle_length_profile(job) if pipeline == 'transcription' else None
    protected_terms = collect_subtitle_protected_terms(
        getattr(job, 'script_context', None),
        runtime_lexicon_bundle=runtime_bundle,
    )
    cues = _build_cues(job, normalized_segments, runtime_bundle) if pipeline == 'transcription' else []
    report = (
        validate_subtitle_cues(
            cues,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_bundle,
            max_cjk_chars=int(subtitle_length_profile['max_chars']) if subtitle_length_profile else 14,
        )
        if cues else CueValidationReport()
    )
    cue_hash = build_cue_hash(cues)
    asr_tokens = _extract_asr_tokens(raw_segments)
    protected_term_items = [{'span_id': f'protected-term-{index + 1:06d}', 'text': term} for index, term in enumerate(protected_terms)]
    lexical_atoms = _extract_lexical_atoms(cues, protected_terms, runtime_bundle)
    semantic_segments = _extract_segments(semantic_utterances)
    serialized_cues = _serialize_cues(cues)
    validation_report = _serialize_validation_report(report)
    edit_patches = list(user_edit_patches or [])
    timeline_revision_source = {
        'job_id': str(getattr(job, 'id', '') or ''),
        'cue_hash': cue_hash,
        'user_edit_patches': edit_patches,
    }
    timeline_revision = f'timeline-{_hash_json(timeline_revision_source)[:16]}'
    validation_report['scope'] = 'current_timeline'
    validation_report['timeline_revision'] = timeline_revision

    artifacts = {
        'schema_version': PIPELINE_ARTIFACT_SCHEMA_VERSION,
        'shadow_mode': True,
        'pipeline': pipeline,
        'generated_at': _utc_now_iso(),
        'refreshed_by': str(refreshed_by or 'unknown'),
        'timeline_revision': timeline_revision,
        'pipeline_version': {
            'utterance_rebuilder': 'v1',
            'protected_term_resolver': 'v1',
            'lexical_atom_builder': 'v1',
            'cue_builder': 'v1',
            'cue_validator': 'v1',
            'runtime_lexicon_bundle': 'v1',
            'jieba_tokenizer': 'v1',
        },
        'source_revision': {
            'job_id': str(getattr(job, 'id', '') or ''),
            'raw_payload_hash': _hash_json(getattr(job, 'raw_payload', None) or raw_segments),
            'normalized_segments_hash': _hash_json(normalized_segments or []),
            'speaker_mappings_hash': _hash_json(getattr(job, 'speaker_mappings', None) or {}),
            'script_context_hash': _hash_json(getattr(job, 'script_context', None) or {}),
        },
        'artifact_hashes': {
            'asr_tokens_hash': _hash_json(asr_tokens),
            'protected_terms_hash': _hash_json(protected_term_items),
            'lexical_atoms_hash': _hash_json(lexical_atoms),
            'semantic_utterances_hash': _hash_json(semantic_segments),
            'subtitle_cues_hash': cue_hash,
            'cue_validation_hash': _hash_json(validation_report),
            'runtime_lexicon_bundle_hash': _runtime_lexicon_bundle_hash(runtime_bundle),
        },
        'asr_tokens_snapshot': _snapshot(asr_tokens),
        'protected_terms_snapshot': _snapshot(protected_term_items, total_count=len(protected_terms)),
        'lexical_atoms_snapshot': _snapshot(lexical_atoms),
        'semantic_utterances_snapshot': _snapshot(semantic_segments),
        'subtitle_cues_snapshot': _snapshot(serialized_cues),
        'runtime_lexicon_bundle_snapshot': runtime_bundle,
        'user_edit_patches_snapshot': _snapshot(edit_patches),
        'cue_validation_report': validation_report,
        'export_manifest': {
            'schema_version': 2,
            'frame_rate': str(getattr(job, 'export_frame_rate', None) or '25'),
            'cue_count': len(cues),
            'cue_hash': cue_hash,
            'source_job_id': str(getattr(job, 'id', '') or ''),
            'preview_hash_matches_export': True,
        },
    }
    if subtitle_length_profile:
        artifacts['subtitle_length_profile'] = {
            'mode': subtitle_length_profile['mode'],
            'max_chars': subtitle_length_profile['max_chars'],
        }
        artifacts['subtitle_length_summary'] = {
            'overlong_cjk_cue_count': _count_validation_issues(report, 'overlong_cjk_cue'),
        }
    return artifacts


def build_cue_hash(cues: Iterable[SubtitleCue]) -> str:
    payload = [
        {
            'index': int(cue.index),
            'start_ms': int(cue.start_ms),
            'end_ms': int(cue.end_ms),
            'speaker_key': str(cue.speaker_key or ''),
            'speaker_name': str(cue.speaker_name or ''),
            'clean_text': str(cue.clean_text or ''),
        }
        for cue in cues or []
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _build_cues(
    job: Job,
    normalized_segments: list[dict[str, Any]],
    runtime_lexicon_bundle: dict[str, Any],
) -> list[SubtitleCue]:
    profile = job_subtitle_length_profile(job)
    return build_subtitle_cues(
        normalized_segments or [],
        getattr(job, 'speaker_mappings', None) or {},
        export_frame_rate=getattr(job, 'export_frame_rate', None),
        remove_standalone_fillers=bool(getattr(job, 'remove_standalone_fillers', False)),
        script_context=getattr(job, 'script_context', None),
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        subtitle_length_mode=profile['mode'],
        subtitle_max_chars=profile['max_chars'],
    )


def _count_validation_issues(report: CueValidationReport, code: str) -> int:
    return sum(1 for issue in report.issues if issue.code == code)


def _snapshot(items: list[dict[str, Any]], *, total_count: Optional[int] = None) -> dict[str, Any]:
    count = len(items) if total_count is None else int(total_count)
    return {
        'count': count,
        'truncated': count > MAX_SNAPSHOT_ITEMS,
        'items': items[:MAX_SNAPSHOT_ITEMS],
    }


def _extract_asr_tokens(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for segment in segments or []:
        segment_id = str(segment.get('segmentId') or '')
        speaker_key = str(segment.get('speakerKey') or '')
        for word_index, word in enumerate(segment.get('words') or []):
            text = str(word.get('text') or '').strip()
            if not text:
                continue
            tokens.append({
                'asr_token_id': f'token-{len(tokens) + 1:06d}',
                'segment_id': segment_id,
                'word_index': word_index,
                'speaker_key': speaker_key,
                'start_ms': _safe_int(word.get('startMs')),
                'end_ms': _safe_int(word.get('endMs')),
                'text': text,
            })
    return tokens


def _extract_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for segment in segments or []:
        segment_id = str(segment.get('segmentId') or '')
        result.append({
            'utterance_id': f'utterance-{len(result) + 1:06d}',
            'segment_id': segment_id,
            'speaker_key': str(segment.get('speakerKey') or ''),
            'start_ms': _safe_int(segment.get('startMs')),
            'end_ms': _safe_int(segment.get('endMs')),
            'text': str(segment.get('text') or ''),
            'source_segment_ids': list(segment.get('sourceSegmentIds') or ([segment_id] if segment_id else [])),
            'repair_tags': list(segment.get('repairTags') or []),
        })
    return result


def _serialize_cues(cues: list[SubtitleCue]) -> list[dict[str, Any]]:
    return [
        {
            'cue_id': f'cue-{int(cue.index):06d}',
            'index': int(cue.index),
            'start_ms': int(cue.start_ms),
            'end_ms': int(cue.end_ms),
            'speaker_key': str(cue.speaker_key or ''),
            'speaker_name': str(cue.speaker_name or ''),
            'clean_text': str(cue.clean_text or ''),
        }
        for cue in cues or []
    ]


def _extract_lexical_atoms(
    cues: list[SubtitleCue],
    protected_terms: tuple[str, ...],
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    atoms: list[dict[str, Any]] = []
    for cue in cues or []:
        for atom in build_lexical_atoms(
            str(cue.clean_text or ''),
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        ):
            atoms.append({
                'atom_id': f'atom-{len(atoms) + 1:06d}',
                'cue_index': int(cue.index),
                'start': int(atom.start),
                'end': int(atom.end),
                'text': atom.text,
                'kind': atom.kind,
            })
    return atoms


def _serialize_validation_report(report: CueValidationReport) -> dict[str, Any]:
    payload = report.to_dict()
    issues = list(payload.get('issues') or [])
    return {
        'schema_version': 1,
        'valid': bool(payload.get('valid')),
        'has_p0': bool(payload.get('has_p0')),
        'p0_count': sum(1 for issue in issues if issue.get('severity') == 'P0'),
        'issue_count': len(issues),
        'issues': issues[:MAX_SNAPSHOT_ITEMS],
        'truncated': len(issues) > MAX_SNAPSHOT_ITEMS,
    }


def _resolve_runtime_lexicon_bundle(
    job: Job,
    runtime_lexicon_bundle: Optional[dict[str, Any]],
) -> dict[str, Any]:
    if runtime_lexicon_bundle is not None:
        return normalize_runtime_lexicon_bundle(runtime_lexicon_bundle)
    artifacts = getattr(job, 'pipeline_artifacts', None)
    if isinstance(artifacts, dict):
        return normalize_runtime_lexicon_bundle(artifacts.get('runtime_lexicon_bundle_snapshot'))
    return normalize_runtime_lexicon_bundle(None)


def _runtime_lexicon_bundle_hash(runtime_lexicon_bundle: dict[str, Any]) -> str:
    term_set_hash = str(runtime_lexicon_bundle.get('term_set_hash') or '').strip()
    if term_set_hash:
        return term_set_hash
    return _hash_json({
        'bundle_version': runtime_lexicon_bundle.get('bundle_version'),
        'terms': runtime_lexicon_bundle.get('terms') or [],
        'disabled_terms': runtime_lexicon_bundle.get('disabled_terms') or [],
        'conflict_report': runtime_lexicon_bundle.get('conflict_report') or [],
    })


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
