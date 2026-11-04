import asyncio
import importlib
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI


@pytest.mark.parametrize('version,kind,handler_name', [
    ('v1', 'voice_cloning', 'post_sample'),
    ('v2', 'voice_cloning', 'post_project_sample'),
    ('v1', 'voiceprints', 'post_person_sample'),
    ('v2', 'voiceprints', 'post_project_person_sample'),
])
@pytest.mark.parametrize('phase', ['prepare', 'save'])
def test_slow_voice_upload_keeps_other_requests_responsive(
    monkeypatch, version, kind, handler_name, phase,
) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'voice_cloning_enabled', True)
    module = importlib.import_module(f'app.api.{version}.{kind}')
    entered, release = Event(), Event()
    person = SimpleNamespace(id='person-1', project_id='project-1')
    project = SimpleNamespace(id='project-1', status='active')
    monkeypatch.setattr(module, 'SessionLocal', lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(module, 'remove_local_file', lambda _path: None)
    for name, result in [('get_person', person), ('list_people', [person]),
                         ('_get_active_project', project), ('_get_active_project_or_http', project)]:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, lambda *args, _result=result, **kwargs: _result)

    async def upload(_file):
        return Path('/unused/upload.wav')

    monkeypatch.setattr(module, 'save_upload_file_locally', upload)
    is_clone = kind == 'voice_cloning'
    prepared = Path('/unused/prepared.wav') if is_clone else SimpleNamespace(
        voiceprint_audio_path=Path('/unused/prepared.wav'), clone_audio_path=None,
    )
    saved = SimpleNamespace(reused_existing=False, sample=object()) if is_clone else object()

    def run_stage(stage, result):
        def process(*args, **kwargs):
            if phase == stage:
                entered.set()
                release.wait(timeout=1)
            return result
        return process

    monkeypatch.setattr(module, 'prepare_voice_clone_sample_audio' if is_clone else
                        'prepare_voiceprint_sample_assets', run_stage('prepare', prepared))
    monkeypatch.setattr(module, 'find_or_create_voice_clone_sample' if is_clone else
                        'save_voiceprint_sample', run_stage('save', saved))
    monkeypatch.setattr(module, 'build_voice_clone_sample_save_response' if is_clone else
                        'build_sample_response', lambda _result: {'id': 'sample-1'})
    app = FastAPI()
    upload_route = ('/projects/{project_id}' if version == 'v2' else '')
    upload_route += ('/people/{person_id}' if not is_clone else '') + '/upload'
    app.add_api_route(upload_route,
                      getattr(module, handler_name), methods=['POST'], response_model=None)

    @app.get('/probe')
    async def probe():
        return {'ok': True}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            pending = asyncio.create_task(client.post(
                upload_route.format(project_id='project-1', person_id='person-1'),
                data={'display_name': 'test', 'project_id': 'project-1'},
                files={'file': ('sample.wav', b'test', 'audio/wav')},
            ))
            try:
                while not entered.is_set() and not pending.done():
                    await asyncio.sleep(0.001)
                if pending.done():
                    await pending
                assert entered.is_set(), 'Upload did not reach the slow processing step'
                response = await asyncio.wait_for(client.get('/probe'), timeout=0.5)
                assert response.status_code == 200
                assert not pending.done(), 'Slow upload blocked the other request'
            finally:
                release.set()
                response = await pending
            assert response.status_code == 200

    asyncio.run(exercise())
