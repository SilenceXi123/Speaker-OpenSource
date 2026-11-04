from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.voice_clone_identity import VoiceCloneIdentity
from app.models.voice_clone_sample import VoiceCloneSample
from app.services import voice_clone_identity as identity_service
from app.services.voice_clone_identity import resolve_voice_clone_identity
from app.services.voiceprint_provider import VoiceprintEmbeddingResult


@pytest.fixture()
def session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'identity.db'}")
    Base.metadata.create_all(bind=engine)
    database_session = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield database_session
    finally:
        database_session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def identity(session: Session) -> VoiceCloneIdentity:
    item = VoiceCloneIdentity(display_name='原主持人', aliases=['原主持人'])
    session.add(item)
    session.commit()
    return item


@pytest.fixture()
def stored_sample(session: Session, identity: VoiceCloneIdentity, tmp_path: Path) -> VoiceCloneSample:
    source = tmp_path / 'stored.wav'
    source.write_bytes(b'RIFF-identical-audio')
    item = VoiceCloneSample(
        display_name='原主持人',
        original_filename=source.name,
        audio_local_path=str(source),
        content_sha256='a' * 64,
        identity_id=identity.id,
        embedding=[1.0, 0.0],
        embedding_status='ready',
        embedding_provider_name='3d_speaker_cli',
        embedding_model_id='speaker-model',
        embedding_message='embedding 提取成功',
    )
    session.add(item)
    session.commit()
    return item


def embedding_result(values: list[float], model_id: str = 'speaker-model') -> VoiceprintEmbeddingResult:
    return VoiceprintEmbeddingResult(
        embedding=values,
        provider_name='3d_speaker_cli',
        provider_model_id=model_id,
        provider_message='embedding 提取成功',
    )


def test_exact_audio_match_ignores_submitted_name(session, stored_sample, tmp_path, monkeypatch):
    incoming = tmp_path / 'renamed.wav'
    incoming.write_bytes(Path(stored_sample.audio_local_path).read_bytes())

    result = resolve_voice_clone_identity(
        session,
        display_name='完全不同的名字',
        content_sha256=stored_sample.content_sha256,
        audio_path=incoming,
    )

    assert result.match_type == 'exact_audio'
    assert result.exact_sample.id == stored_sample.id
    assert result.identity.id == stored_sample.identity_id
    assert result.embedding == [1.0, 0.0]
    assert result.embedding_status == 'ready'
    assert '完全不同的名字' in result.identity.aliases


def test_exact_legacy_sample_gets_identity_in_current_transaction(session, tmp_path):
    source = tmp_path / 'legacy.wav'
    source.write_bytes(b'RIFF-legacy')
    stored_sample = VoiceCloneSample(
        display_name='旧主持人',
        original_filename=source.name,
        audio_local_path=str(source),
        content_sha256='f' * 64,
    )
    session.add(stored_sample)
    session.commit()

    result = resolve_voice_clone_identity(
        session,
        display_name='忽略此名称',
        content_sha256=stored_sample.content_sha256,
        audio_path=source,
    )

    assert result.match_type == 'exact_audio'
    assert result.identity.id == stored_sample.identity_id
    assert session.get(VoiceCloneIdentity, stored_sample.identity_id) is not None


def test_embedding_match_groups_different_recording(session, identity, tmp_path, monkeypatch):
    monkeypatch.setattr(identity_service, 'extract_voiceprint_embedding', lambda _path: embedding_result([0.99, 0.01]))
    identity.reference_embedding = [1.0, 0.0]
    identity.embedding_model_id = 'speaker-model'
    session.commit()

    result = resolve_voice_clone_identity(
        session,
        display_name='改过名字的人',
        content_sha256='b' * 64,
        audio_path=tmp_path / 'different.wav',
    )

    assert result.match_type == 'speaker_embedding'
    assert result.identity.id == identity.id
    assert result.match_score > 0.90
    assert '改过名字的人' in result.identity.aliases
    assert result.embedding_provider_name == '3d_speaker_cli'


def test_zero_similarity_matches_when_threshold_is_zero(session, identity, tmp_path, monkeypatch):
    monkeypatch.setattr(identity_service, 'extract_voiceprint_embedding', lambda _path: embedding_result([0.0, 1.0]))
    identity.reference_embedding = [1.0, 0.0]
    identity.embedding_model_id = 'speaker-model'
    session.commit()

    result = resolve_voice_clone_identity(
        session,
        display_name='零阈值主持人',
        content_sha256='0' * 64,
        audio_path=tmp_path / 'zero.wav',
        threshold=0.0,
    )

    assert result.match_type == 'speaker_embedding'
    assert result.identity.id == identity.id
    assert result.match_score == 0.0


def test_below_threshold_creates_new_identity(session, identity, tmp_path, monkeypatch):
    monkeypatch.setattr(identity_service, 'extract_voiceprint_embedding', lambda _path: embedding_result([0.0, 1.0]))
    identity.reference_embedding = [1.0, 0.0]
    identity.embedding_model_id = 'speaker-model'
    session.commit()

    result = resolve_voice_clone_identity(
        session,
        display_name='新主持人',
        content_sha256='c' * 64,
        audio_path=tmp_path / 'new.wav',
    )

    assert result.match_type == 'new_identity'
    assert result.identity.id != identity.id
    assert result.identity.reference_embedding == [0.0, 1.0]
    assert result.identity.embedding_model_id == 'speaker-model'


def test_embedding_provider_failure_does_not_create_identity(session, tmp_path, monkeypatch):
    monkeypatch.setattr(identity_service, 'extract_voiceprint_embedding', lambda _path: (_ for _ in ()).throw(RuntimeError('offline')))

    with pytest.raises(RuntimeError, match='声纹检查失败'):
        resolve_voice_clone_identity(session, display_name='主持人', content_sha256='d' * 64, audio_path=tmp_path / 'a.wav')

    assert session.query(VoiceCloneIdentity).count() == 0


def test_different_embedding_models_do_not_match(session, identity, tmp_path, monkeypatch):
    identity.embedding_model_id = 'old-model'
    identity.reference_embedding = [1.0, 0.0]
    session.commit()
    monkeypatch.setattr(identity_service, 'extract_voiceprint_embedding', lambda _path: embedding_result([1.0, 0.0], model_id='new-model'))

    result = resolve_voice_clone_identity(
        session,
        display_name='主持人',
        content_sha256='e' * 64,
        audio_path=tmp_path / 'a.wav',
    )

    assert result.match_type == 'new_identity'
