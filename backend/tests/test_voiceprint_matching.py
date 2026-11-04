from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.job import Job
from app.models.person import Person
from app.models.project import Project
from app.models.voiceprint_sample import VoiceprintSample
from app.services.voiceprint_matching import build_voiceprint_match_lookup, match_job_voiceprints
from app.services.voiceprint_provider import VoiceprintEmbeddingResult, VoiceprintProviderStatus


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _create_ready_person(
    session,
    name: str,
    embedding: list[float],
    program_key: Optional[str] = None,
    project_id: Optional[str] = None,
    review_status: str = 'confirmed',
) -> Person:
    person = Person(name=name, project_id=project_id, program_name=program_key, program_key=program_key)
    session.add(person)
    session.commit()
    session.refresh(person)
    session.add(
        VoiceprintSample(
            person_id=person.id,
            original_filename=f'{name}.wav',
            audio_local_path=f'/tmp/{name}.wav',
            duration_ms=1000,
            sample_rate_hz=16000,
            channel_count=1,
            embedding=embedding,
            embedding_status='ready',
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
            source_type='auto_candidate' if review_status == 'candidate' else 'manual_upload',
            review_status=review_status,
        )
    )
    session.commit()
    return person


def test_unconfirmed_candidate_is_not_used_for_automatic_matching() -> None:
    from app.services.voiceprint_matching import _list_reference_people

    session = _build_session()
    _create_ready_person(
        session,
        '尚未验证人物',
        [1.0, 0.0],
        program_key='候选测试',
        review_status='candidate',
    )
    job = _create_job(session, Path('/tmp/unconfirmed.wav'), program_key='候选测试')

    assert _list_reference_people(session, job) == []


def _create_job(
    session,
    source_path: Path,
    program_key: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Job:
    source_path.write_bytes(b'RIFFdemo')
    job = Job(
        source_url='https://example.com/source.wav',
        source_local_path=str(source_path),
        source_media_kind='audio',
        project_id=project_id,
        program_key=program_key,
        program_name=program_key,
        status='completed',
        normalized_segments=[
            {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1800, 'text': '你好'},
        ],
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_match_job_voiceprints_auto_maps_high_confidence(monkeypatch, tmp_path) -> None:
    session = _build_session()
    _create_ready_person(session, '撒贝宁', [1.0, 0.0], program_key='联调节目')
    job = _create_job(session, tmp_path / 'source.wav', program_key='联调节目')
    clip_path = tmp_path / 'clip.wav'
    clip_path.write_bytes(b'RIFFclip')

    monkeypatch.setattr(
        'app.services.voiceprint_matching.get_voiceprint_provider_status',
        lambda: VoiceprintProviderStatus(provider='3d_speaker_cli', model_id='model', available=True, message='ok'),
    )
    monkeypatch.setattr('app.services.voiceprint_matching._extract_speaker_clip', lambda *_args, **_kwargs: clip_path)
    monkeypatch.setattr(
        'app.services.voiceprint_matching.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[0.99, 0.01],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )

    auto_mappings = match_job_voiceprints(session, job)
    lookup = build_voiceprint_match_lookup(session, job.id)

    assert auto_mappings == {'speaker_0': '撒贝宁'}
    assert lookup['speaker_0']['status'] == 'auto_matched'
    assert lookup['speaker_0']['candidates'][0]['person_name'] == '撒贝宁'


def test_match_job_voiceprints_marks_pending_when_provider_unavailable(monkeypatch, tmp_path) -> None:
    session = _build_session()
    _create_ready_person(session, '祝勇', [1.0, 0.0])
    job = _create_job(session, tmp_path / 'source.wav')

    monkeypatch.setattr(
        'app.services.voiceprint_matching.get_voiceprint_provider_status',
        lambda: VoiceprintProviderStatus(provider='disabled', model_id='model', available=False, message='pending'),
    )

    auto_mappings = match_job_voiceprints(session, job)
    lookup = build_voiceprint_match_lookup(session, job.id)

    assert auto_mappings == {}
    assert lookup['speaker_0']['status'] == 'pending_provider'


def test_match_job_voiceprints_prefers_same_program_people(monkeypatch, tmp_path) -> None:
    session = _build_session()
    _create_ready_person(session, '全局人物', [0.0, 1.0], program_key=None)
    _create_ready_person(session, '节目人物', [1.0, 0.0], program_key='典籍里的中国')
    job = _create_job(session, tmp_path / 'source.wav', program_key='典籍里的中国')
    clip_path = tmp_path / 'clip.wav'
    clip_path.write_bytes(b'RIFFclip')

    monkeypatch.setattr(
        'app.services.voiceprint_matching.get_voiceprint_provider_status',
        lambda: VoiceprintProviderStatus(provider='3d_speaker_cli', model_id='model', available=True, message='ok'),
    )
    monkeypatch.setattr('app.services.voiceprint_matching._extract_speaker_clip', lambda *_args, **_kwargs: clip_path)
    monkeypatch.setattr(
        'app.services.voiceprint_matching.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=[1.0, 0.0],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )

    auto_mappings = match_job_voiceprints(session, job)
    lookup = build_voiceprint_match_lookup(session, job.id)

    assert auto_mappings == {'speaker_0': '节目人物'}
    assert lookup['speaker_0']['candidates'][0]['person_name'] == '节目人物'


def test_project_job_reads_own_and_global_but_never_other_project_voiceprints() -> None:
    from app.services.voiceprint_matching import _list_reference_people

    session = _build_session()
    project_a = Project(name='项目 A', name_key='项目 a', legacy_program_key='项目 a')
    project_b = Project(name='项目 B', name_key='项目 b', legacy_program_key='项目 b')
    session.add_all([project_a, project_b])
    session.commit()
    _create_ready_person(session, '项目 A 人物', [1.0, 0.0], program_key='项目 a', project_id=project_a.id)
    _create_ready_person(session, '项目 B 人物', [0.0, 1.0], program_key='项目 b', project_id=project_b.id)
    _create_ready_person(session, '全局人物', [0.5, 0.5])
    job = _create_job(
        session,
        Path('/tmp/project-a.wav'),
        program_key='项目 a',
        project_id=project_a.id,
    )

    references = _list_reference_people(session, job)

    assert {item['person_name'] for item in references} == {'项目 A 人物', '全局人物'}


def test_global_voiceprint_scope_uses_global_person_when_project_has_no_samples() -> None:
    from app.services.voiceprint_matching import _list_reference_people

    session = _build_session()
    project = Project(
        name='全局声纹项目',
        name_key='全局声纹项目',
        legacy_program_key='全局声纹项目',
        default_voiceprint_scope='global',
    )
    session.add(project)
    session.commit()
    _create_ready_person(session, '全局人物', [1.0, 0.0])
    job = _create_job(
        session,
        Path('/tmp/global-project.wav'),
        program_key='全局声纹项目',
        project_id=project.id,
    )

    references = _list_reference_people(session, job)

    assert [item['person_name'] for item in references] == ['全局人物']


def test_global_voiceprint_scope_keeps_project_and_global_people() -> None:
    from app.services.voiceprint_matching import _list_reference_people

    session = _build_session()
    project = Project(
        name='混合声纹项目',
        name_key='混合声纹项目',
        legacy_program_key='混合声纹项目',
        default_voiceprint_scope='global',
    )
    session.add(project)
    session.commit()
    _create_ready_person(
        session,
        '项目人物',
        [1.0, 0.0],
        program_key='混合声纹项目',
        project_id=project.id,
    )
    _create_ready_person(session, '全局人物', [0.0, 1.0])
    job = _create_job(
        session,
        Path('/tmp/mixed-project.wav'),
        program_key='混合声纹项目',
        project_id=project.id,
    )

    references = _list_reference_people(session, job)

    assert {item['person_name'] for item in references} == {'项目人物', '全局人物'}


def test_ambiguous_legacy_person_is_not_read_by_multiple_uuid_projects() -> None:
    from app.services.voiceprint_matching import _list_reference_people

    session = _build_session()
    project_a = Project(name='歧义 A', name_key='歧义 a', legacy_program_key='共享旧项目')
    project_b = Project(name='歧义 B', name_key='歧义 b', legacy_program_key='共享旧项目')
    session.add_all([project_a, project_b])
    session.commit()
    _create_ready_person(session, '歧义旧人物', [1.0, 0.0], program_key='共享旧项目')
    job = _create_job(
        session,
        Path('/tmp/ambiguous-project.wav'),
        program_key='共享旧项目',
        project_id=project_a.id,
    )

    assert _list_reference_people(session, job) == []


def test_resolve_job_audio_path_prefers_normalized_preview_for_audio_jobs(monkeypatch, tmp_path) -> None:
    source_path = tmp_path / 'source.wav'
    source_path.write_bytes(b'RIFFdemo')
    preview_path = tmp_path / 'preview.wav'
    preview_path.write_bytes(b'RIFFpreview')
    job = Job(id='job-audio', source_local_path=str(source_path), source_media_kind='audio')

    monkeypatch.setattr(
        'app.services.voiceprint_matching.prepare_preview_audio',
        lambda path, cache_key, media_kind='audio': preview_path,
    )

    from app.services.voiceprint_matching import _resolve_job_audio_path

    assert _resolve_job_audio_path(job) == preview_path


def test_resolve_job_audio_path_falls_back_to_original_when_preview_fails(monkeypatch, tmp_path) -> None:
    source_path = tmp_path / 'source.wav'
    source_path.write_bytes(b'RIFFdemo')
    job = Job(id='job-audio', source_local_path=str(source_path), source_media_kind='audio')

    monkeypatch.setattr(
        'app.services.voiceprint_matching.prepare_preview_audio',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('preview failed')),
    )

    from app.services.voiceprint_matching import _resolve_job_audio_path

    assert _resolve_job_audio_path(job) == source_path


def test_pick_matching_segments_prefers_multiple_segments_until_target_duration() -> None:
    from app.services.voiceprint_matching import _pick_matching_segments
    from app.services.voiceprint_matching import TARGET_MATCH_AUDIO_MS

    selected = _pick_matching_segments(
        [
            {'speakerKey': 'speaker_0', 'startMs': 2600, 'endMs': 3500},
            {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 2200},
            {'speakerKey': 'speaker_0', 'startMs': 3600, 'endMs': 4500},
            {'speakerKey': 'speaker_0', 'startMs': 2300, 'endMs': 2500},
            {'speakerKey': 'speaker_0', 'startMs': 4600, 'endMs': 6400},
            {'speakerKey': 'speaker_1', 'startMs': 0, 'endMs': 5000},
        ],
        'speaker_0',
    )

    assert [(item['startMs'], item['endMs']) for item in selected] == [
        (0, 2200),
        (2600, 3500),
        (4600, 6400),
    ]
    assert sum(item['endMs'] - item['startMs'] for item in selected) >= TARGET_MATCH_AUDIO_MS


def test_pick_matching_segments_falls_back_to_short_segments() -> None:
    from app.services.voiceprint_matching import _pick_matching_segments

    selected = _pick_matching_segments(
        [
            {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 250},
            {'speakerKey': 'speaker_0', 'startMs': 300, 'endMs': 650},
            {'speakerKey': 'speaker_0', 'startMs': 700, 'endMs': 950},
        ],
        'speaker_0',
    )

    assert [(item['startMs'], item['endMs']) for item in selected] == [
        (0, 250),
        (300, 650),
        (700, 950),
    ]


def test_extract_speaker_clip_concats_multiple_segments(monkeypatch, tmp_path) -> None:
    from app.services.voiceprint_matching import _extract_speaker_clip

    calls: list[list[str]] = []

    def fake_run(command, capture_output, text):
        calls.append(command)
        Path(command[-1]).write_bytes(b'RIFFdemo')
        return SimpleNamespace(returncode=0, stderr='', stdout='')

    monkeypatch.setattr('app.services.voiceprint_matching.subprocess.run', fake_run)

    source_path = tmp_path / 'source.wav'
    source_path.write_bytes(b'RIFFsource')
    output_path = _extract_speaker_clip(
        source_path,
        [
            {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1200},
            {'speakerKey': 'speaker_0', 'startMs': 1800, 'endMs': 3200},
        ],
        tmp_path / 'clips',
    )

    assert output_path.exists()
    assert len(calls) == 3
    assert any(command[:5] == ['ffmpeg', '-y', '-f', 'concat', '-safe'] for command in calls)
