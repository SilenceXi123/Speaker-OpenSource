from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.job import Job
from app.models.person import Person
from app.models.project import Project
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_sample import VoiceCloneSample  # noqa: F401
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
from app.models.voiceprint_match import VoiceprintMatch
from app.models.voiceprint_sample import VoiceprintSample
from app.services.voiceprint_global import promote_consistent_person_to_global


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _add_confirmed_sample(session, person: Person, embedding: list[float], suffix: str) -> VoiceprintSample:
    sample = VoiceprintSample(
        person_id=person.id,
        original_filename=f'{suffix}.wav',
        audio_local_path=f'/tmp/{suffix}.wav',
        duration_ms=5000,
        sample_rate_hz=16000,
        channel_count=1,
        embedding=embedding,
        embedding_status='ready',
        source_type='auto_confirmed',
        review_status='confirmed',
        source_job_id=f'job-{suffix}',
    )
    session.add(sample)
    session.commit()
    return sample


def test_consistent_same_name_across_projects_is_merged_into_global_person() -> None:
    session = _build_session()
    project_a = Project(name='节目 A', name_key='节目 a')
    project_b = Project(name='节目 B', name_key='节目 b')
    session.add_all([project_a, project_b])
    session.commit()
    person_a = Person(name='撒贝宁', project_id=project_a.id, program_name=project_a.name, program_key=project_a.name_key)
    person_b = Person(name='撒贝宁', project_id=project_b.id, program_name=project_b.name, program_key=project_b.name_key)
    session.add_all([person_a, person_b])
    session.commit()
    sample_a = _add_confirmed_sample(session, person_a, [1.0, 0.0], 'a')
    _add_confirmed_sample(session, person_b, [0.99, 0.01], 'b')
    job = Job(source_url='https://example.com/a.wav', project_id=project_a.id, status='completed')
    profile = VoiceCloneProfile(
        project_id=project_a.id,
        person_id=person_a.id,
        source_sample_id=sample_a.id,
        provider='cosyvoice',
        provider_model_family='cosyvoice',
        target_model='cosyvoice-v3.5-plus',
        voice_name='撒贝宁测试音色',
        status='ready',
    )
    session.add_all([job, profile])
    session.commit()
    match = VoiceprintMatch(
        job_id=job.id,
        speaker_key='speaker_0',
        person_id=person_a.id,
        score=0.95,
        status='auto_matched',
        top_candidates=[{'person_id': person_a.id, 'person_name': person_a.name, 'score': 0.95}],
    )
    record = VoiceCloneSynthesisRecord(
        project_id=project_b.id,
        voice_clone_profile_id=profile.id,
        person_id=person_b.id,
        target_model='cosyvoice-v3.5-plus',
        input_text='测试',
        status='ready',
    )
    session.add_all([match, record])
    session.commit()

    result = promote_consistent_person_to_global(session, person_a.id)

    assert result.promoted is True
    assert set(result.merged_person_ids) == {person_a.id, person_b.id}
    global_person = session.get(Person, result.global_person_id)
    assert global_person is not None
    assert global_person.project_id is None
    assert global_person.program_name is None
    assert global_person.program_key is None
    assert session.get(Person, person_a.id) is None
    assert session.get(Person, person_b.id) is None
    assert {sample.person_id for sample in session.query(VoiceprintSample).all()} == {global_person.id}
    assert session.get(VoiceprintMatch, match.id).person_id == global_person.id
    assert session.get(VoiceprintMatch, match.id).top_candidates[0]['person_id'] == global_person.id
    assert session.get(VoiceCloneProfile, profile.id).person_id == global_person.id
    assert session.get(VoiceCloneSynthesisRecord, record.id).person_id == global_person.id


def test_same_name_with_inconsistent_voice_is_not_promoted() -> None:
    session = _build_session()
    project_a = Project(name='节目 A', name_key='节目 a')
    project_b = Project(name='节目 B', name_key='节目 b')
    session.add_all([project_a, project_b])
    session.commit()
    person_a = Person(name='主持人', project_id=project_a.id)
    person_b = Person(name='主持人', project_id=project_b.id)
    session.add_all([person_a, person_b])
    session.commit()
    _add_confirmed_sample(session, person_a, [1.0, 0.0], 'a')
    _add_confirmed_sample(session, person_b, [0.0, 1.0], 'b')

    result = promote_consistent_person_to_global(session, person_a.id)

    assert result.promoted is False
    assert '两个独立项目' in str(result.reason)
    assert session.query(Person).count() == 2
