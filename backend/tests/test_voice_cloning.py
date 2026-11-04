from __future__ import annotations

import io
from types import SimpleNamespace
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import Base, configure_sqlite_engine
from app.models.person import Person  # noqa: F401
from app.models.project import Project
from app.models.voice_clone_deletion_task import VoiceCloneDeletionTask
from app.models.voice_clone_identity import VoiceCloneIdentity
from app.models.voice_clone_identity_review import VoiceCloneIdentityReview
from app.models.voice_clone_profile import VoiceCloneProfile  # noqa: F401
from app.models.voice_clone_sample import VoiceCloneSample  # noqa: F401
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord  # noqa: F401
from app.models.voiceprint_sample import VoiceprintSample  # noqa: F401
from app.schemas.voice_cloning import VoiceClonePreviewRequest, VoiceCloneProfileCreateRequest
from app.schemas.voiceprint import PersonCreateRequest
from app.api.v1.voice_cloning import _build_record_audio_filename, get_synthesis_record_audio
from app.api.v1.voice_cloning import router as voice_cloning_v1_router
from app.api.v2.voice_cloning import router as voice_cloning_v2_router
from app.services import voice_clone_identity as identity_service
from app.services.cosyvoice_voice_clone import create_cosyvoice_voice
from app.services.voice_cloning import (
    build_voice_clone_synthesis_record_response,
    create_voice_clone_profile,
    delete_voice_clone_sample,
    delete_voice_clone_profile,
    delete_voice_clone_synthesis_record,
    _download_preview_audio,
    get_voice_cloning_provider_status,
    _guess_audio_suffix,
    list_voice_clone_profiles,
    list_voice_clone_samples,
    list_voice_clone_synthesis_records,
    save_voice_clone_sample,
    synthesize_voice_clone_preview,
)
from app.services.voice_clone_identity_review import resolve_identity_review
from app.services.voiceprint_provider import VoiceprintEmbeddingResult
from app.services.voiceprints import create_person, save_voiceprint_sample
from app.services import voice_cloning as voice_cloning_service


@pytest.mark.parametrize('model', ['qwen-audio-3.0-tts-plus', 'qwen-audio-3.0-tts-flash', 'qwen-audio-3.1-tts-flash'])
def test_qwen_audio_create_speak_and_expire(monkeypatch, tmp_path, model):
    from datetime import timedelta
    from app.services.voice_retention_cleanup import expire_voice_data
    from app.services.voice_clone_cleanup import drain_voice_clone_deletion_tasks
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_region', 'cn-beijing')
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    calls = []
    def post(url, **kwargs):
        payload = kwargs['json']
        calls.append(payload)
        action = payload['input'].get('action')
        if action == 'create_voice':
            assert payload['input']['target_model'] == model
            return httpx.Response(200, json={'output': {'voice_id': 'new-audio-voice'}})
        if action == 'query_voice':
            return httpx.Response(200, json={'output': {'status': 'OK'}})
        if action == 'delete_voice':
            return httpx.Response(200, json={'output': {}})
        assert payload['model'] == model
        assert '温柔舒缓' in payload['input']['instruction']
        assert 'instructions' not in payload['input']
        return httpx.Response(200, json={'output': {'audio': {'url': 'https://example.test/result.wav'}}})
    monkeypatch.setattr(httpx, 'post', post)
    output = tmp_path / 'generated.wav'
    output.write_bytes(b'WAV')
    monkeypatch.setattr(voice_cloning_service, '_download_preview_audio', lambda *args: output)
    profile = create_voice_clone_profile(session, VoiceCloneProfileCreateRequest(
        clone_sample_id=sample.id, voice_name='new model', provider='qwen_audio', target_model=model))
    assert profile.status == 'ready'
    record = synthesize_voice_clone_preview(session, profile.id, VoiceClonePreviewRequest(text='欢迎使用。', instruction='温柔舒缓'))
    assert record.status == 'ready' and record.provider_snapshot == 'qwen_audio'
    future = record.created_at + timedelta(hours=72, seconds=1)
    session.rollback()
    expire_voice_data(session, now=future)
    assert session.query(VoiceCloneProfile).count() == 0
    assert session.query(VoiceCloneSynthesisRecord).count() == 0
    task = session.query(VoiceCloneDeletionTask).filter_by(kind='provider').one()
    assert (task.provider, task.target_model, task.voice_id) == ('qwen_audio', model, 'new-audio-voice')
    drain_voice_clone_deletion_tasks(session)
    assert any(call['input'].get('action') == 'delete_voice' for call in calls)


def test_qwen_audio_catalog_respects_region_and_rejects_wrong_provider_pair(monkeypatch):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_region', 'cn-beijing')
    options = [item for item in voice_cloning_service._build_model_options() if item.provider == 'qwen_audio']
    assert len(options) == 3
    assert all(item.available and item.capabilities.instruction for item in options)
    with pytest.raises(RuntimeError, match='模型'):
        voice_cloning_service._require_model_available('qwen', options[0].model)
    monkeypatch.setattr(settings, 'voice_cloning_region', 'intl')
    assert all(not item.available for item in get_voice_cloning_provider_status().models if item.provider == 'qwen_audio')


def test_qwen_audio_instruction_failure_is_not_silently_retried_without_tone(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    profile = VoiceCloneProfile(source_clone_sample_id=sample.id, provider='qwen_audio',
        target_model='qwen-audio-3.0-tts-plus', voice_name='voice', voice_id='cloud', status='ready')
    session.add(profile); session.commit()
    calls = []
    def failed_synthesis(**kwargs):
        calls.append(kwargs)
        raise RuntimeError('instruction request failed')
    monkeypatch.setattr(voice_cloning_service, '_synthesize_provider_voice', failed_synthesis)
    record = synthesize_voice_clone_preview(session, profile.id, VoiceClonePreviewRequest(text='你好', instruction='温柔'))
    assert record.status == 'failed' and len(calls) == 1
    assert 'instruction request failed' in record.provider_message


def test_fixed_voice_expiry_hides_dependencies_but_keeps_fresh_project_record(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone
    UTC = timezone.utc
    from app.services import voice_retention
    from app.services.voice_clone_library import list_voice_clone_library
    now = datetime(2026, 9, 11, 12)
    monkeypatch.setattr(voice_retention, 'utc_now', lambda: now)
    session = _build_session()
    project = Project(name='retention', name_key='retention', status='active')
    identity = VoiceCloneIdentity(display_name='expired identity', created_at=now - timedelta(hours=72))
    session.add_all([project, identity]); session.flush()
    sample = VoiceCloneSample(display_name='sample', original_filename='sample.wav', identity_id=identity.id,
        project_id=project.id, created_at=now - timedelta(hours=1), **_ready_clone_sample_facts(tmp_path / 'source.wav'))
    session.add(sample); session.flush()
    profile = VoiceCloneProfile(project_id=project.id, identity_id=identity.id, source_clone_sample_id=sample.id,
        voice_name='voice', provider='qwen', target_model=settings.voice_cloning_qwen_target_model, status='ready', voice_id='voice-id', created_at=now - timedelta(minutes=1))
    session.add(profile); session.flush()
    record = VoiceCloneSynthesisRecord(project_id=project.id, voice_clone_profile_id=profile.id, input_text='saved', target_model=profile.target_model,
        created_at=now - timedelta(minutes=1), status='ready')
    session.add(record); session.commit()
    assert voice_cloning_service.get_voice_clone_sample(session, sample.id) is None
    assert voice_cloning_service.get_voice_clone_profile(session, profile.id) is None
    assert list_voice_clone_library(session, project.id).total == 0
    assert voice_cloning_service.list_voice_clone_synthesis_records(session, project_id=project.id) == [record]
    assert voice_cloning_service.build_voice_clone_synthesis_record_response(record).expires_at == (record.created_at + timedelta(hours=72)).replace(tzinfo=UTC)
    identity.created_at += timedelta(microseconds=1); session.commit()
    assert voice_cloning_service.get_voice_clone_sample(session, sample.id) is sample
    assert voice_cloning_service.build_voice_clone_sample_response(sample).expires_at == (identity.created_at + timedelta(hours=72)).replace(tzinfo=UTC)


def test_expired_exact_audio_and_identity_embedding_are_never_reused(monkeypatch, tmp_path):
    from datetime import datetime, timedelta
    from app.services import voice_retention
    now = datetime(2026, 9, 11, 12)
    monkeypatch.setattr(voice_retention, 'utc_now', lambda: now)
    session = _build_session()
    identity = VoiceCloneIdentity(display_name='old', aliases=['old'], reference_embedding=[1.0, 0.0], embedding_model_id='test', created_at=now - timedelta(hours=72))
    session.add(identity); session.flush()
    sample = VoiceCloneSample(identity_id=identity.id, display_name='old', original_filename='old.wav', audio_local_path='/old.wav', content_sha256='same', created_at=now - timedelta(hours=72))
    session.add(sample); session.commit()
    monkeypatch.setattr(identity_service, 'extract_voiceprint_embedding', lambda path: SimpleNamespace(embedding=[1.0, 0.0], provider_model_id='test', provider_name='test', provider_message='ok'))
    result = identity_service.resolve_voice_clone_identity(session, display_name='new', content_sha256='same', audio_path=tmp_path / 'new.wav')
    assert result.match_type == 'new_identity'
    assert result.identity.id != identity.id
    assert result.exact_sample is None
    assert identity.aliases == ['old']


@pytest.fixture(autouse=True)
def enabled_voice_service(monkeypatch):
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'sfm_workspace_id', 'speaker-space')
    monkeypatch.setattr(voice_cloning_service, 'query_cosyvoice_voice',
                        lambda voice_id: {'output': {'voice_id': voice_id, 'status': 'OK'}})


def _build_session_factory(database_url: str, *, foreign_keys: bool = False):
    engine = create_engine(database_url, connect_args={'check_same_thread': False})
    if foreign_keys:
        configure_sqlite_engine(engine, enable_wal=False)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _build_session():
    return _build_session_factory('sqlite:///:memory:')()


def _ready_clone_sample_facts(path: Path) -> dict[str, object]:
    path.write_bytes(b'RIFF0000WAVE')
    return {
        'audio_local_path': str(path),
        'content_sha256': 'c' * 64,
        'file_size_bytes': path.stat().st_size,
        'duration_ms': 12_000,
        'sample_rate_hz': 24_000,
        'channel_count': 1,
        'clone_readiness_status': 'ready',
    }


@pytest.fixture()
def voice_clone_upload_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    session_factory = _build_session_factory(
        f"sqlite:///{tmp_path / 'voice-clone-upload.db'}",
        foreign_keys=True,
    )
    session = session_factory()
    project = Project(
        name='上传测试项目',
        name_key='上传测试项目',
        legacy_program_key='上传测试项目',
        status='active',
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    upload_dir = tmp_path / 'uploads'
    upload_dir.mkdir()
    library_dir = tmp_path / 'library'
    library_dir.mkdir()
    upload_index = 0
    prepared_index = 0

    async def fake_save_upload_file_locally(file):
        nonlocal upload_index
        upload_index += 1
        path = upload_dir / f'upload-{upload_index}.wav'
        path.write_bytes(await file.read())
        return path

    def fake_prepare_voice_clone_reference_audio(source_path: Path) -> Path:
        nonlocal prepared_index
        prepared_index += 1
        path = upload_dir / f'prepared-{prepared_index}.wav'
        path.write_bytes(source_path.read_bytes())
        return path

    for module_name in ('app.api.v1.voice_cloning', 'app.api.v2.voice_cloning'):
        monkeypatch.setattr(f'{module_name}.SessionLocal', session_factory)
        monkeypatch.setattr(
            f'{module_name}.save_upload_file_locally',
            fake_save_upload_file_locally,
        )
    monkeypatch.setattr(settings, 'local_voice_clone_library_dir', str(library_dir))
    monkeypatch.setattr(
        voice_cloning_service,
        'prepare_voice_clone_reference_audio',
        fake_prepare_voice_clone_reference_audio,
    )
    monkeypatch.setattr('app.services.voice_cloning.get_media_duration_seconds', lambda _path: 12.0)
    monkeypatch.setattr(
        'app.services.voice_cloning.ensure_sample_public_url',
        lambda path: f'https://example.test/{path.name}',
    )
    monkeypatch.setattr(
        identity_service,
        'extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[1.0, 0.0],
            provider_name='test-provider',
            provider_model_id='test-model',
            provider_message='embedding ready',
        ),
    )
    test_app = FastAPI()
    test_app.include_router(voice_cloning_v1_router, prefix='/api/v1/voice-cloning')
    test_app.include_router(voice_cloning_v2_router, prefix='/api/v2')
    try:
        yield (
            TestClient(test_app, raise_server_exceptions=False),
            project,
            upload_dir,
            library_dir,
            session_factory,
        )
    finally:
        session.close()


@pytest.mark.parametrize('endpoint', [
    '/api/v1/voice-cloning/samples',
    '/api/v2/projects/{project_id}/voice-cloning/samples',
])
def test_legacy_and_v2_uploads_cannot_bypass_identity_match(
    voice_clone_upload_api,
    endpoint: str,
) -> None:
    client, project, _upload_dir, _library_dir, _session_factory = voice_clone_upload_api
    url = endpoint.format(project_id=project.id)
    first = client.post(
        url,
        data={'display_name': '主持人', 'project_id': project.id},
        files={'file': ('first.wav', b'RIFF-identical-audio', 'audio/wav')},
    )
    second = client.post(
        url,
        data={
            'display_name': '改名主持人',
            'project_id': project.id,
            'force': 'true',
            'skip_match': 'true',
            'identity_id': 'client-chosen-identity',
        },
        files={'file': ('renamed.wav', b'RIFF-identical-audio', 'audio/wav')},
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()['id'] == first.json()['id']
    assert second.json()['reused_existing'] is True
    assert second.json()['identity_id'] == first.json()['identity_id']
    assert second.json()['match_type'] == 'exact_audio'
    assert second.json()['identity_display_name'] == '主持人'
    assert second.json()['identity_aliases'] == ['主持人', '改名主持人']
    assert second.json()['sample_slots_remaining'] == 2


def test_exact_upload_never_creates_a_second_library_file(
    voice_clone_upload_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, project, _upload_dir, library_dir, _session_factory = voice_clone_upload_api
    url = f'/api/v2/projects/{project.id}/voice-cloning/samples'
    first = client.post(
        url,
        data={'display_name': '主持人'},
        files={'file': ('first.wav', b'RIFF-identical-audio', 'audio/wav')},
    )
    library_before_exact = set(library_dir.iterdir())
    real_resolver = voice_cloning_service.resolve_voice_clone_identity

    def assert_library_unchanged_before_resolver(*args, **kwargs):
        assert set(library_dir.iterdir()) == library_before_exact
        return real_resolver(*args, **kwargs)

    monkeypatch.setattr(
        voice_cloning_service,
        'resolve_voice_clone_identity',
        assert_library_unchanged_before_resolver,
    )
    exact = client.post(
        url,
        data={'display_name': '改名主持人'},
        files={'file': ('renamed.wav', b'RIFF-identical-audio', 'audio/wav')},
    )

    assert first.status_code == 201
    assert exact.status_code == 200
    assert set(library_dir.iterdir()) == library_before_exact


@pytest.mark.parametrize('endpoint', [
    '/api/v1/voice-cloning/samples',
    '/api/v2/projects/{project_id}/voice-cloning/samples',
])
def test_identity_limit_response_includes_existing_sample_summary(
    voice_clone_upload_api,
    endpoint: str,
) -> None:
    client, project, _upload_dir, library_dir, _session_factory = voice_clone_upload_api
    url = endpoint.format(project_id=project.id)
    created = [
        client.post(
            url,
            data={'display_name': f'别名-{index}', 'project_id': project.id},
            files={'file': (f'sample-{index}.wav', f'RIFF-recording-{index}'.encode(), 'audio/wav')},
        ).json()
        for index in range(3)
    ]

    rejected = client.post(
        url,
        data={'display_name': '第四个别名', 'project_id': project.id},
        files={'file': ('sample-3.wav', b'RIFF-recording-3', 'audio/wav')},
    )
    detail = rejected.json()['detail']

    assert rejected.status_code == 409
    assert detail['message'] == '声音身份最多保留 3 条声音复刻样本，请直接复用已有样本'
    assert detail['identity_id'] == created[0]['identity_id']
    assert detail['existing_samples'] == [
        {
            'id': item['id'],
            'display_name': f'别名-{index}',
            'original_filename': f'sample-{index}.wav',
        }
        for index, item in enumerate(created)
    ]
    assert len(list(library_dir.iterdir())) == 3


def test_embedding_failure_removes_temporary_upload(
    voice_clone_upload_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, project, upload_dir, library_dir, _session_factory = voice_clone_upload_api
    monkeypatch.setattr(
        identity_service,
        'extract_voiceprint_embedding',
        lambda _path: (_ for _ in ()).throw(RuntimeError('offline')),
    )

    response = client.post(
        f'/api/v2/projects/{project.id}/voice-cloning/samples',
        data={'display_name': '主持人'},
        files={'file': ('failed.wav', b'RIFF-new-audio', 'audio/wav')},
    )

    assert response.status_code == 503
    assert list(upload_dir.iterdir()) == []
    assert list(library_dir.iterdir()) == []


def test_hash_failure_removes_temporary_upload(
    voice_clone_upload_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, project, upload_dir, library_dir, _session_factory = voice_clone_upload_api
    monkeypatch.setattr(
        voice_cloning_service,
        'hash_voice_clone_audio',
        lambda _path: (_ for _ in ()).throw(OSError('cannot read audio')),
    )

    response = client.post(
        f'/api/v2/projects/{project.id}/voice-cloning/samples',
        data={'display_name': '主持人'},
        files={'file': ('failed.wav', b'RIFF-new-audio', 'audio/wav')},
    )

    assert response.status_code == 500
    assert list(upload_dir.iterdir()) == []
    assert list(library_dir.iterdir()) == []


def test_database_failure_removes_temporary_upload_and_rolls_back_rows(
    voice_clone_upload_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, project, upload_dir, library_dir, session_factory = voice_clone_upload_api
    with monkeypatch.context() as failure:
        failure.setattr(
            session_factory.class_,
            'commit',
            lambda _session: (_ for _ in ()).throw(RuntimeError('database offline')),
        )
        response = client.post(
            f'/api/v2/projects/{project.id}/voice-cloning/samples',
            data={'display_name': '主持人'},
            files={'file': ('failed.wav', b'RIFF-database-failure', 'audio/wav')},
        )

    check_session = session_factory()
    try:
        assert response.status_code == 400
        assert check_session.query(VoiceCloneIdentity).count() == 0
        assert check_session.query(VoiceCloneSample).count() == 0
        assert check_session.query(VoiceCloneIdentityReview).count() == 0
        assert list(upload_dir.iterdir()) == []
        assert list(library_dir.iterdir()) == []
    finally:
        check_session.close()


@pytest.mark.parametrize('failure_phase', ['flush', 'commit'])
def test_database_failure_deletes_only_the_newly_uploaded_owned_oss_object(
    voice_clone_upload_api,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    client, project, _upload_dir, _library_dir, session_factory = voice_clone_upload_api
    public_url = 'https://speaker-test.example/assets/voice/new.wav'
    deleted_keys: list[str] = []
    monkeypatch.setattr(settings, 'oss_public_base_url', 'https://speaker-test.example/assets')
    monkeypatch.setattr(voice_cloning_service, 'ensure_sample_public_url', lambda _path: public_url)
    monkeypatch.setattr(
        voice_cloning_service,
        'delete_oss_object',
        deleted_keys.append,
        raising=False,
    )
    if failure_phase == 'commit':
        monkeypatch.setattr(
            session_factory.class_,
            'commit',
            lambda _session: (_ for _ in ()).throw(RuntimeError('forced commit failure')),
        )
    else:
        real_flush = session_factory.class_.flush

        def fail_sample_flush(database_session, *args, **kwargs):
            if any(isinstance(row, VoiceCloneSample) for row in database_session.new):
                raise RuntimeError('forced flush failure')
            return real_flush(database_session, *args, **kwargs)

        monkeypatch.setattr(session_factory.class_, 'flush', fail_sample_flush)

    response = client.post(
        f'/api/v2/projects/{project.id}/voice-cloning/samples',
        data={'display_name': '主持人'},
        files={'file': ('failed.wav', b'RIFF-new-owned-object', 'audio/wav')},
    )

    assert response.status_code == 400
    assert deleted_keys == ['voice/new.wav']


def test_database_failure_never_deletes_a_preexisting_owned_oss_object(
    voice_clone_upload_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, project, _upload_dir, _library_dir, session_factory = voice_clone_upload_api
    public_url = 'https://speaker-test.example/assets/voice/reused.wav'
    with session_factory() as session:
        session.add(VoiceCloneSample(
            display_name='已有样本',
            original_filename='existing.wav',
            audio_local_path='/tmp/existing.wav',
            audio_public_url=public_url,
        ))
        session.commit()
    deleted_keys: list[str] = []
    monkeypatch.setattr(settings, 'oss_public_base_url', 'https://speaker-test.example/assets')
    monkeypatch.setattr(voice_cloning_service, 'ensure_sample_public_url', lambda _path: public_url)
    monkeypatch.setattr(
        voice_cloning_service,
        'delete_oss_object',
        deleted_keys.append,
        raising=False,
    )
    monkeypatch.setattr(
        session_factory.class_,
        'commit',
        lambda _session: (_ for _ in ()).throw(RuntimeError('forced commit failure')),
    )

    response = client.post(
        f'/api/v2/projects/{project.id}/voice-cloning/samples',
        data={'display_name': '主持人'},
        files={'file': ('failed.wav', b'RIFF-new-row-reused-object', 'audio/wav')},
    )

    assert response.status_code == 400
    assert deleted_keys == []


@pytest.mark.parametrize('endpoint', [
    '/api/v1/voice-cloning/samples/{sample_id}',
    '/api/v2/projects/{project_id}/voice-cloning/samples/{sample_id}',
])
def test_pending_identity_review_blocks_v1_and_v2_sample_delete(
    voice_clone_upload_api,
    endpoint: str,
) -> None:
    client, project, _upload_dir, _library_dir, session_factory = voice_clone_upload_api
    first = client.post(
        f'/api/v2/projects/{project.id}/voice-cloning/samples',
        data={'display_name': '主持人'},
        files={'file': ('first.wav', b'RIFF-first-recording', 'audio/wav')},
    )
    matched = client.post(
        f'/api/v2/projects/{project.id}/voice-cloning/samples',
        data={'display_name': '主持人别名'},
        files={'file': ('matched.wav', b'RIFF-different-recording', 'audio/wav')},
    )
    assert first.status_code == 201
    assert matched.status_code == 201
    with session_factory() as session:
        review_id = session.query(VoiceCloneIdentityReview).one().id

    response = client.delete(endpoint.format(
        project_id=project.id,
        sample_id=matched.json()['id'],
    ))

    assert response.status_code == 409
    assert response.json()['detail'] == {
        'code': 'voice_clone_sample_pending_identity_review',
        'message': '该样本有待处理的声音身份审核，暂不能删除',
        'review_ids': [review_id],
    }
    with session_factory() as session:
        assert session.get(VoiceCloneSample, matched.json()['id']) is not None


def test_resolved_identity_review_keeps_audit_and_nulls_sample_on_shared_delete(
    voice_clone_upload_api,
) -> None:
    client, project, _upload_dir, _library_dir, session_factory = voice_clone_upload_api
    client.post(
        f'/api/v2/projects/{project.id}/voice-cloning/samples',
        data={'display_name': '主持人'},
        files={'file': ('first.wav', b'RIFF-first-recording', 'audio/wav')},
    )
    matched = client.post(
        f'/api/v2/projects/{project.id}/voice-cloning/samples',
        data={'display_name': '主持人别名'},
        files={'file': ('matched.wav', b'RIFF-different-recording', 'audio/wav')},
    )

    with session_factory() as session:
        review = session.query(VoiceCloneIdentityReview).one()
        review_id = review.id
        sample = session.get(VoiceCloneSample, matched.json()['id'])
        resolve_identity_review(session, review_id, 'confirm', 'admin', 'same speaker')
        delete_voice_clone_sample(session, sample, project_id=project.id)

    with session_factory() as session:
        review = session.get(VoiceCloneIdentityReview, review_id)
        assert review.status == 'confirmed'
        assert review.resolved_by == 'admin'
        assert review.resolution_reason == 'same speaker'
        assert review.sample_id is None


def test_get_voice_cloning_provider_status_reports_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'voice_cloning_enabled', False)

    status = get_voice_cloning_provider_status()

    assert status.available is False
    assert status.message == '声音复刻功能未开启'


def test_create_voice_clone_profile_uses_existing_sample(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='测试人物'))

    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'cosyvoice')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_cosyvoice_target_model', 'cosyvoice-v3.5-plus')
    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 15.0)
    monkeypatch.setattr(
        'app.services.voiceprints.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[0.1, 0.2, 0.3],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )
    monkeypatch.setattr('app.services.voiceprints.ensure_sample_public_url', lambda _path: 'https://example.com/sample.wav')
    monkeypatch.setattr(
        'app.services.voice_cloning.create_cosyvoice_voice',
        lambda **_kwargs: {'output': {'voice_id': 'voice-demo-001'}},
    )

    sample_path = tmp_path / 'demo.wav'
    sample_path.write_bytes(b'RIFFdemo')
    sample = save_voiceprint_sample(session, person, sample_path, 'demo.wav')

    profile = create_voice_clone_profile(
        session,
        VoiceCloneProfileCreateRequest(
            person_id=person.id,
            sample_id=sample.id,
            voice_name='testvoice',
            target_model='cosyvoice-v3.5-plus',
        ),
    )

    assert profile.status == 'ready'
    assert profile.voice_id == 'voice-demo-001'
    assert profile.source_sample_id == sample.id


def test_legacy_qwen_creation_adapter_preserves_provider_contract(monkeypatch) -> None:
    from app.services.qwen_voice_clone import create_qwen_voice
    captured = {}
    def post(url, **kwargs):
        captured.update(kwargs['json'])
        return httpx.Response(200, json={'output': {'voice': 'qwen-legacy-voice'}})
    monkeypatch.setattr(httpx, 'post', post)
    response = create_qwen_voice(sample_public_url='https://example.test/sample.wav',
        target_model='qwen3-tts-vc-2026-01-22', voice_name='Legacy', language_hints=['zh'])
    assert response['output']['voice'] == 'qwen-legacy-voice'
    assert captured == {'model': 'qwen-voice-enrollment', 'input': {
        'action': 'create', 'target_model': 'qwen3-tts-vc-2026-01-22',
        'preferred_name': 'legacy', 'audio': {'data': 'https://example.test/sample.wav'}, 'language': 'zh',
    }}


def test_create_voice_clone_profile_allows_provider_override_and_uses_clone_url(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='可选模型人物'))

    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'qwen')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_cosyvoice_target_model', 'cosyvoice-v3.5-plus')
    monkeypatch.setattr(settings, 'voice_cloning_qwen_target_model', 'qwen3-tts-vc-2026-01-22')
    monkeypatch.setattr(settings, 'voice_cloning_sample_target_rate_hz', 24000, raising=False)
    monkeypatch.setattr(settings, 'voice_cloning_sample_channel_count', 1, raising=False)
    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 16.0)
    monkeypatch.setattr(
        'app.services.voiceprints.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[0.1, 0.2, 0.3],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )
    monkeypatch.setattr('app.services.voiceprints.ensure_sample_public_url', lambda _path: 'https://example.com/voiceprint-16k.wav')
    captured: dict[str, object] = {}

    def fake_create_cosyvoice_voice(**kwargs):
        captured.update(kwargs)
        return {'output': {'voice_id': 'cosy-override-voice'}}

    monkeypatch.setattr('app.services.voice_cloning.create_cosyvoice_voice', fake_create_cosyvoice_voice)
    monkeypatch.setattr(
        'app.services.voice_cloning.create_qwen_voice',
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError('should not call qwen provider')),
    )

    voiceprint_path = tmp_path / 'demo-16k.wav'
    clone_path = tmp_path / 'demo-24k.wav'
    voiceprint_path.write_bytes(b'RIFF16K')
    clone_path.write_bytes(b'RIFF24K')
    sample = save_voiceprint_sample(
        session,
        person,
        voiceprint_path,
        'demo.wav',
        clone_audio_local_path=clone_path,
    )
    sample.audio_public_url = 'https://example.com/voiceprint-16k.wav'
    sample.clone_audio_public_url = 'https://example.com/clone-24k.wav'
    session.commit()

    profile = create_voice_clone_profile(
        session,
        VoiceCloneProfileCreateRequest(
            person_id=person.id,
            sample_id=sample.id,
            voice_name='cosyoverride',
            provider='cosyvoice',
            language_hints=['zh'],
        ),
    )

    assert profile.provider == 'cosyvoice'
    assert profile.provider_model_family == 'cosyvoice'
    assert profile.target_model == 'cosyvoice-v3.5-plus'
    assert profile.status == 'ready'
    assert profile.voice_id == 'cosy-override-voice'
    assert profile.source_audio_local_path == str(clone_path)
    assert captured['sample_public_url'] == 'https://example.com/clone-24k.wav'
    assert captured['target_model'] == 'cosyvoice-v3.5-plus'


def test_create_cosyvoice_voice_sends_clone_quality_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = '{}'

        def json(self):
            return {'output': {'voice_id': 'voice-demo'}}

    def fake_post(*_args, **kwargs):
        captured['payload'] = kwargs['json']
        return FakeResponse()

    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_cosyvoice_max_prompt_audio_length', 20.0, raising=False)
    monkeypatch.setattr(settings, 'voice_cloning_cosyvoice_enable_preprocess', False, raising=False)
    monkeypatch.setattr('app.services.cosyvoice_voice_clone.httpx.post', fake_post)

    create_cosyvoice_voice(
        sample_public_url='https://example.com/clone-24k.wav',
        target_model='cosyvoice-v3.5-plus',
        voice_name='qualitytest',
        language_hints=['zh'],
    )

    payload = captured['payload']
    input_payload = payload['input']
    assert input_payload['max_prompt_audio_length'] == 20.0
    assert input_payload['enable_preprocess'] is False
    assert input_payload['url'] == 'https://example.com/clone-24k.wav'


def test_save_voice_clone_sample_is_independent_from_voiceprint_library(monkeypatch) -> None:
    session = _build_session()

    monkeypatch.setattr(
        identity_service,
        'extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[1.0, 0.0],
            provider_name='test-provider',
            provider_model_id='test-model',
            provider_message='embedding ready',
        ),
    )
    monkeypatch.setattr(settings, 'voice_cloning_sample_target_rate_hz', 24000, raising=False)
    monkeypatch.setattr(settings, 'voice_cloning_sample_channel_count', 1, raising=False)
    monkeypatch.setattr('app.services.voice_cloning.get_media_duration_seconds', lambda _path: 14.0)
    monkeypatch.setattr('app.services.voice_cloning.ensure_sample_public_url', lambda path: f'https://example.com/{Path(path).name}')

    with TemporaryDirectory() as tmp_dir:
        clone_path = Path(tmp_dir) / 'liuquan-24k.wav'
        clone_path.write_bytes(b'RIFF24K')

        sample = save_voice_clone_sample(
            session=session,
            display_name='刘权',
            audio_local_path=clone_path,
            original_filename='刘权-信息.wav',
        )

    samples = list_voice_clone_samples(session)
    assert len(samples) == 1
    assert sample.display_name == '刘权'
    assert sample.original_filename == '刘权-信息.wav'
    assert sample.audio_public_url == 'https://example.com/liuquan-24k.wav'
    assert sample.sample_rate_hz == 24000
    assert sample.channel_count == 1
    assert sample.clone_readiness_status == 'ready'
    assert session.query(VoiceprintSample).count() == 0
    assert session.query(Person).count() == 0


def test_v1_lists_keep_unassigned_legacy_clone_assets_visible_without_claiming() -> None:
    session = _build_session()
    project = Project(
        name='兼容项目',
        name_key='兼容项目',
        legacy_program_key='兼容项目',
        status='active',
    )
    sample = VoiceCloneSample(
        display_name='未归属历史样本',
        program_name='历史公共资产',
        original_filename='legacy.wav',
        audio_local_path='/tmp/legacy.wav',
    )
    profile = VoiceCloneProfile(
        program_name='历史公共资产',
        target_model='cosyvoice-v3.5-plus',
        voice_name='未归属历史音色',
        status='ready',
    )
    session.add_all([project, sample, profile])
    session.commit()

    samples = list_voice_clone_samples(session, program_name=project.name)
    profiles = list_voice_clone_profiles(session, program_name=project.name)

    assert [item.id for item in samples] == [sample.id]
    assert [item.id for item in profiles] == [profile.id]
    assert sample.project_id is None
    assert profile.project_id is None


def test_create_voice_clone_profile_uses_independent_clone_sample(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = _build_session()

    monkeypatch.setattr(
        identity_service,
        'extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[1.0, 0.0],
            provider_name='test-provider',
            provider_model_id='test-model',
            provider_message='embedding ready',
        ),
    )
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'cosyvoice')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_cosyvoice_target_model', 'cosyvoice-v3.5-plus')
    monkeypatch.setattr(settings, 'voice_cloning_sample_target_rate_hz', 24000, raising=False)
    monkeypatch.setattr(settings, 'voice_cloning_sample_channel_count', 1, raising=False)
    monkeypatch.setattr('app.services.voice_cloning.get_media_duration_seconds', lambda _path: 18.0)
    monkeypatch.setattr('app.services.voice_cloning.ensure_sample_public_url', lambda _path: 'https://example.com/clone-24k.wav')
    captured: dict[str, object] = {}

    def fake_create_cosyvoice_voice(**kwargs):
        captured.update(kwargs)
        return {'output': {'voice_id': 'qwen-standalone-voice'}}

    monkeypatch.setattr('app.services.voice_cloning.create_cosyvoice_voice', fake_create_cosyvoice_voice)

    clone_path = tmp_path / 'standalone.wav'
    clone_path.write_bytes(b'RIFF24K')
    sample = save_voice_clone_sample(session, '刘权', clone_path, '刘权-信息.wav')

    profile = create_voice_clone_profile(
        session,
        VoiceCloneProfileCreateRequest(
            clone_sample_id=sample.id,
            voice_name='刘权',
            provider='cosyvoice',
            language_hints=['zh'],
        ),
    )

    profiles = list_voice_clone_profiles(session)
    assert len(profiles) == 1
    assert profile.status == 'ready'
    assert profile.voice_id == 'qwen-standalone-voice'
    assert profile.source_clone_sample_id == sample.id
    assert profile.source_sample_id is None
    assert profile.source_audio_local_path == str(clone_path)
    assert profile.person_id in {'', None}
    assert captured['sample_public_url'] == 'https://example.com/clone-24k.wav'


def test_create_profile_rejects_unhealthy_ready_sample_before_provider(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = _build_session()
    missing_path = tmp_path / 'missing-ready-source.wav'
    sample = VoiceCloneSample(
        display_name='本地文件已失效的创建样本',
        original_filename=missing_path.name,
        audio_local_path=str(missing_path),
        audio_public_url='https://example.com/stale-source.wav',
        content_sha256='e' * 64,
        file_size_bytes=12,
        duration_ms=1_000,
        sample_rate_hz=24_000,
        channel_count=1,
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.commit()
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'qwen')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        'app.services.voice_cloning._create_provider_voice',
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError('provider must not be called for an unhealthy source sample')
        ),
    )

    with pytest.raises(RuntimeError, match='当前样本已失效，请重新上传'):
        create_voice_clone_profile(
            session,
            VoiceCloneProfileCreateRequest(
                clone_sample_id=sample.id,
                voice_name='不应创建的音色',
            ),
        )

    assert session.query(VoiceCloneProfile).count() == 0


def test_synthesize_voice_clone_preview_creates_record_and_audio(monkeypatch) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='试听人物'))

    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'cosyvoice')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_cosyvoice_target_model', 'cosyvoice-v3.5-plus')
    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 15.0)
    monkeypatch.setattr(
        'app.services.voiceprints.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[0.1, 0.2, 0.3],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )
    monkeypatch.setattr('app.services.voiceprints.ensure_sample_public_url', lambda _path: 'https://example.com/sample.wav')
    monkeypatch.setattr(
        'app.services.voice_cloning.create_cosyvoice_voice',
        lambda **_kwargs: {'output': {'voice_id': 'voice-demo-002'}},
    )
    monkeypatch.setattr(
        'app.services.voice_cloning.synthesize_cosyvoice_voice',
        lambda **_kwargs: {'output': {'audio': {'url': 'https://example.com/output.mp3'}}},
    )

    with TemporaryDirectory() as tmp_dir:
        sample_path = Path(tmp_dir) / 'demo.wav'
        sample_path.write_bytes(b'RIFFdemo')
        sample = save_voiceprint_sample(session, person, sample_path, 'demo.wav')
        preview_path = Path(tmp_dir) / 'preview.mp3'
        preview_path.write_bytes(b'MP3DATA')
        monkeypatch.setattr('app.services.voice_cloning._download_preview_audio', lambda *_args, **_kwargs: preview_path)

        profile = create_voice_clone_profile(
            session,
            VoiceCloneProfileCreateRequest(
                person_id=person.id,
                sample_id=sample.id,
                voice_name='testvoice-preview',
                target_model='cosyvoice-v3.5-plus',
            ),
        )

        record = synthesize_voice_clone_preview(
            session,
            profile.id,
            VoiceClonePreviewRequest(text='你好，欢迎试听这个音色。'),
        )

        records = list_voice_clone_synthesis_records(session, profile.id)
        preview_exists = Path(record.output_audio_local_path).exists()

    assert record.status == 'ready'
    assert record.output_audio_public_url == 'https://example.com/output.mp3'
    assert record.voice_name_snapshot == 'testvoice-preview'
    assert record.provider_snapshot == 'cosyvoice'
    assert record.target_model_snapshot == 'cosyvoice-v3.5-plus'
    assert preview_exists is True
    assert records[0].id == record.id


def test_synthesis_record_response_preserves_snapshots_without_profile() -> None:
    session = _build_session()
    record = VoiceCloneSynthesisRecord(
        voice_clone_profile_id=None,
        voice_name_snapshot='已删除音色',
        provider_snapshot='qwen',
        target_model_snapshot='qwen3-tts-vc-2026-01-22',
        target_model='qwen3-tts-vc-2026-01-22',
        input_text='历史试听',
        status='ready',
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    response = build_voice_clone_synthesis_record_response(record)

    assert response.voice_clone_profile_id is None
    assert response.voice_name_snapshot == '已删除音色'
    assert response.provider_snapshot == 'qwen'
    assert response.target_model_snapshot == 'qwen3-tts-vc-2026-01-22'


def test_delete_voice_clone_profile_hard_deletes_and_preserves_successful_history(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = _build_session_factory(
        f"sqlite:///{tmp_path / 'direct-hard-delete.db'}"
    )()
    output_path = tmp_path / 'direct-output.wav'
    output_path.write_bytes(b'RIFF-direct-output')
    profile = VoiceCloneProfile(
        provider='qwen',
        provider_model_family='qwen',
        target_model='qwen3-tts-vc-2026-01-22',
        voice_id='voice-direct-hard-delete',
        voice_name='直接删除音色',
        status='ready',
        preview_audio_local_path=str(output_path),
    )
    session.add(profile)
    session.flush()
    successful = VoiceCloneSynthesisRecord(
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='保留输出',
        output_audio_local_path=str(output_path),
        status='ready',
    )
    failed = VoiceCloneSynthesisRecord(
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='删除空失败记录',
        status='failed',
    )
    session.add_all([successful, failed])
    session.commit()
    monkeypatch.setattr(
        'app.services.voice_cloning._delete_provider_voice',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('provider deletion must be queued')
        ),
    )

    deleted = delete_voice_clone_profile(session, profile.id)

    session.expire_all()
    preserved = session.get(VoiceCloneSynthesisRecord, successful.id)
    assert deleted.id == profile.id
    assert session.get(VoiceCloneProfile, profile.id) is None
    assert preserved is not None
    assert preserved.voice_clone_profile_id is None
    assert preserved.voice_name_snapshot == '直接删除音色'
    assert preserved.provider_snapshot == 'qwen'
    assert preserved.target_model_snapshot == 'qwen3-tts-vc-2026-01-22'
    assert preserved.output_audio_local_path == str(output_path)
    assert session.get(VoiceCloneSynthesisRecord, failed.id) is None
    assert {
        task.resource_key
        for task in session.query(VoiceCloneDeletionTask).all()
    } == {
        'provider:qwen:qwen3-tts-vc-2026-01-22:voice-direct-hard-delete',
    }
    assert output_path.is_file()


def test_synthesis_record_fk_sets_profile_null_and_preserves_snapshots() -> None:
    engine = create_engine('sqlite:///:memory:')
    connection = engine.connect()
    connection.exec_driver_sql('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(bind=connection)
    session = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
    )()
    profile = VoiceCloneProfile(
        provider='qwen',
        provider_model_family='qwen',
        target_model='qwen3-tts-vc-2026-01-22',
        voice_name='即将删除的音色',
        status='ready',
    )
    session.add(profile)
    session.flush()
    record = VoiceCloneSynthesisRecord(
        voice_clone_profile_id=profile.id,
        voice_name_snapshot='即将删除的音色',
        provider_snapshot='qwen',
        target_model_snapshot='qwen3-tts-vc-2026-01-22',
        target_model='qwen3-tts-vc-2026-01-22',
        input_text='历史试听',
        status='ready',
    )
    session.add(record)
    session.commit()
    record_id = record.id

    session.execute(delete(VoiceCloneProfile).where(VoiceCloneProfile.id == profile.id))
    session.commit()
    session.expire_all()

    preserved = session.get(VoiceCloneSynthesisRecord, record_id)
    assert preserved is not None
    assert preserved.voice_clone_profile_id is None
    assert preserved.voice_name_snapshot == '即将删除的音色'
    assert preserved.provider_snapshot == 'qwen'
    assert preserved.target_model_snapshot == 'qwen3-tts-vc-2026-01-22'
    session.close()
    connection.close()
    engine.dispose()


def test_synthesize_invalid_source_sample_is_rejected_before_record_creation(
    monkeypatch,
) -> None:
    session = _build_session()
    project = Project(name='失效音色项目', name_key='失效音色项目', status='active')
    session.add(project)
    session.commit()
    session.refresh(project)
    profile = VoiceCloneProfile(
        project_id=project.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model='qwen3-tts-vc-2026-01-22',
        voice_id='invalid-source-voice',
        voice_name='失效样本音色',
        source_clone_sample_id='missing-source-sample',
        status='ready',
    )
    session.add(profile)
    session.commit()
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')

    with pytest.raises(
        voice_cloning_service.VoiceCloneUnavailableError,
        match='该音色已失效并被清理，请重新选择或上传',
    ):
        voice_cloning_service.synthesize_voice_clone_preview(
            session,
            profile.id,
            VoiceClonePreviewRequest(text='测试'),
            project_id=project.id,
        )

    assert session.query(VoiceCloneSynthesisRecord).count() == 0


@pytest.mark.parametrize('file_state', ['missing', 'zero-byte'])
def test_synthesize_ready_clone_sample_without_nonempty_file_is_rejected_before_provider(
    monkeypatch,
    tmp_path: Path,
    file_state: str,
) -> None:
    session = _build_session()
    project = Project(name='样本文件健康检查', name_key='样本文件健康检查', status='active')
    sample_path = tmp_path / f'{file_state}.wav'
    if file_state == 'zero-byte':
        sample_path.touch()
    sample = VoiceCloneSample(
        project_id=project.id,
        display_name='数据库标记为可用的样本',
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        content_sha256='a' * 64,
        file_size_bytes=12,
        duration_ms=1_000,
        sample_rate_hz=24_000,
        channel_count=1,
        clone_readiness_status='ready',
    )
    session.add_all([project, sample])
    session.flush()
    profile = VoiceCloneProfile(
        project_id=project.id,
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_id=f'voice-{file_state}',
        voice_name='文件已失效的音色',
        source_clone_sample_id=sample.id,
        status='ready',
    )
    session.add(profile)
    session.commit()
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'cosyvoice')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        'app.services.voice_cloning._synthesize_provider_voice',
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError('provider must not be called for an unhealthy source sample')
        ),
    )

    with pytest.raises(
        voice_cloning_service.VoiceCloneUnavailableError,
        match='该音色已失效并被清理，请重新选择或上传',
    ):
        synthesize_voice_clone_preview(
            session,
            profile.id,
            VoiceClonePreviewRequest(text='不能发送给 Provider'),
            project_id=project.id,
        )

    assert session.query(VoiceCloneSynthesisRecord).count() == 0


def test_ready_clone_sample_with_persisted_health_facts_is_selectable(
    tmp_path: Path,
) -> None:
    session = _build_session()
    sample_path = tmp_path / 'healthy.wav'
    sample_path.write_bytes(b'RIFF0000WAVE')
    sample = VoiceCloneSample(
        display_name='健康样本',
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        content_sha256='b' * 64,
        file_size_bytes=sample_path.stat().st_size,
        duration_ms=1_000,
        sample_rate_hz=24_000,
        channel_count=1,
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        source_clone_sample_id=sample.id,
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_id='healthy-source-voice',
        voice_name='健康音色',
        status='ready',
    )
    session.add(profile)
    session.commit()

    availability = voice_cloning_service.get_voice_clone_profile_availability(
        session,
        profile,
    )

    assert availability.selectable is True
    assert availability.reason is None


def test_ready_clone_sample_without_trustworthy_persisted_identity_is_unavailable(
    tmp_path: Path,
) -> None:
    session = _build_session()
    sample_path = tmp_path / 'untrusted.wav'
    sample_path.write_bytes(b'RIFF0000WAVE')
    sample = VoiceCloneSample(
        display_name='缺少可信身份的样本',
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        content_sha256=None,
        file_size_bytes=sample_path.stat().st_size,
        duration_ms=1_000,
        sample_rate_hz=24_000,
        channel_count=1,
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        source_clone_sample_id=sample.id,
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_id='untrusted-source-voice',
        voice_name='身份不可信音色',
        status='ready',
    )
    session.add(profile)
    session.commit()

    availability = voice_cloning_service.get_voice_clone_profile_availability(
        session,
        profile,
    )

    assert availability == voice_cloning_service.VoiceCloneProfileAvailability(
        False,
        'source_sample_unavailable',
    )


def test_ready_legacy_voiceprint_sample_with_local_health_metadata_remains_selectable(
    tmp_path: Path,
) -> None:
    session = _build_session()
    person = Person(name='V1 历史人物')
    session.add(person)
    session.flush()
    sample_path = tmp_path / 'legacy-v1.wav'
    sample_path.write_bytes(b'RIFF0000WAVE')
    sample = VoiceprintSample(
        person_id=person.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_audio_local_path=str(sample_path),
        duration_ms=1_000,
        clone_sample_rate_hz=24_000,
        clone_channel_count=1,
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        person_id=person.id,
        source_sample_id=sample.id,
        source_audio_local_path=str(sample_path),
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_id='legacy-v1-voice',
        voice_name='V1 历史音色',
        status='ready',
    )
    session.add(profile)
    session.commit()

    availability = voice_cloning_service.get_voice_clone_profile_availability(
        session,
        profile,
    )

    assert availability.selectable is True


def test_synthesize_unsupported_provider_model_is_rejected_before_record_creation(
    monkeypatch,
) -> None:
    session = _build_session()
    project = Project(name='不支持模型项目', name_key='不支持模型项目', status='active')
    sample = VoiceCloneSample(
        project_id=project.id,
        display_name='有效参考样本',
        original_filename='valid-reference.wav',
        audio_local_path='/tmp/valid-reference.wav',
        clone_readiness_status='ready',
    )
    session.add_all([project, sample])
    session.flush()
    profile = VoiceCloneProfile(
        project_id=project.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model='retired-qwen-model',
        voice_id='unsupported-model-voice',
        voice_name='过期模型音色',
        source_clone_sample_id=sample.id,
        status='ready',
    )
    session.add(profile)
    session.commit()
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')

    with pytest.raises(
        voice_cloning_service.VoiceCloneUnavailableError,
        match='该音色已失效并被清理，请重新选择或上传',
    ):
        voice_cloning_service.synthesize_voice_clone_preview(
            session,
            profile.id,
            VoiceClonePreviewRequest(text='测试'),
            project_id=project.id,
        )

    assert session.query(VoiceCloneSynthesisRecord).count() == 0


def test_synthesize_legacy_profile_normalizes_blank_person_id(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = _build_session()
    sample = VoiceCloneSample(
        display_name='历史音色参考样本',
        original_filename='legacy.wav',
        **_ready_clone_sample_facts(tmp_path / 'legacy.wav'),
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        person_id='',
        source_clone_sample_id=sample.id,
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_id='legacy-voice-id',
        voice_name='历史音色',
        status='ready',
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)

    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        'app.services.voice_cloning._synthesize_provider_voice',
        lambda **_kwargs: {'output': {'audio': {'url': 'https://example.com/legacy.mp3'}}},
    )

    with TemporaryDirectory() as tmp_dir:
        preview_path = Path(tmp_dir) / 'legacy-preview.mp3'
        preview_path.write_bytes(b'MP3DATA')
        monkeypatch.setattr(
            'app.services.voice_cloning._download_preview_audio',
            lambda *_args, **_kwargs: preview_path,
        )
        record = synthesize_voice_clone_preview(
            session,
            profile.id,
            VoiceClonePreviewRequest(text='历史音色试听'),
        )

    assert record.status == 'ready'
    assert record.person_id is None


def test_legacy_qwen_synthesis_adapter_preserves_provider_contract(monkeypatch) -> None:
    from app.services.qwen_voice_clone import synthesize_qwen_voice
    captured = {}
    def post(url, **kwargs):
        captured.update(kwargs['json'])
        return httpx.Response(200, json={'output': {'audio': {'url': 'https://example.test/output.wav'}}})
    monkeypatch.setattr(httpx, 'post', post)
    response = synthesize_qwen_voice(target_model='qwen3-tts-vc-2026-01-22',
        voice='legacy-qwen', text='保留适配器', language_hints=['zh'])
    assert response['output']['audio']['url'] == 'https://example.test/output.wav'
    assert captured == {'model': 'qwen3-tts-vc-2026-01-22', 'input': {
        'voice': 'legacy-qwen', 'text': '保留适配器', 'language_type': 'Chinese',
    }}


def test_synthesize_voice_clone_preview_falls_back_when_instruction_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = _build_session()
    sample = VoiceCloneSample(
        display_name='情绪测试参考样本',
        original_filename='emotion.wav',
        **_ready_clone_sample_facts(tmp_path / 'emotion.wav'),
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        source_clone_sample_id=sample.id,
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_name='情绪测试音色',
        voice_id='voice-emotion',
        status='ready',
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)

    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'cosyvoice')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    calls: list[str | None] = []

    def fake_synthesize_cosyvoice_voice(**kwargs):
        calls.append(kwargs.get('instruction'))
        if kwargs.get('instruction'):
            raise RuntimeError('instruction rejected')
        return {'output': {'audio': {'url': 'https://example.com/fallback.mp3'}}}

    monkeypatch.setattr('app.services.voice_cloning.synthesize_cosyvoice_voice', fake_synthesize_cosyvoice_voice)

    with TemporaryDirectory() as tmp_dir:
        preview_path = Path(tmp_dir) / 'fallback.mp3'
        preview_path.write_bytes(b'MP3DATA')
        monkeypatch.setattr('app.services.voice_cloning._download_preview_audio', lambda *_args, **_kwargs: preview_path)

        record = synthesize_voice_clone_preview(
            session,
            profile.id,
            VoiceClonePreviewRequest(text='请用更开心的方式说这句话。', instruction='开朗活泼，语速稍快'),
        )

    assert record.status == 'ready'
    assert record.output_audio_public_url == 'https://example.com/fallback.mp3'
    assert calls[1] is None
    assert '只朗读输入文本' in str(calls[0] or '')
    assert '开朗活泼，语速稍快' in str(calls[0] or '')
    assert '语气提示调用失败' in (record.provider_message or '')


def test_synthesize_voice_clone_preview_adds_strict_text_instruction_by_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = _build_session()
    sample = VoiceCloneSample(
        display_name='严格文案参考样本',
        original_filename='strict.wav',
        **_ready_clone_sample_facts(tmp_path / 'strict.wav'),
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        source_clone_sample_id=sample.id,
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_name='严格文案音色',
        voice_id='voice-strict-text',
        status='ready',
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)

    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'cosyvoice')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    captured: dict[str, object] = {}

    def fake_synthesize_cosyvoice_voice(**kwargs):
        captured.update(kwargs)
        return {'output': {'audio': {'url': 'https://example.com/strict.wav'}}}

    monkeypatch.setattr('app.services.voice_cloning.synthesize_cosyvoice_voice', fake_synthesize_cosyvoice_voice)

    with TemporaryDirectory() as tmp_dir:
        preview_path = Path(tmp_dir) / 'strict.wav'
        preview_path.write_bytes(b'WAVDATA')
        monkeypatch.setattr('app.services.voice_cloning._download_preview_audio', lambda *_args, **_kwargs: preview_path)

        record = synthesize_voice_clone_preview(
            session,
            profile.id,
            VoiceClonePreviewRequest(text='中国好团队 科创赢未来'),
        )

    assert record.status == 'ready'
    assert '只朗读输入文本' in str(captured.get('instruction') or '')
    assert '不添加样本里的其他话' in str(captured.get('instruction') or '')


def test_retired_qwen_profile_rejects_generation_before_record_and_cloud(monkeypatch, tmp_path) -> None:
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    profile = VoiceCloneProfile(source_clone_sample_id=sample.id, provider='qwen',
        provider_model_family='qwen', target_model='qwen3-tts-vc-retired',
        voice_name='旧 Qwen 模型音色', voice_id='retired-qwen-voice', status='ready')
    session.add(profile); session.commit()
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(voice_cloning_service, '_synthesize_provider_voice',
        lambda **kwargs: pytest.fail('retired model reached generation'))
    with pytest.raises(voice_cloning_service.VoiceCloneUnavailableError):
        synthesize_voice_clone_preview(session, profile.id, VoiceClonePreviewRequest(text='旧模型生成'))
    assert session.query(VoiceCloneSynthesisRecord).count() == 0


def test_guess_audio_suffix_prefers_wav_bytes_over_wrong_content_type() -> None:
    wav_bytes = b'RIFF\x24\x00\x00\x00WAVEfmt '

    assert _guess_audio_suffix('https://example.com/output', 'audio/mpeg', wav_bytes) == '.wav'


def test_download_preview_audio_reencodes_to_premiere_friendly_wav(monkeypatch) -> None:
    source_audio = io.BytesIO()
    with wave.open(source_audio, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b'\x00\x00' * 2400)

    def fake_get(url: str, **_kwargs):
        return httpx.Response(
            200,
            content=source_audio.getvalue(),
            headers={'content-type': 'audio/wav'},
            request=httpx.Request('GET', url),
        )

    with TemporaryDirectory() as tmp_dir:
        monkeypatch.setattr('app.services.voice_cloning.settings', SimpleNamespace(audio_dir_path=Path(tmp_dir)))
        monkeypatch.setattr('app.services.voice_cloning.httpx.get', fake_get)

        output_path = _download_preview_audio('https://example.com/output.wav', 'record-001')

        assert output_path == Path(tmp_dir) / 'voice-clone-preview-record-001.wav'
        assert not (Path(tmp_dir) / 'voice-clone-preview-record-001-source.wav').exists()
        with wave.open(str(output_path), 'rb') as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 48000
            assert 0 < wav_file.getnframes() <= 6000


def test_record_audio_download_filename_uses_actual_local_audio_suffix() -> None:
    record = VoiceCloneSynthesisRecord(
        voice_clone_profile_id='profile-id',
        target_model='qwen3-tts-vc-2026-01-22',
        input_text='中国好团队 科创赢未来',
        output_audio_local_path='/tmp/generated-preview.wav',
        status='ready',
    )

    assert _build_record_audio_filename(record) == '中国好团队科创赢未来.wav'


def test_delete_voice_clone_synthesis_record_removes_audio_file(monkeypatch) -> None:
    session = _build_session()
    profile = VoiceCloneProfile(
        provider='qwen',
        provider_model_family='qwen',
        target_model='qwen3-tts-vc-2026-01-22',
        voice_name='测试音色',
        status='ready',
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)

    with TemporaryDirectory() as tmp_dir:
        audio_path = Path(tmp_dir) / 'generated.wav'
        audio_path.write_bytes(b'WAVDATA')
        profile.preview_audio_local_path = str(audio_path)
        record = VoiceCloneSynthesisRecord(
            voice_clone_profile_id=profile.id,
            target_model=profile.target_model,
            input_text='测试生成',
            output_audio_local_path=str(audio_path),
            status='ready',
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        record_id = record.id

        delete_voice_clone_synthesis_record(session, record)

        assert session.get(VoiceCloneSynthesisRecord, record_id) is None
        assert audio_path.exists() is False
        session.refresh(profile)
        assert profile.preview_audio_local_path is None


def test_synthesis_record_audio_preview_does_not_delete_record(monkeypatch) -> None:
    with TemporaryDirectory() as tmp_dir:
        session_factory = _build_session_factory(f"sqlite:///{Path(tmp_dir) / 'voice-cloning.db'}")
        monkeypatch.setattr('app.api.v1.voice_cloning.SessionLocal', session_factory)
        session = session_factory()
        project = Project(name="Audio records", name_key="audio-records", status="active")
        session.add(project)
        session.commit()
        profile = VoiceCloneProfile(
            project_id=project.id,
            provider='qwen',
            provider_model_family='qwen',
            target_model='qwen3-tts-vc-2026-01-22',
            voice_name='测试音色',
            status='ready',
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)
        audio_path = Path(tmp_dir) / 'generated.wav'
        audio_path.write_bytes(b'WAVDATA')
        record = VoiceCloneSynthesisRecord(
            project_id=project.id,
            voice_clone_profile_id=profile.id,
            target_model=profile.target_model,
            input_text='测试生成',
            output_audio_local_path=str(audio_path),
            status='ready',
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        record_id = record.id

        response = get_synthesis_record_audio(record_id, project_id=project.id)

        assert response.status_code == 200
        assert session.get(VoiceCloneSynthesisRecord, record_id) is not None
        assert audio_path.exists() is True
        session.close()


def test_legacy_consume_download_retains_record_and_audio(monkeypatch) -> None:
    with TemporaryDirectory() as tmp_dir:
        session_factory = _build_session_factory(f"sqlite:///{Path(tmp_dir) / 'voice-cloning.db'}")
        monkeypatch.setattr('app.api.v1.voice_cloning.SessionLocal', session_factory)
        session = session_factory()
        project = Project(name="Audio records", name_key="audio-records", status="active")
        session.add(project)
        session.commit()
        profile = VoiceCloneProfile(
            project_id=project.id,
            provider='qwen',
            provider_model_family='qwen',
            target_model='qwen3-tts-vc-2026-01-22',
            voice_name='测试音色',
            status='ready',
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)
        audio_path = Path(tmp_dir) / 'generated.wav'
        audio_path.write_bytes(b'WAVDATA')
        profile.preview_audio_local_path = str(audio_path)
        record = VoiceCloneSynthesisRecord(
            project_id=project.id,
            voice_clone_profile_id=profile.id,
            target_model=profile.target_model,
            input_text='测试生成',
            output_audio_local_path=str(audio_path),
            status='ready',
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        record_id = record.id

        response = get_synthesis_record_audio(record_id, project_id=project.id, consume=True)
        assert response.background is None

        session.expire_all()
        assert session.get(VoiceCloneSynthesisRecord, record_id) is not None
        assert audio_path.exists() is True
        session.refresh(profile)
        assert profile.preview_audio_local_path == str(audio_path)
        session.close()


@pytest.mark.parametrize('endpoint', ['/api/v1/voice-cloning/samples', '/api/v2/projects/{project_id}/voice-cloning/samples'])
def test_disabled_upload_rejects_before_file_or_identity_work(voice_clone_upload_api, monkeypatch, endpoint):
    client, project, upload_dir, library_dir, session_factory = voice_clone_upload_api
    monkeypatch.setattr(settings, 'voice_cloning_enabled', False)
    response = client.post(endpoint.format(project_id=project.id), data={'display_name': 'test'},
                           files={'file': ('voice.wav', b'RIFF-disabled', 'audio/wav')})
    assert response.status_code == 400
    assert '未开启' in response.json()['detail']
    assert list(upload_dir.iterdir()) == []
    assert list(library_dir.iterdir()) == []
    with session_factory() as session:
        assert session.query(VoiceCloneIdentity).count() == 0


def test_catalog_has_real_model_capabilities_and_honors_disable(monkeypatch):
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    option = get_voice_cloning_provider_status().models[0]
    assert option.model == 'qwen-audio-3.1-tts-next' and option.capabilities.instruction
    assert option.available and option.constraints.sample_max_seconds == 30
    assert option.constraints.instruction_han_weight == 1
    monkeypatch.setattr(settings, 'voice_cloning_enabled', False)
    assert all(not item.available and item.unavailable_reason for item in get_voice_cloning_provider_status().models)


def test_flash_is_selectable_alongside_next_with_its_own_instruction_limits(monkeypatch):
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_region', 'cn-beijing')
    status = get_voice_cloning_provider_status()
    assert {option.model for option in status.models} == {'qwen-audio-3.1-tts-next', 'cosyvoice-v3.5-flash'}
    flash = next(option for option in status.models if option.model == 'cosyvoice-v3.5-flash')
    assert flash.available and flash.capabilities.instruction
    assert flash.constraints.instruction_han_weight == 2
    assert flash.constraints.instruction_provider_max_weight == 100
    assert any(provider.provider == 'cosyvoice' and provider.target_model == flash.model for provider in status.providers)


def _model_test_sample(session, tmp_path):
    facts = _ready_clone_sample_facts(tmp_path / 'model-sample.wav')
    facts['duration_ms'] = 12000
    sample = VoiceCloneSample(display_name='model test', original_filename='model.wav',
                              audio_public_url='https://example.test/model.wav', **facts)
    session.add(sample)
    session.commit()
    return sample


@pytest.mark.parametrize('reuse', [False, True])
def test_disabled_creation_rejects_even_existing_profile(monkeypatch, tmp_path, reuse):
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    if reuse:
        session.add(VoiceCloneProfile(source_clone_sample_id=sample.id, provider='cosyvoice',
                    provider_model_family='cosyvoice', target_model='cosyvoice-v3.5-plus',
                    voice_name='existing', voice_id='cloud-voice', status='ready'))
        session.commit()
    monkeypatch.setattr(settings, 'voice_cloning_enabled', False)
    with pytest.raises(RuntimeError, match='未开启'):
        create_voice_clone_profile(session, VoiceCloneProfileCreateRequest(
            clone_sample_id=sample.id, voice_name='test', provider='cosyvoice'))
    assert session.query(VoiceCloneProfile).count() == int(reuse)


@pytest.mark.parametrize('target_model', ['unknown-model', 'qwen3-tts-vc-2026-01-22'])
def test_creation_rejects_unsupported_exact_model_pair_before_provider(monkeypatch, tmp_path, target_model):
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(voice_cloning_service, 'create_cosyvoice_voice',
                        lambda **kwargs: pytest.fail('unsupported model reached provider'))
    with pytest.raises(RuntimeError, match='模型'):
        create_voice_clone_profile(session, VoiceCloneProfileCreateRequest(
            clone_sample_id=sample.id, voice_name='test', provider='cosyvoice', target_model=target_model))
    assert session.query(VoiceCloneProfile).count() == 0


def test_cosy_creation_waits_for_query_ok_and_detail_refreshes(monkeypatch, tmp_path, voice_clone_upload_api):
    client, project, _, _, session_factory = voice_clone_upload_api
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(voice_cloning_service, 'create_cosyvoice_voice', lambda **kwargs: {'output': {'voice_id': 'cloud-voice'}})
    statuses = iter(['DEPLOYING', 'OK'])
    monkeypatch.setattr(voice_cloning_service, 'query_cosyvoice_voice',
                        lambda voice_id: {'output': {'voice_id': voice_id, 'status': next(statuses)}}, raising=False)
    with session_factory() as session:
        sample = _model_test_sample(session, tmp_path)
        profile = create_voice_clone_profile(session, VoiceCloneProfileCreateRequest(
            clone_sample_id=sample.id, voice_name='test', provider='cosyvoice'), project_id=project.id)
        assert profile.voice_id == 'cloud-voice'
        assert profile.status == 'creating'
        response = client.get(f'/api/v2/projects/{project.id}/voice-cloning/profiles/{profile.id}')
        assert response.status_code == 200
        assert response.json()['status'] == 'ready'
        session.refresh(profile)
        assert profile.status == 'ready'


def test_instruction_overflow_rejects_before_record_and_provider(monkeypatch, tmp_path):
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    profile = VoiceCloneProfile(source_clone_sample_id=sample.id, provider='cosyvoice',
                provider_model_family='cosyvoice', target_model='cosyvoice-v3.5-plus',
                voice_name='existing', voice_id='cloud-voice', status='ready')
    session.add(profile)
    session.commit()
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(voice_cloning_service, 'synthesize_cosyvoice_voice',
                        lambda **kwargs: pytest.fail('overflow reached provider'))
    with pytest.raises(RuntimeError, match='57'):
        synthesize_voice_clone_preview(session, profile.id, VoiceClonePreviewRequest(text='test', instruction='汉' * 29))
    assert session.query(VoiceCloneSynthesisRecord).count() == 0


def test_cosy_adapter_rejects_overflow_instead_of_clipping(monkeypatch):
    from app.services.cosyvoice_voice_clone import synthesize_cosyvoice_voice, CosyVoiceCloneError
    monkeypatch.setattr('app.services.cosyvoice_voice_clone.httpx.post', lambda *args, **kwargs: pytest.fail('overflow reached HTTP'))
    with pytest.raises(CosyVoiceCloneError, match='100'):
        synthesize_cosyvoice_voice(target_model='cosyvoice-v3.5-plus', voice_id='test', text='test', instruction='汉' * 51)


def test_cosy_query_uses_bounded_timeout(monkeypatch):
    from app.services.cosyvoice_voice_clone import query_cosyvoice_voice
    def post(*args, **kwargs):
        assert kwargs['timeout'] <= 10
        return SimpleNamespace(status_code=200, json=lambda: {'output': {'status': 'OK'}})
    monkeypatch.setattr('app.services.cosyvoice_voice_clone.httpx.post', post)
    assert query_cosyvoice_voice('test')['output']['status'] == 'OK'


@pytest.mark.parametrize('field,value,provider', [('duration_ms', 4000, 'cosyvoice'), ('duration_ms', 61000, 'cosyvoice'), ('sample_rate_hz', 16000, 'qwen'), ('channel_count', 2, 'qwen')])
def test_model_sample_limits_reject_before_creation(monkeypatch, tmp_path, field, value, provider):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    setattr(sample, field, value)
    session.commit()
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(voice_cloning_service, '_create_provider_voice', lambda **kwargs: pytest.fail('invalid sample reached provider'))
    with pytest.raises(RuntimeError, match='样本'):
        create_voice_clone_profile(session, VoiceCloneProfileCreateRequest(clone_sample_id=sample.id, voice_name='test', provider=provider))
    assert session.query(VoiceCloneProfile).count() == 0


@pytest.mark.parametrize('duration,expected', [(4.0, 201), (61.0, 400)])
def test_uploaded_sample_enforces_common_limits_and_allows_short_qwen_audio(voice_clone_upload_api, monkeypatch, duration, expected):
    client, project, _, _, _ = voice_clone_upload_api
    monkeypatch.setattr(voice_cloning_service, 'get_media_duration_seconds', lambda path: duration)
    result = client.post(f'/api/v2/projects/{project.id}/voice-cloning/samples',
                         data={'display_name': 'test'}, files={'file': ('sample.wav', b'RIFF-audio', 'audio/wav')})
    assert result.status_code == expected
    if expected == 201:
        assert result.json()['clone_readiness_status'] == 'ready'


def test_cosy_v3_flash_instruction_capability_is_sent_intact(monkeypatch):
    from app.services.cosyvoice_voice_clone import synthesize_cosyvoice_voice
    def post(*args, **kwargs):
        assert kwargs['json']['input']['instruction'] == '清晰自然'
        return SimpleNamespace(status_code=200, json=lambda: {'output': {'audio': {'url': 'https://example.test/a.wav'}}})
    monkeypatch.setattr('app.services.cosyvoice_voice_clone.httpx.post', post)
    synthesize_cosyvoice_voice(target_model='cosyvoice-v3-flash', voice_id='voice', text='test', instruction='清晰自然')


@pytest.mark.parametrize('cloud_status,expected', [('UNDEPLOYED', 'failed'), ('DEPLOYING', 'creating'), ('', 'creating')])
def test_cosy_pending_detail_keeps_cloud_state_and_never_recreates(voice_clone_upload_api, monkeypatch, cloud_status, expected):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    client, project, _, _, session_factory = voice_clone_upload_api
    monkeypatch.setattr(voice_cloning_service, 'query_cosyvoice_voice', lambda voice_id: {'output': {'status': cloud_status}})
    monkeypatch.setattr(voice_cloning_service, 'create_cosyvoice_voice', lambda **kwargs: pytest.fail('refresh must not create'))
    with session_factory() as session:
        profile = VoiceCloneProfile(project_id=project.id, provider='cosyvoice', provider_model_family='cosyvoice',
                    target_model='cosyvoice-v3.5-plus', voice_name='pending', voice_id='cloud-voice', status='creating')
        session.add(profile)
        session.commit()
        result = client.get(f'/api/v2/projects/{project.id}/voice-cloning/profiles/{profile.id}')
        assert result.json()['status'] == expected
        assert result.json()['voice_id'] == 'cloud-voice'


def test_cosy_query_timeout_keeps_id_retryable_and_disabled_detail_does_not_query(voice_clone_upload_api, monkeypatch):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    client, project, _, _, session_factory = voice_clone_upload_api
    def timeout(voice_id):
        raise httpx.ReadTimeout('test timeout')
    monkeypatch.setattr(voice_cloning_service, 'query_cosyvoice_voice', timeout)
    with session_factory() as session:
        profile = VoiceCloneProfile(project_id=project.id, provider='cosyvoice', provider_model_family='cosyvoice',
                    target_model='cosyvoice-v3.5-plus', voice_name='pending', voice_id='cloud-voice', status='creating')
        session.add(profile)
        session.commit()
        url = f'/api/v2/projects/{project.id}/voice-cloning/profiles/{profile.id}'
        result = client.get(url)
        assert result.json()['status'] == 'creating'
        assert result.json()['voice_id'] == 'cloud-voice'
        assert '暂未确认' in result.json()['provider_message']
        monkeypatch.setattr(settings, 'voice_cloning_enabled', False)
        monkeypatch.setattr(voice_cloning_service, 'query_cosyvoice_voice', lambda voice_id: pytest.fail('disabled queried cloud'))
        assert client.get(url).json()['status'] == 'creating'


def test_instruction_budget_counts_extended_han_and_cannot_bypass_with_hidden_phrase():
    for instruction in ['只朗读输入文本' + 'a' * 50, '𰀀' * 29]:
        with pytest.raises(RuntimeError, match='57'):
            voice_cloning_service._resolve_synthesis_instruction(provider='cosyvoice', target_model='cosyvoice-v3.5-plus', instruction=instruction)
    instruction, _ = voice_cloning_service._resolve_synthesis_instruction(provider='cosyvoice', target_model='cosyvoice-v3.5-plus', instruction='a' * 57)
    assert instruction.endswith('a' * 57)


def test_create_response_timeout_keeps_uncertain_profile_and_retry_never_creates_twice(monkeypatch, tmp_path):
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    calls = []
    def accepted_but_response_lost(**kwargs):
        calls.append(kwargs)
        raise httpx.ReadTimeout('provider accepted request but response was lost')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(voice_cloning_service, '_create_provider_voice', accepted_but_response_lost)
    payload = VoiceCloneProfileCreateRequest(clone_sample_id=sample.id, voice_name='test', provider='cosyvoice')
    first = voice_cloning_service.find_or_create_voice_clone_profile(session, payload)
    second = voice_cloning_service.find_or_create_voice_clone_profile(session, payload)
    assert first.profile.status == 'creating'
    assert '未确认' in first.profile.provider_message
    assert second.reused_existing and second.profile.id == first.profile.id
    assert len(calls) == 1
    assert session.query(VoiceCloneProfile).count() == 1


@pytest.mark.parametrize('remove_row', [False, True])
def test_late_create_queues_cloud_voice_without_restoring_expired_or_deleted_profile(monkeypatch, tmp_path, remove_row):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    from datetime import datetime, timedelta
    from app.services import voice_retention
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    now = datetime.utcnow()
    clock = [now]
    monkeypatch.setattr(voice_retention, 'utc_now', lambda: clock[0])
    sample.created_at = now - timedelta(hours=72) + timedelta(seconds=1)
    session.commit()
    def late_create(**kwargs):
        if remove_row:
            session.execute(delete(VoiceCloneProfile)); session.commit()
        else:
            clock[0] += timedelta(seconds=2)
        return {'output': {'voice_id': 'late-cloud-id'}}
    monkeypatch.setattr(voice_cloning_service, '_create_provider_voice', late_create)
    with pytest.raises(voice_cloning_service.VoiceAssetExpiredError):
        voice_cloning_service.create_voice_clone_profile(session, VoiceCloneProfileCreateRequest(
            clone_sample_id=sample.id, voice_name='late', provider='cosyvoice'))
    task = session.query(VoiceCloneDeletionTask).one()
    assert task.kind == 'provider' and task.voice_id == 'late-cloud-id'
    assert all(profile.voice_id is None for profile in session.query(VoiceCloneProfile))


@pytest.mark.parametrize('outcome', ['within_grace', 'after_grace', 'record_deleted'])
def test_synthesis_result_is_independent_within_grace_and_late_outputs_are_queued(monkeypatch, tmp_path, outcome):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    from datetime import datetime, timedelta
    from app.services import voice_retention
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    start = datetime.utcnow()
    clock = [start]
    monkeypatch.setattr(voice_retention, 'utc_now', lambda: clock[0])
    sample.created_at = start - timedelta(hours=72) + timedelta(seconds=1)
    profile = VoiceCloneProfile(source_clone_sample_id=sample.id, voice_name='voice', voice_id='cloud-id',
        provider='cosyvoice', target_model=settings.voice_cloning_cosyvoice_target_model, status='ready')
    session.add(profile); session.commit()
    profile_id = profile.id
    output = tmp_path / 'late-output.wav'
    output.write_bytes(b'RIFF-late-output')
    def synthesize(**kwargs):
        record = session.query(VoiceCloneSynthesisRecord).one()
        if outcome == 'record_deleted':
            session.delete(record); session.commit()
        elif outcome == 'after_grace':
            clock[0] = record.created_at + timedelta(minutes=15)
        else:
            clock[0] += timedelta(seconds=2)
        return {'output': {'audio': {'url': 'https://provider.example/output.wav'}}}
    monkeypatch.setattr(voice_cloning_service, '_synthesize_provider_voice', synthesize)
    monkeypatch.setattr(voice_cloning_service, '_download_preview_audio', lambda *args: output)
    if outcome == 'within_grace':
        record = voice_cloning_service.synthesize_voice_clone_preview(session, profile_id, VoiceClonePreviewRequest(text='saved'))
        assert record.status == 'ready' and record.voice_clone_profile_id is None
        assert record.output_audio_local_path == str(output)
        assert session.query(VoiceCloneDeletionTask).count() == 0
    else:
        with pytest.raises(voice_cloning_service.VoiceAssetExpiredError):
            voice_cloning_service.synthesize_voice_clone_preview(session, profile_id, VoiceClonePreviewRequest(text='saved'))
        assert session.query(VoiceCloneDeletionTask).filter_by(kind='local').one().local_path == str(output.resolve())
        assert not any(record.status == 'ready' for record in session.query(VoiceCloneSynthesisRecord))
    assert session.get(VoiceCloneProfile, profile_id).preview_audio_local_path is None
    assert output.exists()  # API queues owned outputs; this test never executes deletion.


def test_project_records_remain_readable_after_profile_expiry_and_old_records_return_404(monkeypatch, tmp_path):
    from datetime import datetime, timedelta
    from app.services import voice_retention
    from app.api.v2 import voice_cloning as route
    now = datetime.utcnow()
    monkeypatch.setattr(voice_retention, 'utc_now', lambda: now)
    factory = _build_session_factory(f'sqlite:///{tmp_path / "project-records.db"}')
    session = factory()
    project = Project(name='records', name_key='records', status='active')
    session.add(project); session.flush()
    profile = VoiceCloneProfile(project_id=project.id, voice_name='expired', provider='qwen', target_model='test', created_at=now - timedelta(hours=72))
    session.add(profile); session.flush()
    records = [VoiceCloneSynthesisRecord(project_id=project.id, voice_clone_profile_id=profile.id, voice_name_snapshot='saved',
        target_model='test', input_text='saved', status='ready', created_at=now - timedelta(hours=hours)) for hours in (1, 72)]
    session.add_all(records); session.commit()
    project_id, fresh_id, old_id = project.id, records[0].id, records[1].id
    monkeypatch.setattr(route, 'SessionLocal', factory)
    app = FastAPI(); app.include_router(route.router, prefix='/api/v2')
    with TestClient(app) as client:
        base = f'/api/v2/projects/{project_id}/voice-cloning/synthesis-records'
        response = client.get(base)
        assert response.status_code == 200
        assert [item['id'] for item in response.json()] == [fresh_id]
        assert response.json()[0]['expires_at'].endswith('Z')
        assert client.get(f'{base}/{fresh_id}').status_code == 200
        assert client.get(f'{base}/{old_id}').status_code == 404


def test_expired_create_returns_structured_410_before_calling_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    from datetime import datetime, timedelta
    from app.api.v1 import voice_cloning as v1
    from app.api.v2 import voice_cloning as v2
    factory = _build_session_factory(f'sqlite:///{tmp_path / "expired-create.db"}')
    session = factory()
    sample = _model_test_sample(session, tmp_path)
    project = Project(name='expired-create', name_key='expired-create', status='active')
    session.add(project); session.flush()
    sample.project_id = project.id
    sample.created_at = datetime.utcnow() - timedelta(hours=72)
    session.commit()
    sample_id, project_id = sample.id, project.id
    monkeypatch.setattr(v1, 'SessionLocal', factory)
    monkeypatch.setattr(v2, 'SessionLocal', factory)
    monkeypatch.setattr(voice_cloning_service, '_create_provider_voice', lambda **kwargs: pytest.fail('expired sample reached provider'))
    app = FastAPI(); app.include_router(v1.router, prefix='/api/v1/voice-cloning'); app.include_router(v2.router, prefix='/api/v2')
    with TestClient(app) as client:
        for path in ('/api/v1/voice-cloning/profiles', f'/api/v2/projects/{project_id}/voice-cloning/profiles'):
            response = client.post(path, json={'clone_sample_id': sample_id, 'voice_name': 'expired', 'provider': 'cosyvoice'})
            assert response.status_code == 410
            assert response.json()['detail']['code'] == 'voice_asset_expired'


def test_expired_profiles_leave_an_active_identity_with_true_zero_version_count(monkeypatch, tmp_path):
    from datetime import datetime, timedelta
    from app.services import voice_retention
    from app.services.voice_clone_library import list_voice_clone_library, list_voice_clone_group_profiles, list_voice_clone_sample_page
    now = datetime.utcnow()
    monkeypatch.setattr(voice_retention, 'utc_now', lambda: now)
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    project = Project(name='current-identity', name_key='current-identity', status='active')
    identity = VoiceCloneIdentity(display_name='current', created_at=now)
    session.add_all([project, identity]); session.flush()
    sample.identity_id = identity.id
    profile = VoiceCloneProfile(source_clone_sample_id=sample.id, identity_id=identity.id, voice_name='expired-profile',
        provider='qwen', target_model=settings.voice_cloning_qwen_target_model, status='ready', voice_id='old', created_at=now - timedelta(hours=72))
    session.add(profile); session.commit()
    page = list_voice_clone_library(session, project.id)
    assert page.total == 1 and page.available_group_count == 0 and page.exception_group_count == 1
    assert page.groups[0].version_count == 0 and page.groups[0].recommended_profile is None
    assert list_voice_clone_group_profiles(session, project.id, identity.id) == []
    samples = list_voice_clone_sample_page(session, project.id)
    assert samples.total == 1 and not samples.items[0].in_use and samples.items[0].profile_count == 0


@pytest.mark.parametrize('record_deleted', [False, True])
def test_failed_download_queues_owned_output_even_when_processing_record_was_deleted(monkeypatch, tmp_path, record_deleted):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    profile = VoiceCloneProfile(source_clone_sample_id=sample.id, voice_name='voice', voice_id='cloud-id',
        provider='cosyvoice', target_model=settings.voice_cloning_cosyvoice_target_model, status='ready')
    session.add(profile); session.commit()
    monkeypatch.setattr(settings, 'local_audio_dir', str(tmp_path))
    monkeypatch.setattr(voice_cloning_service, 'owned_oss_object_key', lambda url: 'owned/output.wav' if url == 'https://owned.example/output.wav' else None)
    monkeypatch.setattr(voice_cloning_service, '_synthesize_provider_voice', lambda **kwargs: {'output': {'audio': {'url': 'https://owned.example/output.wav'}}})
    def failed_download(url, record_id):
        (settings.audio_dir_path / f'voice-clone-preview-{record_id}.wav').write_bytes(b'partial output')
        if record_deleted:
            session.execute(delete(VoiceCloneSynthesisRecord)); session.commit()
        raise httpx.ReadTimeout('download interrupted')
    monkeypatch.setattr(voice_cloning_service, '_download_preview_audio', failed_download)
    if record_deleted:
        with pytest.raises(voice_cloning_service.VoiceAssetExpiredError):
            voice_cloning_service.synthesize_voice_clone_preview(session, profile.id, VoiceClonePreviewRequest(text='test'))
    else:
        assert voice_cloning_service.synthesize_voice_clone_preview(session, profile.id, VoiceClonePreviewRequest(text='test')).status == 'failed'
    assert session.query(VoiceCloneDeletionTask).filter_by(kind='oss').one().oss_object_key == 'owned/output.wav'
    local_task = session.query(VoiceCloneDeletionTask).filter_by(kind='local').one()
    assert Path(local_task.local_path).read_bytes() == b'partial output'


def test_expired_samples_do_not_consume_identity_slots_and_expired_review_cannot_restore_identity(monkeypatch, tmp_path):
    from datetime import datetime, timedelta
    from app.services.voice_clone_identity_review import list_pending_identity_reviews
    session = _build_session()
    now = datetime.utcnow()
    identity = VoiceCloneIdentity(display_name='current', reference_embedding=[1.0, 0.0], embedding_model_id='test', created_at=now)
    session.add(identity); session.flush()
    old_samples = [VoiceCloneSample(display_name='old', original_filename=f'old-{index}.wav', audio_local_path=f'/old-{index}',
        identity_id=identity.id, created_at=now - timedelta(hours=72)) for index in range(3)]
    session.add_all(old_samples); session.commit()
    incoming = tmp_path / 'fresh.wav'; incoming.write_bytes(b'fresh reference')
    monkeypatch.setattr(identity_service, 'extract_voiceprint_embedding', lambda path: SimpleNamespace(embedding=[1.0, 0.0], provider_model_id='test', provider_name='test', provider_message='ok'))
    monkeypatch.setattr(voice_cloning_service, 'get_media_duration_seconds', lambda path: 12)
    monkeypatch.setattr(voice_cloning_service, 'ensure_sample_public_url', lambda path: 'https://provider.example/sample.wav')
    saved = voice_cloning_service.find_or_create_voice_clone_sample(session, 'current', incoming, incoming.name)
    assert saved.sample_slots_remaining == 2 and saved.identity_id == identity.id
    assert len(voice_cloning_service.list_voice_clone_samples(session)) == 1
    review = session.query(VoiceCloneIdentityReview).one()
    identity.created_at = now - timedelta(hours=72)
    session.commit()
    assert list_pending_identity_reviews(session) == []
    with pytest.raises(LookupError, match='过期'):
        resolve_identity_review(session, review.id, 'release', 'tester', 'expired')
    assert session.query(VoiceCloneIdentity).count() == 1


@pytest.mark.parametrize('configured_provider,expected_provider,expected_model', [
    ('qwen', 'qwen_audio_next', 'qwen-audio-3.1-tts-next'),
    ('qwen_audio', 'qwen_audio_next', 'qwen-audio-3.1-tts-next'),
    ('cosyvoice', 'qwen_audio_next', 'qwen-audio-3.1-tts-next'),
    ('', 'qwen_audio_next', 'qwen-audio-3.1-tts-next'),
    ('retired-provider', 'qwen_audio_next', 'qwen-audio-3.1-tts-next'),
])
def test_current_catalog_ignores_old_targets_and_preserves_valid_provider_preference(
    voice_clone_upload_api, monkeypatch, tmp_path, configured_provider, expected_provider, expected_model,
):
    client, project, _, _, session_factory = voice_clone_upload_api
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_provider', configured_provider)
    monkeypatch.setattr(settings, 'voice_cloning_cosyvoice_target_model', 'cosyvoice-v2')
    monkeypatch.setattr(settings, 'voice_cloning_qwen_target_model', 'qwen3-tts-vc-retired')
    for url in ['/api/v1/voice-cloning/provider-status', f'/api/v2/projects/{project.id}/voice-cloning/provider-status']:
        response = client.get(url)
        assert response.status_code == 200
        status = response.json()
        assert (status['provider'], status['target_model']) == (expected_provider, expected_model)
        assert status['available']
        assert [option['id'] for option in status['models']] == ['qwen_audio_next:qwen-audio-3.1-tts-next', 'cosyvoice:cosyvoice-v3.5-flash']
        assert [option['provider'] for option in status['models'] if option['recommended']] == ['qwen_audio_next']
        assert status['models'][0]['capabilities']['instruction']
        assert [(option['provider'], option['target_model']) for option in status['providers']] == [('qwen_audio_next', 'qwen-audio-3.1-tts-next'), ('cosyvoice', 'cosyvoice-v3.5-flash')]
    def create(**kwargs):
        assert kwargs['provider'] == expected_provider
        assert kwargs['target_model'] == expected_model
        return {'output': {'voice': 'current-qwen-voice', 'voice_id': 'current-cosy-voice'}}
    monkeypatch.setattr(voice_cloning_service, '_create_provider_voice', create)
    monkeypatch.setattr(voice_cloning_service, 'query_qwen_audio_voice', lambda *args, **kwargs: {'output': {'status': 'OK'}})
    with session_factory() as session:
        sample = _model_test_sample(session, tmp_path)
        profile = create_voice_clone_profile(session, VoiceCloneProfileCreateRequest(
            clone_sample_id=sample.id, voice_name='current default', provider=status['provider'], target_model=status['target_model']))
        assert (profile.provider, profile.target_model, profile.status) == (expected_provider, expected_model, 'ready')


@pytest.mark.parametrize('provider,target_model', [
    ('qwen', 'qwen3-tts-vc-retired'), ('cosyvoice', 'cosyvoice-v3-plus'), ('cosyvoice', 'cosyvoice-v2'),
])
@pytest.mark.parametrize('reuse', [False, True])
def test_current_catalog_rejects_legacy_model_creation_even_reuse(monkeypatch, tmp_path, provider, target_model, reuse):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_cosyvoice_target_model', target_model)
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    if reuse:
        session.add(VoiceCloneProfile(source_clone_sample_id=sample.id, provider=provider,
            target_model=target_model, voice_name='legacy', voice_id='legacy-voice', status='ready'))
        session.commit()
    monkeypatch.setattr(voice_cloning_service, '_create_provider_voice', lambda **kwargs: pytest.fail('legacy model reached cloud'))
    with pytest.raises(RuntimeError, match='模型'):
        create_voice_clone_profile(session, VoiceCloneProfileCreateRequest(
            clone_sample_id=sample.id, voice_name='legacy', provider=provider, target_model=target_model))
    assert session.query(VoiceCloneProfile).count() == int(reuse)


@pytest.mark.parametrize('provider,target_model,action,id_field', [
    ('qwen', 'qwen3-tts-vc-2026-01-22', 'delete', 'voice'),
    ('cosyvoice', 'cosyvoice-v2', 'delete_voice', 'voice_id'),
])
def test_legacy_cloud_deletion_remains_available_outside_current_catalog(monkeypatch, provider, target_model, action, id_field):
    monkeypatch.setattr(settings, 'voice_cloning_enabled', False)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    requests = []
    def post(url, **kwargs):
        requests.append(kwargs['json'])
        return httpx.Response(200, json={'output': {}})
    monkeypatch.setattr(httpx, 'post', post)
    voice_cloning_service._delete_provider_voice(provider, 'legacy-cloud-id', target_model=target_model)
    assert len(requests) == 1
    assert requests[0]['input'] == {'action': action, id_field: 'legacy-cloud-id'}


@pytest.mark.parametrize('status_code', [200, 400, 429])
def test_qwen_quota_error_explains_cloud_capacity(monkeypatch, status_code):
    from app.services.qwen_voice_clone import create_qwen_voice, QwenVoiceCloneError
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs: httpx.Response(status_code, json={
        'code': 'BadRequest', 'message': 'Maximum voice-clone voice limit exceeded',
    }))
    with pytest.raises(QwenVoiceCloneError, match='云端复刻音色数量已达上限，请清理') as caught:
        create_qwen_voice(sample_public_url='https://example.test/sample.wav',
            target_model='qwen3-tts-vc-2026-01-22', voice_name='quota')
    assert '下架' not in str(caught.value)


def test_create_voice_clone_profile_supports_current_cosyvoice_provider(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='千问测试人物'))

    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'cosyvoice')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_cosyvoice_target_model', 'cosyvoice-v3.5-plus')
    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 15.0)
    monkeypatch.setattr(
        'app.services.voiceprints.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[0.1, 0.2, 0.3],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )
    monkeypatch.setattr('app.services.voiceprints.ensure_sample_public_url', lambda _path: 'https://example.com/sample.wav')
    monkeypatch.setattr(
        'app.services.voice_cloning.create_cosyvoice_voice',
        lambda **_kwargs: {'output': {'voice_id': 'qwen-tts-vc-demo-voice'}},
    )

    sample_path = tmp_path / 'demo.wav'
    sample_path.write_bytes(b'RIFFdemo')
    sample = save_voiceprint_sample(session, person, sample_path, 'demo.wav')
    sample.clone_sample_rate_hz = 24000
    session.commit()

    profile = create_voice_clone_profile(
        session,
        VoiceCloneProfileCreateRequest(
            person_id=person.id,
            sample_id=sample.id,
            voice_name='qwentest',
            target_model='cosyvoice-v3.5-plus',
            language_hints=['zh'],
        ),
    )

    assert profile.provider == 'cosyvoice'
    assert profile.provider_model_family == 'cosyvoice'
    assert profile.status == 'ready'
    assert profile.voice_id == 'qwen-tts-vc-demo-voice'
    assert profile.target_model == 'cosyvoice-v3.5-plus'



def test_synthesize_voice_clone_preview_supports_current_cosyvoice_provider(monkeypatch) -> None:
    session = _build_session()
    person = create_person(session, PersonCreateRequest(name='千问试听人物'))

    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'cosyvoice')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_cosyvoice_target_model', 'cosyvoice-v3.5-plus')
    monkeypatch.setattr('app.services.voiceprints.get_media_duration_seconds', lambda _path: 15.0)
    monkeypatch.setattr(
        'app.services.voiceprints.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[0.1, 0.2, 0.3],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )
    monkeypatch.setattr('app.services.voiceprints.ensure_sample_public_url', lambda _path: 'https://example.com/sample.wav')
    monkeypatch.setattr(
        'app.services.voice_cloning.create_cosyvoice_voice',
        lambda **_kwargs: {'output': {'voice_id': 'qwen-tts-vc-preview-voice'}},
    )
    monkeypatch.setattr(
        'app.services.voice_cloning.synthesize_cosyvoice_voice',
        lambda **_kwargs: {'output': {'audio': {'url': 'https://example.com/output.wav'}}},
    )

    with TemporaryDirectory() as tmp_dir:
        sample_path = Path(tmp_dir) / 'demo.wav'
        sample_path.write_bytes(b'RIFFdemo')
        sample = save_voiceprint_sample(session, person, sample_path, 'demo.wav')
        sample.clone_sample_rate_hz = 24000
        session.commit()
        preview_path = Path(tmp_dir) / 'preview.wav'
        preview_path.write_bytes(b'WAVDATA')
        monkeypatch.setattr('app.services.voice_cloning._download_preview_audio', lambda *_args, **_kwargs: preview_path)

        profile = create_voice_clone_profile(
            session,
            VoiceCloneProfileCreateRequest(
                person_id=person.id,
                sample_id=sample.id,
                voice_name='qwenpreview',
                target_model='cosyvoice-v3.5-plus',
                language_hints=['zh'],
            ),
        )

        record = synthesize_voice_clone_preview(
            session,
            profile.id,
            VoiceClonePreviewRequest(text='你好，欢迎试听这个千问音色。'),
        )

        preview_exists = Path(record.output_audio_local_path).exists()

    assert record.status == 'ready'
    assert record.target_model == 'cosyvoice-v3.5-plus'
    assert record.output_audio_public_url == 'https://example.com/output.wav'
    assert preview_exists is True





@pytest.mark.parametrize('instruction', [None, '温柔自然'])
def test_officially_retired_qwen_preserves_source_and_blocks_generation(monkeypatch, tmp_path, instruction):
    from app.services.voice_clone_cleanup import build_voice_clone_cleanup_plan
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    sample.content_sha256 = voice_cloning_service.hash_voice_clone_audio(Path(sample.audio_local_path)).content_sha256
    profile = VoiceCloneProfile(source_clone_sample_id=sample.id, provider='qwen',
        target_model='qwen3-tts-vc-2026-01-22', voice_name='原音色', voice_id='original-cloud-id', status='ready')
    session.add(profile)
    session.commit()
    availability = voice_cloning_service.get_voice_clone_profile_availability(session, profile)
    assert not availability.selectable and availability.reason == 'model_retired'
    monkeypatch.setattr(voice_cloning_service, '_synthesize_provider_voice',
        lambda **kwargs: pytest.fail('retired model reached cloud'))
    with pytest.raises(RuntimeError, match='已下线.*参考样本'):
        synthesize_voice_clone_preview(session, profile.id, VoiceClonePreviewRequest(text='测试', instruction=instruction))
    assert session.query(VoiceCloneSynthesisRecord).count() == 0
    assert profile.voice_id == 'original-cloud-id' and profile.status == 'ready'
    monkeypatch.setattr('app.services.voice_clone_cleanup.get_media_duration_seconds', lambda path: 12)
    plan = build_voice_clone_cleanup_plan(session)
    assert not plan.invalid_profiles and not plan.invalid_samples and not plan.deletion_targets
    assert Path(sample.audio_local_path).is_file()
    monkeypatch.setattr(voice_cloning_service, '_create_provider_voice', lambda **kwargs: {'output': {'voice_id': 'new-cloud-id'}})
    replacement = create_voice_clone_profile(session, VoiceCloneProfileCreateRequest(
        clone_sample_id=sample.id, voice_name='新版本', provider='cosyvoice'))
    assert replacement.id != profile.id and replacement.source_clone_sample_id == sample.id
    assert replacement.target_model == 'cosyvoice-v3.5-plus' and replacement.voice_id == 'new-cloud-id'
    assert session.query(VoiceCloneProfile).count() == 2 and session.query(VoiceCloneSample).count() == 1


@pytest.mark.parametrize('reuse', [False, True])
def test_officially_retired_qwen_rejects_creation_even_if_existing_profile(monkeypatch, tmp_path, reuse):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    if reuse:
        session.add(VoiceCloneProfile(source_clone_sample_id=sample.id, provider='qwen',
            target_model='qwen3-tts-vc-2026-01-22', voice_name='旧版本', voice_id='old-id', status='ready'))
        session.commit()
    monkeypatch.setattr(voice_cloning_service, '_create_provider_voice', lambda **kwargs: pytest.fail('retired creation reached cloud'))
    with pytest.raises(RuntimeError, match='已下线'):
        create_voice_clone_profile(session, VoiceCloneProfileCreateRequest(
            clone_sample_id=sample.id, voice_name='测试', provider='qwen', target_model='qwen3-tts-vc-2026-01-22'))
    assert session.query(VoiceCloneProfile).count() == int(reuse)


@pytest.mark.parametrize('has_other_profile', [False, True])
def test_download_completion_releases_reference_and_all_outputs(monkeypatch, tmp_path, has_other_profile):
    from app.services.voice_clone_cleanup import drain_voice_clone_deletion_tasks
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    session = _build_session()
    project = Project(name='download test', name_key='download test', status='active'); session.add(project); session.flush()
    sample = _model_test_sample(session, tmp_path)
    identity = VoiceCloneIdentity(display_name='temporary'); session.add(identity); session.flush()
    sample.identity_id = identity.id
    profile = VoiceCloneProfile(project_id=project.id, source_clone_sample_id=sample.id, identity_id=identity.id,
        voice_name='temporary', provider='qwen_audio', target_model='qwen-audio-3.0-tts-plus', voice_id='temporary-voice', status='ready')
    session.add(profile); session.flush()
    profile_id, sample_id, identity_id = profile.id, sample.id, identity.id
    if not has_other_profile:
        session.add(VoiceCloneProfile(project_id=project.id, source_clone_sample_id=sample.id, identity_id=identity.id,
            voice_name='comparison model', provider='cosyvoice', target_model='cosyvoice-v3.5-plus', voice_id='comparison-voice', status='ready'))
    if has_other_profile:
        session.add(VoiceCloneProfile(source_clone_sample_id=sample.id, identity_id=identity.id,
            voice_name='other project', provider='cosyvoice', target_model='cosyvoice-v3.5-plus', voice_id='other-voice', status='ready'))
    session.commit()
    output = tmp_path/'output.wav'; output.write_bytes(b'generated audio')
    monkeypatch.setattr(voice_cloning_service, '_synthesize_provider_voice', lambda **kwargs: {'output': {'audio': {'url': 'https://example.test/output.wav'}}})
    monkeypatch.setattr(voice_cloning_service, '_download_preview_audio', lambda *args: output)
    record = synthesize_voice_clone_preview(session, profile_id, VoiceClonePreviewRequest(text='test'), project_id=project.id)
    assert record.status == 'ready' and record.voice_clone_profile_id == profile_id
    assert session.get(VoiceCloneProfile, profile_id) is not None
    voice_cloning_service.complete_voice_clone_download(session, project.id, record.id)
    voice_cloning_service.complete_voice_clone_download(session, project.id, record.id)
    session.refresh(record)
    assert record.voice_clone_profile_id is None
    assert session.get(VoiceCloneProfile, profile_id) is None
    assert bool(session.get(VoiceCloneSample, sample_id)) == has_other_profile
    assert bool(session.get(VoiceCloneIdentity, identity_id)) == has_other_profile
    assert record.status == 'downloaded' and record.output_audio_local_path is None
    assert record.output_audio_public_url is None
    assert list_voice_clone_synthesis_records(session, project_id=project.id) == []
    assert session.query(VoiceCloneDeletionTask).filter_by(kind='provider', voice_id='temporary-voice').count() == 1
    monkeypatch.setattr('app.services.voice_clone_cleanup._run_voice_clone_deletion_task', lambda task: (_ for _ in ()).throw(RuntimeError('cloud down')))
    drained = drain_voice_clone_deletion_tasks(session)
    assert drained.failed_count and session.query(VoiceCloneDeletionTask).filter_by(kind='provider', voice_id='temporary-voice').one().attempt_count == 1
    assert session.get(VoiceCloneSynthesisRecord, record.id).status == 'downloaded'


def test_download_completion_waits_for_in_flight_use_of_same_voice(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    session = _build_session()
    project = Project(name='in-flight download', name_key='in-flight download', status='active'); session.add(project); session.flush()
    sample = _model_test_sample(session, tmp_path)
    profile = VoiceCloneProfile(project_id=project.id, source_clone_sample_id=sample.id, voice_name='temporary', provider='qwen_audio',
        target_model='qwen-audio-3.0-tts-plus', voice_id='temporary-voice', status='ready')
    session.add(profile); session.flush()
    other = VoiceCloneSynthesisRecord(voice_clone_profile_id=profile.id, target_model=profile.target_model, input_text='other', status='processing')
    session.add(other); session.commit(); profile_id = profile.id
    output = tmp_path/'output.wav'; output.write_bytes(b'generated audio')
    monkeypatch.setattr(voice_cloning_service, '_synthesize_provider_voice', lambda **kwargs: {'output': {'audio': {'url': 'https://example.test/output.wav'}}})
    monkeypatch.setattr(voice_cloning_service, '_download_preview_audio', lambda *args: output)
    first = synthesize_voice_clone_preview(session, profile_id, VoiceClonePreviewRequest(text='first'), project_id=project.id)
    assert first.voice_clone_profile_id == profile_id
    assert session.get(VoiceCloneProfile, profile_id) is not None and other.status == 'processing'
    voice_cloning_service.complete_voice_clone_download(session, project.id, first.id)
    assert session.query(VoiceCloneDeletionTask).filter_by(kind='profile_release').count() == 1
    assert session.get(VoiceCloneProfile, profile_id) is not None
    other.status = 'ready'; session.commit()
    from app.services.voice_clone_cleanup import drain_voice_clone_deletion_tasks
    drain_voice_clone_deletion_tasks(session)
    session.refresh(first)
    assert first.voice_clone_profile_id is None and session.get(VoiceCloneProfile, profile_id) is None
    assert all(row.status == 'downloaded' and row.output_audio_local_path is None for row in session.query(VoiceCloneSynthesisRecord))


def test_next_requires_workspace_but_flash_can_use_existing_dashscope_endpoint(monkeypatch):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'sfm_workspace_id', 'speaker-space')
    status = get_voice_cloning_provider_status()
    assert [(item.provider, item.model) for item in status.models] == [('qwen_audio_next', 'qwen-audio-3.1-tts-next'), ('cosyvoice', 'cosyvoice-v3.5-flash')]
    assert status.target_model == 'qwen-audio-3.1-tts-next' and status.available
    assert status.models[0].capabilities.instruction and status.models[0].constraints.sample_max_seconds == 30
    monkeypatch.setattr(settings, 'sfm_workspace_id', '')
    assert not get_voice_cloning_provider_status().available
    assert next(item for item in get_voice_cloning_provider_status().models if item.provider == 'cosyvoice').available


def test_next_reference_is_local_and_generation_uses_its_own_sample(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'sfm_workspace_id', 'speaker-space')
    session = _build_session()
    sample = _model_test_sample(session, tmp_path)
    monkeypatch.setattr(voice_cloning_service, 'create_qwen_audio_voice', lambda **kwargs: pytest.fail('Next must not enroll a cloud voice'))
    profile = create_voice_clone_profile(session, VoiceCloneProfileCreateRequest(clone_sample_id=sample.id,
        voice_name='Next sample', provider='qwen_audio_next', target_model='qwen-audio-3.1-tts-next'))
    assert profile.status == 'ready' and profile.source_clone_sample_id == sample.id
    received = []
    def generate(**kwargs):
        received.append(kwargs)
        return {'output': {'audio': {'url': 'https://example.test/next.wav'}}}
    monkeypatch.setattr(voice_cloning_service, 'synthesize_qwen_audio_next_voice', generate)
    output = tmp_path / 'next.wav'; output.write_bytes(b'fixture output')
    monkeypatch.setattr(voice_cloning_service, '_download_preview_audio', lambda *args: output)
    record = synthesize_voice_clone_preview(session, profile.id, VoiceClonePreviewRequest(text='你好', instruction='亲切'))
    assert record.status == 'ready'
    assert received[0]['sample_public_url'] == sample.audio_public_url
    assert received[0]['text'] == '你好'
