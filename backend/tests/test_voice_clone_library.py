from collections import Counter
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import subprocess
import sys
import uuid
import wave

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.person import Person
from app.models.project import Project
from app.models.voice_clone_deletion_task import VoiceCloneDeletionTask
from app.models.voice_clone_identity import VoiceCloneIdentity
from app.models.voice_clone_identity_review import VoiceCloneIdentityReview
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_sample import VoiceCloneSample
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
from app.models.voiceprint_sample import VoiceprintSample
from app.schemas.voice_cloning import VoiceCloneProfileCreateRequest
from app.core.config import settings
from app.services import voice_cloning as voice_cloning_service
from app.services import voice_clone_identity as identity_service
from app.services.voiceprint_provider import VoiceprintEmbeddingResult


@pytest.fixture(autouse=True)
def _stub_cleanup_audio_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(voice_cloning_service, 'query_cosyvoice_voice', lambda voice_id: {'output': {'status': 'OK'}})
    monkeypatch.setattr(
        'app.services.voice_clone_cleanup.get_media_duration_seconds',
        lambda _path: 12.0,
    )


@pytest.fixture()
def session(tmp_path: Path) -> Session:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'voice-clone-library.db'}",
        connect_args={'check_same_thread': False},
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    database_session = session_factory()
    try:
        yield database_session
    finally:
        database_session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def project(session: Session) -> Project:
    item = Project(
        name='声音复刻测试项目',
        name_key='声音复刻测试项目',
        legacy_program_key='声音复刻测试项目',
        status='active',
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _clone_sample_facts(path: Path) -> dict[str, int | str]:
    identity = voice_cloning_service.hash_voice_clone_audio(path)
    return {
        'content_sha256': identity.content_sha256,
        'file_size_bytes': identity.file_size_bytes,
        'duration_ms': 12_000,
        'sample_rate_hz': 24_000,
        'channel_count': 1,
    }


def _embedding_result(values: list[float]) -> VoiceprintEmbeddingResult:
    return VoiceprintEmbeddingResult(
        embedding=values,
        provider_name='test-provider',
        provider_model_id='test-model',
        provider_message='embedding ready',
    )


def _library_profile(
    session: Session,
    *,
    profile_project: Project,
    source_project: Project,
    voice_name: str,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    last_used_at: datetime | None = None,
    status: str = 'ready',
    target_model: str = 'cosyvoice-v3.5-plus',
    source_status: str = 'ready',
    original_filename: str | None = None,
) -> VoiceCloneProfile:
    database_dir = Path(str(session.bind.url.database)).parent
    sample_path = database_dir / f'library-{uuid.uuid4()}.wav'
    sample_path.write_bytes(f'RIFF-{voice_name}'.encode())
    sample = VoiceCloneSample(
        display_name=f'{voice_name} 参考样本',
        project_id=source_project.id,
        program_key=source_project.legacy_program_key,
        program_name=source_project.name,
        original_filename=original_filename or f'{voice_name}.wav',
        audio_local_path=str(sample_path),
        **_clone_sample_facts(sample_path),
        clone_readiness_status=source_status,
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        project_id=profile_project.id,
        source_clone_sample_id=sample.id,
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model=target_model,
        voice_id=f'voice-{voice_name}' if status == 'ready' else None,
        voice_name=voice_name,
        status=status,
        created_at=created_at or datetime.utcnow(),
        updated_at=updated_at or created_at or datetime.utcnow(),
        last_used_at=last_used_at,
    )
    session.add(profile)
    session.flush()
    return profile


def test_audio_identity_is_content_based_and_reports_size(tmp_path: Path) -> None:
    first = tmp_path / 'first.wav'
    second = tmp_path / 'renamed.wav'
    first.write_bytes(b'RIFF-identical-audio')
    second.write_bytes(b'RIFF-identical-audio')

    first_identity = voice_cloning_service.hash_voice_clone_audio(first)
    second_identity = voice_cloning_service.hash_voice_clone_audio(second)

    assert first_identity.content_sha256 == second_identity.content_sha256
    assert first_identity.file_size_bytes == 20
    assert second_identity.file_size_bytes == 20


def test_same_project_and_content_reuses_sample(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding_results = iter([_embedding_result([1.0, 0.0])])
    monkeypatch.setattr(
        identity_service,
        'extract_voiceprint_embedding',
        lambda _path: next(embedding_results),
    )
    monkeypatch.setattr(
        voice_cloning_service,
        'get_media_duration_seconds',
        lambda _path: 12.0,
    )
    monkeypatch.setattr(
        voice_cloning_service,
        'ensure_sample_public_url',
        lambda path: f'https://example.test/{Path(path).name}',
    )
    first = tmp_path / 'first.wav'
    second = tmp_path / 'second.wav'
    first.write_bytes(b'RIFF-same-reference')
    second.write_bytes(b'RIFF-same-reference')

    created = voice_cloning_service.find_or_create_voice_clone_sample(
        session,
        '主持人',
        first,
        'first.wav',
        project_id=project.id,
    )
    reused = voice_cloning_service.find_or_create_voice_clone_sample(
        session,
        '  主持人  ',
        second,
        'second.wav',
        project_id=project.id,
    )

    assert created.reused_existing is False
    assert reused.reused_existing is True
    assert reused.sample.id == created.sample.id
    assert reused.sample.display_name == '主持人'
    assert session.query(VoiceCloneSample).count() == 1


def test_same_content_with_different_name_reuses_existing_sample(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        identity_service,
        'extract_voiceprint_embedding',
        lambda _path: _embedding_result([1.0, 0.0]),
    )
    monkeypatch.setattr(voice_cloning_service, 'get_media_duration_seconds', lambda _path: 12.0)
    monkeypatch.setattr(
        voice_cloning_service,
        'ensure_sample_public_url',
        lambda path: f'https://example.test/{Path(path).name}',
    )
    first_path = tmp_path / 'a.wav'
    second_path = tmp_path / 'b.wav'
    first_path.write_bytes(b'RIFF-identical-renamed-audio')
    second_path.write_bytes(first_path.read_bytes())

    first = voice_cloning_service.find_or_create_voice_clone_sample(
        session,
        '撒贝宁',
        first_path,
        first_path.name,
        project_id=project.id,
    )
    second = voice_cloning_service.find_or_create_voice_clone_sample(
        session,
        '小撒',
        second_path,
        second_path.name,
        project_id=project.id,
    )

    assert second.reused_existing is True
    assert second.sample.id == first.sample.id
    assert second.identity_id == first.identity_id
    assert second.match_type == 'exact_audio'


def test_same_name_and_content_reuses_sample_across_projects(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        identity_service,
        'extract_voiceprint_embedding',
        lambda _path: _embedding_result([1.0, 0.0]),
    )
    monkeypatch.setattr(
        voice_cloning_service,
        'get_media_duration_seconds',
        lambda _path: 12.0,
    )
    monkeypatch.setattr(
        voice_cloning_service,
        'ensure_sample_public_url',
        lambda path: f'https://example.test/{Path(path).name}',
    )
    other_project = Project(
        name='另一个声音复刻项目',
        name_key='另一个声音复刻项目',
        legacy_program_key='另一个声音复刻项目',
        status='active',
    )
    session.add(other_project)
    session.commit()
    session.refresh(other_project)
    first = tmp_path / 'first-project.wav'
    second = tmp_path / 'second-project.wav'
    first.write_bytes(b'RIFF-shared-across-projects')
    second.write_bytes(b'RIFF-shared-across-projects')

    first_result = voice_cloning_service.find_or_create_voice_clone_sample(
        session,
        '主持人',
        first,
        first.name,
        project_id=project.id,
    )
    second_result = voice_cloning_service.find_or_create_voice_clone_sample(
        session,
        '主持人',
        second,
        second.name,
        project_id=other_project.id,
    )

    assert first_result.sample.id == second_result.sample.id
    assert second_result.reused_existing is True
    assert session.query(VoiceCloneSample).count() == 1


def test_fourth_sample_is_rejected_by_identity_even_when_names_change(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        identity_service,
        'extract_voiceprint_embedding',
        lambda _path: _embedding_result([1.0, 0.0]),
    )
    monkeypatch.setattr(voice_cloning_service, 'get_media_duration_seconds', lambda _path: 12.0)
    public_upload_calls: list[Path] = []
    monkeypatch.setattr(
        voice_cloning_service,
        'ensure_sample_public_url',
        lambda path: public_upload_calls.append(path) or f'https://example.test/{path.name}',
    )
    paths = []
    for index in range(4):
        path = tmp_path / f'identity-{index}.wav'
        path.write_bytes(f'RIFF-distinct-recording-{index}'.encode())
        paths.append(path)

    for index, path in enumerate(paths[:3]):
        voice_cloning_service.find_or_create_voice_clone_sample(
            session,
            f'别名-{index}',
            path,
            path.name,
            project_id=project.id,
        )

    with pytest.raises(
        voice_cloning_service.VoiceCloneSampleLimitError,
        match='声音身份最多保留 3 条',
    ):
        voice_cloning_service.find_or_create_voice_clone_sample(
            session,
            '另一个名字',
            paths[3],
            paths[3].name,
            project_id=project.id,
        )

    assert public_upload_calls == paths[:3]
    assert session.query(VoiceCloneSample).count() == 3


def test_speaker_match_persists_resolver_facts_and_creates_pending_review(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embeddings = iter(([1.0, 0.0], [0.99, 0.01]))
    monkeypatch.setattr(
        identity_service,
        'extract_voiceprint_embedding',
        lambda _path: _embedding_result(list(next(embeddings))),
    )
    monkeypatch.setattr(voice_cloning_service, 'get_media_duration_seconds', lambda _path: 12.0)
    monkeypatch.setattr(
        voice_cloning_service,
        'ensure_sample_public_url',
        lambda path: f'https://example.test/{path.name}',
    )
    first_path = tmp_path / 'first-recording.wav'
    matched_path = tmp_path / 'matched-recording.wav'
    first_path.write_bytes(b'RIFF-first-recording')
    matched_path.write_bytes(b'RIFF-matched-recording')

    first = voice_cloning_service.find_or_create_voice_clone_sample(
        session,
        '主持人',
        first_path,
        first_path.name,
        project_id=project.id,
    )
    matched = voice_cloning_service.find_or_create_voice_clone_sample(
        session,
        '改名主持人',
        matched_path,
        matched_path.name,
        project_id=project.id,
    )
    review = session.query(VoiceCloneIdentityReview).one()

    assert matched.match_type == 'speaker_embedding'
    assert matched.identity_id == first.identity_id
    assert matched.sample.embedding == [0.99, 0.01]
    assert matched.sample.embedding_status == 'ready'
    assert matched.sample.embedding_provider_name == 'test-provider'
    assert matched.sample.embedding_model_id == 'test-model'
    assert matched.sample.embedding_message == 'embedding ready'
    assert review.sample_id == matched.sample.id
    assert review.candidate_identity_id == matched.identity_id
    assert review.status == 'pending'


def test_database_failure_rolls_back_new_identity_and_sample(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        identity_service,
        'extract_voiceprint_embedding',
        lambda _path: _embedding_result([1.0, 0.0]),
    )
    monkeypatch.setattr(voice_cloning_service, 'get_media_duration_seconds', lambda _path: 12.0)
    monkeypatch.setattr(voice_cloning_service, 'ensure_sample_public_url', lambda _path: None)
    path = tmp_path / 'database-failure.wav'
    path.write_bytes(b'RIFF-database-failure')

    with monkeypatch.context() as failure:
        failure.setattr(
            session,
            'commit',
            lambda: (_ for _ in ()).throw(RuntimeError('database offline')),
        )
        with pytest.raises(RuntimeError, match='database offline'):
            voice_cloning_service.find_or_create_voice_clone_sample(
                session,
                '新主持人',
                path,
                path.name,
                project_id=project.id,
            )

    assert session.query(VoiceCloneIdentity).count() == 0
    assert session.query(VoiceCloneSample).count() == 0
    assert session.query(VoiceCloneIdentityReview).count() == 0


def test_database_failure_rolls_back_matched_sample_and_review(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = VoiceCloneIdentity(
        display_name='原主持人',
        aliases=['原主持人'],
        reference_embedding=[1.0, 0.0],
        embedding_model_id='test-model',
    )
    session.add(identity)
    session.commit()
    monkeypatch.setattr(
        identity_service,
        'extract_voiceprint_embedding',
        lambda _path: _embedding_result([0.99, 0.01]),
    )
    monkeypatch.setattr(voice_cloning_service, 'get_media_duration_seconds', lambda _path: 12.0)
    monkeypatch.setattr(voice_cloning_service, 'ensure_sample_public_url', lambda _path: None)
    path = tmp_path / 'matched-database-failure.wav'
    path.write_bytes(b'RIFF-matched-database-failure')

    with monkeypatch.context() as failure:
        failure.setattr(
            session,
            'commit',
            lambda: (_ for _ in ()).throw(RuntimeError('database offline')),
        )
        with pytest.raises(RuntimeError, match='database offline'):
            voice_cloning_service.find_or_create_voice_clone_sample(
                session,
                '改名主持人',
                path,
                path.name,
                project_id=project.id,
            )

    assert session.query(VoiceCloneIdentity).count() == 1
    assert session.query(VoiceCloneSample).count() == 0
    assert session.query(VoiceCloneIdentityReview).count() == 0
    assert session.get(VoiceCloneIdentity, identity.id).aliases == ['原主持人']


def test_profile_uses_sample_identity_and_ignores_renamed_voice(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_path = tmp_path / 'host.wav'
    sample_path.write_bytes(b'RIFF-host-reuse')
    identity = VoiceCloneIdentity(
        display_name='主持人',
        aliases=['主持人', '小撒'],
    )
    session.add(identity)
    session.flush()
    sample = VoiceCloneSample(
        display_name='主持人',
        project_id=project.id,
        program_key=project.legacy_program_key,
        program_name=project.name,
        original_filename='host.wav',
        audio_local_path=str(sample_path),
        audio_public_url='https://example.test/host.wav',
        identity_id=identity.id,
        **_clone_sample_facts(sample_path),
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.commit()
    session.refresh(sample)
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    monkeypatch.setattr(settings, 'voice_cloning_provider', 'cosyvoice')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(
        settings,
        'voice_cloning_cosyvoice_target_model',
        'cosyvoice-v3.5-plus',
    )
    provider_calls = 0

    def fake_create(**_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return {'output': {'voice_id': 'voice-once'}}

    monkeypatch.setattr(voice_cloning_service, 'create_cosyvoice_voice', fake_create)
    first_payload = VoiceCloneProfileCreateRequest(
        clone_sample_id=sample.id,
        voice_name='Host Voice',
        provider='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
    )
    repeated_payload = VoiceCloneProfileCreateRequest(
        clone_sample_id=sample.id,
        voice_name='小撒',
        provider='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
    )

    first = voice_cloning_service.find_or_create_voice_clone_profile(
        session,
        first_payload,
        project_id=project.id,
    )
    second = voice_cloning_service.find_or_create_voice_clone_profile(
        session,
        repeated_payload,
        project_id=project.id,
    )

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert second.profile.id == first.profile.id
    assert first.profile.identity_id == identity.id
    assert provider_calls == 1


def test_library_groups_profiles_by_identity_not_name(
    session: Session,
    project: Project,
) -> None:
    identity = VoiceCloneIdentity(
        display_name='撒贝宁',
        aliases=['撒贝宁', '小撒'],
    )
    session.add(identity)
    session.flush()
    first = _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='撒贝宁',
        target_model='qwen3-tts-vc-2026-01-22',
    )
    second = _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='小撒',
        target_model='qwen3-tts-vc-2026-01-22',
    )
    first.identity_id = identity.id
    second.identity_id = identity.id
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    page = library_service.list_voice_clone_library(session, project.id)

    assert len(page.groups) == 1
    assert page.groups[0].group_key == identity.id
    assert page.groups[0].identity_id == identity.id
    assert page.groups[0].identity_display_name == '撒贝宁'
    assert page.groups[0].identity_aliases == ['撒贝宁', '小撒']
    assert page.groups[0].display_name == '撒贝宁'
    assert page.groups[0].version_count == 2


def test_library_uses_legacy_name_group_key_without_identity(
    session: Session,
    project: Project,
) -> None:
    _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='  旧 音色  ',
    )
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    group = library_service.list_voice_clone_library(session, project.id).groups[0]

    assert group.group_key == 'legacy:旧 音色'
    assert group.identity_id is None
    assert group.identity_display_name is None
    assert group.identity_aliases == []


def test_library_searches_identity_display_name_and_aliases(
    session: Session,
    project: Project,
) -> None:
    identity = VoiceCloneIdentity(
        display_name='钟老师',
        aliases=['小钟'],
    )
    session.add(identity)
    session.flush()
    profile = _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='快照音色',
        original_filename='reference.wav',
    )
    profile.identity_id = identity.id
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    by_display_name = library_service.list_voice_clone_library(
        session, project.id, query='钟老师',
    )
    by_alias = library_service.list_voice_clone_library(
        session, project.id, query='小钟',
    )

    assert [group.group_key for group in by_display_name.groups] == [identity.id]
    assert [group.group_key for group in by_alias.groups] == [identity.id]


def test_library_batch_loads_identities_for_profile_summaries(
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = [
        VoiceCloneIdentity(display_name=f'身份 {index}', aliases=[])
        for index in range(2)
    ]
    session.add_all(identities)
    session.flush()
    for index, identity in enumerate(identities):
        profile = _library_profile(
            session,
            profile_project=project,
            source_project=project,
            voice_name=f'快照 {index}',
        )
        profile.identity_id = identity.id
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')
    identity_gets = 0
    original_get = session.get

    def count_identity_get(model, ident, *args, **kwargs):
        nonlocal identity_gets
        if model is VoiceCloneIdentity:
            identity_gets += 1
        return original_get(model, ident, *args, **kwargs)

    monkeypatch.setattr(session, 'get', count_identity_get)

    page = library_service.list_voice_clone_library(session, project.id)

    assert len(page.groups) == 2
    assert identity_gets == 0


def test_library_keeps_unusable_groups_with_explicit_counts(
    session: Session,
    project: Project,
) -> None:
    selectable = _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='可选音色',
    )
    _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='拒绝样本音色',
        source_status='rejected',
    )
    _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='创建中音色',
        status='creating',
    )
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    page = library_service.list_voice_clone_library(session, project.id)

    assert len(page.groups) == page.total == 3
    usable = [group.recommended_profile for group in page.groups if group.recommended_profile.selectable]
    assert [profile.id for profile in usable] == [selectable.id]
    assert usable[0].source_sample_ready is True
    assert page.available_group_count == 1
    assert page.exception_group_count == 2


def test_library_prioritizes_current_project_created_profiles(
    session: Session,
    project: Project,
) -> None:
    other_project = Project(name='共享项目', name_key='共享项目', status='active')
    session.add(other_project)
    session.flush()
    now = datetime.utcnow()
    shared = _library_profile(
        session,
        profile_project=other_project,
        source_project=other_project,
        voice_name='共享且更新',
        created_at=now,
        updated_at=now,
    )
    current = _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='当前项目创建',
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    page = library_service.list_voice_clone_library(session, project.id)

    assert [group.recommended_profile.id for group in page.groups] == [current.id, shared.id]
    assert page.groups[0].recommended_profile.project_scope == 'current_project'


def test_library_prioritizes_profiles_used_by_current_project(
    session: Session,
    project: Project,
) -> None:
    other_project = Project(name='共享使用项目', name_key='共享使用项目', status='active')
    session.add(other_project)
    session.flush()
    now = datetime.utcnow()
    used = _library_profile(
        session,
        profile_project=other_project,
        source_project=other_project,
        voice_name='本项目已使用',
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    newer_shared = _library_profile(
        session,
        profile_project=other_project,
        source_project=other_project,
        voice_name='共享较新',
        created_at=now,
        updated_at=now,
    )
    session.add(VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=used.id,
        target_model=used.target_model,
        input_text='已生成',
        status='ready',
    ))
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    page = library_service.list_voice_clone_library(session, project.id)

    assert [group.recommended_profile.id for group in page.groups] == [used.id, newer_shared.id]
    assert page.groups[0].recommended_profile.project_scope == 'used_in_current_project'


def test_library_searches_source_filename_and_project_name_before_pagination(
    session: Session,
    project: Project,
) -> None:
    source_project = Project(name='专题配音资料库', name_key='专题配音资料库', status='active')
    session.add(source_project)
    session.flush()
    filename_match = _library_profile(
        session,
        profile_project=source_project,
        source_project=source_project,
        voice_name='不含检索词',
        original_filename='专题片旁白.wav',
    )
    project_match = _library_profile(
        session,
        profile_project=source_project,
        source_project=source_project,
        voice_name='另一个不含检索词',
    )
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    filename_page = library_service.list_voice_clone_library(
        session, project.id, query='旁白', limit=1,
    )
    project_page = library_service.list_voice_clone_library(
        session, project.id, query='专题配音', limit=1,
    )
    project_second_page = library_service.list_voice_clone_library(
        session,
        project.id,
        query='专题配音',
        limit=1,
        cursor=project_page.next_cursor,
    )

    assert [group.recommended_profile.id for group in filename_page.groups] == [filename_match.id]
    assert filename_page.groups[0].recommended_profile.source_original_filename == '专题片旁白.wav'
    assert filename_page.groups[0].recommended_profile.source_project_name == source_project.name
    assert project_page.total == 2
    assert project_page.next_cursor is not None
    assert {
        group.recommended_profile.id
        for group in [*project_page.groups, *project_second_page.groups]
    } == {filename_match.id, project_match.id}


def test_library_project_priority_order_is_stable_across_pages(
    session: Session,
    project: Project,
) -> None:
    timestamp = datetime.utcnow()
    profiles = [
        _library_profile(
            session,
            profile_project=project,
            source_project=project,
            voice_name=f'分页音色 {index:02d}',
            created_at=timestamp,
            updated_at=timestamp,
        )
        for index in range(31)
    ]
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    first_page = library_service.list_voice_clone_library(session, project.id, limit=30)
    second_page = library_service.list_voice_clone_library(
        session, project.id, limit=30, cursor=first_page.next_cursor,
    )
    first_ids = [group.recommended_profile.id for group in first_page.groups]
    second_ids = [group.recommended_profile.id for group in second_page.groups]

    assert set(first_ids).isdisjoint(second_ids)
    assert first_ids + second_ids == sorted((profile.id for profile in profiles), reverse=True)


def test_group_versions_use_the_same_project_aware_rank(
    session: Session,
    project: Project,
) -> None:
    other_project = Project(name='音色版本共享项目', name_key='音色版本共享项目', status='active')
    session.add(other_project)
    session.flush()
    now = datetime.utcnow()
    shared_newer = _library_profile(
        session,
        profile_project=other_project,
        source_project=other_project,
        voice_name='同名版本',
        created_at=now,
        updated_at=now,
    )
    current_older = _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='同名版本',
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    group = library_service.list_voice_clone_library(session, project.id).groups[0]
    versions = library_service.list_voice_clone_group_profiles(
        session, project.id, group.group_key,
    )

    assert group.recommended_profile.id == current_older.id
    assert [profile.id for profile in versions] == [current_older.id, shared_newer.id]


def test_current_project_versions_sort_by_recency_without_usage_subtier(
    session: Session,
    project: Project,
) -> None:
    now = datetime.utcnow()
    used_older = _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='当前项目同名版本',
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=2),
        last_used_at=now - timedelta(days=2),
    )
    newer = _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='当前项目同名版本',
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
        last_used_at=now - timedelta(days=1),
    )
    session.add(VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=used_older.id,
        target_model=used_older.target_model,
        input_text='旧版本曾经使用',
        status='ready',
    ))
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    group = library_service.list_voice_clone_library(session, project.id).groups[0]
    versions = library_service.list_voice_clone_group_profiles(
        session,
        project.id,
        group.group_key,
    )

    assert group.recommended_profile.id == newer.id
    assert [profile.id for profile in versions] == [newer.id, used_older.id]


def test_creating_profiles_are_also_visible_in_the_shared_library(
    session: Session,
    project: Project,
) -> None:
    other_project = Project(name='其他创建项目', name_key='其他创建项目', status='active')
    session.add(other_project)
    session.flush()
    current_creating = _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='当前创建中',
        status='creating',
    )
    _library_profile(
        session,
        profile_project=other_project,
        source_project=other_project,
        voice_name='其他创建中',
        status='creating',
    )
    _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='可用音色',
    )
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    creating = library_service.list_creating_voice_clone_profiles(session, project.id)
    library = library_service.list_voice_clone_library(session, project.id)

    assert [profile.id for profile in creating] == [current_creating.id]
    assert current_creating.id in {
        group.recommended_profile.id for group in library.groups
    }
    assert library.total == 3


def test_creating_profiles_exclude_stale_records_but_keep_one_hour_boundary(
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_service = importlib.import_module('app.services.voice_clone_library')
    reference_now = datetime(2026, 7, 29, 12, 0, 0)

    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls) -> datetime:
            return reference_now

    monkeypatch.setattr(library_service, 'datetime', FrozenDateTime)
    monkeypatch.setattr(importlib.import_module('app.services.voice_retention'), 'utc_now', lambda: reference_now)
    fresh = _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='新鲜创建中',
        status='creating',
        created_at=reference_now - timedelta(minutes=30),
        updated_at=reference_now - timedelta(minutes=30),
    )
    boundary = _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='一小时边界创建中',
        status='creating',
        created_at=reference_now - timedelta(hours=1),
        updated_at=reference_now - timedelta(hours=1),
    )
    _library_profile(
        session,
        profile_project=project,
        source_project=project,
        voice_name='陈旧创建中',
        status='creating',
        created_at=reference_now - timedelta(hours=1, seconds=1),
        updated_at=reference_now - timedelta(hours=1, seconds=1),
    )
    session.commit()

    creating = library_service.list_creating_voice_clone_profiles(session, project.id)

    assert {profile.id for profile in creating} == {fresh.id, boundary.id}


def test_library_groups_same_name_profiles_and_selects_recommended(
    session: Session,
    project: Project,
) -> None:
    now = datetime.utcnow()
    source_path = Path(str(session.bind.url.database)).parent / 'host.wav'
    source_path.write_bytes(b'RIFF-host-library')
    source_sample = VoiceCloneSample(
        display_name='主持人男参考样本',
        project_id=project.id,
        original_filename='host.wav',
        audio_local_path=str(source_path),
        **_clone_sample_facts(source_path),
        clone_readiness_status='ready',
    )
    session.add(source_sample)
    session.flush()
    host_profiles = [
        VoiceCloneProfile(
            project_id=project.id,
            source_clone_sample_id=source_sample.id,
            voice_name='主持人男',
            provider='qwen',
            provider_model_family='qwen',
            target_model='qwen3-tts-vc-2026-01-22',
            voice_id=f'host-{index}',
            status='ready',
            created_at=now - timedelta(hours=3 - index),
            updated_at=now - timedelta(days=3 - index),
            last_used_at=now - timedelta(days=3 - index),
        )
        for index in range(3)
    ]
    other_profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=source_sample.id,
        voice_name='王冰冰',
        provider='qwen',
        provider_model_family='qwen',
        target_model='qwen3-tts-vc-2026-01-22',
        voice_id='wang-bingbing',
        status='ready',
        created_at=now,
        updated_at=now,
        last_used_at=now,
    )
    session.add_all([*host_profiles, other_profile])
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    page = library_service.list_voice_clone_library(
        session,
        project.id,
        query=None,
        limit=30,
        cursor=None,
    )

    assert len(page.groups) == 2
    host_group = next(group for group in page.groups if group.display_name == '主持人男')
    assert host_group.version_count == 3
    assert host_group.recommended_profile.id == host_profiles[-1].id


def test_sample_page_uses_non_overlapping_offset_cursor(
    session: Session,
    project: Project,
) -> None:
    now = datetime.utcnow()
    samples = [
        VoiceCloneSample(
            display_name=f'样本 {index:02d}',
            project_id=project.id,
            program_key=project.legacy_program_key,
            program_name=project.name,
            original_filename=f'sample-{index:02d}.wav',
            audio_local_path=f'/tmp/sample-{index:02d}.wav',
            clone_readiness_status='ready',
            created_at=now - timedelta(minutes=index),
        )
        for index in range(35)
    ]
    session.add_all(samples)
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    first_page = library_service.list_voice_clone_sample_page(
        session,
        project.id,
        query=None,
        status=None,
        limit=30,
        cursor=None,
    )
    second_page = library_service.list_voice_clone_sample_page(
        session,
        project.id,
        query=None,
        status=None,
        limit=30,
        cursor=first_page.next_cursor,
    )

    first_ids = {item.id for item in first_page.items}
    second_ids = {item.id for item in second_page.items}
    assert len(first_page.items) == 30
    assert first_page.next_cursor is not None
    assert len(second_page.items) == 5
    assert first_ids.isdisjoint(second_ids)


def test_cleanup_plan_is_dry_run_and_leaves_rows_and_files_untouched(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    first_path = tmp_path / 'first-duplicate.wav'
    second_path = tmp_path / 'second-duplicate.wav'
    first_path.write_bytes(b'RIFF-cleanup-duplicate')
    second_path.write_bytes(b'RIFF-cleanup-duplicate')
    now = datetime.utcnow()
    first_sample = VoiceCloneSample(
        display_name='主持人',
        project_id=project.id,
        original_filename=first_path.name,
        audio_local_path=str(first_path),
        **_clone_sample_facts(first_path),
        clone_readiness_status='ready',
        last_used_at=now - timedelta(days=1),
    )
    second_sample = VoiceCloneSample(
        display_name='  主持人  ',
        project_id=project.id,
        original_filename=second_path.name,
        audio_local_path=str(second_path),
        **_clone_sample_facts(second_path),
        clone_readiness_status='ready',
        last_used_at=now,
    )
    session.add_all([first_sample, second_sample])
    session.commit()
    session.refresh(first_sample)
    session.refresh(second_sample)
    session.add_all([
        VoiceCloneProfile(
            project_id=project.id,
            source_clone_sample_id=first_sample.id,
            voice_name='主持人',
            provider='cosyvoice',
            provider_model_family='cosyvoice',
            target_model='cosyvoice-v3.5-plus',
            voice_id='voice-first',
            status='ready',
        ),
        VoiceCloneProfile(
            project_id=project.id,
            source_clone_sample_id=second_sample.id,
            voice_name='主持人',
            provider='cosyvoice',
            provider_model_family='cosyvoice',
            target_model='cosyvoice-v3.5-plus',
            voice_id='voice-second',
            status='ready',
        ),
    ])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert len(plan.duplicate_groups) == 1
    assert plan.duplicate_groups[0].canonical_sample_id == second_sample.id
    assert session.query(VoiceCloneSample).count() == 2
    assert session.query(VoiceCloneProfile).count() == 2
    assert first_path.exists()
    assert second_path.exists()


@pytest.mark.parametrize(
    ('case_name', 'status', 'voice_id', 'target_model', 'created_delta', 'reason'),
    [
        ('failed', 'failed', 'voice-failed', settings.voice_cloning_qwen_target_model, timedelta(), 'profile_failed'),
        ('deleted', 'deleted', 'voice-deleted', settings.voice_cloning_qwen_target_model, timedelta(), 'profile_deleted'),
        ('ready-without-voice-id', 'ready', None, settings.voice_cloning_qwen_target_model, timedelta(), 'missing_voice_id'),
        ('creating-over-one-hour', 'creating', None, settings.voice_cloning_qwen_target_model, timedelta(hours=1, seconds=1), 'creating_timeout'),
        ('missing-provider-model', 'ready', 'voice-old-model', 'retired-model', timedelta(), 'unsupported_provider_model'),
    ],
)
def test_cleanup_plan_marks_each_invalid_profile_reason(
    tmp_path: Path,
    session: Session,
    project: Project,
    case_name: str,
    status: str,
    voice_id: str | None,
    target_model: str,
    created_delta: timedelta,
    reason: str,
) -> None:
    sample_path = tmp_path / f'{case_name}.wav'
    sample_path.write_bytes(b'RIFF-valid-reference')
    sample = VoiceCloneSample(
        display_name=case_name,
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        **_clone_sample_facts(sample_path),
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=target_model,
        voice_id=voice_id,
        voice_name=case_name,
        status=status,
        created_at=datetime.utcnow() - created_delta,
        updated_at=datetime.utcnow() - created_delta,
    )
    session.add(profile)
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert [
        (entry.profile_id, entry.reason)
        for entry in plan.invalid_profiles
    ] == [(profile.id, reason)]


def test_cleanup_creating_timeout_uses_updated_at_not_created_at(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    sample_path = tmp_path / 'creating-updated-at.wav'
    sample_path.write_bytes(b'RIFF-creating-updated-at')
    sample = VoiceCloneSample(
        display_name='创建时间判断样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    active_sample_path = tmp_path / 'creating-active.wav'
    active_sample_path.write_bytes(b'RIFF-creating-active')
    active_sample = VoiceCloneSample(
        display_name='活跃创建样本',
        project_id=project.id,
        original_filename=active_sample_path.name,
        audio_local_path=str(active_sample_path),
        clone_readiness_status='ready',
    )
    session.add(active_sample)
    session.flush()
    stale = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=settings.voice_cloning_qwen_target_model,
        voice_name='最近创建但早已停滞',
        status='creating',
        created_at=datetime.utcnow() - timedelta(minutes=30),
        updated_at=datetime.utcnow() - timedelta(hours=2),
    )
    active = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=active_sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=settings.voice_cloning_qwen_target_model,
        voice_name='很早创建但最近更新',
        status='creating',
        created_at=datetime.utcnow() - timedelta(hours=2),
        updated_at=datetime.utcnow() - timedelta(minutes=30),
    )
    session.add_all([stale, active])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert [(entry.profile_id, entry.reason) for entry in plan.invalid_profiles] == [
        (stale.id, 'creating_timeout'),
    ]


@pytest.mark.parametrize(
    ('provider', 'target_model'),
    [
        ('', settings.voice_cloning_qwen_target_model),
        ('qwen', ''),
    ],
)
def test_cleanup_marks_blank_provider_or_target_model_invalid(
    tmp_path: Path,
    session: Session,
    project: Project,
    provider: str,
    target_model: str,
) -> None:
    sample_path = tmp_path / f'blank-config-{provider or "provider"}.wav'
    sample_path.write_bytes(b'RIFF-blank-config')
    sample = VoiceCloneSample(
        display_name='配置缺失样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider=provider,
        provider_model_family=provider,
        target_model=target_model,
        voice_id='voice-blank-config',
        voice_name='配置缺失音色',
        status='ready',
    )
    session.add(profile)
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert [(entry.profile_id, entry.reason) for entry in plan.invalid_profiles] == [
        (profile.id, 'unsupported_provider_model'),
    ]


@pytest.mark.parametrize('source_kind', ['voiceprint-rejected', 'voiceprint-missing', 'clone-missing'])
def test_cleanup_marks_unavailable_source_profile_invalid(
    tmp_path: Path,
    session: Session,
    project: Project,
    source_kind: str,
) -> None:
    profile_kwargs: dict[str, str] = {}
    if source_kind == 'voiceprint-rejected':
        person = Person(name='旧声纹人物', project_id=project.id)
        session.add(person)
        session.flush()
        source_path = tmp_path / 'voiceprint-rejected.wav'
        source_path.write_bytes(b'RIFF-voiceprint-rejected')
        source = VoiceprintSample(
            person_id=person.id,
            original_filename=source_path.name,
            audio_local_path=str(source_path),
            clone_readiness_status='rejected',
        )
        session.add(source)
        session.flush()
        profile_kwargs['source_sample_id'] = source.id
    elif source_kind == 'voiceprint-missing':
        profile_kwargs['source_sample_id'] = 'missing-voiceprint-sample'
    else:
        profile_kwargs['source_clone_sample_id'] = 'missing-clone-sample'
    profile = VoiceCloneProfile(
        project_id=project.id,
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model=settings.voice_cloning_cosyvoice_target_model,
        voice_id=f'voice-{source_kind}',
        voice_name=f'来源失效 {source_kind}',
        status='ready',
        **profile_kwargs,
    )
    session.add(profile)
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert [(entry.profile_id, entry.reason) for entry in plan.invalid_profiles] == [
        (profile.id, 'source_sample_unavailable'),
    ]


@pytest.mark.parametrize(
    ('case_name', 'readiness', 'file_bytes', 'stored_hash', 'reason'),
    [
        ('rejected-sample', 'rejected', b'RIFF-rejected', None, 'source_sample_rejected'),
        ('missing-file', 'ready', None, None, 'sample_file_missing'),
        ('zero-byte-file', 'ready', b'', None, 'sample_file_empty'),
        ('hash-mismatch', 'ready', b'RIFF-current', '0' * 64, 'sample_identity_untrusted'),
    ],
)
def test_cleanup_plan_marks_each_invalid_sample_reason(
    tmp_path: Path,
    session: Session,
    project: Project,
    case_name: str,
    readiness: str,
    file_bytes: bytes | None,
    stored_hash: str | None,
    reason: str,
) -> None:
    sample_path = tmp_path / f'{case_name}.wav'
    if file_bytes is not None:
        sample_path.write_bytes(file_bytes)
    sample = VoiceCloneSample(
        display_name=case_name,
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        content_sha256=stored_hash,
        clone_readiness_status=readiness,
    )
    session.add(sample)
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert [
        (entry.sample_id, entry.reason)
        for entry in plan.invalid_samples
    ] == [(sample.id, reason)]


@pytest.mark.parametrize('initial_status', ['warning', 'unknown'])
def test_cleanup_reprobes_warning_or_unknown_and_retains_ready_sample(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    initial_status: str,
) -> None:
    sample_path = tmp_path / f'reprobe-{initial_status}.wav'
    sample_path.write_bytes(b'RIFF-reprobe-ready')
    sample = VoiceCloneSample(
        display_name='重探样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        sample_rate_hz=24_000,
        channel_count=1,
        clone_readiness_status=initial_status,
    )
    session.add(sample)
    session.commit()
    monkeypatch.setattr(
        'app.services.voice_clone_cleanup.get_media_duration_seconds',
        lambda _path: 12.0,
    )
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert plan.invalid_samples == ()
    assert [
        (entry.sample_id, entry.status)
        for entry in plan.readiness_updates
    ] == [(sample.id, 'ready')]
    session.refresh(sample)
    assert sample.clone_readiness_status == initial_status


def test_cleanup_reprobes_warning_hash_mismatch_before_retaining_sample(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_path = tmp_path / 'reprobe-warning-new-content.wav'
    sample_path.write_bytes(b'RIFF-reprobe-warning-new-content')
    sample = VoiceCloneSample(
        display_name='指纹变化待重探样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        content_sha256='0' * 64,
        sample_rate_hz=24_000,
        channel_count=1,
        clone_readiness_status='warning',
    )
    session.add(sample)
    session.commit()
    monkeypatch.setattr(
        'app.services.voice_clone_cleanup.get_media_duration_seconds',
        lambda _path: 12.0,
    )
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert plan.invalid_samples == ()
    assert len(plan.readiness_updates) == 1
    assert plan.readiness_updates[0].sample_id == sample.id
    assert plan.readiness_updates[0].content_sha256 != '0' * 64


def test_cleanup_marks_ready_sample_with_unreadable_audio_invalid(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_path = tmp_path / 'ready-but-undecodable.wav'
    sample_path.write_bytes(b'not-decodable-audio')
    sample = VoiceCloneSample(
        display_name='不可解码 ready 样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.commit()
    monkeypatch.setattr(
        'app.services.voice_clone_cleanup.get_media_duration_seconds',
        lambda _path: 0.0,
    )
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert [(entry.sample_id, entry.reason) for entry in plan.invalid_samples] == [
        (sample.id, 'source_sample_rejected'),
    ]


def test_cleanup_failed_reprobe_schedules_sample_and_linked_profile(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_path = tmp_path / 'reprobe-rejected.wav'
    sample_path.write_bytes(b'RIFF-reprobe-rejected')
    sample = VoiceCloneSample(
        display_name='重探失败样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        sample_rate_hz=24_000,
        channel_count=1,
        clone_readiness_status='warning',
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=settings.voice_cloning_qwen_target_model,
        voice_id='voice-reprobe-rejected',
        voice_name='重探失败音色',
        status='ready',
    )
    session.add(profile)
    session.commit()
    monkeypatch.setattr(
        'app.services.voice_clone_cleanup.get_media_duration_seconds',
        lambda _path: 0.0,
    )
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert [(entry.sample_id, entry.reason) for entry in plan.invalid_samples] == [
        (sample.id, 'source_sample_rejected'),
    ]
    assert [(entry.profile_id, entry.reason) for entry in plan.invalid_profiles] == [
        (profile.id, 'source_sample_rejected'),
    ]


def test_cleanup_failed_reprobe_decode_error_schedules_sample(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_path = tmp_path / 'reprobe-decode-error.wav'
    sample_path.write_bytes(b'not-decodable-audio')
    sample = VoiceCloneSample(
        display_name='解码失败样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='unknown',
    )
    session.add(sample)
    session.commit()
    monkeypatch.setattr(
        'app.services.voice_clone_cleanup.get_media_duration_seconds',
        lambda _path: (_ for _ in ()).throw(RuntimeError('decode failed')),
    )
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert [(entry.sample_id, entry.reason) for entry in plan.invalid_samples] == [
        (sample.id, 'source_sample_rejected'),
    ]


def test_cleanup_preserves_healthy_sample_used_by_selectable_profile(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    sample_path = tmp_path / 'shared-healthy.wav'
    sample_path.write_bytes(b'RIFF-shared-healthy')
    sample = VoiceCloneSample(
        display_name='共享健康样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        **_clone_sample_facts(sample_path),
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    invalid_profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model=settings.voice_cloning_cosyvoice_target_model,
        voice_id=None,
        voice_name='失效音色',
        status='failed',
    )
    selectable_profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model=settings.voice_cloning_cosyvoice_target_model,
        voice_id='voice-shared-healthy',
        voice_name='可用音色',
        status='ready',
    )
    session.add_all([invalid_profile, selectable_profile])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert {entry.profile_id for entry in plan.invalid_profiles} == {
        invalid_profile.id,
    }
    assert sample.id not in {entry.sample_id for entry in plan.invalid_samples}


def test_cleanup_schedules_healthy_sample_orphaned_by_invalid_profile(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    sample_path = tmp_path / 'orphaned-healthy.wav'
    sample_bytes = b'RIFF-orphaned-healthy'
    sample_path.write_bytes(sample_bytes)
    sample = VoiceCloneSample(
        display_name='孤儿健康样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    invalid_profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=settings.voice_cloning_qwen_target_model,
        voice_id='voice-orphaned-healthy',
        voice_name='失效孤儿音色',
        status='failed',
    )
    session.add(invalid_profile)
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert [(entry.sample_id, entry.reason) for entry in plan.invalid_samples] == [
        (sample.id, 'source_sample_orphaned'),
    ]
    assert plan.scheduled_sample_count == 1
    assert plan.releasable_bytes == len(sample_bytes)
    assert {
        target.resource_key for target in plan.deletion_targets
    } >= {f'local:{sample_path}'}


def test_cleanup_preserves_sample_referenced_by_active_creating_profile(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    sample_path = tmp_path / 'active-creating-reference.wav'
    sample_path.write_bytes(b'RIFF-active-creating-reference')
    sample = VoiceCloneSample(
        display_name='创建中引用样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    failed_profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=settings.voice_cloning_qwen_target_model,
        voice_id='voice-failed-shared-creating',
        voice_name='已失败版本',
        status='failed',
    )
    active_creating_profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=settings.voice_cloning_qwen_target_model,
        voice_name='活跃创建版本',
        status='creating',
        created_at=datetime.utcnow() - timedelta(hours=2),
        updated_at=datetime.utcnow() - timedelta(minutes=30),
    )
    session.add_all([failed_profile, active_creating_profile])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert [(entry.profile_id, entry.reason) for entry in plan.invalid_profiles] == [
        (failed_profile.id, 'profile_failed'),
    ]
    assert sample.id not in {entry.sample_id for entry in plan.invalid_samples}


def test_cleanup_invalid_empty_path_reports_zero_releasable_bytes(
    session: Session,
    project: Project,
) -> None:
    sample = VoiceCloneSample(
        display_name='空路径样本',
        project_id=project.id,
        original_filename='empty-path.wav',
        audio_local_path='',
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert [(entry.sample_id, entry.reason) for entry in plan.invalid_samples] == [
        (sample.id, 'sample_file_missing'),
    ]
    assert plan.releasable_bytes == 0


def test_hard_cleanup_plan_detaches_success_and_deletes_no_output_failures(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    sample_path = tmp_path / 'record-disposition.wav'
    sample_path.write_bytes(b'RIFF-record-disposition')
    sample = VoiceCloneSample(
        display_name='记录处分样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=settings.voice_cloning_qwen_target_model,
        voice_id='voice-invalid-records',
        voice_name='记录处分音色',
        status='failed',
    )
    session.add(profile)
    session.flush()
    successful = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='成功输出',
        output_audio_local_path=str(tmp_path / 'successful.wav'),
        status='ready',
    )
    failed = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='失败无输出',
        status='failed',
    )
    processing = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='处理中无输出',
        status='processing',
    )
    session.add_all([successful, failed, processing])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert plan.detach_record_ids == (successful.id,)
    assert set(plan.delete_record_ids) == {failed.id, processing.id}


def test_cleanup_plan_fingerprint_is_stable_and_covers_foreign_key_baseline(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    sample_path = tmp_path / 'fingerprint.wav'
    sample_path.write_bytes(b'RIFF-fingerprint')
    sample = VoiceCloneSample(
        display_name='指纹样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    profiles = [
        VoiceCloneProfile(
            project_id=project.id,
            source_clone_sample_id=sample.id,
            provider='qwen',
            provider_model_family='qwen',
            target_model=settings.voice_cloning_qwen_target_model,
            voice_id=f'voice-fingerprint-{index}',
            voice_name=f'指纹音色 {index}',
            status='ready',
        )
        for index in range(2)
    ]
    session.add_all(profiles)
    session.flush()
    record = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profiles[0].id,
        target_model=profiles[0].target_model,
        input_text='指纹基线',
        status='ready',
    )
    session.add(record)
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    first = cleanup_service.build_voice_clone_cleanup_plan(session)
    repeated = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert first.plan_version == 1
    assert len(first.plan_fingerprint) == 64
    assert first.plan_fingerprint == repeated.plan_fingerprint
    assert first.foreign_key_baseline == repeated.foreign_key_baseline

    record.voice_clone_profile_id = profiles[1].id
    session.commit()
    changed = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert changed.foreign_key_baseline != first.foreign_key_baseline
    assert changed.plan_fingerprint != first.plan_fingerprint


def test_hard_cleanup_plan_snapshots_provider_and_local_deletion_targets(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    sample_path = tmp_path / 'invalid-source.wav'
    preview_path = tmp_path / 'invalid-preview.wav'
    sample_path.write_bytes(b'RIFF-invalid-source')
    preview_path.write_bytes(b'RIFF-invalid-preview')
    sample = VoiceCloneSample(
        display_name='失效源样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='rejected',
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=settings.voice_cloning_qwen_target_model,
        voice_id='voice-invalid-targets',
        voice_name='失效目标音色',
        status='ready',
        preview_audio_local_path=str(preview_path),
    )
    session.add(profile)
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert {
        (
            target.kind,
            target.resource_key,
            target.voice_id,
            target.local_path,
        )
        for target in plan.deletion_targets
    } == {
        (
            'provider',
            f'provider:qwen:{settings.voice_cloning_qwen_target_model}:voice-invalid-targets',
            'voice-invalid-targets',
            None,
        ),
        ('local', f'local:{preview_path}', None, str(preview_path)),
        ('local', f'local:{sample_path}', None, str(sample_path)),
    }


def test_hard_cleanup_plan_enqueues_only_unshared_owned_sample_oss_keys(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        'oss_public_base_url',
        'https://speaker-test.example.com/assets',
    )
    shared_url = 'https://speaker-test.example.com/assets/voices/shared.wav'
    output_url = 'https://speaker-test.example.com/assets/voices/output.wav'
    delete_url = 'https://speaker-test.example.com/assets/voices/delete.wav'
    external_url = 'https://external.example.com/voices/external.wav'
    rows = []
    for index, (status, public_url) in enumerate((
        ('rejected', shared_url),
        ('ready', shared_url),
        ('rejected', output_url),
        ('rejected', delete_url),
        ('rejected', delete_url),
        ('rejected', external_url),
    )):
        path = tmp_path / f'oss-source-{index}.wav'
        path.write_bytes(f'RIFF-oss-source-{index}'.encode())
        rows.append(VoiceCloneSample(
            display_name=f'OSS 样本 {index}',
            project_id=project.id,
            original_filename=path.name,
            audio_local_path=str(path),
            audio_public_url=public_url,
            clone_readiness_status=status,
        ))
    session.add_all(rows)
    session.add(VoiceCloneSynthesisRecord(
        project_id=project.id,
        target_model=settings.voice_cloning_qwen_target_model,
        input_text='保留 OSS 输出',
        output_audio_public_url=output_url,
        status='ready',
    ))
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)
    oss_targets = [
        target for target in plan.deletion_targets if target.kind == 'oss'
    ]

    assert [
        (target.resource_key, target.oss_object_key)
        for target in oss_targets
    ] == [('oss:voices/delete.wav', 'voices/delete.wav')]


def test_deletion_task_drain_completes_provider_oss_local_and_missing(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    managed_root = tmp_path / 'voice-cloning'
    managed_root.mkdir()
    local_file = managed_root / 'preview.wav'
    local_file.write_bytes(b'preview')
    missing_file = managed_root / 'already-missing.wav'
    monkeypatch.setattr(
        settings,
        'local_voice_clone_library_dir',
        str(managed_root),
    )
    monkeypatch.setattr(settings, 'oss_region', 'cn-test')
    monkeypatch.setattr(settings, 'oss_bucket', 'speaker-test')
    monkeypatch.setattr(settings, 'oss_public_base_url', 'https://oss.example.com')
    monkeypatch.setattr(settings, 'oss_access_key_id', 'configured')
    monkeypatch.setattr(settings, 'oss_access_key_secret', 'configured')
    provider_calls = []
    oss_calls = []
    monkeypatch.setattr(
        cleanup_service,
        '_delete_provider_voice',
        lambda provider, voice_id, **kwargs: provider_calls.append(
            (provider, voice_id, kwargs['target_model'])
        ),
    )
    monkeypatch.setattr(
        cleanup_service,
        'delete_oss_object',
        lambda object_key: oss_calls.append(object_key),
    )
    tasks = [
        VoiceCloneDeletionTask(
            project_id=project.id,
            kind='provider',
            resource_key='provider:qwen:model:voice-1',
            provider='qwen',
            target_model='model',
            voice_id='voice-1',
        ),
        VoiceCloneDeletionTask(
            project_id=project.id,
            kind='oss',
            resource_key='oss:voices/source.wav',
            oss_object_key='voices/source.wav',
        ),
        VoiceCloneDeletionTask(
            project_id=project.id,
            kind='local',
            resource_key=f'local:{local_file}',
            local_path=str(local_file),
        ),
        VoiceCloneDeletionTask(
            project_id=project.id,
            kind='local',
            resource_key=f'local:{missing_file}',
            local_path=str(missing_file),
        ),
    ]
    session.add_all(tasks)
    session.commit()

    result = cleanup_service.drain_voice_clone_deletion_tasks(session, limit=20)

    assert result.attempted_count == 4
    assert result.completed_count == 4
    assert result.failed_count == 0
    assert provider_calls == [('qwen', 'voice-1', 'model')]
    assert oss_calls == ['voices/source.wav']
    assert not local_file.exists()
    assert session.query(VoiceCloneDeletionTask).count() == 0


def test_deletion_task_drain_cancels_targets_referenced_after_queueing(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    managed_root = tmp_path / 'voice-cloning'
    managed_root.mkdir()
    sample_path = managed_root / 'sample.wav'
    preview_path = managed_root / 'preview.wav'
    output_path = managed_root / 'output.wav'
    for path in (sample_path, preview_path, output_path):
        path.write_bytes(path.name.encode())
    monkeypatch.setattr(
        settings,
        'local_voice_clone_library_dir',
        str(managed_root),
    )
    monkeypatch.setattr(settings, 'oss_region', 'cn-test')
    monkeypatch.setattr(settings, 'oss_bucket', 'speaker-test')
    monkeypatch.setattr(
        settings,
        'oss_public_base_url',
        'https://oss.example.com/assets',
    )
    monkeypatch.setattr(settings, 'oss_access_key_id', 'configured')
    monkeypatch.setattr(settings, 'oss_access_key_secret', 'configured')
    sample_url = 'https://oss.example.com/assets/voices/sample.wav'
    output_url = 'https://oss.example.com/assets/voices/output.wav'
    tasks = [
        VoiceCloneDeletionTask(
            project_id=project.id,
            kind='local',
            resource_key=f'local:{sample_path}',
            local_path=str(sample_path),
        ),
        VoiceCloneDeletionTask(
            project_id=project.id,
            kind='local',
            resource_key=f'local:{preview_path}',
            local_path=str(preview_path),
        ),
        VoiceCloneDeletionTask(
            project_id=project.id,
            kind='local',
            resource_key=f'local:{output_path}',
            local_path=str(output_path),
        ),
        VoiceCloneDeletionTask(
            project_id=project.id,
            kind='oss',
            resource_key='oss:voices/sample.wav',
            oss_object_key='voices/sample.wav',
        ),
        VoiceCloneDeletionTask(
            project_id=project.id,
            kind='oss',
            resource_key='oss:voices/output.wav',
            oss_object_key='voices/output.wav',
        ),
        VoiceCloneDeletionTask(
            project_id=project.id,
            kind='provider',
            resource_key='provider:qwen:model:shared-voice',
            provider='qwen',
            target_model='model',
            voice_id='shared-voice',
        ),
    ]
    session.add_all(tasks)
    session.commit()

    sample = VoiceCloneSample(
        display_name='重新引用样本',
        project_id=project.id,
        original_filename='sample.wav',
        audio_local_path=str(sample_path),
        audio_public_url=sample_url,
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    session.add(VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen3-tts',
        provider_model_family='qwen',
        target_model='model',
        voice_id='shared-voice',
        voice_name='重新引用音色',
        status='ready',
        preview_audio_local_path=str(preview_path),
    ))
    session.add(VoiceCloneSynthesisRecord(
        project_id=project.id,
        target_model='model',
        input_text='重新引用成功输出',
        output_audio_local_path=str(output_path),
        output_audio_public_url=output_url,
        status='ready',
    ))
    session.commit()
    provider_calls = []
    oss_calls = []
    monkeypatch.setattr(
        cleanup_service,
        '_delete_provider_voice',
        lambda *_args, **_kwargs: provider_calls.append('called'),
    )
    monkeypatch.setattr(
        cleanup_service,
        'delete_oss_object',
        lambda *_args, **_kwargs: oss_calls.append('called'),
    )

    result = cleanup_service.drain_voice_clone_deletion_tasks(session)

    assert result.attempted_count == 6
    assert result.completed_count == 6
    assert result.failed_count == 0
    assert provider_calls == []
    assert oss_calls == []
    assert [path.read_bytes() for path in (
        sample_path,
        preview_path,
        output_path,
    )] == [b'sample.wav', b'preview.wav', b'output.wav']
    assert session.query(VoiceCloneDeletionTask).count() == 0


def test_deletion_task_drain_keeps_empty_provider_task_despite_qwen_reference(
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    session.add(VoiceCloneProfile(
        project_id=project.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model='model',
        voice_id='shared-voice',
        voice_name='健康音色',
        status='ready',
    ))
    task = VoiceCloneDeletionTask(
        project_id=project.id,
        kind='provider',
        resource_key='provider::model:shared-voice',
        provider='',
        target_model='model',
        voice_id='shared-voice',
    )
    session.add(task)
    session.commit()
    provider_calls = []
    monkeypatch.setattr(
        cleanup_service,
        '_delete_provider_voice',
        lambda *_args, **_kwargs: provider_calls.append('called'),
    )

    result = cleanup_service.drain_voice_clone_deletion_tasks(session)

    session.refresh(task)
    assert result.failed_count == 1
    assert task.attempt_count == 1
    assert provider_calls == []


def test_deletion_task_drain_rejects_final_symlink_without_deleting_target(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    managed_root = tmp_path / 'voice-cloning'
    managed_root.mkdir()
    generated_output = managed_root / 'successful-output.wav'
    generated_output.write_bytes(b'successful output')
    queued_path = managed_root / 'stale-preview.wav'
    queued_path.symlink_to(generated_output)
    session.add(VoiceCloneSynthesisRecord(
        project_id=project.id,
        target_model='model',
        input_text='已成功生成',
        output_audio_local_path=str(generated_output),
        status='ready',
    ))
    monkeypatch.setattr(
        settings,
        'local_voice_clone_library_dir',
        str(managed_root),
    )
    task = VoiceCloneDeletionTask(
        project_id=project.id,
        kind='local',
        resource_key=f'local:{queued_path}',
        local_path=str(queued_path),
    )
    session.add(task)
    session.commit()

    result = cleanup_service.drain_voice_clone_deletion_tasks(session)

    session.refresh(task)
    assert result.failed_count == 1
    assert task.attempt_count == 1
    assert queued_path.is_symlink()
    assert generated_output.read_bytes() == b'successful output'


def test_deletion_task_drain_rejects_symlink_component_inside_managed_root(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    managed_root = tmp_path / 'voice-cloning'
    managed_root.mkdir()
    real_directory = managed_root / 'real'
    real_directory.mkdir()
    generated_output = real_directory / 'successful-output.wav'
    generated_output.write_bytes(b'successful output')
    alias_directory = managed_root / 'alias'
    alias_directory.symlink_to(real_directory, target_is_directory=True)
    queued_path = alias_directory / generated_output.name
    session.add(VoiceCloneSynthesisRecord(
        project_id=project.id,
        target_model='model',
        input_text='已成功生成',
        output_audio_local_path=str(generated_output),
        status='ready',
    ))
    monkeypatch.setattr(
        settings,
        'local_voice_clone_library_dir',
        str(managed_root),
    )
    task = VoiceCloneDeletionTask(
        project_id=project.id,
        kind='local',
        resource_key=f'local:{queued_path}',
        local_path=str(queued_path),
    )
    session.add(task)
    session.commit()

    result = cleanup_service.drain_voice_clone_deletion_tasks(session)

    session.refresh(task)
    assert result.failed_count == 1
    assert task.attempt_count == 1
    assert alias_directory.is_symlink()
    assert generated_output.read_bytes() == b'successful output'


def test_deletion_task_drain_rejects_parent_symlink_escape(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    managed_root = tmp_path / 'voice-cloning'
    managed_root.mkdir()
    unmanaged_root = tmp_path / 'outside'
    unmanaged_root.mkdir()
    unmanaged_file = unmanaged_root / 'keep.wav'
    unmanaged_file.write_bytes(b'keep outside')
    (managed_root / 'linked-parent').symlink_to(
        unmanaged_root,
        target_is_directory=True,
    )
    queued_path = managed_root / 'linked-parent' / unmanaged_file.name
    monkeypatch.setattr(
        settings,
        'local_voice_clone_library_dir',
        str(managed_root),
    )
    task = VoiceCloneDeletionTask(
        project_id=project.id,
        kind='local',
        resource_key=f'local:{queued_path}',
        local_path=str(queued_path),
    )
    session.add(task)
    session.commit()

    result = cleanup_service.drain_voice_clone_deletion_tasks(session)

    assert result.failed_count == 1
    assert unmanaged_file.read_bytes() == b'keep outside'
    assert session.get(VoiceCloneDeletionTask, task.id) is not None


def test_deletion_task_drain_isolates_failures_and_uses_safe_capped_backoff(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    managed_root = tmp_path / 'voice-cloning'
    managed_root.mkdir()
    monkeypatch.setattr(
        settings,
        'local_voice_clone_library_dir',
        str(managed_root),
    )
    now = datetime(2026, 7, 30, 9, 0, 0)
    failing = VoiceCloneDeletionTask(
        project_id=project.id,
        kind='provider',
        resource_key='provider:qwen:model:voice-secret',
        provider='qwen',
        target_model='model',
        voice_id='voice-secret',
        attempt_count=10,
    )
    succeeding = VoiceCloneDeletionTask(
        project_id=project.id,
        kind='local',
        resource_key=f'local:{managed_root / "missing.wav"}',
        local_path=str(managed_root / 'missing.wav'),
    )
    session.add_all([failing, succeeding])
    session.commit()
    monkeypatch.setattr(
        cleanup_service,
        '_delete_provider_voice',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(
                'https://private.example.com?token=secret access_key=do-not-store'
            )
        ),
    )

    result = cleanup_service.drain_voice_clone_deletion_tasks(
        session,
        now=now,
    )

    session.refresh(failing)
    assert result.attempted_count == 2
    assert result.completed_count == 1
    assert result.failed_count == 1
    assert failing.attempt_count == 11
    assert failing.next_attempt_at == now + timedelta(minutes=1440)
    assert failing.last_error == 'RuntimeError: asset deletion failed'
    assert 'secret' not in failing.last_error
    assert session.get(VoiceCloneDeletionTask, succeeding.id) is None


def test_deletion_task_drain_orders_due_tasks_limits_and_normalizes_aware_now(
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    calls = []
    monkeypatch.setattr(
        cleanup_service,
        '_delete_provider_voice',
        lambda _provider, voice_id, **_kwargs: calls.append(voice_id),
    )
    tasks = [
        VoiceCloneDeletionTask(
            id='00000000-0000-0000-0000-000000000003',
            project_id=project.id,
            kind='provider',
            resource_key='provider:qwen:model:third',
            provider='qwen',
            target_model='model',
            voice_id='third',
            next_attempt_at=datetime(2026, 7, 30, 4, 30),
            created_at=datetime(2026, 7, 30, 3, 0),
        ),
        VoiceCloneDeletionTask(
            id='00000000-0000-0000-0000-000000000002',
            project_id=project.id,
            kind='provider',
            resource_key='provider:qwen:model:second',
            provider='qwen',
            target_model='model',
            voice_id='second',
            next_attempt_at=datetime(2026, 7, 30, 4, 0),
            created_at=datetime(2026, 7, 30, 3, 0),
        ),
        VoiceCloneDeletionTask(
            id='00000000-0000-0000-0000-000000000001',
            project_id=project.id,
            kind='provider',
            resource_key='provider:qwen:model:first',
            provider='qwen',
            target_model='model',
            voice_id='first',
            next_attempt_at=datetime(2026, 7, 30, 4, 0),
            created_at=datetime(2026, 7, 30, 2, 0),
        ),
    ]
    session.add_all(tasks)
    session.commit()

    result = cleanup_service.drain_voice_clone_deletion_tasks(
        session,
        limit=2,
        now=datetime(2026, 7, 30, 12, 15, tzinfo=timezone(timedelta(hours=8))),
    )

    assert result.attempted_count == 2
    assert calls == ['first', 'second']
    assert session.query(VoiceCloneDeletionTask).one().voice_id == 'third'


@pytest.mark.parametrize(
    ('kind', 'fields'),
    [
        ('provider', {}),
        ('oss', {'oss_object_key': 'voices/unconfigured.wav'}),
        ('unknown', {}),
    ],
)
def test_deletion_task_drain_keeps_malformed_or_unconfigured_tasks(
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    fields: dict[str, str],
) -> None:
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    monkeypatch.setattr(settings, 'oss_region', '')
    task = VoiceCloneDeletionTask(
        project_id=project.id,
        kind=kind,
        resource_key=f'{kind}:malformed',
        **fields,
    )
    session.add(task)
    session.commit()

    result = cleanup_service.drain_voice_clone_deletion_tasks(
        session,
        now=datetime(2026, 7, 30, 9, 0, 0),
    )

    session.refresh(task)
    assert result.failed_count == 1
    assert task.attempt_count == 1
    assert task.next_attempt_at == datetime(2026, 7, 30, 9, 2, 0)


def test_deletion_task_drain_never_deletes_unmanaged_paths_or_directories(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    managed_root = tmp_path / 'voice-cloning'
    managed_root.mkdir()
    directory = managed_root / 'directory'
    directory.mkdir()
    unmanaged = tmp_path / 'outside.wav'
    unmanaged.write_bytes(b'keep')
    monkeypatch.setattr(
        settings,
        'local_voice_clone_library_dir',
        str(managed_root),
    )
    tasks = [
        VoiceCloneDeletionTask(
            project_id=project.id,
            kind='local',
            resource_key=f'local:{unmanaged}',
            local_path=str(unmanaged),
        ),
        VoiceCloneDeletionTask(
            project_id=project.id,
            kind='local',
            resource_key=f'local:{directory}',
            local_path=str(directory),
        ),
    ]
    session.add_all(tasks)
    session.commit()

    result = cleanup_service.drain_voice_clone_deletion_tasks(session)

    assert result.failed_count == 2
    assert unmanaged.read_bytes() == b'keep'
    assert directory.is_dir()
    assert session.query(VoiceCloneDeletionTask).count() == 2


def test_deletion_task_drain_rolls_back_and_raises_when_commit_fails(
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    task = VoiceCloneDeletionTask(
        project_id=project.id,
        kind='provider',
        resource_key='provider:qwen:model:commit-failure',
        provider='qwen',
        target_model='model',
        voice_id='commit-failure',
    )
    session.add(task)
    session.commit()
    provider_calls = []
    rollback_calls = []
    real_rollback = session.rollback
    monkeypatch.setattr(
        cleanup_service,
        '_delete_provider_voice',
        lambda *_args, **_kwargs: provider_calls.append('deleted'),
    )
    monkeypatch.setattr(
        session,
        'commit',
        lambda: (_ for _ in ()).throw(RuntimeError('database commit failed')),
    )
    monkeypatch.setattr(
        session,
        'rollback',
        lambda: (rollback_calls.append('rollback'), real_rollback())[1],
    )

    with pytest.raises(RuntimeError, match='database commit failed'):
        cleanup_service.drain_voice_clone_deletion_tasks(session)

    assert provider_calls == ['deleted']
    assert rollback_calls == ['rollback']
    assert session.get(VoiceCloneDeletionTask, task.id) is not None


def test_cleanup_cli_runs_deletion_tasks_without_building_plan(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    managed_missing = settings.voice_clone_library_dir_path / (
        f'cli-missing-{project.id}.wav'
    )
    task = VoiceCloneDeletionTask(
        project_id=project.id,
        kind='local',
        resource_key=f'local:{managed_missing}',
        local_path=str(managed_missing),
    )
    session.add(task)
    session.commit()
    task_id = task.id
    backend_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [
            sys.executable,
            'scripts/cleanup_voice_clone_library.py',
            '--database-url',
            str(session.bind.url),
            '--json',
            '--run-deletion-tasks',
            '--limit',
            '1',
        ],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        'mode': 'deletion-tasks',
        'deletion_tasks': {
            'attempted_count': 1,
            'completed_count': 1,
            'failed_count': 0,
        },
    }
    session.expire_all()
    assert session.get(VoiceCloneDeletionTask, task_id) is None


@pytest.mark.parametrize(
    ('flag', 'value'),
    [
        ('--plan', 'plan.json'),
        ('--output-plan', 'plan.json'),
        ('--backup-path', 'backup.db'),
        ('--archive-dir', 'archive'),
    ],
)
def test_cleanup_cli_rejects_deletion_task_plan_combinations(
    session: Session,
    flag: str,
    value: str,
) -> None:
    backend_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [
            sys.executable,
            'scripts/cleanup_voice_clone_library.py',
            '--database-url',
            str(session.bind.url),
            '--run-deletion-tasks',
            flag,
            value,
        ],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert '--run-deletion-tasks' in completed.stderr


def test_hard_cleanup_apply_backs_up_detaches_history_and_enqueues_only(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    sample_path = tmp_path / 'hard-source.wav'
    output_path = tmp_path / 'hard-success.wav'
    sample_path.write_bytes(b'RIFF-hard-source')
    output_path.write_bytes(b'RIFF-hard-output')
    sample = VoiceCloneSample(
        display_name='硬清理样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='rejected',
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=settings.voice_cloning_qwen_target_model,
        voice_id='voice-hard-cleanup',
        voice_name='硬清理音色',
        status='ready',
        preview_audio_local_path=str(output_path),
    )
    session.add(profile)
    session.flush()
    successful = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='保留成功输出',
        output_audio_local_path=str(output_path),
        status='ready',
    )
    failed = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='删除失败空记录',
        status='failed',
    )
    session.add_all([successful, failed])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    plan = cleanup_service.build_voice_clone_cleanup_plan(session)
    plan_path = tmp_path / 'hard-plan.json'
    plan_path.write_text(
        json.dumps(plan.as_dict(), ensure_ascii=False, sort_keys=True),
        encoding='utf-8',
    )
    backup_path = tmp_path / 'hard-backup.db'

    cleanup_service.apply_voice_clone_cleanup_plan(
        session,
        plan,
        backup_path=backup_path,
        plan_path=plan_path,
    )

    session.expire_all()
    preserved = session.get(VoiceCloneSynthesisRecord, successful.id)
    assert backup_path.is_file()
    assert session.get(VoiceCloneProfile, profile.id) is None
    assert session.get(VoiceCloneSample, sample.id) is None
    assert preserved is not None
    assert preserved.voice_clone_profile_id is None
    assert preserved.voice_name_snapshot == '硬清理音色'
    assert preserved.provider_snapshot == 'qwen'
    assert preserved.target_model_snapshot == settings.voice_cloning_qwen_target_model
    assert preserved.project_id == project.id
    assert preserved.output_audio_local_path == str(output_path)
    assert session.get(VoiceCloneSynthesisRecord, failed.id) is None
    assert {
        task.resource_key
        for task in session.query(VoiceCloneDeletionTask).all()
    } == {
        f'provider:qwen:{settings.voice_cloning_qwen_target_model}:voice-hard-cleanup',
        f'local:{sample_path}',
    }
    assert sample_path.is_file()
    assert output_path.is_file()


def test_hard_cleanup_rejects_tampered_saved_plan_without_mutation(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    sample_path = tmp_path / 'tampered.wav'
    sample_path.write_bytes(b'RIFF-tampered')
    sample = VoiceCloneSample(
        display_name='篡改计划样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='rejected',
    )
    session.add(sample)
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    plan = cleanup_service.build_voice_clone_cleanup_plan(session)
    saved = plan.as_dict()
    saved['plan_fingerprint'] = '0' * 64
    plan_path = tmp_path / 'tampered-plan.json'
    plan_path.write_text(json.dumps(saved), encoding='utf-8')
    backup_path = tmp_path / 'tampered-backup.db'

    with pytest.raises(RuntimeError, match='计划'):
        cleanup_service.apply_voice_clone_cleanup_plan(
            session,
            plan,
            backup_path=backup_path,
            plan_path=plan_path,
        )

    session.expire_all()
    assert session.get(VoiceCloneSample, sample.id) is not None
    assert session.query(VoiceCloneDeletionTask).count() == 0
    assert sample_path.is_file()


def test_hard_cleanup_rejects_changed_fk_baseline_before_rows_files_or_tasks(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    sample_path = tmp_path / 'fk-stale.wav'
    output_path = tmp_path / 'fk-output.wav'
    sample_path.write_bytes(b'RIFF-fk-stale')
    output_path.write_bytes(b'RIFF-fk-output')
    sample = VoiceCloneSample(
        display_name='FK 计划样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    invalid_profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=settings.voice_cloning_qwen_target_model,
        voice_id='voice-fk-invalid',
        voice_name='FK 失效音色',
        status='failed',
    )
    healthy_profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=settings.voice_cloning_qwen_target_model,
        voice_id='voice-fk-healthy',
        voice_name='FK 健康音色',
        status='ready',
    )
    session.add_all([invalid_profile, healthy_profile])
    session.flush()
    record = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=invalid_profile.id,
        target_model=invalid_profile.target_model,
        input_text='FK 输出',
        output_audio_local_path=str(output_path),
        status='ready',
    )
    session.add(record)
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    plan = cleanup_service.build_voice_clone_cleanup_plan(session)
    plan_path = tmp_path / 'fk-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')
    backup_path = tmp_path / 'fk-backup.db'
    record.voice_clone_profile_id = healthy_profile.id
    session.commit()

    with pytest.raises(RuntimeError, match='过期|变化'):
        cleanup_service.apply_voice_clone_cleanup_plan(
            session,
            plan,
            backup_path=backup_path,
            plan_path=plan_path,
        )

    session.expire_all()
    assert backup_path.is_file()
    assert session.get(VoiceCloneProfile, invalid_profile.id) is not None
    assert session.get(VoiceCloneSynthesisRecord, record.id).voice_clone_profile_id == healthy_profile.id
    assert session.query(VoiceCloneDeletionTask).count() == 0
    assert sample_path.is_file()
    assert output_path.is_file()


def test_cleanup_apply_relinks_profiles_then_enqueues_duplicate_file(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    duplicate_path = tmp_path / 'duplicate.wav'
    canonical_path = tmp_path / 'canonical.wav'
    duplicate_path.write_bytes(b'RIFF-cleanup-apply')
    canonical_path.write_bytes(b'RIFF-cleanup-apply')
    now = datetime.utcnow()
    duplicate = VoiceCloneSample(
        display_name='撒贝宁',
        project_id=project.id,
        original_filename=duplicate_path.name,
        audio_local_path=str(duplicate_path),
        **_clone_sample_facts(duplicate_path),
        clone_readiness_status='ready',
        last_used_at=now - timedelta(days=2),
    )
    canonical = VoiceCloneSample(
        display_name='  撒贝宁  ',
        project_id=project.id,
        original_filename=canonical_path.name,
        audio_local_path=str(canonical_path),
        **_clone_sample_facts(canonical_path),
        clone_readiness_status='ready',
        last_used_at=now,
    )
    session.add_all([duplicate, canonical])
    session.commit()
    session.refresh(duplicate)
    session.refresh(canonical)
    profiles = [
        VoiceCloneProfile(
            project_id=project.id,
            source_clone_sample_id=source_id,
            voice_name=f'主持人-{index}',
            provider='cosyvoice',
            provider_model_family='cosyvoice',
            target_model='cosyvoice-v3.5-plus',
            voice_id=f'voice-{index}',
            status='ready',
        )
        for index, source_id in enumerate((duplicate.id, canonical.id))
    ]
    session.add_all(profiles)
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    plan = cleanup_service.build_voice_clone_cleanup_plan(session)
    plan_path = tmp_path / 'dedupe-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')

    result = cleanup_service.apply_voice_clone_cleanup_plan(
        session,
        plan,
        backup_path=tmp_path / 'dedupe-backup.db',
        plan_path=plan_path,
        archive_dir=tmp_path / 'cleanup-archive',
    )

    assert result.merged_sample_count == 1
    assert result.relinked_profile_count == 1
    assert session.query(VoiceCloneSample).count() == 1
    assert {
        profile.source_clone_sample_id
        for profile in session.query(VoiceCloneProfile).all()
    } == {canonical.id}
    assert canonical_path.exists()
    assert duplicate_path.exists()
    assert result.removed_file_count == 0
    assert session.query(VoiceCloneDeletionTask).filter_by(
        resource_key=f'local:{duplicate_path.absolute()}',
        local_path=str(duplicate_path.absolute()),
    ).one()


def test_cleanup_canonicalizes_retained_local_paths_before_scheduling_deletion(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    managed_root = tmp_path / 'managed'
    (managed_root / 'tmp').mkdir(parents=True)
    shared_path = managed_root / 'healthy.wav'
    shared_path.write_bytes(b'RIFF-shared-healthy-output')
    alias_path = managed_root / 'tmp' / '..' / shared_path.name
    invalid_sample = VoiceCloneSample(
        display_name='失效别名样本',
        project_id=project.id,
        original_filename='invalid.wav',
        audio_local_path=str(alias_path),
        clone_readiness_status='rejected',
    )
    healthy_sample = VoiceCloneSample(
        display_name='健康共享样本',
        project_id=project.id,
        original_filename=shared_path.name,
        audio_local_path=str(shared_path),
        clone_readiness_status='ready',
    )
    successful_history = VoiceCloneSynthesisRecord(
        project_id=project.id,
        target_model=settings.voice_cloning_qwen_target_model,
        input_text='健康历史输出',
        output_audio_local_path=str(shared_path),
        status='ready',
    )
    session.add_all([invalid_sample, healthy_sample, successful_history])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert invalid_sample.id in {
        entry.sample_id for entry in plan.invalid_samples
    }
    assert not any(
        target.kind == 'local' for target in plan.deletion_targets
    )
    assert shared_path.read_bytes() == b'RIFF-shared-healthy-output'


def test_deletion_task_canonicalizes_alias_before_reference_check(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_root = tmp_path / 'managed'
    (managed_root / 'tmp').mkdir(parents=True)
    healthy_path = managed_root / 'healthy.wav'
    healthy_path.write_bytes(b'keep healthy sample')
    alias_path = managed_root / 'tmp' / '..' / healthy_path.name
    sample = VoiceCloneSample(
        display_name='健康别名样本',
        project_id=project.id,
        original_filename=healthy_path.name,
        audio_local_path=str(alias_path),
        clone_readiness_status='ready',
    )
    task = VoiceCloneDeletionTask(
        project_id=project.id,
        kind='local',
        resource_key=f'local:{healthy_path}',
        local_path=str(healthy_path),
    )
    session.add_all([sample, task])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    monkeypatch.setattr(
        cleanup_service,
        '_voice_clone_managed_roots',
        lambda: (managed_root,),
    )

    result = cleanup_service.drain_voice_clone_deletion_tasks(session)

    assert result.completed_count == 1
    assert result.failed_count == 0
    assert healthy_path.read_bytes() == b'keep healthy sample'
    assert session.get(VoiceCloneDeletionTask, task.id) is None


def test_cleanup_preserves_real_file_referenced_by_history_symlink(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed_root = tmp_path / 'managed'
    managed_root.mkdir()
    real_path = managed_root / 'history-real.wav'
    real_path.write_bytes(b'RIFF-history-through-symlink')
    history_link = managed_root / 'history-link.wav'
    history_link.symlink_to(real_path)
    invalid_sample = VoiceCloneSample(
        display_name='待删真实文件样本',
        project_id=project.id,
        original_filename=real_path.name,
        audio_local_path=str(real_path),
        clone_readiness_status='rejected',
    )
    history = VoiceCloneSynthesisRecord(
        project_id=project.id,
        target_model=settings.voice_cloning_qwen_target_model,
        input_text='通过符号链接保留的历史成果',
        output_audio_local_path=str(history_link),
        status='ready',
    )
    task = VoiceCloneDeletionTask(
        project_id=project.id,
        kind='local',
        resource_key=f'local:{real_path}',
        local_path=str(real_path),
    )
    session.add_all([invalid_sample, history, task])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    monkeypatch.setattr(
        cleanup_service,
        '_voice_clone_managed_roots',
        lambda: (managed_root,),
    )

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert not any(
        target.kind == 'local' and target.local_path == str(real_path)
        for target in plan.deletion_targets
    )

    result = cleanup_service.drain_voice_clone_deletion_tasks(session)

    assert result.completed_count == 1
    assert result.failed_count == 0
    assert session.get(VoiceCloneDeletionTask, task.id) is None
    assert real_path.read_bytes() == b'RIFF-history-through-symlink'
    assert history_link.read_bytes() == b'RIFF-history-through-symlink'
    assert session.get(VoiceCloneSynthesisRecord, history.id) is not None


@pytest.mark.parametrize('symlink_kind', ['parent', 'final'])
def test_cleanup_duplicate_deletion_uses_guarded_task_for_symlink_paths(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
    symlink_kind: str,
) -> None:
    managed_root = tmp_path / 'voice-cloning'
    managed_root.mkdir()
    outside_root = tmp_path / 'outside'
    outside_root.mkdir()
    outside_file = outside_root / f'{symlink_kind}.wav'
    outside_file.write_bytes(b'RIFF-symlink-duplicate')
    canonical_path = managed_root / f'canonical-{symlink_kind}.wav'
    canonical_path.write_bytes(b'RIFF-symlink-duplicate')
    if symlink_kind == 'parent':
        linked_parent = managed_root / 'linked-parent'
        linked_parent.symlink_to(outside_root, target_is_directory=True)
        duplicate_path = linked_parent / outside_file.name
    else:
        duplicate_path = managed_root / 'linked-final.wav'
        duplicate_path.symlink_to(outside_file)
    now = datetime.utcnow()
    duplicate = VoiceCloneSample(
        display_name=f'符号链接-{symlink_kind}',
        project_id=project.id,
        original_filename=duplicate_path.name,
        audio_local_path=str(duplicate_path),
        clone_readiness_status='ready',
        last_used_at=now - timedelta(days=1),
    )
    canonical = VoiceCloneSample(
        display_name=f'符号链接-{symlink_kind}',
        project_id=project.id,
        original_filename=canonical_path.name,
        audio_local_path=str(canonical_path),
        **_clone_sample_facts(canonical_path),
        clone_readiness_status='ready',
        last_used_at=now,
    )
    session.add_all([duplicate, canonical])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    monkeypatch.setattr(
        cleanup_service,
        '_voice_clone_managed_roots',
        lambda: (managed_root,),
    )
    plan = cleanup_service.build_voice_clone_cleanup_plan(session)
    plan_path = tmp_path / f'{symlink_kind}-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')

    cleanup_service.apply_voice_clone_cleanup_plan(
        session,
        plan,
        backup_path=tmp_path / f'{symlink_kind}-backup.db',
        plan_path=plan_path,
        archive_dir=tmp_path / f'{symlink_kind}-archive',
    )

    assert duplicate_path.exists()
    assert outside_file.read_bytes() == b'RIFF-symlink-duplicate'
    archive_manifest = json.loads(
        (tmp_path / f'{symlink_kind}-archive' / 'manifest.json').read_text(
            encoding='utf-8'
        )
    )
    assert archive_manifest['archived_file_count'] == 0
    assert archive_manifest['entries'][0]['archive_error'] == (
        'unsafe_local_path'
    )
    assert not list(
        (tmp_path / f'{symlink_kind}-archive').glob('*.wav')
    )
    deletion_task = session.query(VoiceCloneDeletionTask).filter_by(
        kind='local',
        local_path=str(duplicate_path),
    ).one()

    result = cleanup_service.drain_voice_clone_deletion_tasks(session)

    assert result.failed_count == 1
    assert duplicate_path.exists()
    assert outside_file.read_bytes() == b'RIFF-symlink-duplicate'
    assert session.get(VoiceCloneDeletionTask, deletion_task.id) is not None


def test_cleanup_name_cap_hard_deletes_linked_profile_and_preserves_history(
    tmp_path: Path,
    session: Session,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        'oss_public_base_url',
        'https://speaker-test.example.com/assets',
    )
    now = datetime.utcnow()
    samples: list[VoiceCloneSample] = []
    profiles: list[VoiceCloneProfile] = []
    for index in range(4):
        sample_path = tmp_path / f'name-cap-{index}.wav'
        sample_path.write_bytes(f'RIFF-name-cap-{index}'.encode())
        sample = VoiceCloneSample(
            display_name='同名人物',
            project_id=project.id,
            original_filename=sample_path.name,
            audio_local_path=str(sample_path),
            audio_public_url=(
                'https://speaker-test.example.com/assets/name-cap/removed.wav'
                if index == 3 else None
            ),
            **_clone_sample_facts(sample_path),
            clone_readiness_status='ready',
            last_used_at=now - timedelta(days=index),
        )
        session.add(sample)
        session.flush()
        profile = VoiceCloneProfile(
            project_id=project.id,
            source_clone_sample_id=sample.id,
            source_audio_local_path=sample.audio_local_path,
            provider='cosyvoice',
            provider_model_family='cosyvoice',
            target_model=settings.voice_cloning_cosyvoice_target_model,
            voice_id=f'voice-name-cap-{index}',
            voice_name=f'同名人物-{index}',
            status='ready',
            preview_audio_local_path=(
                str(tmp_path / 'removed-preview.wav')
                if index == 3 else None
            ),
        )
        session.add(profile)
        samples.append(sample)
        profiles.append(profile)
    session.flush()
    preview_path = tmp_path / 'removed-preview.wav'
    preview_path.write_bytes(b'RIFF-removed-preview')
    history_path = tmp_path / 'preserved-history.wav'
    history_path.write_bytes(b'RIFF-preserved-history')
    successful = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profiles[3].id,
        target_model=profiles[3].target_model,
        input_text='保留同名上限历史',
        output_audio_local_path=str(history_path),
        status='ready',
    )
    failed = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profiles[3].id,
        target_model=profiles[3].target_model,
        input_text='删除同名上限空失败记录',
        status='failed',
    )
    session.add_all([successful, failed])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert plan.name_cap_groups[0].removed_sample_ids == (samples[3].id,)
    assert [
        (entry.profile_id, entry.reason) for entry in plan.invalid_profiles
    ] == [(profiles[3].id, 'name_cap')]
    assert plan.detach_record_ids == (successful.id,)
    assert plan.delete_record_ids == (failed.id,)
    plan_path = tmp_path / 'name-cap-profile-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')

    cleanup_service.apply_voice_clone_cleanup_plan(
        session,
        plan,
        backup_path=tmp_path / 'name-cap-profile-backup.db',
        plan_path=plan_path,
        archive_dir=tmp_path / 'name-cap-profile-archive',
    )

    session.expire_all()
    preserved = session.get(VoiceCloneSynthesisRecord, successful.id)
    assert session.get(VoiceCloneProfile, profiles[3].id) is None
    assert session.get(VoiceCloneSample, samples[3].id) is None
    assert preserved is not None
    assert preserved.voice_clone_profile_id is None
    assert preserved.voice_name_snapshot == profiles[3].voice_name
    assert preserved.provider_snapshot == profiles[3].provider
    assert preserved.target_model_snapshot == profiles[3].target_model
    assert session.get(VoiceCloneSynthesisRecord, failed.id) is None
    assert history_path.read_bytes() == b'RIFF-preserved-history'
    assert Path(samples[3].audio_local_path).is_file()
    assert preview_path.is_file()
    assert {
        task.resource_key
        for task in session.query(VoiceCloneDeletionTask).all()
    } >= {
        f'provider:cosyvoice:{profiles[3].target_model}:{profiles[3].voice_id}',
        f'local:{Path(samples[3].audio_local_path).absolute()}',
        f'local:{preview_path.absolute()}',
        'oss:name-cap/removed.wav',
    }


def test_cleanup_duplicate_relink_into_name_cap_removal_is_fully_planned(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    now = datetime.utcnow()
    samples: list[VoiceCloneSample] = []
    profiles: list[VoiceCloneProfile] = []
    for index in range(5):
        sample_path = tmp_path / f'combined-{index}.wav'
        content = (
            b'RIFF-low-priority-duplicate'
            if index >= 3
            else f'RIFF-high-priority-{index}'.encode()
        )
        sample_path.write_bytes(content)
        sample = VoiceCloneSample(
            display_name='组合清理人物',
            project_id=project.id,
            original_filename=sample_path.name,
            audio_local_path=str(sample_path),
            **_clone_sample_facts(sample_path),
            clone_readiness_status='ready',
            last_used_at=now - timedelta(days=index),
        )
        session.add(sample)
        session.flush()
        profile = VoiceCloneProfile(
            project_id=project.id,
            source_clone_sample_id=sample.id,
            source_audio_local_path=sample.audio_local_path,
            provider='cosyvoice',
            provider_model_family='cosyvoice',
            target_model=settings.voice_cloning_cosyvoice_target_model,
            voice_id=f'voice-combined-{index}',
            voice_name=f'组合清理音色-{index}',
            status='ready',
        )
        session.add(profile)
        samples.append(sample)
        profiles.append(profile)
    session.flush()
    history_path = tmp_path / 'combined-history.wav'
    history_path.write_bytes(b'RIFF-combined-history')
    successful = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profiles[4].id,
        target_model=profiles[4].target_model,
        input_text='保留重复样本音色的成功历史',
        output_audio_local_path=str(history_path),
        status='ready',
    )
    failed = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profiles[3].id,
        target_model=profiles[3].target_model,
        input_text='删除被裁音色的失败空记录',
        status='failed',
    )
    session.add_all([successful, failed])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    removed_profile_ids = {profiles[3].id, profiles[4].id}
    removed_sample_ids = {samples[3].id, samples[4].id}
    removed_provider_keys = {
        f'provider:cosyvoice:{profiles[index].target_model}:{profiles[index].voice_id}'
        for index in (3, 4)
    }
    removed_local_keys = {
        f'local:{Path(samples[index].audio_local_path).absolute()}'
        for index in (3, 4)
    }

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert len(plan.duplicate_groups) == 1
    assert plan.duplicate_groups[0].canonical_sample_id == samples[3].id
    assert plan.duplicate_groups[0].duplicate_sample_ids == (samples[4].id,)
    assert plan.name_cap_groups[0].removed_sample_ids == (samples[3].id,)
    assert {
        entry.profile_id
        for entry in plan.invalid_profiles
        if entry.reason == 'name_cap'
    } == removed_profile_ids
    assert plan.detach_record_ids == (successful.id,)
    assert plan.delete_record_ids == (failed.id,)
    plan_path = tmp_path / 'combined-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')

    cleanup_service.apply_voice_clone_cleanup_plan(
        session,
        plan,
        backup_path=tmp_path / 'combined-backup.db',
        plan_path=plan_path,
        archive_dir=tmp_path / 'combined-archive',
    )

    session.expire_all()
    preserved = session.get(VoiceCloneSynthesisRecord, successful.id)
    assert preserved is not None
    assert preserved.voice_clone_profile_id is None
    assert preserved.voice_name_snapshot == '组合清理音色-4'
    assert preserved.provider_snapshot == 'cosyvoice'
    assert (
        preserved.target_model_snapshot
        == settings.voice_cloning_cosyvoice_target_model
    )
    assert history_path.read_bytes() == b'RIFF-combined-history'
    assert session.get(VoiceCloneSynthesisRecord, failed.id) is None
    assert all(
        session.get(VoiceCloneProfile, profile_id) is None
        for profile_id in removed_profile_ids
    )
    assert all(
        session.get(VoiceCloneSample, sample_id) is None
        for sample_id in removed_sample_ids
    )
    assert all(
        session.get(VoiceCloneProfile, profiles[index].id) is not None
        and session.get(VoiceCloneSample, samples[index].id) is not None
        for index in range(3)
    )
    queued_keys = {
        task.resource_key
        for task in session.query(VoiceCloneDeletionTask).all()
    }
    assert queued_keys >= removed_provider_keys | removed_local_keys


def test_cleanup_cli_human_dry_run_reports_hard_cleanup_plan(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    missing_path = tmp_path / 'missing.wav'
    sample = VoiceCloneSample(
        display_name='CLI 人类报告',
        project_id=project.id,
        original_filename=missing_path.name,
        audio_local_path=str(missing_path),
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.flush()
    profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=sample.id,
        provider='qwen',
        provider_model_family='qwen',
        target_model=settings.voice_cloning_qwen_target_model,
        voice_id='voice-cli-human-report',
        voice_name='CLI 人类报告',
        status='ready',
    )
    session.add(profile)
    session.flush()
    detached = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='保留',
        output_audio_local_path=str(tmp_path / 'history.wav'),
        status='ready',
    )
    deleted = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='删除',
        status='failed',
    )
    processing = VoiceCloneSynthesisRecord(
        project_id=project.id,
        voice_clone_profile_id=profile.id,
        target_model=profile.target_model,
        input_text='删除处理中空记录',
        status='processing',
    )
    session.add_all([detached, deleted, processing])
    session.commit()
    backend_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [
            sys.executable,
            'scripts/cleanup_voice_clone_library.py',
            '--database-url',
            str(session.bind.url),
        ],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert '模式：只预览' in completed.stdout
    assert '失效音色：1 条（sample_file_missing=1）' in completed.stdout
    assert '失效样本：1 条（sample_file_missing=1）' in completed.stdout
    assert (
        '生成记录：解绑并保留 1 条，'
        '删除失败/处理中且无输出 2 条'
    ) in completed.stdout
    assert '删除目标：2 条（local=1，provider=1）' in completed.stdout
    assert '计划指纹：' in completed.stdout
    assert '外键基线：5 条引用' in completed.stdout
    assert '缺失文件：1 条（已纳入失效样本清理计划）' in completed.stdout


def test_cleanup_cli_human_dry_run_reports_fingerprint_conflict_action(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    sample_path = tmp_path / 'fingerprint-conflict.wav'
    with wave.open(str(sample_path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b'\0\0' * 16_000 * 12)
    sample = VoiceCloneSample(
        display_name='指纹冲突样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        content_sha256='f' * 64,
        file_size_bytes=sample_path.stat().st_size,
        duration_ms=12_000,
        sample_rate_hz=16_000,
        channel_count=1,
        clone_readiness_status='ready',
    )
    session.add(sample)
    session.commit()
    backend_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [
            sys.executable,
            'scripts/cleanup_voice_clone_library.py',
            '--database-url',
            str(session.bind.url),
        ],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (
        '指纹冲突：1 条（计划删除 1 条，复检修复 0 条）'
        in completed.stdout
    )


def test_cleanup_cli_defaults_to_json_dry_run_without_mutation(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    first_path = tmp_path / 'cli-first.wav'
    second_path = tmp_path / 'cli-second.wav'
    for path in (first_path, second_path):
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8_000)
            audio.writeframes(b'\0\0' * 8_000)
    session.add_all([
        VoiceCloneSample(
            display_name='CLI 样本',
            project_id=project.id,
            original_filename=first_path.name,
            audio_local_path=str(first_path),
            clone_readiness_status='ready',
        ),
        VoiceCloneSample(
            display_name='  CLI 样本  ',
            project_id=project.id,
            original_filename=second_path.name,
            audio_local_path=str(second_path),
            clone_readiness_status='ready',
        ),
    ])
    session.commit()
    backend_root = Path(__file__).resolve().parents[1]
    output_plan = tmp_path / 'cli-plan.json'

    completed = subprocess.run(
        [
            sys.executable,
            'scripts/cleanup_voice_clone_library.py',
            '--database-url',
            str(session.bind.url),
            '--json',
            '--output-plan',
            str(output_plan),
        ],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload['mode'] == 'dry-run'
    assert payload['plan']['duplicate_sample_count'] == 1
    assert json.loads(output_plan.read_text(encoding='utf-8')) == payload['plan']
    session.expire_all()
    assert session.query(VoiceCloneSample).count() == 2
    assert first_path.exists()
    assert second_path.exists()


def test_cleanup_cli_applies_only_saved_plan_with_backup(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    sample_path = tmp_path / 'cli-invalid.wav'
    sample_path.write_bytes(b'RIFF-cli-invalid')
    sample = VoiceCloneSample(
        display_name='CLI 失效样本',
        project_id=project.id,
        original_filename=sample_path.name,
        audio_local_path=str(sample_path),
        clone_readiness_status='rejected',
    )
    session.add(sample)
    session.commit()
    sample_id = sample.id
    backend_root = Path(__file__).resolve().parents[1]
    plan_path = tmp_path / 'cli-apply-plan.json'
    backup_path = tmp_path / 'cli-apply-backup.db'
    preview = subprocess.run(
        [
            sys.executable,
            'scripts/cleanup_voice_clone_library.py',
            '--database-url',
            str(session.bind.url),
            '--json',
            '--output-plan',
            str(plan_path),
        ],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert preview.returncode == 0, preview.stderr

    applied = subprocess.run(
        [
            sys.executable,
            'scripts/cleanup_voice_clone_library.py',
            '--database-url',
            str(session.bind.url),
            '--json',
            '--apply',
            '--plan',
            str(plan_path),
            '--backup-path',
            str(backup_path),
        ],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)['mode'] == 'apply'
    session.expire_all()
    assert backup_path.is_file()
    assert session.get(VoiceCloneSample, sample_id) is None
    assert session.query(VoiceCloneDeletionTask).filter_by(
        resource_key=f'local:{sample_path}'
    ).one()
    assert sample_path.is_file()


def test_cleanup_apply_rejects_stale_plan_before_database_or_file_mutation(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    canonical_path = tmp_path / 'stale-canonical.wav'
    duplicate_path = tmp_path / 'stale-duplicate.wav'
    canonical_path.write_bytes(b'RIFF-stale-plan')
    duplicate_path.write_bytes(b'RIFF-stale-plan')
    canonical = VoiceCloneSample(
        display_name='规范样本',
        project_id=project.id,
        original_filename=canonical_path.name,
        audio_local_path=str(canonical_path),
        **_clone_sample_facts(canonical_path),
        clone_readiness_status='ready',
        last_used_at=datetime.utcnow(),
    )
    duplicate = VoiceCloneSample(
        display_name='规范样本',
        project_id=project.id,
        original_filename=duplicate_path.name,
        audio_local_path=str(duplicate_path),
        **_clone_sample_facts(duplicate_path),
        clone_readiness_status='ready',
    )
    session.add_all([canonical, duplicate])
    session.commit()
    session.refresh(canonical)
    session.refresh(duplicate)
    profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=duplicate.id,
        voice_name='待验证音色',
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_id='voice-stale',
        status='ready',
    )
    session.add(profile)
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    plan = cleanup_service.build_voice_clone_cleanup_plan(session)
    plan_path = tmp_path / 'stale-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')
    duplicate_path.write_bytes(b'RIFF-content-changed-after-preview')

    with pytest.raises(RuntimeError, match='重新预览'):
        cleanup_service.apply_voice_clone_cleanup_plan(
            session,
            plan,
            backup_path=tmp_path / 'stale-backup.db',
            plan_path=plan_path,
            archive_dir=tmp_path / 'stale-archive',
        )

    session.expire_all()
    assert session.query(VoiceCloneSample).count() == 2
    assert session.get(VoiceCloneProfile, profile.id).source_clone_sample_id == duplicate.id
    assert canonical_path.exists()
    assert duplicate_path.exists()


def test_cleanup_globally_merges_exact_name_duplicates_and_trims_name_to_three(
    tmp_path: Path,
    session: Session,
    project: Project,
) -> None:
    other_project = Project(
        name='另一个项目',
        name_key='另一个项目',
        legacy_program_key='另一个项目',
        status='active',
    )
    session.add(other_project)
    session.commit()
    session.refresh(other_project)
    now = datetime.utcnow()
    contents = [
        b'RIFF-sabening-exact',
        b'RIFF-sabening-exact',
        b'RIFF-sabening-second',
        b'RIFF-sabening-third',
        b'RIFF-sabening-trimmed',
    ]
    samples: list[VoiceCloneSample] = []
    for index, content in enumerate(contents):
        path = tmp_path / f'sabening-{index}.wav'
        path.write_bytes(content)
        sample = VoiceCloneSample(
            display_name=' 撒贝宁 ' if index % 2 else '撒贝宁',
            project_id=project.id if index % 2 == 0 else other_project.id,
            original_filename=path.name,
            audio_local_path=str(path),
            **_clone_sample_facts(path),
            clone_readiness_status='rejected' if index == 4 else 'ready',
            last_used_at=now - timedelta(days=index),
        )
        session.add(sample)
        samples.append(sample)
    session.commit()
    for sample in samples:
        session.refresh(sample)
    ready_profile = VoiceCloneProfile(
        project_id=other_project.id,
        source_clone_sample_id=samples[1].id,
        source_audio_local_path=samples[1].audio_local_path,
        voice_name='撒贝宁',
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_id='voice-sabening-ready',
        status='ready',
    )
    retained_profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=samples[2].id,
        source_audio_local_path=samples[2].audio_local_path,
        voice_name='撒贝宁',
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_id='voice-sabening-second',
        status='ready',
    )
    detached_profile = VoiceCloneProfile(
        project_id=project.id,
        source_clone_sample_id=samples[4].id,
        source_audio_local_path=samples[4].audio_local_path,
        voice_name='撒贝宁旧版',
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_id='voice-sabening-preserved',
        status='failed',
    )
    session.add_all([ready_profile, retained_profile, detached_profile])
    session.commit()
    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')

    plan = cleanup_service.build_voice_clone_cleanup_plan(session)

    assert plan.exact_duplicate_sample_count == 1
    assert plan.name_cap_sample_count == 0
    assert session.query(VoiceCloneSample).count() == 5
    plan_path = tmp_path / 'global-cleanup-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')

    result = cleanup_service.apply_voice_clone_cleanup_plan(
        session,
        plan,
        backup_path=tmp_path / 'global-cleanup-backup.db',
        plan_path=plan_path,
        archive_dir=tmp_path / 'archive',
    )

    session.expire_all()
    remaining = session.query(VoiceCloneSample).all()
    assert len(remaining) == 3
    assert {sample.name_key for sample in remaining} == {'撒贝宁'}
    assert result.merged_sample_count == 1
    assert result.trimmed_sample_count == 0
    assert result.detached_profile_count == 0
    assert session.get(VoiceCloneProfile, ready_profile.id).source_clone_sample_id in {
        sample.id for sample in remaining
    }
    assert session.get(VoiceCloneProfile, detached_profile.id) is None
    assert session.query(VoiceCloneDeletionTask).filter_by(
        resource_key=(
            'provider:cosyvoice:cosyvoice-v3.5-plus:'
            'voice-sabening-preserved'
        )
    ).one()
    assert (tmp_path / 'sabening-4.wav').is_file()
    assert (tmp_path / 'archive' / 'manifest.json').is_file()


def test_large_demo_library_stays_grouped_and_bounded(
    tmp_path: Path,
    session: Session,
) -> None:
    seed_module = importlib.import_module('scripts.seed_voice_clone_demo')
    result = seed_module.seed_voice_clone_demo(
        session,
        tmp_path / 'demo-media',
        sample_count=480,
        profile_count=356,
        name_count=23,
    )
    # Keep the scale fixture active under fixed 72-hour retention; its original
    # historical dates are unrelated to the grouping/memory regression.
    now = datetime.utcnow()
    for model in (VoiceCloneSample, VoiceCloneProfile):
        for index, asset in enumerate(session.scalars(select(model))):
            asset.created_at = now - timedelta(minutes=index)
    session.commit()
    library_service = importlib.import_module('app.services.voice_clone_library')

    page = library_service.list_voice_clone_library(
        session,
        result.project_id,
        query=None,
        limit=30,
        cursor=None,
    )

    assert result.sample_count == 480
    assert result.profile_count == 356
    assert page.total == 23
    assert len(page.groups) <= 30
    assert all(
        group.recommended_profile.voice_name.strip() == group.display_name
        for group in page.groups
    )
    assert all(
        'profiles' not in group.model_dump()
        for group in page.groups
    )

    cleanup_service = importlib.import_module('app.services.voice_clone_cleanup')
    plan = cleanup_service.build_voice_clone_cleanup_plan(session)
    plan_path = tmp_path / 'demo-cleanup-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')
    cleanup_service.apply_voice_clone_cleanup_plan(
        session,
        plan,
        backup_path=tmp_path / 'demo-cleanup-backup.db',
        plan_path=plan_path,
        archive_dir=tmp_path / 'demo-cleanup-archive',
    )
    remaining_counts = Counter(
        voice_cloning_service.normalize_voice_clone_name(sample.display_name)
        for sample in session.query(VoiceCloneSample).all()
    )

    assert remaining_counts
    assert max(remaining_counts.values()) <= 3


def test_library_page_query_and_orm_loads_stay_bounded_by_page_size(
    session: Session,
    project: Project,
) -> None:
    for index in range(80):
        _library_profile(
            session,
            profile_project=project,
            source_project=project,
            voice_name=f'有界音色 {index:03d}',
        )
    session.commit()
    project_id = project.id
    session.expunge_all()
    library_service = importlib.import_module('app.services.voice_clone_library')
    query_count = 0
    loaded = Counter()

    def count_query(*_args):
        nonlocal query_count
        query_count += 1

    def count_load(_session, instance):
        loaded[type(instance).__name__] += 1

    event.listen(session.bind, 'before_cursor_execute', count_query)
    event.listen(session, 'loaded_as_persistent', count_load)
    try:
        page = library_service.list_voice_clone_library(
            session,
            project_id,
            limit=5,
        )
    finally:
        event.remove(session.bind, 'before_cursor_execute', count_query)
        event.remove(session, 'loaded_as_persistent', count_load)

    assert page.total == 80
    assert len(page.groups) == 5
    assert page.next_cursor == '5'
    assert query_count <= 12
    assert loaded['VoiceCloneProfile'] <= 5
    assert loaded['VoiceCloneSample'] <= 5
    assert sum(loaded.values()) <= 16


def test_sample_page_query_and_orm_loads_stay_bounded_by_page_size(
    session: Session,
    project: Project,
) -> None:
    now = datetime.utcnow()
    samples = [
        VoiceCloneSample(
            display_name=f'有界样本 {index:03d}',
            project_id=project.id,
            original_filename=f'bounded-{index:03d}.wav',
            audio_local_path=f'/tmp/bounded-{index:03d}.wav',
            clone_readiness_status='ready',
            created_at=now - timedelta(minutes=index),
        )
        for index in range(80)
    ]
    session.add_all(samples)
    session.flush()
    session.add_all([
        VoiceCloneProfile(
            project_id=project.id,
            source_clone_sample_id=sample.id,
            provider='qwen',
            target_model=settings.voice_cloning_qwen_target_model,
            voice_name=sample.display_name,
        )
        for sample in samples
    ])
    session.commit()
    project_id = project.id
    session.expunge_all()
    library_service = importlib.import_module('app.services.voice_clone_library')
    query_count = 0
    loaded = Counter()

    def count_query(*_args):
        nonlocal query_count
        query_count += 1

    def count_load(_session, instance):
        loaded[type(instance).__name__] += 1

    event.listen(session.bind, 'before_cursor_execute', count_query)
    event.listen(session, 'loaded_as_persistent', count_load)
    try:
        page = library_service.list_voice_clone_sample_page(
            session,
            project_id,
            limit=5,
        )
    finally:
        event.remove(session.bind, 'before_cursor_execute', count_query)
        event.remove(session, 'loaded_as_persistent', count_load)

    assert page.total == 80
    assert len(page.items) == 5
    assert page.next_cursor == '5'
    assert query_count <= 5
    assert loaded['VoiceCloneSample'] <= 6
    assert loaded['VoiceCloneProfile'] == 0


def test_library_uses_catalog_and_keeps_all_identity_states(session, project, monkeypatch):
    library = importlib.import_module('app.services.voice_clone_library')
    monkeypatch.setattr(settings, 'voice_cloning_cosyvoice_target_model', 'cosyvoice-v3-plus')
    identities = [VoiceCloneIdentity(display_name=name, aliases=['sample-only alias'] if name == 'sample only' else []) for name in ('ready', 'failed', 'sample only')]
    session.add_all(identities)
    session.flush()
    ready = _library_profile(session, profile_project=project, source_project=project, voice_name='ready', target_model='cosyvoice-v3.5-plus')
    ready.provider = 'cosyvoice'
    ready.identity_id = identities[0].id
    failed = _library_profile(session, profile_project=project, source_project=project, voice_name='failed', status='failed')
    failed.identity_id = identities[1].id
    session.add(VoiceCloneSample(identity_id=identities[2].id, project_id=project.id, display_name='sample only', original_filename='never-created.wav', audio_local_path='/no-file'))
    session.commit()
    page = library.list_voice_clone_library(session, project.id)
    assert page.total == 3
    assert page.available_group_count == 1
    assert page.exception_group_count == 2
    groups = {group.identity_id: group for group in page.groups}
    assert groups[identities[0].id].recommended_profile.selectable
    assert groups[identities[1].id].health_status == 'failed'
    assert groups[identities[2].id].recommended_profile is None
    assert groups[identities[2].id].version_count == 0
    assert library.list_voice_clone_library(session, project.id, query='sample-only alias').total == 1
    assert library.list_voice_clone_library(session, project.id, query='never-created.wav').total == 1
    versions = library.list_voice_clone_group_profiles(session, project.id, identities[1].id)
    assert [profile.id for profile in versions] == [failed.id]


@pytest.mark.parametrize('profile_status', ['creating', 'failed', 'ready'])
def test_library_search_keeps_unused_identity_recordings_after_profile_creation(
    session: Session,
    project: Project,
    profile_status: str,
) -> None:
    library = importlib.import_module('app.services.voice_clone_library')
    identity = VoiceCloneIdentity(display_name='同一声音', aliases=[])
    source_project = Project(
        name='未引用录音来源项目', name_key='未引用录音来源项目', status='active',
    )
    session.add_all([identity, source_project])
    session.flush()
    session.add_all([
        VoiceCloneSample(
            identity_id=identity.id,
            project_id=source_project.id,
            display_name='未引用录音名称',
            original_filename=f'unused-recording-{index}.wav',
            audio_local_path=f'/missing-unused-recording-{index}',
        )
        for index in range(2)
    ])
    session.commit()
    queries = ('未引用录音名称', 'unused-recording', source_project.name)
    for query in queries:
        page = library.list_voice_clone_library(session, project.id, query=query)
        assert page.total == 1
        assert page.groups[0].recommended_profile is None

    profile = _library_profile(
        session, profile_project=project, source_project=project,
        voice_name='同一声音', status=profile_status,
    )
    profile.identity_id = identity.id
    session.get(VoiceCloneSample, profile.source_clone_sample_id).identity_id = identity.id
    session.commit()
    for query in queries:
        page = library.list_voice_clone_library(session, project.id, query=query)
        assert page.total == 1
        assert len(page.groups) == 1
        assert page.groups[0].identity_id == identity.id
        assert page.groups[0].version_count == 1
        assert page.groups[0].recommended_profile.id == profile.id


def test_library_group_samples_are_identity_scoped_including_unused_and_rejected(session, project, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1 import voice_cloning as v1
    from app.api.v2 import voice_cloning as v2
    identity = VoiceCloneIdentity(display_name='same display name', aliases=[])
    other = VoiceCloneIdentity(display_name='same display name', aliases=[])
    session.add_all([identity, other])
    session.flush()
    samples = [VoiceCloneSample(identity_id=identity.id, display_name='same display name', original_filename=f'{i}.wav', audio_local_path=f'/sample-{i}', clone_readiness_status='rejected' if i == 2 else 'ready') for i in range(3)]
    session.add_all([*samples, VoiceCloneSample(identity_id=other.id, display_name='same display name', original_filename='other.wav', audio_local_path='/other')])
    session.flush()
    session.add(VoiceCloneProfile(source_clone_sample_id=samples[0].id, identity_id=identity.id, voice_name='same display name', provider='qwen', target_model=settings.voice_cloning_qwen_target_model, status='failed'))
    session.commit()
    expected = {sample.id for sample in samples}
    app = FastAPI()
    app.include_router(v1.router, prefix='/api/v1/voice-cloning')
    app.include_router(v2.router, prefix='/api/v2')
    monkeypatch.setattr(v1, 'SessionLocal', lambda: session)
    monkeypatch.setattr(v2, 'SessionLocal', lambda: session)
    client = TestClient(app)
    for url in (f'/api/v1/voice-cloning/library/{identity.id}/samples', f'/api/v2/projects/{project.id}/voice-cloning/library/{identity.id}/samples'):
        result = client.get(url)
        assert result.status_code == 200
        items = result.json()
        assert isinstance(items, list)
        assert {item['id'] for item in items} == expected
        assert sum(item['profile_count'] for item in items) == 1
        assert sum(item['in_use'] for item in items) == 1
    legacy = _library_profile(session, profile_project=project, source_project=project, voice_name='legacy group')
    session.commit()
    legacy_sample_id = legacy.source_clone_sample_id
    result = client.get('/api/v1/voice-cloning/library/legacy:legacy%20group/samples')
    assert [item['id'] for item in result.json()] == [legacy_sample_id]


def test_sample_only_identity_library_pagination_stays_bounded(session, project):
    library = importlib.import_module('app.services.voice_clone_library')
    session.add_all(VoiceCloneIdentity(display_name=f'empty {i:03d}', aliases=[]) for i in range(80))
    session.commit()
    project_id = project.id
    session.expunge_all()
    loaded = Counter()
    def count_load(_session, item):
        loaded[type(item).__name__] += 1
    event.listen(session, 'loaded_as_persistent', count_load)
    try:
        page = library.list_voice_clone_library(session, project_id, limit=5)
    finally:
        event.remove(session, 'loaded_as_persistent', count_load)
    assert page.total == page.exception_group_count == 80
    assert page.available_group_count == 0
    assert len(page.groups) == 5
    assert page.next_cursor == '5'
    assert loaded['VoiceCloneIdentity'] == 5
    assert loaded['VoiceCloneProfile'] == 0
    assert all(group.recommended_profile is None for group in page.groups)
