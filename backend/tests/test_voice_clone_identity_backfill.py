from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.person import Person  # noqa: F401
from app.models.voice_clone_identity import VoiceCloneIdentity
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_sample import VoiceCloneSample
from app.models.voiceprint_sample import VoiceprintSample  # noqa: F401
from app.services.voiceprint_provider import VoiceprintEmbeddingResult
from scripts import backfill_voice_clone_identities as backfill_script
from scripts.backfill_voice_clone_identities import run_backfill
from scripts.migrate_v2_voice_clone_identities import (
    inspect_voice_clone_identity_migration,
)


def _embedding(
    values: list[float],
    model_id: str = 'speaker-model',
) -> VoiceprintEmbeddingResult:
    return VoiceprintEmbeddingResult(
        embedding=values,
        provider_name='test-provider',
        provider_model_id=model_id,
        provider_message='test embedding',
    )


@pytest.fixture()
def fake_embeddings():
    return lambda _path: _embedding([1.0, 0.0])


def _create_database(path: Path) -> Path:
    engine = create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(engine)
    engine.dispose()
    inspect_voice_clone_identity_migration(path, apply=True)
    return path


def _add_sample(
    database: Path,
    sample_id: str,
    audio_path: Path,
    *,
    display_name: str,
    identity_id: str | None = None,
) -> None:
    engine = create_engine(f'sqlite:///{database}')
    with sessionmaker(bind=engine)() as session:
        session.add(VoiceCloneSample(
            id=sample_id,
            display_name=display_name,
            original_filename=audio_path.name,
            audio_local_path=str(audio_path),
            clone_readiness_status='ready',
            identity_id=identity_id,
        ))
        session.commit()
    engine.dispose()


@pytest.fixture()
def legacy_database(tmp_path: Path) -> Path:
    database = _create_database(tmp_path / 'legacy.db')
    first = tmp_path / 'first.wav'
    duplicate = tmp_path / 'duplicate.wav'
    different_recording = tmp_path / 'different.wav'
    first.write_bytes(b'RIFF-exact-audio')
    duplicate.write_bytes(first.read_bytes())
    different_recording.write_bytes(b'RIFF-different-recording')
    _add_sample(database, 'sample-1', first, display_name='主持人甲')
    _add_sample(database, 'sample-2', duplicate, display_name='甲老师')
    _add_sample(database, 'sample-3', different_recording, display_name='改名后的甲')

    engine = create_engine(f'sqlite:///{database}')
    with sessionmaker(bind=engine)() as session:
        session.add(VoiceCloneProfile(
            id='profile-1',
            provider='qwen',
            target_model='test-model',
            voice_name='历史音色',
            source_clone_sample_id='sample-3',
        ))
        session.commit()
    engine.dispose()
    return database


def test_backfill_dry_run_does_not_write_database(
    legacy_database: Path,
    tmp_path: Path,
    fake_embeddings,
) -> None:
    before = legacy_database.read_bytes()

    report = run_backfill(
        legacy_database,
        tmp_path / 'report.json',
        fake_embeddings,
    )

    assert report['database_path'] == str(legacy_database.resolve())
    assert report['embedding_model'] == 'speaker-model'
    assert report['threshold'] == 0.90
    assert report['exact_groups'] == 1
    assert report['speaker_match_candidates'] == 1
    assert len(report['exact_hash_groups']) == 1
    assert len(report['acoustic_candidates']) == 1
    assert len(report['proposed_identities']) == 1
    assert len(report['confirmation_token']) == 64
    canonical = {
        key: value
        for key, value in report.items()
        if key != 'confirmation_token'
    }
    assert report['confirmation_token'] == hashlib.sha256(json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode()).hexdigest()
    assert json.loads((tmp_path / 'report.json').read_text()) == report
    assert legacy_database.read_bytes() == before


def test_backfill_apply_requires_matching_confirmation_file(
    legacy_database: Path,
    tmp_path: Path,
) -> None:
    before = legacy_database.read_bytes()

    with pytest.raises(RuntimeError, match='确认文件'):
        run_backfill(
            legacy_database,
            tmp_path / 'report.json',
            apply=True,
        )

    assert legacy_database.read_bytes() == before


def test_backfill_apply_writes_only_identity_links_and_embeddings(
    legacy_database: Path,
    tmp_path: Path,
    fake_embeddings,
) -> None:
    report_path = tmp_path / 'report.json'
    preview = run_backfill(legacy_database, report_path, fake_embeddings)
    confirmation = tmp_path / 'confirmation.json'
    confirmation.write_text(json.dumps({
        'confirmation_token': preview['confirmation_token'],
    }))

    applied = run_backfill(
        legacy_database,
        report_path,
        fake_embeddings,
        apply=True,
        confirmation_file=confirmation,
    )

    assert applied['confirmation_token'] == preview['confirmation_token']
    engine = create_engine(f'sqlite:///{legacy_database}')
    with sessionmaker(bind=engine)() as session:
        assert session.scalar(select(func.count()).select_from(VoiceCloneIdentity)) == 1
        samples = list(session.scalars(select(VoiceCloneSample).order_by(VoiceCloneSample.id)))
        assert len(samples) == 3
        assert len({sample.identity_id for sample in samples}) == 1
        assert all(sample.embedding == [1.0, 0.0] for sample in samples)
        assert all(sample.embedding_model_id == 'speaker-model' for sample in samples)
        assert all(sample.content_sha256 is None for sample in samples)
        assert all(sample.file_size_bytes is None for sample in samples)
        profile = session.get(VoiceCloneProfile, 'profile-1')
        assert profile is not None
        assert profile.identity_id == samples[0].identity_id
    engine.dispose()
    assert all(path.exists() for path in (
        legacy_database.parent / 'first.wav',
        legacy_database.parent / 'duplicate.wav',
        legacy_database.parent / 'different.wav',
    ))


def test_backfill_apply_rejects_stale_file_before_database_write(
    legacy_database: Path,
    tmp_path: Path,
    fake_embeddings,
) -> None:
    report_path = tmp_path / 'report.json'
    preview = run_backfill(legacy_database, report_path, fake_embeddings)
    confirmation = tmp_path / 'confirmation.txt'
    confirmation.write_text(preview['confirmation_token'])
    (tmp_path / 'first.wav').write_bytes(b'RIFF-changed-after-preview')
    before = legacy_database.read_bytes()

    with pytest.raises(RuntimeError, match='过期|变化'):
        run_backfill(
            legacy_database,
            report_path,
            fake_embeddings,
            apply=True,
            confirmation_file=confirmation,
        )

    assert legacy_database.read_bytes() == before


def test_backfill_blocks_exact_group_above_three_sample_limit(
    tmp_path: Path,
    fake_embeddings,
) -> None:
    database = _create_database(tmp_path / 'cap.db')
    for index in range(4):
        audio_path = tmp_path / f'cap-{index}.wav'
        audio_path.write_bytes(b'RIFF-same-four-times')
        _add_sample(
            database,
            f'sample-{index}',
            audio_path,
            display_name=f'主持人-{index}',
        )

    report = run_backfill(database, tmp_path / 'cap-report.json', fake_embeddings)

    assert len(report['three_sample_conflicts']) == 1
    assert report['proposed_identities'] == []
    assert report['sample_updates'] == []


def test_backfill_skips_group_from_a_different_embedding_model(tmp_path: Path) -> None:
    database = _create_database(tmp_path / 'models.db')
    first = tmp_path / 'a.wav'
    second = tmp_path / 'b.wav'
    first.write_bytes(b'RIFF-model-a')
    second.write_bytes(b'RIFF-model-b')
    _add_sample(database, 'sample-a', first, display_name='主持人 A')
    _add_sample(database, 'sample-b', second, display_name='主持人 B')

    def different_models(path: Path) -> VoiceprintEmbeddingResult:
        return _embedding(
            [1.0, 0.0],
            model_id='model-a' if path.name == 'a.wav' else 'model-b',
        )

    report = run_backfill(
        database,
        tmp_path / 'models-report.json',
        different_models,
    )

    assert report['embedding_model'] == 'model-a'
    assert report['model_mismatches'] == [{
        'sample_ids': ['sample-b'],
        'expected_model': 'model-a',
        'actual_model': 'model-b',
    }]
    assert [entry['sample_ids'] for entry in report['proposed_identities']] == [
        ['sample-a'],
    ]


def test_backfill_skips_whole_known_hash_group_when_one_file_is_missing(
    tmp_path: Path,
    fake_embeddings,
) -> None:
    database = _create_database(tmp_path / 'missing.db')
    readable = tmp_path / 'readable.wav'
    missing = tmp_path / 'missing.wav'
    readable.write_bytes(b'RIFF-known-exact-group')
    content_sha256 = hashlib.sha256(readable.read_bytes()).hexdigest()
    _add_sample(database, 'sample-readable', readable, display_name='主持人')
    _add_sample(database, 'sample-missing', missing, display_name='主持人')
    engine = create_engine(f'sqlite:///{database}')
    with sessionmaker(bind=engine)() as session:
        for sample in session.scalars(select(VoiceCloneSample)):
            sample.content_sha256 = content_sha256
        session.commit()
    engine.dispose()

    report = run_backfill(
        database,
        tmp_path / 'missing-report.json',
        fake_embeddings,
    )

    assert report['unreadable_files'][0]['sample_id'] == 'sample-missing'
    assert report['sample_updates'] == []
    assert report['proposed_identities'] == []


@pytest.mark.parametrize(
    'target_kind,link_kind',
    [
        ('database', 'direct'),
        ('database', 'symlink'),
        ('database', 'hardlink'),
        ('audio', 'direct'),
        ('audio', 'symlink'),
        ('audio', 'hardlink'),
    ],
)
def test_backfill_rejects_output_aliasing_database_or_audio(
    legacy_database: Path,
    tmp_path: Path,
    fake_embeddings,
    target_kind: str,
    link_kind: str,
) -> None:
    target = (
        legacy_database
        if target_kind == 'database'
        else tmp_path / 'first.wav'
    )
    output = target
    if link_kind != 'direct':
        output = tmp_path / f'{target_kind}-{link_kind}.json'
        if link_kind == 'symlink':
            output.symlink_to(target)
        else:
            os.link(target, output)
    before_database = legacy_database.read_bytes()
    before_audio = (tmp_path / 'first.wav').read_bytes()

    with pytest.raises(RuntimeError, match='输出|报告|冲突'):
        run_backfill(legacy_database, output, fake_embeddings)

    assert legacy_database.read_bytes() == before_database
    assert (tmp_path / 'first.wav').read_bytes() == before_audio


def test_backfill_apply_rejects_audio_output_before_database_write(
    legacy_database: Path,
    tmp_path: Path,
    fake_embeddings,
) -> None:
    confirmation = tmp_path / 'safe-preview.json'
    run_backfill(legacy_database, confirmation, fake_embeddings)
    output = tmp_path / 'first.wav'
    before_database = legacy_database.read_bytes()
    before_audio = output.read_bytes()

    with pytest.raises(RuntimeError, match='输出|报告|冲突'):
        run_backfill(
            legacy_database,
            output,
            fake_embeddings,
            apply=True,
            confirmation_file=confirmation,
        )

    assert legacy_database.read_bytes() == before_database
    assert output.read_bytes() == before_audio


def test_backfill_apply_report_staging_failure_precedes_database_mutation(
    legacy_database: Path,
    tmp_path: Path,
    fake_embeddings,
) -> None:
    preview_path = tmp_path / 'safe-preview.json'
    preview = run_backfill(legacy_database, preview_path, fake_embeddings)
    confirmation = tmp_path / 'confirmation.json'
    confirmation.write_text(json.dumps({
        'confirmation_token': preview['confirmation_token'],
    }))
    blocked_parent = tmp_path / 'not-a-directory'
    blocked_parent.write_text('file')

    with pytest.raises(OSError):
        run_backfill(
            legacy_database,
            blocked_parent / 'report.json',
            fake_embeddings,
            apply=True,
            confirmation_file=confirmation,
        )

    engine = create_engine(f'sqlite:///{legacy_database}')
    with sessionmaker(bind=engine)() as session:
        assert session.scalar(select(func.count()).select_from(VoiceCloneIdentity)) == 0
        assert all(
            sample.identity_id is None
            for sample in session.scalars(select(VoiceCloneSample))
        )
    engine.dispose()


def test_backfill_finalize_failure_states_that_database_was_already_applied(
    legacy_database: Path,
    tmp_path: Path,
    fake_embeddings,
    monkeypatch,
) -> None:
    report_path = tmp_path / 'report.json'
    preview = run_backfill(legacy_database, report_path, fake_embeddings)
    confirmation = tmp_path / 'confirmation.json'
    confirmation.write_text(json.dumps({
        'confirmation_token': preview['confirmation_token'],
    }))
    monkeypatch.setattr(
        backfill_script,
        '_finalize_staged_report',
        lambda *_args: (_ for _ in ()).throw(OSError('disk unavailable')),
        raising=False,
    )

    with pytest.raises(RuntimeError, match='已经应用.*禁止重试'):
        run_backfill(
            legacy_database,
            report_path,
            fake_embeddings,
            apply=True,
            confirmation_file=confirmation,
        )

    engine = create_engine(f'sqlite:///{legacy_database}')
    with sessionmaker(bind=engine)() as session:
        assert session.scalar(select(func.count()).select_from(VoiceCloneIdentity)) == 1
        assert all(
            sample.identity_id is not None
            for sample in session.scalars(select(VoiceCloneSample))
        )
    engine.dispose()


def test_backfill_skips_identity_null_group_with_old_embedding_model(
    tmp_path: Path,
    fake_embeddings,
) -> None:
    database = _create_database(tmp_path / 'legacy-model.db')
    audio = tmp_path / 'legacy-model.wav'
    audio.write_bytes(b'RIFF-legacy-model')
    _add_sample(database, 'sample-legacy', audio, display_name='主持人')
    engine = create_engine(f'sqlite:///{database}')
    with sessionmaker(bind=engine)() as session:
        sample = session.get(VoiceCloneSample, 'sample-legacy')
        sample.embedding = [0.0, 1.0]
        sample.embedding_model_id = 'old-model'
        session.commit()
    engine.dispose()
    before = database.read_bytes()

    report = run_backfill(
        database,
        tmp_path / 'legacy-model-report.json',
        fake_embeddings,
    )

    assert report['model_mismatches'] == [{
        'sample_ids': ['sample-legacy'],
        'expected_model': 'old-model',
        'actual_model': 'speaker-model',
    }]
    assert report['proposed_identities'] == []
    assert report['sample_updates'] == []
    assert database.read_bytes() == before


def test_backfill_blocks_group_linked_from_profile_with_dangling_identity(
    tmp_path: Path,
    fake_embeddings,
) -> None:
    database = _create_database(tmp_path / 'dangling-profile.db')
    audio = tmp_path / 'dangling-profile.wav'
    audio.write_bytes(b'RIFF-dangling-profile')
    _add_sample(database, 'sample-legacy', audio, display_name='主持人')
    engine = create_engine(f'sqlite:///{database}')
    with sessionmaker(bind=engine)() as session:
        session.add(VoiceCloneProfile(
            id='profile-dangling',
            provider='qwen',
            target_model='test-model',
            voice_name='历史音色',
            source_clone_sample_id='sample-legacy',
            identity_id='missing-identity',
        ))
        session.commit()
    engine.dispose()

    report = run_backfill(
        database,
        tmp_path / 'dangling-profile-report.json',
        fake_embeddings,
    )

    assert any(
        entry['kind'] == 'profile_identity'
        for entry in report['dangling_references']
    )
    assert report['proposed_identities'] == []
    assert report['sample_updates'] == []
    assert report['profile_updates'] == []


def test_backfill_blocks_group_when_profile_identity_conflicts_with_plan(
    tmp_path: Path,
    fake_embeddings,
) -> None:
    database = _create_database(tmp_path / 'profile-conflict.db')
    audio = tmp_path / 'profile-conflict.wav'
    audio.write_bytes(b'RIFF-profile-conflict')
    _add_sample(database, 'sample-legacy', audio, display_name='主持人')
    engine = create_engine(f'sqlite:///{database}')
    with sessionmaker(bind=engine)() as session:
        session.add(VoiceCloneIdentity(
            id='existing-identity',
            display_name='另一个人',
            aliases=[],
            reference_embedding=[0.0, 1.0],
            embedding_model_id='speaker-model',
        ))
        session.add(VoiceCloneProfile(
            id='profile-conflict',
            provider='qwen',
            target_model='test-model',
            voice_name='历史音色',
            source_clone_sample_id='sample-legacy',
            identity_id='existing-identity',
        ))
        session.commit()
    engine.dispose()

    report = run_backfill(
        database,
        tmp_path / 'profile-conflict-report.json',
        fake_embeddings,
    )

    assert any(
        entry['kind'] == 'profile_identity_conflict'
        for entry in report['dangling_references']
    )
    assert report['proposed_identities'] == []
    assert report['sample_updates'] == []
    assert report['profile_updates'] == []


def test_backfill_apply_rejects_concurrent_embedding_metadata_change(
    legacy_database: Path,
    tmp_path: Path,
    fake_embeddings,
    monkeypatch,
) -> None:
    report_path = tmp_path / 'concurrent-report.json'
    preview = run_backfill(legacy_database, report_path, fake_embeddings)
    confirmation = tmp_path / 'concurrent-confirmation.txt'
    confirmation.write_text(preview['confirmation_token'])
    real_confirmation_token = backfill_script._confirmation_file_token

    def mutate_after_scan(path: Path) -> str:
        engine = create_engine(f'sqlite:///{legacy_database}')
        with sessionmaker(bind=engine)() as session:
            sample = session.get(VoiceCloneSample, 'sample-1')
            sample.embedding_status = 'concurrent-status'
            sample.embedding_provider_name = 'concurrent-provider'
            sample.embedding_message = 'concurrent-message'
            session.commit()
        engine.dispose()
        return real_confirmation_token(path)

    monkeypatch.setattr(
        backfill_script,
        '_confirmation_file_token',
        mutate_after_scan,
    )

    with pytest.raises(RuntimeError, match='数据库已变化|过期'):
        run_backfill(
            legacy_database,
            report_path,
            fake_embeddings,
            apply=True,
            confirmation_file=confirmation,
        )

    engine = create_engine(f'sqlite:///{legacy_database}')
    with sessionmaker(bind=engine)() as session:
        sample = session.get(VoiceCloneSample, 'sample-1')
        assert sample.identity_id is None
        assert sample.embedding is None
        assert sample.embedding_status == 'concurrent-status'
        assert sample.embedding_provider_name == 'concurrent-provider'
        assert sample.embedding_message == 'concurrent-message'
        assert session.scalar(select(func.count()).select_from(VoiceCloneIdentity)) == 0
    engine.dispose()
