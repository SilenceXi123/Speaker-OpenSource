from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.job import Job
from app.models.project import Project
from app.services.jobs import (
    create_job,
    delete_job,
    normalize_subtitle_backplate_config,
    select_subtitle_backplate_asset,
    update_subtitle_backplate_asset,
)
from app.services.projects import build_project_response


def _session_for(engine):
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _asset(object_key: str, **extra) -> dict:
    return {
        'file_name': f'{object_key}.png',
        'object_key': object_key,
        **extra,
    }


def test_backplate_reader_accepts_legacy_shapes_without_borrowing_missing_role() -> None:
    legacy_single = _asset('legacy')
    direct_role_map = {'with_speaker': _asset('with')}
    roles_wrapper = {
        'roles': {'without_speaker': _asset('without')},
    }

    normalized_legacy = normalize_subtitle_backplate_config(legacy_single)

    assert normalized_legacy == {
        'schema_version': 1,
        'roles': {
            'with_speaker': {**legacy_single, 'role': 'with_speaker'},
            'without_speaker': {**legacy_single, 'role': 'without_speaker'},
        },
    }
    assert select_subtitle_backplate_asset(direct_role_map, 'with_speaker')['object_key'] == 'with'
    assert select_subtitle_backplate_asset(direct_role_map, 'without_speaker') is None
    assert select_subtitle_backplate_asset(roles_wrapper, 'without_speaker')['object_key'] == 'without'
    assert select_subtitle_backplate_asset(roles_wrapper, 'with_speaker') is None


def test_new_job_copies_project_backplate_snapshot_and_project_count() -> None:
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    session = _session_for(engine)
    original = {
        'schema_version': 1,
        'roles': {
            'with_speaker': _asset(
                'project/with-v1.png',
                asset_id='asset-v1',
                role='with_speaker',
                library_scope='project',
            ),
        },
    }
    project = Project(
        name='底图项目',
        name_key='底图项目',
        subtitle_backplate_assets=original,
    )
    session.add(project)
    session.commit()

    first_job = create_job(
        session,
        source_url='https://example.com/first.wav',
        project_id=project.id,
    )
    project.subtitle_backplate_assets = {
        'schema_version': 1,
        'roles': {
            'with_speaker': _asset(
                'project/with-v2.png',
                asset_id='asset-v2',
                role='with_speaker',
                library_scope='project',
            ),
        },
    }
    session.commit()
    second_job = create_job(
        session,
        source_url='https://example.com/second.wav',
        project_id=project.id,
    )

    assert first_job.subtitle_backplate_asset['roles']['with_speaker']['asset_id'] == 'asset-v1'
    assert second_job.subtitle_backplate_asset['roles']['with_speaker']['asset_id'] == 'asset-v2'
    assert build_project_response(session, project).asset_count == 1


def test_role_updates_refresh_stale_session_before_merging(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'backplate-concurrency.db'}",
        connect_args={'check_same_thread': False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    first_session = factory()
    second_session = factory()
    job = Job(source_url='https://example.com/source.wav', status='completed')
    first_session.add(job)
    first_session.commit()
    job_id = job.id
    assert first_session.get(Job, job_id).subtitle_backplate_asset is None

    update_subtitle_backplate_asset(
        second_session,
        job_id,
        _asset('with'),
        role='with_speaker',
    )
    update_subtitle_backplate_asset(
        first_session,
        job_id,
        _asset('without'),
        role='without_speaker',
    )

    first_session.expire_all()
    stored = first_session.get(Job, job_id).subtitle_backplate_asset
    assert stored['roles']['with_speaker']['object_key'] == 'with'
    assert stored['roles']['without_speaker']['object_key'] == 'without'


def test_role_and_job_deletion_keep_project_backplate_file(tmp_path) -> None:
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    session = _session_for(engine)
    library_file = tmp_path / 'project-backplate.png'
    retired_library_file = tmp_path / 'retired-project-backplate.png'
    library_file.write_bytes(b'current')
    retired_library_file.write_bytes(b'retired')
    project_asset = _asset(
        'projects/p1/subtitle-backplates/project-backplate.png',
        asset_id='project-asset',
        local_path=str(library_file),
        library_scope='project',
        role='with_speaker',
    )
    project = Project(
        name='删除生命周期',
        name_key='删除生命周期',
        subtitle_backplate_assets={
            'schema_version': 1,
            'roles': {'with_speaker': project_asset},
        },
    )
    session.add(project)
    session.commit()
    job = Job(
        source_url='https://example.com/source.wav',
        status='completed',
        project_id=project.id,
        subtitle_backplate_asset={
            'file_name': retired_library_file.name,
            'object_key': 'projects/p1/subtitle-backplates/retired-project-backplate.png',
            'local_path': str(retired_library_file),
        },
    )
    session.add(job)
    session.commit()

    update_subtitle_backplate_asset(session, job.id, None)

    assert library_file.exists()
    assert retired_library_file.exists()
    job.subtitle_backplate_asset = {
        'schema_version': 1,
        'roles': {'with_speaker': project_asset},
    }
    session.commit()
    delete_job(session, job.id)
    assert library_file.exists()
    assert retired_library_file.exists()
    assert session.get(Project, project.id).subtitle_backplate_assets['roles']['with_speaker']


def test_role_update_does_not_delete_old_file_before_commit(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    session = _session_for(engine)
    old_file = tmp_path / 'old-backplate.png'
    old_file.write_bytes(b'old')
    job = Job(
        source_url='https://example.com/source.wav',
        status='completed',
        subtitle_backplate_asset={
            'file_name': old_file.name,
            'object_key': old_file.name,
            'local_path': str(old_file),
        },
    )
    session.add(job)
    session.commit()

    def fail_commit() -> None:
        raise RuntimeError('simulated commit failure')

    monkeypatch.setattr(session, 'commit', fail_commit)
    with pytest.raises(RuntimeError, match='simulated commit failure'):
        update_subtitle_backplate_asset(
            session,
            job.id,
            _asset('replacement'),
            role='with_speaker',
        )

    assert old_file.exists()
