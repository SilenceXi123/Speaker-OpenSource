from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.v1 import admin as admin_api
from app.core.config import settings
from app.db.session import Base
from app.models.voice_clone_identity import VoiceCloneIdentity
from app.models.voice_clone_identity_review import VoiceCloneIdentityReview
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_sample import VoiceCloneSample
from app.services.admin_auth import build_admin_session_token
from app.services import voice_retention
from main import app


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(
        f'sqlite:///{tmp_path / "identity-reviews.db"}',
        connect_args={'check_same_thread': False},
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def admin_client(monkeypatch, session_factory) -> TestClient:
    monkeypatch.setattr(settings, 'admin_username', 'admin')
    monkeypatch.setattr(settings, 'admin_password_hash', 'configured-password-hash')
    monkeypatch.setattr(settings, 'admin_session_secret', 'session-secret')
    monkeypatch.setattr(settings, 'admin_session_cookie_name', 'speaker_admin_session')
    monkeypatch.setattr(settings, 'admin_session_max_age_seconds', 3600)
    monkeypatch.setattr(admin_api, 'SessionLocal', session_factory)
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set(settings.admin_session_cookie_name, build_admin_session_token('admin'))
    return client


@pytest.fixture
def pending_review(session_factory, monkeypatch) -> str:
    monkeypatch.setattr(voice_retention, 'utc_now', lambda: datetime(2026, 8, 31, 10, 0, 0))
    with session_factory() as session:
        candidate = VoiceCloneIdentity(
            id='candidate-id',
            display_name='主持人甲',
            aliases=['主持人甲', '新上传名'],
            reference_embedding=[1.0, 0.0],
            embedding_model_id='wespeaker-v1',
            created_at=datetime(2026, 8, 31, 8, 0, 0),
        )
        existing_sample = VoiceCloneSample(
            id='candidate-sample-id',
            display_name='主持人甲',
            name_key='主持人甲',
            original_filename='candidate.wav',
            audio_local_path='/tmp/candidate.wav',
            identity_id=candidate.id,
            embedding=[1.0, 0.0],
            embedding_status='ready',
            embedding_model_id='wespeaker-v1',
            created_at=datetime(2026, 8, 31, 8, 0, 0),
        )
        reviewed_sample = VoiceCloneSample(
            id='reviewed-sample-id',
            display_name='新上传名',
            name_key='新上传名',
            original_filename='new-upload.wav',
            audio_local_path='/tmp/new-upload.wav',
            identity_id=candidate.id,
            embedding=[0.0, 1.0],
            embedding_status='ready',
            embedding_provider_name='wespeaker',
            embedding_model_id='wespeaker-v1',
            created_at=datetime(2026, 8, 31, 9, 0, 0),
        )
        review = VoiceCloneIdentityReview(
            id='review-id',
            candidate_identity_id=candidate.id,
            sample_id=reviewed_sample.id,
            submitted_name='新上传名',
            match_score=0.9123,
            created_at=datetime(2026, 8, 31, 9, 0, 1),
        )
        session.add_all([
            candidate,
            existing_sample,
            reviewed_sample,
            review,
            VoiceCloneProfile(
                id='reviewed-profile-id',
                source_clone_sample_id=reviewed_sample.id,
                target_model='cosyvoice-v3.5-plus',
                voice_name='新上传名',
                identity_id=candidate.id,
            ),
            VoiceCloneProfile(
                id='candidate-profile-id',
                source_clone_sample_id=existing_sample.id,
                target_model='cosyvoice-v3.5-plus',
                voice_name='主持人甲',
                identity_id=candidate.id,
            ),
        ])
        session.commit()
    return 'review-id'


def test_identity_reviews_require_admin_session(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'admin_username', 'admin')
    monkeypatch.setattr(settings, 'admin_password_hash', 'configured-password-hash')
    monkeypatch.setattr(settings, 'admin_session_secret', 'session-secret')
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get('/api/v1/admin/voice-clone-identities/reviews').status_code == 401
    assert client.post(
        '/api/v1/admin/voice-clone-identities/reviews/review-id/resolve',
        json={'action': 'confirm'},
    ).status_code == 401


def test_identity_review_list_rejects_cross_origin_admin_request(
    admin_client,
    pending_review,
) -> None:
    response = admin_client.get(
        '/api/v1/admin/voice-clone-identities/reviews',
        headers={'Origin': 'http://malicious.test'},
    )

    assert response.status_code == 403


def test_identity_review_resolution_rejects_cross_origin_admin_request(
    admin_client,
    session_factory,
    pending_review,
) -> None:
    response = admin_client.post(
        f'/api/v1/admin/voice-clone-identities/reviews/{pending_review}/resolve',
        headers={'Origin': 'http://malicious.test'},
        json={'action': 'release', 'reason': '恶意跨源请求'},
    )

    assert response.status_code == 403
    with session_factory() as session:
        assert session.get(VoiceCloneIdentityReview, pending_review).status == 'pending'


@pytest.mark.parametrize(('method', 'path'), [
    ('GET', '/api/v1/admin/voice-clone-identities/reviews'),
    ('POST', '/api/v1/admin/voice-clone-identities/reviews/review-id/resolve'),
])
def test_identity_review_routes_reject_empty_origin_header(
    admin_client,
    pending_review,
    method,
    path,
) -> None:
    response = admin_client.request(
        method,
        path,
        headers={'Origin': ''},
        json={'action': 'confirm'} if method == 'POST' else None,
    )

    assert response.status_code == 403


def test_identity_review_list_accepts_same_origin_admin_request(
    admin_client,
    pending_review,
) -> None:
    response = admin_client.get(
        '/api/v1/admin/voice-clone-identities/reviews',
        headers={'Origin': 'http://testserver'},
    )

    assert response.status_code == 200


def test_admin_page_has_identity_review_card_and_one_shared_audio_player() -> None:
    response = TestClient(app, raise_server_exceptions=False).get('/admin')

    assert response.status_code == 200
    assert 'id="identity-review-list"' in response.text
    assert 'id="identity-review-player"' in response.text
    assert response.text.count('<audio') == 1


def test_admin_lists_pending_reviews_with_both_audio_entries(
    admin_client,
    session_factory,
    pending_review,
) -> None:
    with session_factory() as session:
        session.add(VoiceCloneIdentityReview(
            id='resolved-review-id',
            candidate_identity_id='candidate-id',
            sample_id='reviewed-sample-id',
            submitted_name='旧审核',
            match_score=0.99,
            status='confirmed',
        ))
        session.commit()

    response = admin_client.get('/api/v1/admin/voice-clone-identities/reviews')

    assert response.status_code == 200
    assert response.json() == [{
        'id': pending_review,
        'sample_id': 'reviewed-sample-id',
        'submitted_name': '新上传名',
        'original_filename': 'new-upload.wav',
        'candidate_identity_id': 'candidate-id',
        'candidate_display_name': '主持人甲',
        'candidate_aliases': ['主持人甲', '新上传名'],
        'match_score': 0.9123,
        'embedding_model_id': 'wespeaker-v1',
        'created_at': '2026-08-31T09:00:01Z',
        'expires_at': '2026-09-03T08:00:00Z',
        'sample_audio_url': '/api/v1/voice-cloning/samples/reviewed-sample-id/audio',
        'candidate_sample_audio_url': '/api/v1/voice-cloning/samples/candidate-sample-id/audio',
    }]


def test_admin_can_confirm_match_without_rebinding(
    admin_client,
    session_factory,
    pending_review,
) -> None:
    response = admin_client.post(
        f'/api/v1/admin/voice-clone-identities/reviews/{pending_review}/resolve',
        json={'action': 'confirm', 'reason': '两段试听确认是同一人'},
    )

    assert response.status_code == 200
    assert response.json()['status'] == 'confirmed'
    assert response.json()['resolved_by'] == 'admin'
    assert response.json()['resolution_reason'] == '两段试听确认是同一人'
    assert response.json()['resolved_at']
    with session_factory() as session:
        assert session.get(VoiceCloneSample, 'reviewed-sample-id').identity_id == 'candidate-id'
        assert session.get(VoiceCloneProfile, 'reviewed-profile-id').identity_id == 'candidate-id'
        assert session.scalar(select(func.count()).select_from(VoiceCloneIdentity)) == 1


def test_release_requires_non_empty_reason(
    admin_client,
    session_factory,
    pending_review,
) -> None:
    response = admin_client.post(
        f'/api/v1/admin/voice-clone-identities/reviews/{pending_review}/resolve',
        json={'action': 'release', 'reason': '   '},
    )

    assert response.status_code == 400
    with session_factory() as session:
        assert session.get(VoiceCloneIdentityReview, pending_review).status == 'pending'
        assert session.scalar(select(func.count()).select_from(VoiceCloneIdentity)) == 1


def test_admin_can_release_false_match_and_repeat_is_idempotent(
    admin_client,
    session_factory,
    pending_review,
) -> None:
    payload = {'action': 'release', 'reason': '两段试听确认不是同一人'}

    first = admin_client.post(
        f'/api/v1/admin/voice-clone-identities/reviews/{pending_review}/resolve',
        json=payload,
    )
    second = admin_client.post(
        f'/api/v1/admin/voice-clone-identities/reviews/{pending_review}/resolve',
        json=payload,
    )

    assert first.status_code == 200
    assert first.json()['status'] == 'released'
    assert first.json()['resolved_by'] == 'admin'
    assert first.json()['resolution_reason'] == payload['reason']
    assert second.status_code == 200
    with session_factory() as session:
        review = session.get(VoiceCloneIdentityReview, pending_review)
        reviewed_sample = session.get(VoiceCloneSample, 'reviewed-sample-id')
        released_identity = session.get(VoiceCloneIdentity, reviewed_sample.identity_id)
        assert review.status == 'released'
        assert reviewed_sample.identity_id != 'candidate-id'
        assert released_identity.display_name == '新上传名'
        assert released_identity.aliases == ['新上传名']
        assert released_identity.reference_embedding == [0.0, 1.0]
        assert released_identity.embedding_model_id == 'wespeaker-v1'
        assert session.get(VoiceCloneProfile, 'reviewed-profile-id').identity_id == released_identity.id
        assert session.get(VoiceCloneSample, 'candidate-sample-id').identity_id == 'candidate-id'
        assert session.get(VoiceCloneProfile, 'candidate-profile-id').identity_id == 'candidate-id'
        assert session.get(VoiceCloneIdentity, 'candidate-id').aliases == ['主持人甲']
        assert session.scalar(select(func.count()).select_from(VoiceCloneIdentity)) == 2


def test_release_keeps_alias_used_by_another_candidate_sample(
    admin_client,
    session_factory,
    pending_review,
) -> None:
    with session_factory() as session:
        session.add(VoiceCloneSample(
            id='same-alias-sample-id',
            display_name='新上传名',
            name_key='新上传名',
            original_filename='same-alias.wav',
            audio_local_path='/tmp/same-alias.wav',
            identity_id='candidate-id',
        ))
        session.commit()

    response = admin_client.post(
        f'/api/v1/admin/voice-clone-identities/reviews/{pending_review}/resolve',
        json={'action': 'release', 'reason': '不是同一人'},
    )

    assert response.status_code == 200
    with session_factory() as session:
        assert session.get(VoiceCloneIdentity, 'candidate-id').aliases == ['主持人甲', '新上传名']


def test_release_failure_rolls_back_all_changes(
    monkeypatch,
    session_factory,
    pending_review,
) -> None:
    from app.services.voice_clone_identity_review import resolve_identity_review

    session = session_factory()
    monkeypatch.setattr(session, 'commit', lambda: (_ for _ in ()).throw(RuntimeError('commit failed')))

    with pytest.raises(RuntimeError, match='commit failed'):
        resolve_identity_review(
            session,
            pending_review,
            'release',
            'admin',
            '不是同一人',
        )
    session.close()

    with session_factory() as check_session:
        assert check_session.get(VoiceCloneIdentityReview, pending_review).status == 'pending'
        assert check_session.get(VoiceCloneSample, 'reviewed-sample-id').identity_id == 'candidate-id'
        assert check_session.get(VoiceCloneProfile, 'reviewed-profile-id').identity_id == 'candidate-id'
        assert check_session.get(VoiceCloneIdentity, 'candidate-id').aliases == ['主持人甲', '新上传名']
        assert check_session.scalar(select(func.count()).select_from(VoiceCloneIdentity)) == 1


def test_resolution_rejects_unknown_action(admin_client, pending_review) -> None:
    response = admin_client.post(
        f'/api/v1/admin/voice-clone-identities/reviews/{pending_review}/resolve',
        json={'action': 'merge-later', 'reason': 'unsupported'},
    )

    assert response.status_code == 400


def test_identity_review_import_without_python311_datetime_utc(monkeypatch):
    import datetime
    import importlib.util
    from app.services import voice_clone_identity_review

    monkeypatch.delattr(datetime, 'UTC', raising=False)
    spec = importlib.util.spec_from_file_location(
        'voice_clone_identity_review_python310', voice_clone_identity_review.__file__,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.list_pending_identity_reviews)
