from datetime import datetime, timedelta, timezone
import importlib
import os

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import Base, configure_sqlite_engine
from app.models.job import Job
from app.models.person import Person
from app.models.project import Project
from app.models.voice_clone_deletion_task import VoiceCloneDeletionTask
from app.models.voice_clone_identity import VoiceCloneIdentity
from app.models.voice_clone_identity_review import VoiceCloneIdentityReview
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_sample import VoiceCloneSample
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
from app.models.voiceprint_match import VoiceprintMatch
from app.models.voiceprint_sample import VoiceprintSample


NOW = datetime(2026, 9, 11, 12)


@pytest.fixture()
def session(tmp_path, monkeypatch):
    for setting in ('local_audio_dir', 'local_upload_dir', 'local_render_dir',
                    'local_voice_clone_library_dir', 'local_voiceprint_library_dir'):
        monkeypatch.setattr(settings, setting, str(tmp_path / setting))
    engine = create_engine(f'sqlite:///{tmp_path / "retention.db"}')
    configure_sqlite_engine(engine)
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        yield session
    engine.dispose()


def purge(session, now=NOW, **kwargs):
    cleanup = importlib.import_module('app.services.voice_retention_cleanup')
    return cleanup.expire_voice_data(session, now=now, **kwargs)


def profile(**kwargs):
    return VoiceCloneProfile(voice_name='声音', provider='cosyvoice',
                             target_model='cosyvoice-v3.5-plus', status='ready', **kwargs)


def record(**kwargs):
    return VoiceCloneSynthesisRecord(target_model='cosyvoice-v3.5-plus', input_text='文案', **kwargs)


def test_expiry_uses_exact_created_boundary_not_recent_use(session):
    expired = VoiceCloneIdentity(display_name='expired', created_at=NOW - timedelta(hours=72), updated_at=NOW)
    fresh = VoiceCloneIdentity(display_name='fresh', created_at=NOW - timedelta(hours=72) + timedelta(microseconds=1))
    session.add_all([expired, fresh])
    session.commit()
    expired_id, fresh_id = expired.id, fresh.id

    result = purge(session, now=NOW.replace(tzinfo=timezone.utc))

    assert result.deleted_count == 1
    assert session.get(VoiceCloneIdentity, expired_id) is None
    assert session.get(VoiceCloneIdentity, fresh_id) is not None
    assert purge(session).deleted_count == 0


def test_expired_identity_cascades_but_fresh_generation_keeps_own_lifetime(session, tmp_path):
    identity = VoiceCloneIdentity(display_name='old', aliases=['old'], reference_embedding=[1.0], created_at=NOW - timedelta(hours=72))
    session.add(identity)
    session.flush()
    sample = VoiceCloneSample(identity_id=identity.id, display_name='new recording', original_filename='source.wav', audio_local_path=str(tmp_path / 'source.wav'), created_at=NOW - timedelta(hours=1), last_used_at=NOW)
    session.add(sample)
    session.flush()
    voice = profile(identity_id=identity.id, source_clone_sample_id=sample.id, voice_id='old-voice', created_at=NOW - timedelta(hours=1))
    session.add(voice)
    session.flush()
    output = record(voice_clone_profile_id=voice.id, status='ready', created_at=NOW - timedelta(hours=1), output_audio_local_path=str(tmp_path / 'fresh.wav'))
    session.add(output)
    session.commit()
    ids = identity.id, sample.id, voice.id, output.id

    purge(session)

    assert session.get(VoiceCloneIdentity, ids[0]) is None
    assert session.get(VoiceCloneSample, ids[1]) is None
    assert session.get(VoiceCloneProfile, ids[2]) is None
    kept = session.get(VoiceCloneSynthesisRecord, ids[3])
    assert kept.voice_clone_profile_id is None
    assert kept.voice_name_snapshot == '声音'
    assert kept.output_audio_local_path.endswith('fresh.wav')
    assert session.scalar(select(VoiceCloneDeletionTask).where(VoiceCloneDeletionTask.kind == 'provider')).voice_id == 'old-voice'


def test_processing_dependency_grace_is_fixed_fifteen_minutes(session):
    identity = VoiceCloneIdentity(display_name='old', created_at=NOW - timedelta(hours=72))
    session.add(identity)
    session.flush()
    voice = profile(identity_id=identity.id, created_at=NOW - timedelta(hours=1), updated_at=NOW)
    session.add(voice)
    session.flush()
    processing = record(voice_clone_profile_id=voice.id, status='processing', created_at=NOW - timedelta(minutes=14))
    session.add(processing)
    session.commit()
    identity_id, voice_id, record_id = identity.id, voice.id, processing.id

    assert purge(session).deleted_count == 0
    assert session.get(VoiceCloneIdentity, identity_id) is not None
    purge(session, now=NOW + timedelta(minutes=1))

    assert session.get(VoiceCloneIdentity, identity_id) is None
    assert session.get(VoiceCloneProfile, voice_id) is None
    detached = session.get(VoiceCloneSynthesisRecord, record_id)
    assert detached.voice_clone_profile_id is None
    assert detached.status == 'failed'


def test_finished_inflight_output_survives_parent_cleanup(session):
    voice = profile(created_at=NOW - timedelta(hours=72), voice_id='expired-voice')
    session.add(voice)
    session.flush()
    output = record(voice_clone_profile_id=voice.id, created_at=NOW - timedelta(minutes=1), status='processing')
    session.add(output)
    session.commit()
    voice_id, record_id = voice.id, output.id
    purge(session)
    output.status = 'ready'
    output.output_audio_local_path = '/late-output.wav'
    session.commit()

    purge(session)

    assert session.get(VoiceCloneProfile, voice_id) is None
    kept = session.get(VoiceCloneSynthesisRecord, record_id)
    assert kept.status == 'ready'
    assert kept.created_at == NOW - timedelta(minutes=1)
    assert kept.voice_clone_profile_id is None


def test_expiry_cleans_voiceprints_reviews_matches_and_protects_project_assets(session, monkeypatch):
    from app.services.voice_clone_cleanup import drain_voice_clone_deletion_tasks
    monkeypatch.setattr(settings, 'oss_public_base_url', 'https://owned.example')
    root = settings.voiceprint_library_dir_path
    root.mkdir(exist_ok=True)
    source, clone = root / 'project-source.wav', root / 'old-clone.wav'
    source.write_bytes(b'project source')
    clone.write_bytes(b'old clone')
    person = Person(name='preserved person', created_at=NOW - timedelta(days=100))
    job = Job(source_url='https://owned.example/project-source.wav', source_local_path=str(source), normalized_segments=[{'text': 'keep'}])
    identity = VoiceCloneIdentity(display_name='fresh', created_at=NOW)
    session.add_all([person, job, identity])
    session.flush()
    voiceprint = VoiceprintSample(person_id=person.id, original_filename=source.name, audio_local_path=str(source), audio_public_url=job.source_url, clone_audio_local_path=str(clone), embedding=[0.5] * 100, created_at=NOW - timedelta(hours=72))
    fresh = VoiceprintSample(person_id=person.id, original_filename='fresh.wav', audio_local_path='/fresh.wav', embedding=[0.9], created_at=NOW)
    session.add_all([voiceprint, fresh])
    session.flush()
    voice = profile(source_sample_id=voiceprint.id, created_at=NOW - timedelta(hours=1))
    review = VoiceCloneIdentityReview(candidate_identity_id=identity.id, submitted_name='old review', match_score=0.9, status='resolved', created_at=NOW - timedelta(hours=72))
    match = VoiceprintMatch(person_id=person.id, job_id=job.id, speaker_key='speaker_0', top_candidates=[{'score': 0.9}], created_at=NOW - timedelta(hours=72), updated_at=NOW)
    session.add_all([voice, review, match])
    session.commit()
    ids = voiceprint.id, fresh.id, voice.id, review.id, match.id, person.id, job.id

    purge(session)
    drained = drain_voice_clone_deletion_tasks(session, now=NOW)

    assert drained.failed_count == 0
    for model, row_id in ((VoiceprintSample, ids[0]), (VoiceCloneProfile, ids[2]),
                          (VoiceCloneIdentityReview, ids[3]), (VoiceprintMatch, ids[4])):
        assert session.get(model, row_id) is None
    assert session.get(VoiceprintSample, ids[1]).embedding == [0.9]
    assert session.get(Person, ids[5]).name == 'preserved person'
    assert session.get(Job, ids[6]).normalized_segments == [{'text': 'keep'}]
    assert source.read_bytes() == b'project source'
    assert not clone.exists()


def test_expired_record_queues_both_assets_and_clears_preview_before_drain(session, monkeypatch):
    from app.services import voice_clone_cleanup as cleanup
    monkeypatch.setattr(settings, 'oss_public_base_url', 'https://owned.example')
    deleted_keys = []
    monkeypatch.setattr(settings, 'oss_access_key_id', 'test')
    monkeypatch.setattr(settings, 'oss_access_key_secret', 'test')
    monkeypatch.setattr(settings, 'oss_bucket', 'test')
    monkeypatch.setattr(settings, 'oss_region', 'oss-cn-beijing')
    monkeypatch.setattr(cleanup, 'delete_oss_object', lambda key: deleted_keys.append(key))
    root = settings.audio_dir_path
    root.mkdir(exist_ok=True)
    path = root / 'voice-clone-preview-old.wav'
    path.write_bytes(b'old output')
    voice = profile(created_at=NOW, preview_audio_local_path=str(path))
    session.add(voice)
    session.flush()
    output = record(voice_clone_profile_id=voice.id, status='ready', created_at=NOW - timedelta(hours=72), output_audio_local_path=str(path), output_audio_public_url='https://owned.example/old.wav')
    session.add(output)
    session.commit()
    voice_id, record_id = voice.id, output.id
    delete_checks = []
    def queue_before_delete(conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith('DELETE FROM VOICE_CLONE_SYNTHESIS_RECORDS'):
            delete_checks.append(conn.exec_driver_sql('SELECT count(*) FROM voice_clone_deletion_tasks').scalar())
    event.listen(session.bind, 'before_cursor_execute', queue_before_delete)
    try:
        purge(session)
    finally:
        event.remove(session.bind, 'before_cursor_execute', queue_before_delete)

    assert delete_checks == [2]
    assert session.get(VoiceCloneSynthesisRecord, record_id) is None
    assert session.get(VoiceCloneProfile, voice_id).preview_audio_local_path is None
    assert path.exists()
    assert cleanup.drain_voice_clone_deletion_tasks(session, now=NOW).completed_count == 2
    assert not path.exists()
    assert deleted_keys == ['old.wav']


def test_retention_batches_rows_without_loading_vectors_or_all_profile_records(session):
    voice = profile(created_at=NOW - timedelta(hours=72))
    session.add(voice)
    session.flush()
    session.add_all(record(voice_clone_profile_id=voice.id, status='ready', created_at=NOW) for _ in range(5))
    session.add_all(VoiceCloneIdentity(display_name=f'old {i}', reference_embedding=[0.1] * 256, created_at=NOW - timedelta(hours=72)) for i in range(5))
    session.commit()
    voice_id = voice.id
    session.expunge_all()
    loaded = []
    def loaded_model(_session, instance):
        loaded.append(type(instance).__name__)
    event.listen(session, 'loaded_as_persistent', loaded_model)
    try:
        first = purge(session, batch_size=2)
    finally:
        event.remove(session, 'loaded_as_persistent', loaded_model)

    assert first.detached_record_count == 2
    assert first.deleted_by_table['voice_clone_identities'] == 2
    assert 'VoiceCloneIdentity' not in loaded
    assert 'VoiceCloneSynthesisRecord' not in loaded
    assert session.get(VoiceCloneProfile, voice_id) is not None
    purge(session, batch_size=2)
    purge(session, batch_size=2)
    assert session.get(VoiceCloneProfile, voice_id) is None
    assert session.scalar(select(VoiceCloneSynthesisRecord.id).where(VoiceCloneSynthesisRecord.voice_clone_profile_id.is_not(None))) is None
    assert session.query(VoiceCloneSynthesisRecord).count() == 5


def test_generic_tmp_cleanup_preserves_live_voice_outputs_and_pending_queue(session):
    from app.services.storage_cleanup import run_storage_cleanup
    root = settings.audio_dir_path
    root.mkdir(exist_ok=True)
    paths = [root / name for name in ('voice-clone-preview-live.wav', 'pending.wav', 'project-source.wav', 'unreferenced.tmp')]
    for path in paths:
        path.write_bytes(b'content')
        old = (NOW - timedelta(hours=48)).timestamp()
        os.utime(path, (old, old))
    session.add_all([
        record(status='ready', created_at=NOW - timedelta(hours=48), output_audio_local_path=str(paths[0])),
        VoiceCloneDeletionTask(kind='local', resource_key=f'local:{paths[1]}', local_path=str(paths[1])),
        Job(source_url='https://example.test/source', source_local_path=str(paths[2])),
    ])
    session.commit()

    run_storage_cleanup(session, now=NOW)

    assert all(path.exists() for path in paths[:3])
    assert not paths[3].exists()


def test_independent_sample_expiry_respects_creation_grace_without_poll_renewal(session):
    identity = VoiceCloneIdentity(display_name='fresh identity', created_at=NOW)
    session.add(identity)
    session.flush()
    sample = VoiceCloneSample(identity_id=identity.id, display_name='old recording', original_filename='old.wav', audio_local_path='/old.wav', created_at=NOW - timedelta(hours=72), last_used_at=NOW)
    session.add(sample)
    session.flush()
    voice = profile(identity_id=identity.id, source_clone_sample_id=sample.id, created_at=NOW - timedelta(minutes=14), updated_at=NOW)
    voice.status = 'creating'
    session.add(voice)
    session.commit()
    identity_id, sample_id, voice_id = identity.id, sample.id, voice.id
    assert purge(session).deleted_count == 0
    voice.updated_at = NOW + timedelta(minutes=1)
    session.commit()

    purge(session, now=NOW + timedelta(minutes=1))

    assert session.get(VoiceCloneIdentity, identity_id) is not None
    assert session.get(VoiceCloneSample, sample_id) is None
    assert session.get(VoiceCloneProfile, voice_id) is None


def test_expired_provider_failure_remains_queued_for_retry(session, monkeypatch):
    from app.services import voice_clone_cleanup as cleanup
    session.add(profile(voice_id='old-voice', created_at=NOW - timedelta(hours=72)))
    session.commit()
    purge(session)
    def fail(*_args, **_kwargs):
        raise RuntimeError('temporary provider outage')
    monkeypatch.setattr(cleanup, '_delete_provider_voice', fail)

    assert cleanup.drain_voice_clone_deletion_tasks(session, now=NOW).failed_count == 1
    task = session.scalar(select(VoiceCloneDeletionTask))
    assert task.attempt_count == 1
    assert task.next_attempt_at == NOW + timedelta(minutes=2)
    assert purge(session).deleted_count == 0
    monkeypatch.setattr(cleanup, '_delete_provider_voice', lambda *_args, **_kwargs: None)
    assert cleanup.drain_voice_clone_deletion_tasks(session, now=NOW + timedelta(minutes=2)).completed_count == 1
    assert session.scalar(select(VoiceCloneDeletionTask.id)) is None


def test_expiry_queue_and_database_deletion_roll_back_together(session):
    voice = profile(voice_id='old-voice', created_at=NOW - timedelta(hours=72))
    session.add(voice)
    session.commit()
    voice_id = voice.id
    def fail_delete(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith('DELETE FROM VOICE_CLONE_PROFILES'):
            raise RuntimeError('interrupted transaction')
    event.listen(session.bind, 'before_cursor_execute', fail_delete)
    try:
        with pytest.raises(RuntimeError, match='interrupted transaction'):
            purge(session)
    finally:
        event.remove(session.bind, 'before_cursor_execute', fail_delete)

    assert session.get(VoiceCloneProfile, voice_id) is not None
    assert session.scalar(select(VoiceCloneDeletionTask.id)) is None
