from __future__ import annotations

from typing import Any


PIPELINE_ARTIFACT_SCHEMA_VERSION = 'pipeline_artifacts.v2-shadow'

REQUIRED_TOP_LEVEL_KEYS = (
    'schema_version',
    'shadow_mode',
    'timeline_revision',
    'source_revision',
    'artifact_hashes',
    'cue_validation_report',
    'export_manifest',
)

REQUIRED_SNAPSHOTS = (
    'asr_tokens_snapshot',
    'semantic_utterances_snapshot',
    'lexical_atoms_snapshot',
    'subtitle_cues_snapshot',
    'user_edit_patches_snapshot',
)


class PipelineArtifactSchemaError(ValueError):
    pass


def validate_pipeline_artifacts(artifacts: dict[str, Any]) -> None:
    if not isinstance(artifacts, dict):
        raise PipelineArtifactSchemaError('pipeline artifacts must be a dict')

    if artifacts.get('schema_version') != PIPELINE_ARTIFACT_SCHEMA_VERSION:
        raise PipelineArtifactSchemaError(f"unsupported schema_version: {artifacts.get('schema_version')}")

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in artifacts:
            raise PipelineArtifactSchemaError(f'missing required key: {key}')

    for key in REQUIRED_SNAPSHOTS:
        _validate_snapshot(key, artifacts.get(key))

    artifact_hashes = artifacts.get('artifact_hashes')
    if not isinstance(artifact_hashes, dict):
        raise PipelineArtifactSchemaError('artifact_hashes must be a dict')

    export_manifest = artifacts.get('export_manifest')
    if not isinstance(export_manifest, dict):
        raise PipelineArtifactSchemaError('export_manifest must be a dict')

    if export_manifest.get('cue_hash') != artifact_hashes.get('subtitle_cues_hash'):
        raise PipelineArtifactSchemaError('export_manifest.cue_hash must match artifact_hashes.subtitle_cues_hash')


def summarize_pipeline_artifacts(artifacts: dict[str, Any]) -> dict[str, Any]:
    validate_pipeline_artifacts(artifacts)

    cue_validation_report = artifacts.get('cue_validation_report') or {}
    export_manifest = artifacts.get('export_manifest') or {}
    subtitle_cues_snapshot = artifacts.get('subtitle_cues_snapshot') or {}

    return {
        'schema_version': artifacts.get('schema_version'),
        'shadow_mode': bool(artifacts.get('shadow_mode')),
        'timeline_revision': artifacts.get('timeline_revision'),
        'subtitle_cue_count': _int_like(subtitle_cues_snapshot.get('count')),
        'p0_count': _int_like(cue_validation_report.get('p0_count')),
        'issue_count': _int_like(cue_validation_report.get('issue_count')),
        'cue_hash': export_manifest.get('cue_hash'),
    }


def _validate_snapshot(key: str, snapshot: Any) -> None:
    if not isinstance(snapshot, dict):
        raise PipelineArtifactSchemaError(f'missing required snapshot: {key}')
    if 'count' not in snapshot:
        raise PipelineArtifactSchemaError(f'invalid snapshot shape for {key}: missing count')
    if 'items' not in snapshot:
        raise PipelineArtifactSchemaError(f'invalid snapshot shape for {key}: missing items')
    _int_like(snapshot.get('count'), key=key)


def _int_like(value: Any, *, key: str = 'value') -> int:
    if isinstance(value, bool):
        raise PipelineArtifactSchemaError(f'{key} count must be int-like')
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PipelineArtifactSchemaError(f'{key} count must be int-like') from exc
