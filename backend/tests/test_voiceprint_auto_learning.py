from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.job import Job
from app.models.person import Person
from app.models.project import Project
from app.models.voice_clone_profile import VoiceCloneProfile  # noqa: F401
from app.models.voice_clone_sample import VoiceCloneSample  # noqa: F401
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord  # noqa: F401
from app.models.voiceprint_match import VoiceprintMatch  # noqa: F401
from app.models.voiceprint_sample import VoiceprintSample
from app.services.voiceprint_provider import VoiceprintEmbeddingResult


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _create_completed_job(session, source_path: Path, *, text: str = '大家好欢迎来到节目') -> Job:
    source_path.write_bytes(b'RIFFsource')
    job = Job(
        source_url='https://example.com/source.wav',
        source_local_path=str(source_path),
        source_media_kind='audio',
        status='completed',
        program_key='中国好团队',
        program_name='中国好团队',
        speaker_mappings={'speaker_0': '刘权'},
        normalized_segments=[
            {
                'speakerKey': 'speaker_0',
                'startMs': 1000,
                'endMs': 5200,
                'text': text,
            }
        ],
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _patch_audio_and_embedding(monkeypatch, tmp_path, embedding=None) -> Path:
    clip_path = tmp_path / 'candidate.wav'
    clip_path.write_bytes(b'RIFFcandidate')
    monkeypatch.setattr(
        'app.services.voiceprint_auto_learning._extract_candidate_clip',
        lambda *_args, **_kwargs: clip_path,
    )
    monkeypatch.setattr(
        'app.services.voiceprint_auto_learning.extract_voiceprint_embedding',
        lambda _path: VoiceprintEmbeddingResult(
            embedding=embedding or [1.0, 0.0],
            provider_name='3d_speaker_cli',
            provider_model_id='model',
            provider_message='ok',
        ),
    )
    return clip_path


def test_learn_voiceprint_candidates_creates_candidate_for_confirmed_named_speaker(monkeypatch, tmp_path) -> None:
    from app.services.voiceprint_auto_learning import learn_voiceprint_candidates_for_job

    session = _build_session()
    job = _create_completed_job(session, tmp_path / 'source.wav')
    _patch_audio_and_embedding(monkeypatch, tmp_path)

    result = learn_voiceprint_candidates_for_job(session, job)

    assert result.created_count == 1
    assert result.skipped_count == 0
    person = session.query(Person).filter(Person.name == '刘权').one()
    sample = session.query(VoiceprintSample).filter(VoiceprintSample.person_id == person.id).one()
    assert sample.source_type == 'auto_candidate'
    assert sample.review_status == 'candidate'
    assert sample.source_job_id == job.id
    assert sample.source_speaker_key == 'speaker_0'
    assert sample.source_start_ms == 1000
    assert sample.source_end_ms == 5200
    assert sample.embedding_status == 'ready'
    assert sample.usage_types == ['voiceprint']


def test_auto_learning_assigns_job_project_id(monkeypatch, tmp_path) -> None:
    from app.services.voiceprint_auto_learning import learn_voiceprint_candidates_for_job

    session = _build_session()
    project = Project(name='自动学习项目', name_key='自动学习项目', legacy_program_key='中国好团队')
    session.add(project)
    session.commit()
    job = _create_completed_job(session, tmp_path / 'project-source.wav')
    job.project_id = project.id
    session.commit()
    _patch_audio_and_embedding(monkeypatch, tmp_path)

    result = learn_voiceprint_candidates_for_job(session, job)

    assert result.created_count == 1
    person = session.query(Person).filter(Person.name == '刘权').one()
    assert person.project_id == project.id


def test_auto_learning_does_not_rebind_global_same_name(monkeypatch, tmp_path) -> None:
    from app.services.voiceprint_auto_learning import learn_voiceprint_candidates_for_job

    session = _build_session()
    project = Project(name='隔离学习项目', name_key='隔离学习项目', legacy_program_key='中国好团队')
    global_person = Person(name='刘权')
    session.add_all([project, global_person])
    session.commit()
    job = _create_completed_job(session, tmp_path / 'isolated-source.wav')
    job.project_id = project.id
    session.commit()
    _patch_audio_and_embedding(monkeypatch, tmp_path)

    result = learn_voiceprint_candidates_for_job(session, job)

    assert result.created_count == 1
    session.refresh(global_person)
    assert global_person.project_id is None
    project_people = session.query(Person).filter(Person.project_id == project.id, Person.name == '刘权').all()
    assert len(project_people) == 1


def test_learn_voiceprint_candidates_skips_default_speaker_name(monkeypatch, tmp_path) -> None:
    from app.services.voiceprint_auto_learning import learn_voiceprint_candidates_for_job

    session = _build_session()
    job = _create_completed_job(session, tmp_path / 'source.wav')
    job.speaker_mappings = {'speaker_0': 'speaker_0'}
    session.commit()
    _patch_audio_and_embedding(monkeypatch, tmp_path)

    result = learn_voiceprint_candidates_for_job(session, job)

    assert result.created_count == 0
    assert result.skipped_count == 1
    assert session.query(VoiceprintSample).count() == 0


def test_learn_voiceprint_candidates_skips_filler_only_segment(monkeypatch, tmp_path) -> None:
    from app.services.voiceprint_auto_learning import learn_voiceprint_candidates_for_job

    session = _build_session()
    job = _create_completed_job(session, tmp_path / 'source.wav', text='嗯')
    _patch_audio_and_embedding(monkeypatch, tmp_path)

    result = learn_voiceprint_candidates_for_job(session, job)

    assert result.created_count == 0
    assert result.skipped_count == 1
    assert session.query(VoiceprintSample).count() == 0


def test_learn_voiceprint_candidates_respects_candidate_limit(monkeypatch, tmp_path) -> None:
    from app.services.voiceprint_auto_learning import MAX_CANDIDATES_PER_PERSON, learn_voiceprint_candidates_for_job

    session = _build_session()
    person = Person(name='刘权', program_name='中国好团队', program_key='中国好团队')
    session.add(person)
    session.commit()
    for index in range(MAX_CANDIDATES_PER_PERSON):
        session.add(
            VoiceprintSample(
                person_id=person.id,
                original_filename=f'candidate-{index}.wav',
                audio_local_path=f'/tmp/candidate-{index}.wav',
                duration_ms=3000,
                sample_rate_hz=16000,
                channel_count=1,
                usage_types=['voiceprint'],
                embedding_status='ready',
                source_type='auto_candidate',
                review_status='candidate',
            )
        )
    session.commit()
    job = _create_completed_job(session, tmp_path / 'source.wav')
    _patch_audio_and_embedding(monkeypatch, tmp_path)

    result = learn_voiceprint_candidates_for_job(session, job)

    assert result.created_count == 1
    assert result.pruned_count == 1
    assert session.query(VoiceprintSample).filter(VoiceprintSample.person_id == person.id).count() == MAX_CANDIDATES_PER_PERSON


def test_auto_learning_passively_confirms_consistent_samples_from_distinct_jobs(monkeypatch, tmp_path) -> None:
    from app.services.voiceprint_auto_learning import learn_voiceprint_candidates_for_job

    session = _build_session()
    first_job = _create_completed_job(session, tmp_path / 'first.wav')
    _patch_audio_and_embedding(monkeypatch, tmp_path, embedding=[1.0, 0.0])
    first_result = learn_voiceprint_candidates_for_job(session, first_job)

    second_job = _create_completed_job(session, tmp_path / 'second.wav')
    _patch_audio_and_embedding(monkeypatch, tmp_path, embedding=[0.9, 0.1])
    second_result = learn_voiceprint_candidates_for_job(session, second_job)

    person = session.query(Person).filter(Person.name == '刘权').one()
    samples = session.query(VoiceprintSample).filter(VoiceprintSample.person_id == person.id).all()
    assert first_result.created_count == 1
    assert second_result.created_count == 1
    assert second_result.auto_confirmed_count == 2
    assert len(samples) == 2
    assert {sample.review_status for sample in samples} == {'confirmed'}
    assert {sample.source_type for sample in samples} == {'auto_confirmed'}
    assert all(float(sample.confidence_score or 0) >= 0.99 for sample in samples)


def test_auto_learning_does_not_confirm_repeated_sample_from_same_job(monkeypatch, tmp_path) -> None:
    from app.services.voiceprint_auto_learning import learn_voiceprint_candidates_for_job

    session = _build_session()
    job = _create_completed_job(session, tmp_path / 'source.wav')
    _patch_audio_and_embedding(monkeypatch, tmp_path, embedding=[1.0, 0.0])

    first_result = learn_voiceprint_candidates_for_job(session, job)
    second_result = learn_voiceprint_candidates_for_job(session, job)

    sample = session.query(VoiceprintSample).one()
    assert first_result.created_count == 1
    assert second_result.created_count == 0
    assert second_result.duplicate_count == 1
    assert sample.review_status == 'candidate'


def test_learn_voiceprint_candidates_skips_duplicate_embedding(monkeypatch, tmp_path) -> None:
    from app.services.voiceprint_auto_learning import learn_voiceprint_candidates_for_job

    session = _build_session()
    person = Person(name='刘权', program_name='中国好团队', program_key='中国好团队')
    session.add(person)
    session.commit()
    session.add(
        VoiceprintSample(
            person_id=person.id,
            original_filename='existing.wav',
            audio_local_path='/tmp/existing.wav',
            duration_ms=3000,
            sample_rate_hz=16000,
            channel_count=1,
            usage_types=['voiceprint'],
            embedding=[1.0, 0.0],
            embedding_status='ready',
            source_type='auto_candidate',
            review_status='candidate',
        )
    )
    session.commit()
    job = _create_completed_job(session, tmp_path / 'source.wav')
    _patch_audio_and_embedding(monkeypatch, tmp_path, embedding=[1.0, 0.001])

    result = learn_voiceprint_candidates_for_job(session, job)

    assert result.created_count == 0
    assert result.duplicate_count == 1
    assert session.query(VoiceprintSample).filter(VoiceprintSample.person_id == person.id).count() == 1
