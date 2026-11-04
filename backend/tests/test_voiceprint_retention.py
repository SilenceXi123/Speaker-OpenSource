from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.session import Base
from app.models.job import Job
from app.models.person import Person
from app.models.voiceprint_match import VoiceprintMatch
from app.models.voiceprint_sample import VoiceprintSample
from app.services import voice_retention
from app.services.voiceprints import (
    build_sample_response, confirm_voiceprint_candidate, delete_person,
    get_voiceprint_sample, list_person_samples,
)
from app.services.voiceprint_matching import _list_reference_people, build_voiceprint_match_lookup
from app.services.voiceprint_auto_learning import _confirmed_sample_count_for_person, _find_duplicate_sample
from app.services.voiceprint_global import _confirmed_samples
from app.services.voiceprint_policy import auto_confirm_consistent_samples, enforce_voiceprint_sample_limit


def test_voiceprint_expires_at_exactly_72_hours_before_worker_runs(monkeypatch):
    now = datetime(2026, 9, 11, 12)
    monkeypatch.setattr(voice_retention, 'utc_now', lambda: now)
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        person = Person(name='测试人物')
        job = Job(source_url='https://example.com/source.wav', status='completed')
        session.add_all([person, job])
        session.flush()
        old = VoiceprintSample(person_id=person.id, original_filename='old.wav', audio_local_path='/tmp/old.wav',
            created_at=now - timedelta(hours=72), embedding=[1.0, 0.0], embedding_status='ready', review_status='confirmed')
        fresh = VoiceprintSample(person_id=person.id, original_filename='fresh.wav', audio_local_path='/tmp/fresh.wav',
            created_at=now - timedelta(hours=72) + timedelta(seconds=1), embedding=[0.0, 1.0], embedding_status='ready', review_status='confirmed')
        session.add_all([old, fresh, VoiceprintMatch(job_id=job.id, speaker_key='speaker_old', created_at=old.created_at)])
        session.commit()

        assert list_person_samples(session, person.id) == [fresh]
        assert get_voiceprint_sample(session, old.id) is None
        assert get_voiceprint_sample(session, fresh.id) is fresh
        assert build_sample_response(fresh).expires_at == now + timedelta(seconds=1)
        with pytest.raises(LookupError):
            confirm_voiceprint_candidate(session, old)
        assert [item['embedding'] for item in _list_reference_people(session, job)] == [[0.0, 1.0]]
        assert build_voiceprint_match_lookup(session, job.id) == {}


def test_expired_vectors_do_not_block_or_confirm_new_samples(monkeypatch):
    now = datetime(2026, 9, 11, 12)
    monkeypatch.setattr(voice_retention, 'utc_now', lambda: now)
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        person = Person(name='测试人物')
        session.add(person)
        session.flush()
        old = VoiceprintSample(person_id=person.id, original_filename='old.wav', audio_local_path='/tmp/old.wav',
            created_at=now - timedelta(hours=72), embedding=[1.0, 0.0], embedding_status='ready', review_status='confirmed')
        fresh = VoiceprintSample(person_id=person.id, original_filename='new.wav', audio_local_path='/tmp/new.wav',
            created_at=now, embedding=[1.0, 0.0], embedding_status='ready', review_status='candidate', source_type='auto_candidate')
        session.add(old)
        session.commit()
        assert _confirmed_sample_count_for_person(session, person.id) == 0
        assert _find_duplicate_sample(session, person.id, [1.0, 0.0]) is None
        assert _confirmed_samples(session, person.id) == []
        session.add(fresh)
        session.flush()
        assert auto_confirm_consistent_samples(session, fresh) == 0
        assert enforce_voiceprint_sample_limit(session, person.id, maximum=1).removed_count == 0
        assert fresh.review_status == 'candidate'


def test_expired_samples_are_still_removed_when_person_is_deleted(monkeypatch, tmp_path):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        person = Person(name='删除人物')
        session.add(person)
        session.flush()
        audio = tmp_path / 'expired.wav'
        audio.write_bytes(b'test')
        sample = VoiceprintSample(person_id=person.id, original_filename=audio.name, audio_local_path=str(audio),
            created_at=datetime(2020, 1, 1))
        session.add(sample)
        session.commit()
        sample_id = sample.id
        delete_person(session, person)
        assert session.get(VoiceprintSample, sample_id) is None
        assert not audio.exists()
