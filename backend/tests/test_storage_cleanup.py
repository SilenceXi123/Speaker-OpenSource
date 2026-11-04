from datetime import datetime, timedelta
import tracemalloc

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import Base
from app.models.job import Job
from app.services.storage_cleanup import build_storage_cleanup_plan, run_storage_cleanup


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_cleanup_plan_does_not_materialize_historical_transcripts(monkeypatch, tmp_path):
    from app.services import voice_clone_cleanup  # Register shared asset tables before creating the test database.

    roots = {name: tmp_path / name for name in ('media', 'uploads', 'audio', 'render')}
    for root in roots.values():
        root.mkdir()
    for setting, root in [('local_media_library_dir', 'media'), ('local_upload_dir', 'uploads'),
                          ('local_audio_dir', 'audio'), ('local_render_dir', 'render')]:
        monkeypatch.setattr(settings, setting, str(roots[root]))
    monkeypatch.setattr(settings, 'storage_cleanup_completed_source_days', 1)
    session = _build_session()
    now = datetime(2026, 11, 4, 12)
    build_storage_cleanup_plan(session, now=now)  # Warm lazy imports outside the allocation measurement.
    source = roots['media'] / 'retained.wav'
    source.write_bytes(b'keep')
    for _ in range(80):
        session.add(Job(source_url='https://example.test/audio.wav', status='completed',
            source_local_path=str(source), created_at=now - timedelta(days=3),
            processing_metrics={'storage_policy_version': 2},
            raw_payload={'text': 'a' * 100_000}, pipeline_artifacts={'text': 'b' * 100_000}))
    session.commit()
    session.expunge_all()
    tracemalloc.start()
    try:
        plan = build_storage_cleanup_plan(session, now=now, managed_jobs_only=True)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        session.close()
    assert plan.item_count == 80 and plan.total_bytes == 320
    assert source.read_bytes() == b'keep'
    assert peak < 8 * 1024 * 1024, f'cleanup loaded historical subtitle payloads: {peak} bytes'


def test_storage_cleanup_preview_does_not_delete_files(monkeypatch, tmp_path) -> None:
    session = _build_session()
    media_dir = tmp_path / 'library' / 'media'
    upload_dir = tmp_path / 'tmp' / 'uploads'
    audio_dir = tmp_path / 'tmp' / 'audio'
    render_dir = tmp_path / 'tmp' / 'render'
    for directory in [media_dir, upload_dir, audio_dir, render_dir]:
        directory.mkdir(parents=True)
    old_source = media_dir / 'old.wav'
    old_source.write_bytes(b'audio')
    old_tmp = upload_dir / 'old-upload.wav'
    old_tmp.write_bytes(b'tmp')
    now = datetime(2026, 7, 4, 12, 0, 0)
    old_timestamp = (now - timedelta(days=3)).timestamp()
    old_tmp.touch()
    old_tmp.chmod(0o644)
    old_tmp.parent.mkdir(parents=True, exist_ok=True)
    import os

    os.utime(old_tmp, (old_timestamp, old_timestamp))

    monkeypatch.setattr(settings, 'local_media_library_dir', str(media_dir))
    monkeypatch.setattr(settings, 'local_upload_dir', str(upload_dir))
    monkeypatch.setattr(settings, 'local_audio_dir', str(audio_dir))
    monkeypatch.setattr(settings, 'local_render_dir', str(render_dir))
    monkeypatch.setattr(settings, 'storage_cleanup_completed_source_days', 1, raising=False)
    monkeypatch.setattr(settings, 'storage_cleanup_tmp_days', 1, raising=False)

    session.add(
        Job(
            source_url='https://example.com/old.wav',
            source_local_path=str(old_source),
            status='completed',
            created_at=now - timedelta(days=3),
        )
    )
    session.commit()

    report = build_storage_cleanup_plan(session, now=now)

    assert report.total_bytes == len(b'audio') + len(b'tmp')
    assert old_source.exists()
    assert old_tmp.exists()


def test_storage_cleanup_run_deletes_only_expired_finished_media(monkeypatch, tmp_path) -> None:
    session = _build_session()
    media_dir = tmp_path / 'library' / 'media'
    upload_dir = tmp_path / 'tmp' / 'uploads'
    audio_dir = tmp_path / 'tmp' / 'audio'
    render_dir = tmp_path / 'tmp' / 'render'
    for directory in [media_dir, upload_dir, audio_dir, render_dir]:
        directory.mkdir(parents=True)
    old_source = media_dir / 'old.wav'
    fresh_source = media_dir / 'fresh.wav'
    running_source = media_dir / 'running.wav'
    outside_source = tmp_path / 'outside.wav'
    for path, content in [
        (old_source, b'old'),
        (fresh_source, b'fresh'),
        (running_source, b'running'),
        (outside_source, b'outside'),
    ]:
        path.write_bytes(content)
    now = datetime(2026, 7, 4, 12, 0, 0)

    monkeypatch.setattr(settings, 'local_media_library_dir', str(media_dir))
    monkeypatch.setattr(settings, 'local_upload_dir', str(upload_dir))
    monkeypatch.setattr(settings, 'local_audio_dir', str(audio_dir))
    monkeypatch.setattr(settings, 'local_render_dir', str(render_dir))
    monkeypatch.setattr(settings, 'storage_cleanup_completed_source_days', 15, raising=False)
    monkeypatch.setattr(settings, 'storage_cleanup_failed_source_days', 7, raising=False)
    monkeypatch.setattr(settings, 'storage_cleanup_tmp_days', 1, raising=False)

    old_job = Job(
        source_url='https://example.com/old.wav',
        source_local_path=str(old_source),
        status='completed',
        created_at=now - timedelta(days=20),
    )
    fresh_job = Job(
        source_url='https://example.com/fresh.wav',
        source_local_path=str(fresh_source),
        status='completed',
        created_at=now - timedelta(days=2),
    )
    running_job = Job(
        source_url='https://example.com/running.wav',
        source_local_path=str(running_source),
        status='running',
        created_at=now - timedelta(days=20),
    )
    outside_job = Job(
        source_url='https://example.com/outside.wav',
        source_local_path=str(outside_source),
        status='completed',
        created_at=now - timedelta(days=20),
    )
    session.add_all([old_job, fresh_job, running_job, outside_job])
    session.commit()

    report = run_storage_cleanup(session, now=now)
    session.refresh(old_job)
    session.refresh(fresh_job)
    session.refresh(running_job)
    session.refresh(outside_job)

    assert report.deleted_bytes == len(b'old')
    assert not old_source.exists()
    assert old_job.source_local_path is None
    assert fresh_source.exists()
    assert fresh_job.source_local_path == str(fresh_source)
    assert running_source.exists()
    assert running_job.source_local_path == str(running_source)
    assert outside_source.exists()
    assert outside_job.source_local_path == str(outside_source)
