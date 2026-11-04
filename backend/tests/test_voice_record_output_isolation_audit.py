"""Synthetic regressions for generated text/audio isolation across shared voices."""
from pathlib import Path
import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from app.api.v1 import voice_cloning as v1
from app.api.v2 import voice_cloning as v2
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
from tests.test_voice_cloning_project_isolation import isolated_database, _project, _profile

@pytest.fixture()
def output_fixture(isolated_database, monkeypatch, tmp_path):
    session, factory = isolated_database
    monkeypatch.setattr(v1, 'SessionLocal', factory)
    project_a = _project(session, 'Output A', 'output-a')
    project_b = _project(session, 'Output B', 'output-b')
    profile = _profile(session, project_b, 'Shared voice')
    records = []
    for project, label in ((project_a, 'A'), (project_b, 'B')):
        record = VoiceCloneSynthesisRecord(project_id=project.id, voice_clone_profile_id=profile.id,
            target_model=profile.target_model, input_text=f'{label} project private generated text',
            output_audio_public_url=f'https://outputs.example.invalid/{label}.wav', status='ready')
        session.add(record); session.flush()
        output = tmp_path / f'voice-clone-preview-{record.id}.wav'
        output.write_bytes(f'RIFF-{label}-PRIVATE-OUTPUT'.encode())
        record.output_audio_local_path = str(output)
        records.append(record)
    record_a, record_b = records
    profile.preview_text = record_b.input_text
    profile.preview_audio_local_path = record_b.output_audio_local_path
    session.commit()
    ids = {'a': project_a.id, 'b': project_b.id, 'profile': profile.id,
           'record_a': record_a.id, 'record_b': record_b.id,
           'text_b': record_b.input_text, 'path_b': record_b.output_audio_local_path}
    remote_calls = []
    def fetch_remote(url, filename):
        remote_calls.append(url)
        return Response(b'REMOTE-B-PRIVATE-OUTPUT', media_type='audio/wav')
    monkeypatch.setattr(v1, '_fetch_remote_audio', fetch_remote)
    monkeypatch.setattr(v2, '_fetch_remote_audio', fetch_remote)
    app = FastAPI()
    app.include_router(v1.router, prefix='/api/v1/voice-cloning')
    app.include_router(v2.router, prefix='/api/v2')
    with TestClient(app) as client:
        yield client, ids, factory, remote_calls


def test_v2_record_routes_keep_generated_outputs_in_their_project(output_fixture):
    client, ids, _factory, remote_calls = output_fixture
    base = f'/api/v2/projects/{ids["a"]}/voice-cloning'
    for path in ('/synthesis-records', f'/profiles/{ids["profile"]}/synthesis-records'):
        response = client.get(base + path)
        assert response.status_code == 200
        assert {row['id'] for row in response.json()} == {ids['record_a']}
    own_audio = client.get(base + f'/synthesis-records/{ids["record_a"]}/audio')
    assert own_audio.status_code == 200 and own_audio.content == b'RIFF-A-PRIVATE-OUTPUT'
    for suffix in ('', '/audio'):
        assert client.get(base + f'/synthesis-records/{ids["record_b"]}' + suffix).status_code == 404
    assert client.delete(base + f'/synthesis-records/{ids["record_b"]}').status_code == 404
    assert remote_calls == []


def test_v1_record_routes_require_explicit_project(output_fixture):
    client, ids, factory, _calls = output_fixture
    root = '/api/v1/voice-cloning'
    assert client.get(root + f'/profiles/{ids["profile"]}/synthesis-records').status_code == 422
    assert client.get(root + f'/synthesis-records/{ids["record_b"]}/audio').status_code == 422
    assert client.delete(root + f'/synthesis-records/{ids["record_b"]}').status_code == 422
    with factory() as session:
        assert session.get(VoiceCloneSynthesisRecord, ids['record_b']) is not None


def test_v1_profile_record_list_must_not_reveal_other_project_output(output_fixture):
    client, ids, _factory, _calls = output_fixture
    response = client.get(f'/api/v1/voice-cloning/profiles/{ids["profile"]}/synthesis-records', params={'project_id': ids['a']})
    assert response.status_code == 200
    assert {row['id'] for row in response.json()} == {ids['record_a']}


@pytest.mark.parametrize('remote_only', [False, True])
def test_v1_audio_exit_must_not_bypass_record_project(remote_only, output_fixture):
    client, ids, factory, remote_calls = output_fixture
    if remote_only:
        with factory() as session:
            session.get(VoiceCloneSynthesisRecord, ids['record_b']).output_audio_local_path = None
            session.commit()
    response = client.get(f'/api/v1/voice-cloning/synthesis-records/{ids["record_b"]}/audio', params={'project_id': ids['a']})
    assert response.status_code == 404, 'Foreign generated audio was served via V1'
    assert remote_calls == []


def test_v1_delete_must_not_remove_foreign_project_output(output_fixture):
    client, ids, factory, _calls = output_fixture
    response = client.delete(f'/api/v1/voice-cloning/synthesis-records/{ids["record_b"]}', params={'project_id': ids['a']})
    assert response.status_code == 404, 'Foreign generated output was deleted via V1'
    with factory() as session:
        assert session.get(VoiceCloneSynthesisRecord, ids['record_b']) is not None
    assert Path(ids['path_b']).exists()


def test_shared_profile_detail_must_not_export_other_project_generation_snapshot(output_fixture):
    client, ids, _factory, _calls = output_fixture
    response = client.get(f'/api/v2/projects/{ids["a"]}/voice-cloning/profiles/{ids["profile"]}')
    assert response.status_code == 200
    payload = response.json()
    assert payload.get('preview_text') is None
    assert payload.get('preview_audio_local_path') is None


@pytest.mark.parametrize('scope', ['invalid', 'archived'])
def test_v1_record_routes_map_invalid_project_state_without_server_error(scope, output_fixture):
    from app.models.project import Project

    client, ids, factory, remote_calls = output_fixture
    project_id, expected = 'missing-project', 404
    if scope == 'archived':
        project_id, expected = ids['a'], 409
        with factory() as session:
            session.get(Project, project_id).status = 'archived'
            session.commit()
    root = '/api/v1/voice-cloning'
    endpoints = (
        ('GET', f'/profiles/{ids["profile"]}/synthesis-records'),
        ('GET', f'/synthesis-records/{ids["record_b"]}/audio'),
        ('DELETE', f'/synthesis-records/{ids["record_b"]}'),
    )
    for method, path in endpoints:
        response = client.request(method, root + path, params={'project_id': project_id})
        assert response.status_code == expected
    assert remote_calls == []
    with factory() as session:
        assert session.get(VoiceCloneSynthesisRecord, ids['record_b']) is not None
