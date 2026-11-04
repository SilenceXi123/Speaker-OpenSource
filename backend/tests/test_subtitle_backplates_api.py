from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.job import Job
from app.models.project import Project
from app.services.jobs import create_job, delete_job
from main import app


@pytest.fixture()
def project_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = create_engine(
        'sqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    monkeypatch.setattr('app.api.v2.transcriptions.SessionLocal', factory)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(project_session: Session) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _project(name: str) -> Project:
    return Project(name=name, name_key=name.casefold())


def test_v2_backplate_endpoints_reject_cross_project_before_file_save(
    client: TestClient,
    project_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_a = _project('底图项目 A')
    project_b = _project('底图项目 B')
    project_session.add_all([project_a, project_b])
    project_session.commit()
    job_b = Job(
        source_url='https://example.com/b.wav',
        project_id=project_b.id,
        status='completed',
    )
    project_session.add(job_b)
    project_session.commit()
    save_calls: list[str] = []

    async def fail_if_saved(*args, **kwargs):
        save_calls.append('saved')
        raise AssertionError('cross-project upload must fail before saving')

    monkeypatch.setattr(
        'app.api.v2.transcriptions.save_upload_file_locally',
        fail_if_saved,
    )
    base = (
        f'/api/v2/projects/{project_a.id}/transcriptions/jobs/{job_b.id}'
        '/subtitle-backplate'
    )

    upload = client.post(
        base,
        files={'file': ('backplate.png', b'png', 'image/png')},
        data={'role': 'with_speaker'},
    )
    delete = client.delete(base, params={'role': 'with_speaker'})
    read = client.get(f'{base}/file', params={'role': 'with_speaker'})
    load = client.post(f'{base}/from-project-library')

    assert [upload.status_code, delete.status_code, read.status_code, load.status_code] == [
        404,
        404,
        404,
        404,
    ]
    assert save_calls == []


def test_v2_backplate_upload_inheritance_library_load_and_deletion_lifecycle(
    client: TestClient,
    project_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = _project('底图生命周期')
    project_session.add(project)
    project_session.commit()
    current_job = Job(
        source_url='https://example.com/current.wav',
        project_id=project.id,
        status='completed',
    )
    project_session.add(current_job)
    project_session.commit()
    upload_index = 0
    library_paths: list[Path] = []

    async def fake_save(*args, **kwargs) -> Path:
        nonlocal upload_index
        upload_index += 1
        path = tmp_path / f'upload-{upload_index}.png'
        path.write_bytes(f'upload-{upload_index}'.encode())
        return path

    def fake_copy(*, source_path: Path, target_dir: Path, original_name: str) -> Path:
        path = tmp_path / f'library-{len(library_paths) + 1}.png'
        path.write_bytes(source_path.read_bytes())
        library_paths.append(path)
        return path

    monkeypatch.setattr('app.api.v2.transcriptions.save_upload_file_locally', fake_save)
    monkeypatch.setattr('app.api.v2.transcriptions.copy_local_file_to_library', fake_copy)
    base = (
        f'/api/v2/projects/{project.id}/transcriptions/jobs/{current_job.id}'
        '/subtitle-backplate'
    )

    first_upload = client.post(
        base,
        files={'file': ('first.png', b'first', 'image/png')},
        data={'role': 'with_speaker', 'client_path': '/Client/first.png'},
    )

    assert first_upload.status_code == 201
    first_asset = first_upload.json()
    assert first_asset['asset_id']
    assert first_asset['object_key'] == library_paths[0].name
    assert first_asset['library_scope'] == 'project'
    project_session.expire_all()
    stored_project = project_session.get(Project, project.id)
    assert stored_project.subtitle_backplate_assets['schema_version'] == 1
    assert stored_project.subtitle_backplate_assets['roles']['with_speaker']['asset_id'] == first_asset['asset_id']

    inherited_job = create_job(
        project_session,
        source_url='https://example.com/inherited.wav',
        project_id=project.id,
    )
    inherited_job.status = 'completed'
    project_session.commit()
    assert inherited_job.subtitle_backplate_asset['roles']['with_speaker']['asset_id'] == first_asset['asset_id']

    second_upload = client.post(
        base,
        files={'file': ('second.png', b'second', 'image/png')},
        data={'role': 'with_speaker'},
    )

    assert second_upload.status_code == 201
    second_asset = second_upload.json()
    assert second_asset['asset_id'] != first_asset['asset_id']
    project_session.expire_all()
    assert project_session.get(Job, inherited_job.id).subtitle_backplate_asset['roles']['with_speaker']['asset_id'] == first_asset['asset_id']
    assert library_paths[0].exists()

    inherited_base = (
        f'/api/v2/projects/{project.id}/transcriptions/jobs/{inherited_job.id}'
        '/subtitle-backplate'
    )
    loaded = client.post(f'{inherited_base}/from-project-library')
    read = client.get(f'{inherited_base}/file', params={'role': 'with_speaker'})
    deleted = client.delete(inherited_base, params={'role': 'with_speaker'})

    assert loaded.status_code == 200
    assert loaded.json()['roles']['with_speaker']['asset_id'] == second_asset['asset_id']
    assert read.status_code == 200
    assert read.content == b'upload-2'
    assert deleted.status_code == 204
    project_session.expire_all()
    assert project_session.get(Job, inherited_job.id).subtitle_backplate_asset is None
    assert project_session.get(Project, project.id).subtitle_backplate_assets['roles']['with_speaker']['asset_id'] == second_asset['asset_id']
    assert library_paths[1].exists()

    delete_job(project_session, inherited_job.id)
    assert library_paths[0].exists()
    assert library_paths[1].exists()
