from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.job import Job  # noqa: F401
from app.models.person import Person
from app.models.project import Project  # noqa: F401
from app.models.voice_clone_profile import VoiceCloneProfile  # noqa: F401
from app.models.voice_clone_sample import VoiceCloneSample  # noqa: F401
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord  # noqa: F401
from app.models.voiceprint_match import VoiceprintMatch  # noqa: F401
from app.models.voiceprint_sample import VoiceprintSample
from app.services.voiceprint_policy import (
    MAX_VOICEPRINT_SAMPLES_PER_PERSON,
    enforce_voiceprint_sample_limit,
)


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_retention_keeps_confirmed_manual_sample_and_caps_total_at_five() -> None:
    session = _build_session()
    person = Person(name='跨节目主持人')
    session.add(person)
    session.commit()
    manual = VoiceprintSample(
        person_id=person.id,
        original_filename='manual.wav',
        audio_local_path='/tmp/manual.wav',
        duration_ms=5000,
        embedding=[1.0, 0.0],
        embedding_status='ready',
        source_type='manual_upload',
        review_status='confirmed',
        created_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(manual)
    for index in range(MAX_VOICEPRINT_SAMPLES_PER_PERSON):
        session.add(
            VoiceprintSample(
                person_id=person.id,
                original_filename=f'candidate-{index}.wav',
                audio_local_path=f'/tmp/candidate-{index}.wav',
                duration_ms=5000,
                embedding=[0.0, 1.0],
                embedding_status='ready',
                source_type='auto_candidate',
                review_status='candidate',
                created_at=datetime.utcnow() + timedelta(seconds=index),
            )
        )
    session.commit()

    result = enforce_voiceprint_sample_limit(session, person.id)
    session.commit()

    retained = session.query(VoiceprintSample).filter(VoiceprintSample.person_id == person.id).all()
    assert result.removed_count == 1
    assert len(retained) == MAX_VOICEPRINT_SAMPLES_PER_PERSON
    assert manual.id in {sample.id for sample in retained}
    assert manual.id not in result.removed_sample_ids
