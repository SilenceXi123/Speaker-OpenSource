from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.job import Job  # noqa: F401
from app.models.person import Person
from app.models.project import Project  # noqa: F401
from app.schemas.upload import UploadResponse
from app.schemas.voiceprint import VoiceprintSampleResponse
from main import app


@pytest.fixture()
def project_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = create_engine(
        'sqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    monkeypatch.setattr('app.api.v2.projects.SessionLocal', session_factory)
    monkeypatch.setattr('app.api.v1.transcriptions.SessionLocal', session_factory)
    monkeypatch.setattr('app.api.v1.voiceprints.SessionLocal', session_factory)
    monkeypatch.setattr('app.api.v2.voiceprints.SessionLocal', session_factory)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(project_session: Session) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_v2_project_lifecycle(client: TestClient) -> None:
    created_response = client.post(
        '/api/v2/projects',
        json={
            'name': ' 中国好团队 ',
            'owner_name': '制作一组',
            'default_language': 'zh',
            'default_frame_rate': '25',
        },
    )

    assert created_response.status_code == 201
    created = created_response.json()
    assert created['name'] == '中国好团队'
    assert created['status'] == 'active'
    assert created['id']
    assert '中国好团队' in {
        item['program_name'] for item in client.get('/api/v1/voiceprints/programs').json()
    }

    detail_response = client.get(f"/api/v2/projects/{created['id']}")
    assert detail_response.status_code == 200

    renamed_response = client.patch(
        f"/api/v2/projects/{created['id']}",
        json={'name': '中国好团队第二季'},
    )
    assert renamed_response.status_code == 400
    assert '暂不支持项目改名' in renamed_response.json()['detail']

    updated_response = client.patch(
        f"/api/v2/projects/{created['id']}",
        json={'owner_name': '制作二组'},
    )
    assert updated_response.status_code == 200
    assert updated_response.json()['id'] == created['id']
    assert updated_response.json()['owner_name'] == '制作二组'

    archived_response = client.post(f"/api/v2/projects/{created['id']}/archive")
    assert archived_response.status_code == 200
    assert archived_response.json()['status'] == 'archived'
    assert client.get('/api/v2/projects').json() == []
    assert '中国好团队' not in {
        item['program_name'] for item in client.get('/api/v1/voiceprints/programs').json()
    }
    assert len(client.get('/api/v2/projects', params={'include_archived': True}).json()) == 1

    restored_response = client.post(f"/api/v2/projects/{created['id']}/restore")
    assert restored_response.status_code == 200
    assert restored_response.json()['status'] == 'active'
    assert '中国好团队' in {
        item['program_name'] for item in client.get('/api/v1/voiceprints/programs').json()
    }


def test_v2_project_duplicate_returns_conflict(client: TestClient) -> None:
    first = client.post('/api/v2/projects', json={'name': '重复项目'})
    duplicate = client.post('/api/v2/projects', json={'name': '  重复项目  '})

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert '同名项目' in duplicate.json()['detail']


def test_v2_voiceprint_people_are_scoped_by_url_project(
    client: TestClient,
    project_session: Session,
) -> None:
    project_a = client.post('/api/v2/projects', json={'name': '声纹项目 A'}).json()
    project_b = client.post('/api/v2/projects', json={'name': '声纹项目 B'}).json()

    created_a = client.post(
        f"/api/v2/projects/{project_a['id']}/voiceprints/people",
        json={
            'name': '同名主持人',
            'project_id': project_b['id'],
            'program_name': '错误客户端项目',
        },
    )
    created_b = client.post(
        f"/api/v2/projects/{project_b['id']}/voiceprints/people",
        json={'name': '同名主持人'},
    )

    assert created_a.status_code == 201
    assert created_b.status_code == 201
    assert created_a.json()['project_id'] == project_a['id']
    assert created_a.json()['program_name'] == '声纹项目 A'
    assert created_b.json()['project_id'] == project_b['id']
    global_person = Person(name='跨节目主持人')
    project_session.add(global_person)
    project_session.commit()
    assert set(item['id'] for item in client.get(
        f"/api/v2/projects/{project_a['id']}/voiceprints/people"
    ).json()) == {created_a.json()['id'], global_person.id}
    assert set(item['id'] for item in client.get(
        f"/api/v2/projects/{project_b['id']}/voiceprints/people"
    ).json()) == {created_b.json()['id'], global_person.id}
    assert project_session.query(Person).count() == 3


def test_v1_voiceprint_person_dual_writes_formal_project(client: TestClient) -> None:
    response = client.post(
        '/api/v1/voiceprints/people',
        json={'name': '旧客户端人物', 'program_name': '旧客户端声纹项目'},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload['project_id']
    project = client.get(f"/api/v2/projects/{payload['project_id']}")
    assert project.status_code == 200
    assert project.json()['name'] == '旧客户端声纹项目'


def test_v2_voiceprint_person_rejects_archived_project(client: TestClient) -> None:
    project = client.post('/api/v2/projects', json={'name': '已归档声纹项目'}).json()
    client.post(f"/api/v2/projects/{project['id']}/archive")

    response = client.post(
        f"/api/v2/projects/{project['id']}/voiceprints/people",
        json={'name': '不应创建'},
    )

    assert response.status_code == 409
    assert '已归档' in response.json()['detail']


def test_v2_voiceprint_sample_rejects_cross_project_person_before_file_processing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_a = client.post('/api/v2/projects', json={'name': '样本项目 A'}).json()
    project_b = client.post('/api/v2/projects', json={'name': '样本项目 B'}).json()
    person_b = client.post(
        f"/api/v2/projects/{project_b['id']}/voiceprints/people",
        json={'name': '项目 B 人物'},
    ).json()
    save_calls: list[str] = []

    async def fake_save(*args, **kwargs):
        save_calls.append('save')
        raise AssertionError('cross-project request must fail before saving the file')

    monkeypatch.setattr('app.api.v2.voiceprints.save_upload_file_locally', fake_save)

    response = client.post(
        f"/api/v2/projects/{project_a['id']}/voiceprints/people/{person_b['id']}/samples",
        files={'file': ('sample.wav', b'RIFF0000WAVE', 'audio/wav')},
    )

    assert response.status_code == 404
    assert '当前项目' in response.json()['detail']
    assert save_calls == []


def test_v2_voiceprint_sample_accepts_current_project_person(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    project = client.post('/api/v2/projects', json={'name': '样本当前项目'}).json()
    person = client.post(
        f"/api/v2/projects/{project['id']}/voiceprints/people",
        json={'name': '当前项目人物'},
    ).json()
    uploaded_path = tmp_path / 'uploaded.wav'
    uploaded_path.write_bytes(b'RIFF0000WAVE')

    async def fake_save(*args, **kwargs):
        return uploaded_path

    monkeypatch.setattr('app.api.v2.voiceprints.save_upload_file_locally', fake_save)
    monkeypatch.setattr(
        'app.api.v2.voiceprints.prepare_voiceprint_sample_assets',
        lambda *args, **kwargs: SimpleNamespace(
            voiceprint_audio_path=uploaded_path,
            clone_audio_path=uploaded_path,
        ),
    )
    monkeypatch.setattr('app.api.v2.voiceprints.save_voiceprint_sample', lambda **kwargs: object())
    monkeypatch.setattr(
        'app.api.v2.voiceprints.build_sample_response',
        lambda _sample: VoiceprintSampleResponse(
            id='sample-1',
            original_filename='sample.wav',
            embedding_status='ready',
            created_at=datetime.utcnow(),
        ),
    )

    response = client.post(
        f"/api/v2/projects/{project['id']}/voiceprints/people/{person['id']}/samples",
        files={'file': ('sample.wav', b'RIFF0000WAVE', 'audio/wav')},
    )

    assert response.status_code == 201
    assert response.json()['id'] == 'sample-1'


def test_v2_project_with_job_cannot_be_archived(client: TestClient, project_session: Session) -> None:
    project = client.post('/api/v2/projects', json={'name': '不能归档'}).json()
    project_session.add(Job(source_url='https://example.com/a.wav', project_id=project['id'], status='completed'))
    project_session.commit()

    response = client.post(f"/api/v2/projects/{project['id']}/archive")

    assert response.status_code == 409
    assert '只允许归档空项目' in response.json()['detail']


def test_v2_project_can_be_force_purged_with_existing_job(
    client: TestClient,
    project_session: Session,
) -> None:
    project = client.post('/api/v2/projects', json={'name': '强制清理项目'}).json()
    job = Job(
        source_url='https://example.com/a.wav',
        project_id=project['id'],
        status='processing',
    )
    project_session.add(job)
    project_session.commit()
    job_id = job.id

    response = client.delete(f"/api/v2/projects/{project['id']}")

    assert response.status_code == 204
    project_session.expire_all()
    assert project_session.get(Project, project['id']) is None
    assert project_session.get(Job, job_id) is None


def test_v2_media_job_locks_project_id_and_server_project_name(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    project = client.post('/api/v2/projects', json={'name': '锁定项目'}).json()
    media_path = tmp_path / 'sample.wav'
    media_path.write_bytes(b'RIFF0000WAVE')

    async def fake_save_upload_file_locally(*args, **kwargs):
        return media_path

    class FakeClient:
        def submit_transcription(self, *args, **kwargs):
            return {'task_id': 'task-1', 'request_id': 'request-1'}

    upload = UploadResponse(
        object_key='audio/sample.wav',
        public_url='https://example.test/audio.wav',
        content_type='audio/wav',
        size_bytes=media_path.stat().st_size,
    )
    chunk = SimpleNamespace(path=media_path, offset_ms=0, duration_seconds=1.0, index=0)
    monkeypatch.setattr('app.api.v1.transcriptions.save_upload_file_locally', fake_save_upload_file_locally)
    monkeypatch.setattr('app.api.v1.transcriptions.validate_recognition_source_constraints', lambda *args, **kwargs: None)
    monkeypatch.setattr('app.api.v1.transcriptions.copy_local_file_to_library', lambda *args, **kwargs: media_path)
    monkeypatch.setattr('app.api.v1.transcriptions.prepare_recognition_audio', lambda *args, **kwargs: media_path)
    monkeypatch.setattr('app.api.v1.transcriptions.split_recognition_audio', lambda *args, **kwargs: [chunk])
    monkeypatch.setattr('app.api.v1.transcriptions.upload_local_file_to_oss', lambda *args, **kwargs: upload)
    monkeypatch.setattr('app.api.v1.transcriptions.get_media_duration_seconds', lambda *args, **kwargs: 1.0)
    monkeypatch.setattr('app.api.v1.transcriptions.FunAsrClient', lambda: FakeClient())

    response = client.post(
        f"/api/v2/projects/{project['id']}/transcriptions/media-jobs",
        files={'file': ('sample.wav', b'RIFF0000WAVE', 'audio/wav')},
    )

    assert response.status_code == 201
    job = response.json()['job']
    assert job['project_id'] == project['id']
    assert job['program_name'] == '锁定项目'


def test_v2_media_job_rejects_missing_or_archived_project(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr('app.api.v1.transcriptions.FunAsrClient', lambda: SimpleNamespace())
    missing = client.post(
        '/api/v2/projects/missing/transcriptions/media-jobs',
        files={'file': ('sample.wav', b'RIFF0000WAVE', 'audio/wav')},
    )
    assert missing.status_code == 404

    project = client.post('/api/v2/projects', json={'name': '归档项目'}).json()
    client.post(f"/api/v2/projects/{project['id']}/archive")
    archived = client.post(
        f"/api/v2/projects/{project['id']}/transcriptions/media-jobs",
        files={'file': ('sample.wav', b'RIFF0000WAVE', 'audio/wav')},
    )

    assert archived.status_code == 409
    assert '已归档' in archived.json()['detail']


def test_v2_media_job_rechecks_project_after_upload_and_cleans_unbound_objects(
    client: TestClient,
    project_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr('app.api.v1.transcriptions.FunAsrClient', lambda: SimpleNamespace())
    project = client.post('/api/v2/projects', json={'name': '上传竞态项目'}).json()
    media_path = tmp_path / 'sample.wav'
    media_path.write_bytes(b'RIFF0000WAVE')
    uploaded_keys: list[str] = []
    deleted_keys: list[str] = []

    async def fake_save_upload_file_locally(*args, **kwargs):
        stored_project = project_session.get(Project, project['id'])
        stored_project.status = 'archived'
        project_session.commit()
        return media_path

    def fake_upload(*args, **kwargs):
        object_key = f'audio/sample-{len(uploaded_keys) + 1}.wav'
        uploaded_keys.append(object_key)
        return UploadResponse(
            object_key=object_key,
            public_url=f'https://example.test/{object_key}',
            content_type='audio/wav',
            size_bytes=media_path.stat().st_size,
        )

    chunk = SimpleNamespace(path=media_path, offset_ms=0, duration_seconds=1.0, index=0)
    monkeypatch.setattr('app.api.v1.transcriptions.save_upload_file_locally', fake_save_upload_file_locally)
    monkeypatch.setattr('app.api.v1.transcriptions.validate_recognition_source_constraints', lambda *args, **kwargs: None)
    monkeypatch.setattr('app.api.v1.transcriptions.copy_local_file_to_library', lambda *args, **kwargs: media_path)
    monkeypatch.setattr('app.api.v1.transcriptions.prepare_recognition_audio', lambda *args, **kwargs: media_path)
    monkeypatch.setattr('app.api.v1.transcriptions.split_recognition_audio', lambda *args, **kwargs: [chunk])
    monkeypatch.setattr('app.api.v1.transcriptions.upload_local_file_to_oss', fake_upload)
    monkeypatch.setattr('app.api.v1.transcriptions.delete_oss_object', deleted_keys.append)
    monkeypatch.setattr('app.api.v1.transcriptions.get_media_duration_seconds', lambda *args, **kwargs: 1.0)

    response = client.post(
        f"/api/v2/projects/{project['id']}/transcriptions/media-jobs",
        files={'file': ('sample.wav', b'RIFF0000WAVE', 'audio/wav')},
    )

    assert response.status_code == 409
    assert '已归档' in response.json()['detail']
    assert deleted_keys == uploaded_keys
    assert project_session.query(Job).count() == 0


def test_v1_project_name_auto_binds_formal_project(project_session: Session) -> None:
    from app.api.v1.transcriptions import _resolve_locked_project_context

    first = _resolve_locked_project_context(
        project_session,
        project_id=None,
        requested_program_name='旧客户端项目',
        fallback_name='sample.wav',
    )
    second = _resolve_locked_project_context(
        project_session,
        project_id=None,
        requested_program_name='旧客户端项目',
        fallback_name='sample.wav',
    )

    assert first == second
    assert first[0]
    assert first[1:] == ('旧客户端项目', '旧客户端项目')
    assert project_session.query(Project).count() == 1
