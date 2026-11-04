import pytest

from app.services.pipeline_artifact_schema import (
    PIPELINE_ARTIFACT_SCHEMA_VERSION,
    PipelineArtifactSchemaError,
    summarize_pipeline_artifacts,
    validate_pipeline_artifacts,
)


def _valid_shadow_artifacts() -> dict:
    cue_hash = 'a' * 64
    return {
        'schema_version': PIPELINE_ARTIFACT_SCHEMA_VERSION,
        'shadow_mode': True,
        'timeline_revision': 'rev-001',
        'source_revision': {'job_id': 'job-1'},
        'artifact_hashes': {'subtitle_cues_hash': cue_hash},
        'asr_tokens_snapshot': {'count': 1, 'truncated': False, 'items': [{'text': 'hello'}]},
        'semantic_utterances_snapshot': {'count': 1, 'truncated': False, 'items': [{'text': 'hello'}]},
        'lexical_atoms_snapshot': {'count': 1, 'truncated': False, 'items': [{'text': 'hello'}]},
        'subtitle_cues_snapshot': {'count': 1, 'truncated': False, 'items': [{'clean_text': 'hello'}]},
        'user_edit_patches_snapshot': {'count': 0, 'truncated': False, 'items': []},
        'cue_validation_report': {
            'valid': True,
            'has_p0': False,
            'p0_count': 0,
            'issue_count': 0,
            'issues': [],
        },
        'export_manifest': {'cue_hash': cue_hash, 'cue_count': 1, 'frame_rate': '25'},
    }


def test_validate_pipeline_artifacts_accepts_stage_two_shadow_artifact() -> None:
    validate_pipeline_artifacts(_valid_shadow_artifacts())


def test_summarize_pipeline_artifacts_returns_stage_two_summary() -> None:
    summary = summarize_pipeline_artifacts(_valid_shadow_artifacts())

    assert summary['schema_version'] == PIPELINE_ARTIFACT_SCHEMA_VERSION
    assert summary['subtitle_cue_count'] == 1
    assert summary['p0_count'] == 0
    assert summary['issue_count'] == 0
    assert summary['cue_hash'] == 'a' * 64


def test_validate_pipeline_artifacts_rejects_missing_required_snapshot() -> None:
    artifacts = _valid_shadow_artifacts()
    del artifacts['asr_tokens_snapshot']

    with pytest.raises(PipelineArtifactSchemaError, match='asr_tokens_snapshot'):
        validate_pipeline_artifacts(artifacts)


def test_validate_pipeline_artifacts_rejects_mismatched_cue_hash() -> None:
    artifacts = _valid_shadow_artifacts()
    artifacts['export_manifest']['cue_hash'] = 'b' * 64

    with pytest.raises(PipelineArtifactSchemaError):
        validate_pipeline_artifacts(artifacts)
