from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.v1 import admin as admin_api
from app.db.session import Base
from app.models.job import Job
from app.models.job_event import JobEvent  # noqa: F401
from app.models.speaker_memory import SpeakerMemory  # noqa: F401
from app.services.job_events import append_job_event
from app.services.pipeline_artifact_schema import PIPELINE_ARTIFACT_SCHEMA_VERSION
from app.services.stage_two_smoke import build_stage_two_smoke_report


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _valid_shadow_artifacts(job_id: str, *, pipeline: str = 'transcription', p0_count: int = 0) -> dict:
    cue_hash = 'c' * 64 if pipeline == 'transcription' else '0' * 64
    cue_count = 2 if pipeline == 'transcription' else 0
    runtime_bundle = {
        'bundle_version': 1,
        'term_set_hash': 'runtime-lexicon-hash-001',
        'terms': [{'text': '人工智能', 'protection': 'hard', 'source': 'project'}] if pipeline == 'transcription' else [],
        'disabled_terms': [],
        'conflict_report': [],
    }
    return {
        'schema_version': PIPELINE_ARTIFACT_SCHEMA_VERSION,
        'shadow_mode': True,
        'pipeline': pipeline,
        'timeline_revision': 'timeline-smoke-001',
        'source_revision': {'job_id': job_id},
        'artifact_hashes': {
            'subtitle_cues_hash': cue_hash,
            'runtime_lexicon_bundle_hash': runtime_bundle['term_set_hash'],
        },
        'asr_tokens_snapshot': {'count': 2, 'truncated': False, 'items': []},
        'semantic_utterances_snapshot': {'count': 1, 'truncated': False, 'items': []},
        'lexical_atoms_snapshot': {'count': 2, 'truncated': False, 'items': []},
        'subtitle_cues_snapshot': {'count': cue_count, 'truncated': False, 'items': []},
        'user_edit_patches_snapshot': {'count': 0, 'truncated': False, 'items': []},
        'runtime_lexicon_bundle_snapshot': runtime_bundle,
        'cue_validation_report': {
            'valid': p0_count == 0,
            'has_p0': p0_count > 0,
            'p0_count': p0_count,
            'issue_count': p0_count,
            'issues': [],
        },
        'export_manifest': {
            'schema_version': 2,
            'frame_rate': '25',
            'cue_count': cue_count,
            'cue_hash': cue_hash,
            'preview_hash_matches_export': True,
        },
    }


def _check(report: dict, key: str) -> dict:
    return next(item for item in report['checks'] if item['key'] == key)


def test_stage_two_smoke_report_passes_completed_transcription_with_safe_artifacts() -> None:
    session = _build_session()
    job = Job(
        id='job-stage-two-smoke-pass',
        kind='transcription',
        status='completed',
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
        program_key='项目-A',
        program_name='项目-A',
        created_at=datetime(2026, 6, 22, 10, 0, 0),
        pipeline_artifacts=_valid_shadow_artifacts('job-stage-two-smoke-pass'),
    )
    session.add(job)
    session.commit()
    for event_type in (
        'job_created',
        'provider_task_submitted',
        'processing_update',
        'job_completed',
        'pipeline_artifact_checked',
        'pipeline_artifact_exported',
    ):
        append_job_event(session, job=job, event_type=event_type, stage='smoke', status='completed')

    events = session.query(JobEvent).filter(JobEvent.job_id == job.id).all()

    report = build_stage_two_smoke_report(job=job, events=events)

    assert report['overall_status'] == 'pass'
    assert _check(report, 'pipeline_artifacts_schema')['status'] == 'pass'
    assert _check(report, 'cue_hash_consistency')['status'] == 'pass'
    assert _check(report, 'runtime_lexicon_bundle')['status'] == 'pass'
    assert _check(report, 'payload_safety')['status'] == 'pass'


def test_stage_two_smoke_report_flags_p0_and_sensitive_event_payload() -> None:
    session = _build_session()
    job = Job(
        id='job-stage-two-smoke-fail',
        kind='transcription',
        status='completed',
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
        created_at=datetime(2026, 6, 22, 10, 0, 0),
        pipeline_artifacts=_valid_shadow_artifacts('job-stage-two-smoke-fail', p0_count=1),
    )
    session.add(job)
    session.commit()
    append_job_event(
        session,
        job=job,
        event_type='job_completed',
        stage='finalize',
        status='completed',
        details={'signed_url': 'https://oss.example.com/file.wav?X-OSS-Signature=secret'},
    )
    events = session.query(JobEvent).filter(JobEvent.job_id == job.id).all()

    report = build_stage_two_smoke_report(job=job, events=events)

    assert report['overall_status'] == 'fail'
    assert _check(report, 'cue_validator')['status'] == 'fail'
    assert _check(report, 'payload_safety')['status'] == 'fail'
    assert _check(report, 'payload_safety')['details']['hit_count'] == 1


def test_stage_two_smoke_report_confirms_transcript_isolation() -> None:
    session = _build_session()
    job = Job(
        id='job-stage-two-transcript',
        kind='transcript',
        status='completed',
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
        created_at=datetime(2026, 6, 22, 10, 0, 0),
        pipeline_artifacts=_valid_shadow_artifacts('job-stage-two-transcript', pipeline='transcript'),
    )
    session.add(job)
    session.commit()

    report = build_stage_two_smoke_report(job=job, events=[])

    assert _check(report, 'transcript_isolation')['status'] == 'pass'
    assert _check(report, 'runtime_lexicon_bundle')['status'] == 'skip'


def test_admin_stage_two_smoke_endpoint_returns_report(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        id='job-stage-two-admin',
        kind='transcription',
        status='completed',
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
        created_at=datetime(2026, 6, 22, 10, 0, 0),
        pipeline_artifacts=_valid_shadow_artifacts('job-stage-two-admin'),
    )
    session.add(job)
    session.commit()
    monkeypatch.setattr(admin_api, 'SessionLocal', lambda: session)

    response = admin_api.get_admin_job_stage_two_smoke(job.id)

    assert response.job_id == job.id
    assert response.overall_status in {'pass', 'warn'}
    assert any(check.key == 'pipeline_artifacts_schema' for check in response.checks)
