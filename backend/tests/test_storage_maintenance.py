from datetime import datetime, timedelta
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import Base
from app.models.job import Job
from app.models.voice_clone_deletion_task import VoiceCloneDeletionTask
from app.services.storage_maintenance import run_storage_maintenance_once


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_maintenance_cleans_only_managed_stale_jobs_and_expired_tmp(monkeypatch, tmp_path) -> None:
    session = _build_session()
    media_dir = tmp_path / 'library' / 'media'
    upload_dir = tmp_path / 'tmp' / 'uploads'
    audio_dir = tmp_path / 'tmp' / 'audio'
    render_dir = tmp_path / 'tmp' / 'render'
    for directory in (media_dir, upload_dir, audio_dir, render_dir):
        directory.mkdir(parents=True)

    now = datetime(2026, 7, 21, 12, 0, 0)
    managed_source = media_dir / 'managed.wav'
    legacy_source = media_dir / 'legacy.wav'
    fresh_source = media_dir / 'fresh.wav'
    old_tmp = upload_dir / 'old.tmp'
    for path in (managed_source, legacy_source, fresh_source, old_tmp):
        path.write_bytes(b'data')
    old_timestamp = (now - timedelta(days=3)).timestamp()
    os.utime(old_tmp, (old_timestamp, old_timestamp))

    monkeypatch.setattr(settings, 'local_media_library_dir', str(media_dir))
    monkeypatch.setattr(settings, 'local_upload_dir', str(upload_dir))
    monkeypatch.setattr(settings, 'local_audio_dir', str(audio_dir))
    monkeypatch.setattr(settings, 'local_render_dir', str(render_dir))
    monkeypatch.setattr(settings, 'storage_cleanup_tmp_days', 1)
    monkeypatch.setattr(settings, 'storage_cleanup_failed_source_days', 1)
    monkeypatch.setattr(settings, 'storage_cleanup_stale_active_hours', 48)

    managed_stale = Job(
        source_url='https://example.com/managed.wav',
        source_local_path=str(managed_source),
        status='pending',
        processing_metrics={'storage_policy_version': 2},
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=3),
    )
    legacy_stale = Job(
        source_url='https://example.com/legacy.wav',
        source_local_path=str(legacy_source),
        status='pending',
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=3),
    )
    managed_fresh = Job(
        source_url='https://example.com/fresh.wav',
        source_local_path=str(fresh_source),
        status='pending',
        processing_metrics={'storage_policy_version': 2},
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
    )
    session.add_all([managed_stale, legacy_stale, managed_fresh])
    session.commit()

    report = run_storage_maintenance_once(session, now=now)
    session.refresh(managed_stale)
    session.refresh(legacy_stale)
    session.refresh(managed_fresh)

    assert report.aborted_job_ids == [managed_stale.id]
    assert managed_stale.status == 'aborted'
    assert managed_stale.source_local_path is None
    assert not managed_source.exists()
    assert legacy_stale.status == 'pending'
    assert legacy_stale.source_local_path == str(legacy_source)
    assert legacy_source.exists()
    assert managed_fresh.status == 'pending'
    assert fresh_source.exists()
    assert not old_tmp.exists()


def test_maintenance_tick_drains_due_voice_clone_deletion_tasks(
    monkeypatch,
    tmp_path,
) -> None:
    session = _build_session()
    voice_clone_root = tmp_path / 'voice-cloning'
    voice_clone_root.mkdir()
    missing_path = voice_clone_root / 'missing.wav'
    monkeypatch.setattr(
        settings,
        'local_voice_clone_library_dir',
        str(voice_clone_root),
    )
    task = VoiceCloneDeletionTask(
        kind='local',
        resource_key=f'local:{missing_path}',
        local_path=str(missing_path),
    )
    session.add(task)
    session.commit()

    report = run_storage_maintenance_once(
        session,
        now=datetime(2026, 7, 30, 9, 0, 0),
    )

    assert report.voice_clone_deletion_tasks.attempted_count == 1
    assert report.voice_clone_deletion_tasks.completed_count == 1
    assert report.voice_clone_deletion_tasks.failed_count == 0
    assert session.get(VoiceCloneDeletionTask, task.id) is None


def test_voice_expiry_runs_with_both_feature_switches_disabled(monkeypatch):
    from app.models.voice_clone_identity import VoiceCloneIdentity
    session = _build_session()
    now = datetime(2026, 9, 11, 12)
    identity = VoiceCloneIdentity(display_name='expired', created_at=now - timedelta(hours=72))
    session.add(identity)
    session.commit()
    identity_id = identity.id
    monkeypatch.setattr(settings, 'storage_cleanup_enabled', False)
    monkeypatch.setattr(settings, 'voice_cloning_enabled', False)

    report = run_storage_maintenance_once(session, now=now)

    assert report.voice_retention.deleted_count == 1
    assert session.get(VoiceCloneIdentity, identity_id) is None
    assert report.cleanup.deleted_count == 0


def test_worker_starts_for_retention_unless_explicitly_disabled(monkeypatch):
    from app.services import storage_maintenance as maintenance
    started = []
    class FakeThread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            started.append(True)
        def is_alive(self):
            return bool(started)
    monkeypatch.setattr(maintenance.threading, 'Thread', FakeThread)
    monkeypatch.setattr(maintenance, '_maintenance_thread', None)
    monkeypatch.setattr(settings, 'storage_cleanup_enabled', False)
    assert maintenance.start_storage_maintenance_worker(enabled=False) is False
    assert maintenance.start_storage_maintenance_worker() is True
    assert started == [True]


def test_worker_ticks_retention_each_minute_but_storage_keeps_its_interval(monkeypatch):
    from contextlib import nullcontext
    from app.services import storage_maintenance as maintenance
    from app.services.storage_cleanup import StorageCleanupReport
    seconds = [0]
    calls = []
    class FakeStop:
        def is_set(self):
            return len(calls) == 4
        def wait(self, interval):
            assert interval == 60
            seconds[0] += interval
    def run(_session, *, include_storage_cleanup=True):
        calls.append(include_storage_cleanup)
        return maintenance.StorageMaintenanceReport([], StorageCleanupReport(False))
    monkeypatch.setattr(maintenance, '_maintenance_stop_event', FakeStop())
    monkeypatch.setattr(maintenance, 'run_storage_maintenance_once', run)
    monkeypatch.setattr(maintenance, 'monotonic', lambda: seconds[0], raising=False)
    monkeypatch.setattr(settings, 'storage_cleanup_interval_seconds', 120)

    maintenance._run_storage_maintenance_worker(lambda: nullcontext(None))

    assert calls == [True, False, True, False]
