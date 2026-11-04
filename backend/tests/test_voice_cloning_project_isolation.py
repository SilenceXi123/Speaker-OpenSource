from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, exc as sqlalchemy_exc
from sqlalchemy.orm import Session, sessionmaker

from app.api.v2 import voice_cloning as voice_cloning_api
from app.core.config import settings
from app.db.session import Base
from app.models.person import Person
from app.models.project import Project
from app.models.voice_clone_deletion_task import VoiceCloneDeletionTask
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_sample import VoiceCloneSample
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
from app.models.voiceprint_sample import VoiceprintSample
from app.schemas.voice_cloning import VoiceClonePreviewRequest, VoiceCloneProfileCreateRequest
from app.services.voice_cloning import (
    VoiceCloneProjectConflictError,
    create_voice_clone_profile,
    delete_voice_clone_profile,
    find_or_create_voice_clone_profile,
    get_voice_clone_profile_for_project,
    get_voice_clone_synthesis_record_for_project,
    list_voice_clone_samples_for_project,
    save_voice_clone_sample,
    synthesize_voice_clone_preview,
)
from app.services.voice_clone_library import list_voice_clone_library
from app.services.voiceprint_provider import VoiceprintEmbeddingResult


@pytest.fixture()
def isolated_database(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr('app.services.voice_cloning.query_cosyvoice_voice', lambda voice_id: {'output': {'status': 'OK'}})
    engine = create_engine(
        f"sqlite:///{tmp_path / 'voice-clone-isolation.db'}",
        connect_args={'check_same_thread': False},
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(voice_cloning_api, 'SessionLocal', session_factory)
    session = session_factory()
    try:
        yield session, session_factory
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _project(session: Session, name: str, legacy_key: str, *, status: str = 'active') -> Project:
    project = Project(
        name=name,
        name_key=name.lower(),
        legacy_program_key=legacy_key,
        status=status,
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def _clone_sample(
    session: Session,
    name: str,
    *,
    project_id: str | None,
    program_key: str | None,
    program_name: str | None,
    audio_path: str | None = None,
) -> VoiceCloneSample:
    sample_path = (
        Path(audio_path)
        if audio_path
        else Path(str(session.get_bind().url.database)).parent / f'{name}.wav'
    )
    if not audio_path:
        sample_path.write_bytes(b'RIFF0000WAVE')
    sample = VoiceCloneSample(
        display_name=name,
        project_id=project_id,
        program_key=program_key,
        program_name=program_name,
        original_filename=f'{name}.wav',
        audio_local_path=str(sample_path),
        audio_public_url='https://example.test/reference.wav',
        content_sha256='d' * 64,
        file_size_bytes=sample_path.stat().st_size,
        duration_ms=12_000,
        sample_rate_hz=24_000,
        channel_count=1,
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.commit()
    session.refresh(sample)
    return sample


def _profile(session: Session, project: Project, name: str) -> VoiceCloneProfile:
    profile = VoiceCloneProfile(
        project_id=project.id,
        program_key=project.legacy_program_key,
        program_name=project.name,
        provider='qwen',
        provider_model_family='qwen',
        target_model='qwen3-tts-vc-2026-01-22',
        voice_id=f'voice-{name}',
        voice_name=name,
        status='ready',
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


@pytest.mark.parametrize('action', ['create', 'preview'])
def test_v2_retired_model_message_survives_api_and_preserves_assets(isolated_database, action):
    session, _ = isolated_database
    project = _project(session, 'Retired model', 'retired-model')
    profile = _profile(session, project, 'retired')
    app = FastAPI()
    app.include_router(voice_cloning_api.router, prefix='/api/v2')
    client = TestClient(app)
    path = f'/api/v2/projects/{project.id}/voice-cloning/profiles'
    payload = {'voice_name': 'retired', 'provider': 'qwen', 'target_model': profile.target_model}
    if action == 'preview':
        path += f'/{profile.id}/preview'
        payload = {'text': '测试'}
    response = client.post(path, json=payload)
    assert response.status_code == 400
    assert '已下线' in response.json()['detail'] and '原参考样本' in response.json()['detail']
    assert session.get(VoiceCloneProfile, profile.id).voice_id == 'voice-retired'
    assert session.query(VoiceCloneSynthesisRecord).count() == 0


def test_project_scoped_sample_reads_include_unambiguous_legacy_rows_without_claiming(
    isolated_database,
) -> None:
    session, _ = isolated_database
    project_a = _project(session, 'Project A', 'project-a-old')
    project_b = _project(session, 'Project B', 'project-b-old')
    sample_a = _clone_sample(
        session,
        'sample-a',
        project_id=project_a.id,
        program_key='project-b-old',
        program_name='Project B',
    )
    sample_b = _clone_sample(
        session,
        'sample-b',
        project_id=project_b.id,
        program_key='project-a-old',
        program_name='Project A',
    )
    legacy_a = _clone_sample(
        session,
        'legacy-a',
        project_id=None,
        program_key='project-a-old',
        program_name='Project A',
    )
    unowned = _clone_sample(
        session,
        'unowned',
        project_id=None,
        program_key=None,
        program_name=None,
    )

    visible = list_voice_clone_samples_for_project(session, project_a.id)

    assert {item.id for item in visible} == {sample_a.id, legacy_a.id}
    assert session.get(VoiceCloneSample, legacy_a.id).project_id is None
    assert session.get(VoiceCloneSample, unowned.id).project_id is None
    assert sample_b.id not in {item.id for item in visible}


def test_shared_voice_assets_can_be_reused_cross_project_while_records_stay_scoped(
    isolated_database,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    session, _ = isolated_database
    project_a = _project(session, 'Shared A', 'shared-a')
    project_b = _project(session, 'Shared B', 'shared-b')
    sample_b = _clone_sample(
        session,
        '撒贝宁',
        project_id=project_b.id,
        program_key=project_b.legacy_program_key,
        program_name=project_b.name,
    )
    profile_b = VoiceCloneProfile(
        project_id=project_b.id,
        source_clone_sample_id=sample_b.id,
        source_audio_local_path=sample_b.audio_local_path,
        program_key=project_b.legacy_program_key,
        program_name=project_b.name,
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_id='shared-sabening',
        voice_name='撒贝宁',
        status='ready',
    )
    session.add(profile_b)
    session.commit()
    session.refresh(profile_b)

    library = list_voice_clone_library(session, project_a.id, query='撒贝宁')
    assert library.total == 1
    assert library.groups[0].recommended_profile.id == profile_b.id

    reused = find_or_create_voice_clone_profile(
        session,
        VoiceCloneProfileCreateRequest(
            clone_sample_id=sample_b.id,
            voice_name=' 撒贝宁 ',
            provider='cosyvoice',
            target_model=profile_b.target_model,
        ),
        project_id=project_a.id,
    )
    assert reused.reused_existing is True
    assert reused.profile.id == profile_b.id

    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'cosyvoice')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        'app.services.voice_cloning.synthesize_cosyvoice_voice',
        lambda **_kwargs: {'output': {'audio': {'url': 'https://example.test/output.wav'}}},
    )
    preview_path = tmp_path / 'shared-preview.wav'
    preview_path.write_bytes(b'RIFF0000WAVE')
    monkeypatch.setattr(
        'app.services.voice_cloning._download_preview_audio',
        lambda *_args: preview_path,
    )

    record = synthesize_voice_clone_preview(
        session,
        profile_b.id,
        VoiceClonePreviewRequest(text='跨项目复用声音'),
        project_id=project_a.id,
    )

    assert record.project_id == project_a.id
    assert record.program_key == project_a.legacy_program_key
    assert record.program_name == project_a.name
    assert get_voice_clone_synthesis_record_for_project(
        session,
        project_a.id,
        record.id,
    ).id == record.id
    assert get_voice_clone_synthesis_record_for_project(
        session,
        project_b.id,
        record.id,
    ) is None


def test_owner_can_delete_shared_profile_and_preserve_successful_records_in_all_projects(
    isolated_database,
    tmp_path: Path,
) -> None:
    session, _ = isolated_database
    owner = _project(session, 'Voice Owner', 'voice-owner')
    consumer = _project(session, 'Voice Consumer', 'voice-consumer')
    preview_path = tmp_path / 'disposable-preview.wav'
    owner_output = tmp_path / 'owner-result.wav'
    consumer_output = tmp_path / 'consumer-result.wav'
    preview_path.write_bytes(b'RIFF-preview')
    owner_output.write_bytes(b'RIFF-owner-result')
    consumer_output.write_bytes(b'RIFF-consumer-result')
    profile = VoiceCloneProfile(
        project_id=owner.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model='qwen3-tts-vc-2026-01-22',
        voice_id='shared-profile-to-delete',
        voice_name='共享音色历史快照',
        preview_audio_local_path=str(preview_path),
        status='ready',
    )
    session.add(profile)
    session.flush()
    owner_record = VoiceCloneSynthesisRecord(
        project_id=owner.id,
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='来源项目成果',
        output_audio_local_path=str(owner_output),
        status='ready',
    )
    consumer_record = VoiceCloneSynthesisRecord(
        project_id=consumer.id,
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='使用项目成果',
        output_audio_local_path=str(consumer_output),
        status='ready',
    )
    failed_record = VoiceCloneSynthesisRecord(
        project_id=consumer.id,
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='没有输出的失败记录',
        status='failed',
    )
    session.add_all([owner_record, consumer_record, failed_record])
    session.commit()

    with pytest.raises(
        VoiceCloneProjectConflictError,
        match='共享音色只能由其来源项目删除',
    ):
        delete_voice_clone_profile(
            session,
            profile.id,
            project_id=consumer.id,
        )

    with pytest.raises(
        VoiceCloneProjectConflictError,
        match='共享音色只能由其来源项目删除',
    ):
        delete_voice_clone_profile(session, profile.id)

    delete_voice_clone_profile(session, profile.id, project_id=owner.id)

    session.expire_all()
    assert session.get(VoiceCloneProfile, profile.id) is None
    assert session.get(VoiceCloneSynthesisRecord, failed_record.id) is None
    for record_id, project_id, output_path in [
        (owner_record.id, owner.id, owner_output),
        (consumer_record.id, consumer.id, consumer_output),
    ]:
        preserved = session.get(VoiceCloneSynthesisRecord, record_id)
        assert preserved is not None
        assert preserved.project_id == project_id
        assert preserved.voice_clone_profile_id is None
        assert preserved.voice_name_snapshot == '共享音色历史快照'
        assert preserved.provider_snapshot == 'qwen'
        assert preserved.target_model_snapshot == 'qwen3-tts-vc-2026-01-22'
        assert preserved.output_audio_local_path == str(output_path)
        assert output_path.is_file()
    assert {
        task.resource_key
        for task in session.query(VoiceCloneDeletionTask).all()
    } == {
        'provider:qwen:qwen3-tts-vc-2026-01-22:shared-profile-to-delete',
        f'local:{preview_path}',
    }
    assert preview_path.is_file()


@pytest.mark.parametrize(
    ('profile_state', 'expected_status'),
    [
        ('missing', 404),
        ('unavailable', 409),
    ],
)
def test_v2_preview_uses_unavailable_contract_before_provider_for_missing_or_unavailable_profile(
    isolated_database,
    monkeypatch: pytest.MonkeyPatch,
    profile_state: str,
    expected_status: int,
) -> None:
    session, _ = isolated_database
    project = _project(session, 'Preview Contract', 'preview-contract')
    profile_id = 'physically-deleted-profile'
    if profile_state == 'unavailable':
        profile = _profile(session, project, 'unavailable-profile')
        profile.status = 'failed'
        session.commit()
        profile_id = profile.id

    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'qwen')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        'app.services.voice_cloning._synthesize_provider_voice',
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError('provider must not be called for an unavailable profile')
        ),
    )
    app = FastAPI()
    app.include_router(voice_cloning_api.router, prefix='/api/v2')
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        f'/api/v2/projects/{project.id}/voice-cloning/profiles/{profile_id}/preview',
        json={'text': '不应提交给 Provider'},
    )

    assert response.status_code == expected_status
    assert response.json()['detail'] == {
        'code': 'voice_clone_profile_unavailable',
        'message': '该音色已失效并被清理，请重新选择或上传',
    }
    assert session.query(VoiceCloneSynthesisRecord).count() == 0


def test_v2_paths_share_voice_assets_but_keep_records_and_deletes_project_scoped(
    isolated_database,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session, _ = isolated_database
    project_a = _project(session, 'API A', 'api-a')
    project_b = _project(session, 'API B', 'api-b')
    sample_a = _clone_sample(
        session,
        'api-sample-a',
        project_id=project_a.id,
        program_key='api-a',
        program_name='API A',
    )
    sample_b = _clone_sample(
        session,
        'api-sample-b',
        project_id=project_b.id,
        program_key='api-b',
        program_name='API B',
    )
    profile_b = _profile(session, project_b, 'profile-b')
    record_b = VoiceCloneSynthesisRecord(
        project_id=project_b.id,
        voice_clone_profile_id=profile_b.id,
        program_key='api-b',
        program_name='API B',
        target_model=profile_b.target_model,
        input_text='项目 B 试听',
        status='ready',
    )
    session.add(record_b)
    profile_a = _profile(session, project_a, 'profile-a')
    audio_a = tmp_path / 'record-a.wav'
    audio_a.write_bytes(b'RIFF0000WAVE')
    record_a = VoiceCloneSynthesisRecord(
        project_id=project_a.id,
        voice_clone_profile_id=profile_a.id,
        program_key='api-a',
        program_name='API A',
        target_model=profile_a.target_model,
        input_text='项目 A 试听',
        output_audio_local_path=str(audio_a),
        status='ready',
    )
    corrupt_profile = VoiceCloneProfile(
        project_id=project_a.id,
        source_clone_sample_id=sample_b.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model='qwen3-tts-vc-2026-01-22',
        voice_name='corrupt-profile',
        status='ready',
    )
    corrupt_record = VoiceCloneSynthesisRecord(
        project_id=project_a.id,
        voice_clone_profile_id=profile_b.id,
        target_model=profile_b.target_model,
        input_text='错误父链',
        status='ready',
    )
    foreign_child_record = VoiceCloneSynthesisRecord(
        project_id=project_b.id,
        voice_clone_profile_id=profile_a.id,
        target_model=profile_a.target_model,
        input_text='跨项目子记录',
        status='ready',
    )
    session.add_all([record_a, corrupt_profile, corrupt_record, foreign_child_record])
    session.commit()

    app = FastAPI()
    app.include_router(voice_cloning_api.router, prefix='/api/v2')
    monkeypatch.setattr(
        voice_cloning_api,
        '_fetch_remote_audio',
        lambda *_args: voice_cloning_api.Response(
            content=b'RIFF-shared-sample',
            media_type='audio/wav',
        ),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(f'/api/v2/projects/{project_a.id}/voice-cloning/samples')
    assert response.status_code == 200
    assert {item['id'] for item in response.json()} == {sample_a.id, sample_b.id}

    cross_audio = client.get(
        f'/api/v2/projects/{project_a.id}/voice-cloning/samples/{sample_b.id}/audio'
    )
    cross_sample = client.get(
        f'/api/v2/projects/{project_a.id}/voice-cloning/samples/{sample_b.id}'
    )
    cross_profile = client.get(
        f'/api/v2/projects/{project_a.id}/voice-cloning/profiles/{profile_b.id}'
    )
    cross_record_detail = client.get(
        f'/api/v2/projects/{project_a.id}/voice-cloning/synthesis-records/{record_b.id}'
    )
    cross_delete = client.delete(
        f'/api/v2/projects/{project_a.id}/voice-cloning/samples/{sample_b.id}'
    )
    cross_record = client.get(
        f'/api/v2/projects/{project_a.id}/voice-cloning/synthesis-records/{record_b.id}/audio'
    )
    corrupt_profile_read = client.get(
        f'/api/v2/projects/{project_a.id}/voice-cloning/profiles/{corrupt_profile.id}'
    )
    corrupt_record_read = client.get(
        f'/api/v2/projects/{project_a.id}/voice-cloning/synthesis-records/{corrupt_record.id}'
    )
    assert cross_audio.status_code == 200
    assert cross_sample.status_code == 200
    assert cross_profile.status_code == 200
    assert cross_record_detail.status_code == 404
    assert cross_delete.status_code == 409
    assert cross_record.status_code == 404
    assert corrupt_profile_read.status_code == 200
    assert corrupt_record_read.status_code == 200
    session.expire_all()
    assert session.get(VoiceCloneSample, sample_b.id) is not None
    assert session.get(VoiceCloneSynthesisRecord, record_b.id) is not None

    consume_compatible_read = client.get(
        f'/api/v2/projects/{project_a.id}/voice-cloning/synthesis-records/{record_a.id}/audio',
        params={'consume': 'true'},
    )
    assert consume_compatible_read.status_code == 200
    session.expire_all()
    assert session.get(VoiceCloneSynthesisRecord, record_a.id) is not None

    provider_delete_calls: list[str] = []
    monkeypatch.setattr(
        'app.services.voice_cloning._delete_provider_voice',
        lambda _provider, voice_id: provider_delete_calls.append(voice_id),
    )
    profile_a_id = profile_a.id
    foreign_child_record_id = foreign_child_record.id
    owner_profile_delete = client.delete(
        f'/api/v2/projects/{project_a.id}/voice-cloning/profiles/{profile_a_id}'
    )
    assert owner_profile_delete.status_code == 200
    assert owner_profile_delete.json() == {
        'id': profile_a_id,
        'deleted': True,
    }
    assert provider_delete_calls == []
    session.expire_all()
    assert session.get(VoiceCloneProfile, profile_a_id) is None
    assert session.get(
        VoiceCloneSynthesisRecord,
        foreign_child_record_id,
    ).voice_clone_profile_id is None

    profile_count_before_conflict_requests = session.query(VoiceCloneProfile).count()
    monkeypatch.setattr(settings, 'voice_cloning_enabled', False)
    body_conflict = client.post(
        f'/api/v2/projects/{project_a.id}/voice-cloning/profiles',
        json={
            'project_id': project_b.id,
            'clone_sample_id': sample_a.id,
            'voice_name': 'must-not-create',
        },
    )
    source_conflict = client.post(
        f'/api/v2/projects/{project_a.id}/voice-cloning/profiles',
        json={'clone_sample_id': sample_b.id, 'voice_name': 'must-not-create-either'},
    )
    assert body_conflict.status_code == 409
    assert source_conflict.status_code == 400
    assert session.query(VoiceCloneProfile).count() == profile_count_before_conflict_requests

    cross_preview = client.post(
        f'/api/v2/projects/{project_a.id}/voice-cloning/profiles/{profile_b.id}/preview',
        json={'text': '不能跨项目试听'},
    )
    assert cross_preview.status_code == 400

    route_paths = {route.path for route in voice_cloning_api.router.routes}
    assert '/projects/{project_id}/voice-cloning/samples' in route_paths
    assert '/projects/{project_id}/voice-cloning/profiles/{profile_id}/preview' in route_paths
    assert '/projects/{project_id}/voice-cloning/synthesis-records/{record_id}/audio' in route_paths


def test_project_record_audio_reads_history_without_profile_warning(
    isolated_database,
    tmp_path: Path,
) -> None:
    session, _ = isolated_database
    project = _project(session, 'History Audio', 'history-audio-old')
    audio_path = tmp_path / 'preserved-history.wav'
    audio_path.write_bytes(b'WAVDATA')
    record = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=None,
        voice_name_snapshot='已删除音色',
        provider_snapshot='qwen',
        target_model_snapshot='qwen3-tts-vc-2026-01-22',
        target_model='qwen3-tts-vc-2026-01-22',
        input_text='保留的历史试听',
        output_audio_local_path=str(audio_path),
        status='ready',
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    app = FastAPI()
    app.include_router(voice_cloning_api.router, prefix='/api/v2')
    client = TestClient(app)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always', sqlalchemy_exc.SAWarning)
        response = client.get(
            f'/api/v2/projects/{project.id}/voice-cloning/synthesis-records/{record.id}/audio'
        )

    assert response.status_code == 200
    assert response.content == b'WAVDATA'
    assert [
        item
        for item in caught
        if issubclass(item.category, sqlalchemy_exc.SAWarning)
    ] == []


def test_profile_rejects_cross_project_person_and_voiceprint_sample(isolated_database, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'sfm_workspace_id', 'speaker-space')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    session, _ = isolated_database
    project_a = _project(session, 'People A', 'people-a')
    project_b = _project(session, 'People B', 'people-b')
    person_b = Person(
        name='Person B',
        project_id=project_b.id,
        program_key=project_b.legacy_program_key,
        program_name=project_b.name,
    )
    session.add(person_b)
    session.commit()
    session.refresh(person_b)
    voiceprint_b = VoiceprintSample(
        person_id=person_b.id,
        original_filename='person-b.wav',
        audio_local_path='/tmp/person-b.wav',
        clone_audio_public_url='https://example.test/person-b.wav',
        clone_readiness_status='ready',
    )
    session.add(voiceprint_b)
    session.commit()
    session.refresh(voiceprint_b)

    with pytest.raises(VoiceCloneProjectConflictError, match='不属于当前项目'):
        create_voice_clone_profile(
            session,
            VoiceCloneProfileCreateRequest(
                person_id=person_b.id,
                sample_id=voiceprint_b.id,
                voice_name='cross-person',
            ),
            project_id=project_a.id,
        )

    assert session.query(VoiceCloneProfile).count() == 0


def test_profile_and_record_inherit_source_project_and_v1_alias_dual_writes(
    isolated_database,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    session, _ = isolated_database
    project_a = _project(session, 'Inheritance A', 'inheritance-a-old')
    project_b = _project(session, 'Inheritance B', 'inheritance-b-old')
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'cosyvoice')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_cosyvoice_target_model', 'cosyvoice-v3.5-plus')
    monkeypatch.setattr(
        'app.services.voice_cloning.create_cosyvoice_voice',
        lambda **_kwargs: {'output': {'voice_id': 'inherited-voice'}},
    )
    monkeypatch.setattr(
        'app.services.voice_cloning.synthesize_cosyvoice_voice',
        lambda **_kwargs: {'output': {'audio': {'url': 'https://example.test/output.wav'}}},
    )
    preview_path = tmp_path / 'preview.wav'
    preview_path.write_bytes(b'RIFF0000WAVE')
    monkeypatch.setattr('app.services.voice_cloning._download_preview_audio', lambda *_args: preview_path)

    source = _clone_sample(
        session,
        'source-a',
        project_id=project_a.id,
        program_key=project_a.legacy_program_key,
        program_name=project_a.name,
    )
    profile = create_voice_clone_profile(
        session,
        VoiceCloneProfileCreateRequest(clone_sample_id=source.id, voice_name='inherited'),
    )
    record = synthesize_voice_clone_preview(
        session,
        profile.id,
        VoiceClonePreviewRequest(text='继承项目生成记录'),
        project_id=project_a.id,
    )

    assert profile.project_id == project_a.id
    assert profile.program_key == project_a.legacy_program_key
    assert profile.program_name == project_a.name
    assert record.project_id == project_a.id
    assert record.program_key == profile.program_key
    assert get_voice_clone_synthesis_record_for_project(session, project_a.id, record.id).id == record.id
    assert get_voice_clone_synthesis_record_for_project(session, project_b.id, record.id) is None

    legacy_audio = tmp_path / 'legacy.wav'
    legacy_audio.write_bytes(b'RIFF0000WAVE')
    monkeypatch.setattr(
        'app.services.voice_clone_identity.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[1.0, 0.0],
            provider_name='test-provider',
            provider_model_id='test-model',
            provider_message='embedding ready',
        ),
    )
    monkeypatch.setattr('app.services.voice_cloning.get_media_duration_seconds', lambda _path: 12.0)
    monkeypatch.setattr(
        'app.services.voice_cloning.ensure_sample_public_url',
        lambda _path: 'https://example.test/legacy.wav',
    )
    dual_written = save_voice_clone_sample(
        session,
        display_name='legacy-client',
        audio_local_path=legacy_audio,
        original_filename='legacy.wav',
        program_name=project_a.name,
    )

    assert dual_written.project_id == project_a.id
    assert dual_written.program_key == project_a.legacy_program_key
    assert dual_written.program_name == project_a.name


def test_legacy_profile_and_record_reads_resolve_parent_order_without_claiming(
    isolated_database,
) -> None:
    session, _ = isolated_database
    project = _project(session, 'Legacy Claim', 'legacy-claim-old')
    sample = _clone_sample(
        session,
        'legacy-source',
        project_id=None,
        program_key='legacy-claim-old',
        program_name='Legacy Claim',
    )
    profile = VoiceCloneProfile(
        project_id=None,
        source_clone_sample_id=sample.id,
        program_key='legacy-claim-old',
        program_name='Legacy Claim',
        provider='qwen',
        provider_model_family='qwen',
        target_model='qwen3-tts-vc-2026-01-22',
        voice_name='legacy-profile',
        status='ready',
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    record = VoiceCloneSynthesisRecord(
        project_id=None,
        voice_clone_profile_id=profile.id,
        program_key='legacy-claim-old',
        program_name='Legacy Claim',
        target_model=profile.target_model,
        input_text='旧记录',
        status='ready',
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    resolved_profile = get_voice_clone_profile_for_project(session, project.id, profile.id)
    resolved_record = get_voice_clone_synthesis_record_for_project(session, project.id, record.id)

    assert resolved_profile is not None
    assert resolved_record is not None
    assert resolved_profile.project_id is None
    assert resolved_record.project_id is None



def test_download_ack_is_project_scoped_idempotent_and_not_triggered_by_playback(isolated_database, tmp_path):
    session, _ = isolated_database
    project = _project(session, 'Download A', 'download-a')
    other = _project(session, 'Download B', 'download-b')
    profile = VoiceCloneProfile(project_id=project.id, provider='qwen_audio', target_model='qwen-audio-3.0-tts-plus',
        voice_name='temporary', voice_id='cloud-voice', status='ready')
    session.add(profile); session.flush(); profile_id = profile.id
    output = tmp_path/'output.wav'; output.write_bytes(b'RIFF' + bytes(50))
    record = VoiceCloneSynthesisRecord(project_id=project.id, voice_clone_profile_id=profile.id,
        target_model=profile.target_model, input_text='test', status='ready', output_audio_local_path=str(output))
    session.add(record); session.commit(); record_id = record.id
    app = FastAPI(); app.include_router(voice_cloning_api.router, prefix='/api/v2')
    client = TestClient(app)
    base = f'/api/v2/projects/{project.id}/voice-cloning/synthesis-records/{record_id}'
    assert client.get(base+'/audio').status_code == 200
    session.expire_all(); assert session.get(VoiceCloneProfile, profile_id) is not None
    foreign = f'/api/v2/projects/{other.id}/voice-cloning/synthesis-records/{record_id}/download-complete'
    assert client.post(foreign).status_code == 404
    session.expire_all(); assert session.get(VoiceCloneProfile, profile_id) is not None
    record.status = 'failed'; session.commit()
    assert client.post(base+'/download-complete').status_code == 400
    session.expire_all(); record.status = 'ready'; session.commit()
    assert client.post(base+'/download-complete').status_code == 204
    assert client.post(base+'/download-complete').status_code == 204
    session.expire_all()
    assert session.get(VoiceCloneProfile, profile_id) is None
    assert session.get(VoiceCloneSynthesisRecord, record_id).voice_clone_profile_id is None
    assert client.get(base+'/audio').status_code == 404
    assert session.get(VoiceCloneSynthesisRecord, record_id).output_audio_local_path is None
