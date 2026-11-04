from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.job import Job  # noqa: F401
from app.models.person import Person
from app.models.project import Project
from app.models.voice_clone_profile import VoiceCloneProfile  # noqa: F401
from app.models.voice_clone_sample import VoiceCloneSample  # noqa: F401
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord  # noqa: F401
from app.models.voiceprint_match import VoiceprintMatch  # noqa: F401
from app.models.voiceprint_sample import VoiceprintSample
from app.services.voiceprint_maintenance import reconcile_voiceprint_library
from app.services.voiceprint_policy import MAX_VOICEPRINT_SAMPLES_PER_PERSON


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _candidate(person: Person, index: int, embedding: list[float]) -> VoiceprintSample:
    return VoiceprintSample(
        person_id=person.id,
        original_filename=f'{person.id}-{index}.wav',
        audio_local_path=f'/tmp/{person.id}-{index}.wav',
        duration_ms=5000,
        embedding=embedding,
        embedding_status='ready',
        source_type='auto_candidate',
        review_status='candidate',
        source_job_id=f'job-{person.id}-{index}',
    )


def test_reconcile_confirms_repeated_evidence_and_prunes_to_five() -> None:
    session = _build_session()
    project = Project(name='历史项目', name_key='历史项目')
    person = Person(name='主持人', project_id=project.id)
    session.add_all([project, person])
    session.commit()
    session.add_all([
        _candidate(person, index, [1.0, index * 0.001])
        for index in range(MAX_VOICEPRINT_SAMPLES_PER_PERSON + 1)
    ])
    session.commit()

    report = reconcile_voiceprint_library(session)

    samples = session.query(VoiceprintSample).filter(VoiceprintSample.person_id == person.id).all()
    assert report.auto_confirmed_count == MAX_VOICEPRINT_SAMPLES_PER_PERSON + 1
    assert report.pruned_sample_count == 1
    assert len(samples) == MAX_VOICEPRINT_SAMPLES_PER_PERSON
    assert {sample.review_status for sample in samples} == {'confirmed'}


def test_reconcile_promotes_consistent_confirmed_people_across_projects() -> None:
    session = _build_session()
    project_a = Project(name='节目 A', name_key='节目 a')
    project_b = Project(name='节目 B', name_key='节目 b')
    session.add_all([project_a, project_b])
    session.commit()
    person_a = Person(name='公共主持人', project_id=project_a.id)
    person_b = Person(name='公共主持人', project_id=project_b.id)
    session.add_all([person_a, person_b])
    session.commit()
    session.add_all([
        _candidate(person_a, 1, [1.0, 0.0]),
        _candidate(person_a, 2, [0.99, 0.01]),
        _candidate(person_b, 1, [0.98, 0.02]),
        _candidate(person_b, 2, [0.97, 0.03]),
    ])
    session.commit()

    report = reconcile_voiceprint_library(session)

    people = session.query(Person).all()
    assert report.auto_confirmed_count == 4
    assert report.global_promoted_count == 1
    assert report.merged_person_count == 2
    assert len(people) == 1
    assert people[0].project_id is None
    assert session.query(VoiceprintSample).filter(VoiceprintSample.person_id == people[0].id).count() == 4
