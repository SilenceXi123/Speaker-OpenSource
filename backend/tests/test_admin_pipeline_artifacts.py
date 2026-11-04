from datetime import datetime
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.v1 import admin as admin_api
from app.db.session import Base
from app.models.job import Job
from app.models.speaker_memory import SpeakerMemory  # noqa: F401
from app.services.pipeline_artifact_schema import PIPELINE_ARTIFACT_SCHEMA_VERSION


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _valid_shadow_artifacts(job_id: str) -> dict:
    cue_hash = 'b' * 64
    return {
        'schema_version': PIPELINE_ARTIFACT_SCHEMA_VERSION,
        'shadow_mode': True,
        'timeline_revision': 'timeline-admin-001',
        'source_revision': {'job_id': job_id},
        'artifact_hashes': {'subtitle_cues_hash': cue_hash},
        'asr_tokens_snapshot': {'count': 2, 'truncated': False, 'items': []},
        'semantic_utterances_snapshot': {'count': 1, 'truncated': False, 'items': []},
        'lexical_atoms_snapshot': {'count': 2, 'truncated': False, 'items': []},
        'subtitle_cues_snapshot': {'count': 2, 'truncated': False, 'items': []},
        'user_edit_patches_snapshot': {'count': 1, 'truncated': False, 'items': []},
        'cue_validation_report': {
            'valid': True,
            'has_p0': False,
            'p0_count': 0,
            'issue_count': 1,
            'issues': [],
        },
        'export_manifest': {
            'schema_version': 2,
            'frame_rate': '25',
            'cue_count': 2,
            'cue_hash': cue_hash,
        },
    }


def test_admin_job_pipeline_artifacts_returns_summary(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        id='job-admin-artifact',
        kind='media',
        status='completed',
        source_url='https://example.com/admin.wav',
        source_media_kind='audio',
        program_key='项目-A',
        program_name='项目-A',
        created_at=datetime(2026, 6, 20, 9, 0, 0),
        pipeline_artifacts=_valid_shadow_artifacts('job-admin-artifact'),
    )
    job_id = job.id
    session.add(job)
    session.commit()
    job_id = job.id
    monkeypatch.setattr(admin_api, 'SessionLocal', lambda: session)

    response = admin_api.get_admin_job_pipeline_artifacts(job.id)

    assert response.summary.schema_version == PIPELINE_ARTIFACT_SCHEMA_VERSION
    assert response.summary.subtitle_cue_count == 2
    assert response.summary.issue_count == 1
    assert response.artifacts['user_edit_patches_snapshot']['count'] == 1


def test_admin_job_pipeline_artifacts_rejects_missing_artifacts(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        id='job-admin-missing-artifact',
        kind='media',
        status='completed',
        source_url='https://example.com/missing.wav',
        source_media_kind='audio',
        program_key='项目-A',
        program_name='项目-A',
        created_at=datetime(2026, 6, 20, 9, 0, 0),
    )
    session.add(job)
    session.commit()
    monkeypatch.setattr(admin_api, 'SessionLocal', lambda: session)

    with pytest.raises(HTTPException) as exc_info:
        admin_api.get_admin_job_pipeline_artifacts(job.id)

    assert exc_info.value.status_code == 409
    assert 'pipeline artifacts' in exc_info.value.detail


def test_admin_job_pipeline_artifacts_export_downloads_json(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        id='job-admin-export-artifact',
        kind='media',
        status='completed',
        source_url='https://example.com/export.wav',
        source_media_kind='audio',
        program_key='项目-A',
        program_name='项目-A',
        created_at=datetime(2026, 6, 20, 9, 0, 0),
        pipeline_artifacts=_valid_shadow_artifacts('job-admin-export-artifact'),
    )
    session.add(job)
    session.commit()
    monkeypatch.setattr(admin_api, 'SessionLocal', lambda: session)

    response = admin_api.get_admin_job_pipeline_artifacts_export(job.id)
    payload = json.loads(response.body.decode('utf-8'))

    assert response.media_type == 'application/json; charset=utf-8'
    assert 'speaker-pipeline-artifacts-job-admin-export-artifact.json' in response.headers['Content-Disposition']
    assert payload['summary']['schema_version'] == PIPELINE_ARTIFACT_SCHEMA_VERSION
    assert payload['artifacts']['source_revision']['job_id'] == job.id


def test_admin_job_pipeline_artifacts_reports_missing_job(monkeypatch) -> None:
    session = _build_session()
    missing_job_id = 'missing-artifact-job'
    monkeypatch.setattr(admin_api, 'SessionLocal', lambda: session)

    with pytest.raises(HTTPException) as exc_info:
        admin_api.get_admin_job_pipeline_artifacts(missing_job_id)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == '任务不存在'


def test_admin_storage_cleanup_preview_returns_plan(monkeypatch, tmp_path) -> None:
    session = _build_session()
    media_dir = tmp_path / 'media'
    upload_dir = tmp_path / 'uploads'
    audio_dir = tmp_path / 'audio'
    render_dir = tmp_path / 'render'
    for directory in [media_dir, upload_dir, audio_dir, render_dir]:
        directory.mkdir(parents=True)
    old_source = media_dir / 'old.wav'
    old_source.write_bytes(b'audio')
    session.add(
        Job(
            id='cleanup-preview-job',
            kind='media',
            status='completed',
            source_url='https://example.com/old.wav',
            source_local_path=str(old_source),
            created_at=datetime(2026, 6, 1, 12, 0, 0),
        )
    )
    session.commit()
    monkeypatch.setattr(admin_api, 'SessionLocal', lambda: session)
    monkeypatch.setattr(admin_api.settings, 'local_media_library_dir', str(media_dir))
    monkeypatch.setattr(admin_api.settings, 'local_upload_dir', str(upload_dir))
    monkeypatch.setattr(admin_api.settings, 'local_audio_dir', str(audio_dir))
    monkeypatch.setattr(admin_api.settings, 'local_render_dir', str(render_dir))
    monkeypatch.setattr(admin_api.settings, 'storage_cleanup_completed_source_days', 1)

    response = admin_api.get_admin_storage_cleanup_preview(_username='admin')

    assert response.dry_run is True
    assert response.total_bytes == len(b'audio')
    assert old_source.exists()


def test_admin_storage_cleanup_run_deletes_expired_media(monkeypatch, tmp_path) -> None:
    session = _build_session()
    media_dir = tmp_path / 'media'
    upload_dir = tmp_path / 'uploads'
    audio_dir = tmp_path / 'audio'
    render_dir = tmp_path / 'render'
    for directory in [media_dir, upload_dir, audio_dir, render_dir]:
        directory.mkdir(parents=True)
    old_source = media_dir / 'old.wav'
    old_source.write_bytes(b'audio')
    job = Job(
        id='cleanup-run-job',
        kind='media',
        status='completed',
        source_url='https://example.com/old.wav',
        source_local_path=str(old_source),
        created_at=datetime(2026, 6, 1, 12, 0, 0),
    )
    job_id = job.id
    session.add(job)
    session.commit()
    monkeypatch.setattr(admin_api, 'SessionLocal', lambda: session)
    monkeypatch.setattr(admin_api.settings, 'local_media_library_dir', str(media_dir))
    monkeypatch.setattr(admin_api.settings, 'local_upload_dir', str(upload_dir))
    monkeypatch.setattr(admin_api.settings, 'local_audio_dir', str(audio_dir))
    monkeypatch.setattr(admin_api.settings, 'local_render_dir', str(render_dir))
    monkeypatch.setattr(admin_api.settings, 'storage_cleanup_completed_source_days', 1)

    response = admin_api.post_admin_storage_cleanup_run(_username='admin')
    saved_job = session.get(Job, job_id)

    assert response.dry_run is False
    assert response.deleted_bytes == len(b'audio')
    assert not old_source.exists()
    assert saved_job.source_local_path is None
