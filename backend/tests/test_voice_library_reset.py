from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import subprocess
import sys
import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.person import Person
from app.models.project import Project
from app.models.voice_clone_deletion_task import VoiceCloneDeletionTask
from app.models.voice_clone_identity import VoiceCloneIdentity
from app.models.voice_clone_identity_review import VoiceCloneIdentityReview
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_sample import VoiceCloneSample
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
from app.models.voiceprint_sample import VoiceprintSample
from tests.test_voice_clone_library import session, project

@pytest.fixture(autouse=True)
def isolated_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'local_audio_dir', str(tmp_path / 'audio'))
    monkeypatch.setattr(settings, 'local_voiceprint_library_dir', str(tmp_path / 'recognition-assets'))
    monkeypatch.setattr(settings, 'local_voice_clone_library_dir', str(tmp_path / 'clone-assets'))


def test_reset_all_removes_voice_data_and_orphan_audio_without_backups(
    tmp_path: Path, session: Session, project: Project, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.job import Job
    from app.models.voiceprint_match import VoiceprintMatch
    cleanup = importlib.import_module('app.services.voice_clone_cleanup')
    voiceprints = tmp_path / 'voiceprints'
    clones = tmp_path / 'voice-cloning'
    voiceprints.mkdir()
    clones.mkdir()
    monkeypatch.setattr(settings, 'local_voiceprint_library_dir', str(voiceprints))
    monkeypatch.setattr(settings, 'local_voice_clone_library_dir', str(clones))
    monkeypatch.setattr(settings, 'oss_public_base_url', 'https://owned.example')
    paths = [voiceprints / 'reference.wav', clones / 'sample.wav', clones / 'output.wav', clones / 'orphan.wav']
    for path in paths:
        path.write_bytes(b'old voice')
    preserved = clones / 'project-source.wav'
    preserved.write_bytes(b'project source')
    person = Person(name='保留人物', project_id=project.id)
    job = Job(project_id=project.id, source_url='https://owned.example/project-source.wav', source_local_path=str(preserved), normalized_segments=[{'text': '保留字幕'}])
    session.add_all([person, job])
    session.flush()
    identity = VoiceCloneIdentity(display_name='旧身份', aliases=['旧别名'], reference_embedding=[0.2, 0.8])
    session.add(identity)
    session.flush()
    sample = VoiceCloneSample(identity_id=identity.id, display_name='旧声音', original_filename='sample.wav', audio_local_path=str(paths[1]))
    recognition = VoiceprintSample(person_id=person.id, original_filename='reference.wav', audio_local_path=str(paths[0]), embedding=[0.1, 0.9])
    session.add_all([sample, recognition])
    session.flush()
    session.add_all(VoiceCloneIdentityReview(candidate_identity_id=identity.id, sample_id=sample.id if status == 'pending' else None, submitted_name='旧审核', match_score=0.95, status=status) for status in ('pending', 'resolved'))
    profile = VoiceCloneProfile(identity_id=identity.id, source_clone_sample_id=sample.id, source_sample_id=recognition.id, source_audio_local_path=str(preserved), voice_name='旧声音', provider='cosyvoice', target_model='cosyvoice-v3-plus', voice_id='old-voice', status='ready')
    session.add(profile)
    session.flush()
    session.add_all([
        VoiceprintMatch(job_id=job.id, person_id=person.id, speaker_key='speaker_0', top_candidates=[{'person_id': person.id}]),
        VoiceCloneSynthesisRecord(voice_clone_profile_id=profile.id, target_model='cosyvoice-v3-plus', input_text='旧生成', output_audio_local_path=str(paths[2]), status='ready'),
        VoiceCloneSynthesisRecord(target_model='cosyvoice-v3-plus', input_text='共享音频', output_audio_public_url=job.source_url, status='ready'),
    ])
    session.commit()
    session.execute(text('PRAGMA foreign_keys=ON'))
    session.execute(text('PRAGMA secure_delete=OFF'))
    assert session.scalar(text('PRAGMA secure_delete')) == 0
    plan = cleanup.build_voice_clone_cleanup_plan(session, reset_all=True)
    assert dict(plan.reset_table_counts) == {'voiceprint_samples': 1, 'voiceprint_matches': 1, 'voice_clone_samples': 1, 'voice_clone_profiles': 1, 'voice_clone_synthesis_records': 2, 'voice_clone_identities': 1, 'voice_clone_identity_reviews': 2}
    assert len(plan.deletion_targets) == 5
    assert all(path.exists() for path in paths)
    def queue_precedes_delete(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('DELETE FROM'):
            assert connection.exec_driver_sql('PRAGMA secure_delete').scalar() == 1
            assert connection.exec_driver_sql('SELECT count(*) FROM voice_clone_deletion_tasks').scalar() == 5
    event.listen(session.get_bind(), 'before_cursor_execute', queue_precedes_delete)
    plan_path = tmp_path / 'reset-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')
    cleanup.apply_voice_clone_cleanup_plan(session, plan, backup_path=None, plan_path=plan_path, no_backup=True)
    event.remove(session.get_bind(), 'before_cursor_execute', queue_precedes_delete)
    assert not list(tmp_path.glob('*backup*'))
    for model in (VoiceCloneSample, VoiceCloneProfile, VoiceCloneSynthesisRecord, VoiceprintSample, VoiceprintMatch, VoiceCloneIdentity, VoiceCloneIdentityReview):
        assert session.query(model).count() == 0
    assert session.get(Person, person.id).name == '保留人物'
    assert session.get(Project, project.id) is not None
    assert session.get(Job, job.id).normalized_segments == [{'text': '保留字幕'}]
    monkeypatch.setattr(cleanup, '_delete_provider_voice', lambda *_args, **_kwargs: None)
    drained = cleanup.drain_voice_clone_deletion_tasks(session)
    assert drained.failed_count == 0
    assert drained.completed_count == 5
    assert all(not path.exists() for path in paths)
    assert preserved.read_bytes() == b'project source'
    repeated = cleanup.build_voice_clone_cleanup_plan(session, reset_all=True)
    assert sum(dict(repeated.reset_table_counts).values()) == 0
    assert repeated.deletion_targets == ()


def test_reset_all_rejects_stale_plan_and_requires_explicit_no_backup(
    tmp_path: Path, session: Session,
) -> None:
    cleanup = importlib.import_module('app.services.voice_clone_cleanup')
    plan = cleanup.build_voice_clone_cleanup_plan(session, reset_all=True)
    plan_path = tmp_path / 'reset-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')
    with pytest.raises(RuntimeError, match='备份'):
        cleanup.apply_voice_clone_cleanup_plan(session, plan, backup_path=None, plan_path=plan_path)
    session.add(VoiceCloneSample(display_name='新增', original_filename='new.wav', audio_local_path=str(tmp_path / 'new.wav')))
    session.commit()
    with pytest.raises(RuntimeError, match='过期'):
        cleanup.apply_voice_clone_cleanup_plan(session, plan, backup_path=None, plan_path=plan_path, no_backup=True)
    assert session.query(VoiceCloneSample).count() == 1
    assert session.query(VoiceCloneDeletionTask).count() == 0


def test_maintenance_patch_only_changes_switch_and_exact_voice_routes(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from scripts.voice_library_maintenance import MIDDLEWARE, enabled_value, with_enabled, without_middleware
    env = 'DASHSCOPE_API_KEY=secret-value\nVOICE_CLONING_ENABLED=true\nAPP_PORT=8787\n'
    changed = with_enabled(env, False)
    assert enabled_value(changed) is False
    assert with_enabled(changed, True) == env
    assert 'secret-value' not in json.dumps({'previous_enabled': enabled_value(env)})
    with pytest.raises(RuntimeError):
        enabled_value('VOICE_CLONING_ENABLED=true\nVOICE_CLONING_ENABLED=false\n')
    app = FastAPI()
    app.get('/api/v1/health')(lambda: {'ok': True})
    app.get('/api/v1/voice-cloning/samples')(lambda: [])
    app.post('/api/v1/voiceprints/people/one/samples')(lambda: {'saved': True})
    state = SimpleNamespace(voice_cloning_enabled=False, voice_cloning_region='cn-beijing')
    namespace = {'app': app, 'settings': state}
    exec(compile(MIDDLEWARE, '<maintenance>', 'exec'), namespace)
    marker = tmp_path / 'active'
    namespace['_VoiceMaintenancePath'] = lambda _path: marker
    client = TestClient(app)
    assert client.get('/api/v1/voice-cloning/provider-status').json()['enabled'] is False
    assert client.post('/api/v1/voice-cloning/samples').status_code == 503
    assert client.get('/api/v2/projects/one/voice-cloning/samples').status_code == 503
    assert client.get('/api/v1/health?path=/voice-cloning').status_code == 200
    assert client.get('/api/v1/voice-cloning-other').status_code == 404
    marker.touch()
    assert client.post('/api/v1/admin/voice-clone-identities/reviews/old/resolve').status_code == 503
    assert client.post('/api/v1/voiceprints/people/one/samples').status_code == 503
    assert client.post('/api/v2/projects/one/voiceprints/people/one/samples').status_code == 503
    marker.unlink()
    assert client.post('/api/v1/voiceprints/people/one/samples').status_code == 200
    state.voice_cloning_enabled = True
    assert client.get('/api/v1/voice-cloning/samples').status_code == 200
    source = 'existing_code = True\n'
    assert without_middleware(source + MIDDLEWARE).rstrip() == source.rstrip()


def test_reset_cli_requires_explicit_mode_and_never_creates_backup(
    tmp_path: Path, session: Session,
) -> None:
    import os
    database_url = str(session.get_bind().url)
    env = {**os.environ, 'LOCAL_AUDIO_DIR': str(tmp_path / 'audio'), 'LOCAL_VOICEPRINT_LIBRARY_DIR': str(tmp_path / 'voiceprints'), 'LOCAL_VOICE_CLONE_LIBRARY_DIR': str(tmp_path / 'voice-cloning')}
    command = [sys.executable, 'scripts/cleanup_voice_clone_library.py', '--database-url', database_url, '--json']
    plan_path = tmp_path / 'cli-reset-plan.json'
    preview = subprocess.run([*command, '--reset-all', '--output-plan', str(plan_path)], text=True, capture_output=True, env=env)
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)['plan']['reset_all'] is True
    refused = subprocess.run([*command, '--apply', '--plan', str(plan_path), '--no-backup'], text=True, capture_output=True, env=env)
    assert refused.returncode != 0
    applied = subprocess.run([*command, '--reset-all', '--apply', '--plan', str(plan_path), '--no-backup'], text=True, capture_output=True, env=env)
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)['mode'] == 'apply'
    assert not list(tmp_path.glob('*backup*'))


@pytest.mark.parametrize('missing_legacy_files', [False, True])
def test_administrator_pause_and_resume_preserve_other_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_legacy_files: bool,
) -> None:
    from scripts import voice_library_maintenance as maintenance
    backend = tmp_path / 'backend'
    backend.mkdir()
    services = backend / 'app/services'
    services.mkdir(parents=True)
    for filename, (function, _result) in maintenance.VOICE_GUARDS.items():
        if missing_legacy_files and ('identity_review' in filename or filename.startswith('scripts/')):
            continue
        (backend / filename).parent.mkdir(parents=True, exist_ok=True)
        (backend / filename).write_text(f'def {function}(session, job):\n    return None\n', encoding='utf-8')
    main_path, env_path = backend / 'main.py', tmp_path / '.env'
    main_path.write_text('app = object()\n', encoding='utf-8')
    original_env = 'DASHSCOPE_API_KEY=do-not-copy\nVOICE_CLONING_ENABLED=true\nAPP_PORT=8787\n'
    env_path.write_text(original_env, encoding='utf-8')
    state_dir = tmp_path / 'maintenance-state'
    monkeypatch.setattr(maintenance, 'BACKEND', backend)
    monkeypatch.setattr(maintenance, 'STATE_DIR', state_dir)
    monkeypatch.setattr(maintenance.sys, 'platform', 'linux')
    monkeypatch.setattr(maintenance.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(maintenance, 'stop_backend', lambda _server: None)
    monkeypatch.setattr(maintenance, 'start_backend', lambda _server: None)
    monkeypatch.setattr(maintenance, 'verify_paused', lambda: None)
    monkeypatch.setattr(maintenance.sys, 'argv', ['maintenance', '--server', 'beijing', '--pause'])
    maintenance.main()
    assert maintenance.enabled_value(env_path.read_text()) is False
    assert maintenance.BEGIN in main_path.read_text()
    assert state_dir.stat().st_mode & 0o777 == 0o711
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in state_dir.iterdir())
    guarded = maintenance.with_voice_guard('def save(session, job):\n    return "created"\n', 'save', 'return "skipped"')
    scope = {'Path': Path}
    exec(guarded, scope)
    assert scope['save'](None, None) == 'skipped'
    assert (state_dir / 'main-before.py').read_text() == 'app = object()\n'
    assert json.loads((state_dir / 'state.json').read_text()) == {'previous_enabled': True}
    assert all('do-not-copy' not in path.read_text() for path in state_dir.iterdir())
    if missing_legacy_files:
        review_path = backend / 'app/services/voice_clone_identity_review.py'
        assert not review_path.exists()
        review_path.write_text('def renamed_function():\n    pass\n')
        with pytest.raises(RuntimeError, match='Voice function changed'):
            maintenance.main()
        review_path.write_text('def resolve_identity_review(session, job):\n    return None\n')
    maintenance.main()
    assert main_path.read_text().count(maintenance.BEGIN) == 1
    assert json.loads((state_dir / 'state.json').read_text()) == {'previous_enabled': True}
    assert all((backend / filename).read_text().count(maintenance.BEGIN) == 1
        for filename in maintenance.VOICE_GUARDS if (backend / filename).exists())
    monkeypatch.setattr(maintenance.sys, 'argv', ['maintenance', '--server', 'beijing', '--resume'])
    with pytest.raises(RuntimeError, match='No completed reset'):
        maintenance.main()
    (state_dir / 'state.json').write_text(json.dumps({'previous_enabled': True, 'reset_applied': True}))
    order = []
    monkeypatch.setattr(maintenance, 'stop_backend', lambda _server: order.append('stop'))
    def verify_stopped(*args, **kwargs):
        assert order == ['stop']
        assert (state_dir / 'active').exists()
        assert maintenance.BEGIN in main_path.read_text()
        order.append('verified')
    monkeypatch.setattr(maintenance.subprocess, 'run', verify_stopped)
    maintenance.main()
    assert order == ['stop', 'verified']
    assert env_path.read_text() == original_env
    assert maintenance.BEGIN not in main_path.read_text()
    assert not (state_dir / 'active').exists()
    assert scope['save'](None, None) == 'created'
    for filename in maintenance.VOICE_GUARDS:
        if (backend / filename).exists():
            assert maintenance.BEGIN not in (backend / filename).read_text()
    monkeypatch.setattr(maintenance.sys, 'argv', ['maintenance', '--server', 'beijing', '--pause'])
    maintenance.main()
    assert json.loads((state_dir / 'state.json').read_text()) == {'previous_enabled': True}


@pytest.mark.parametrize('already_running', [False, True])
def test_shanghai_restart_uses_baota_launcher_to_preserve_environment_and_pid(tmp_path, monkeypatch, already_running):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from scripts import voice_library_maintenance as maintenance

    calls = []
    monkeypatch.setattr(maintenance, 'BACKEND', tmp_path)
    monkeypatch.setattr(maintenance, 'shanghai_pids', lambda: [123] if already_running else [])
    monkeypatch.setenv('HOME', '/root')
    monkeypatch.setenv('MODELSCOPE_CACHE', '/configured/cache')
    monkeypatch.setattr(maintenance.pwd, 'getpwnam', lambda _name: SimpleNamespace(pw_uid=1001, pw_gid=1001, pw_dir='/home/www'))
    monkeypatch.setattr(maintenance.subprocess, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    def reject_direct_launch(*_args, **_kwargs):
        raise AssertionError('Direct uvicorn launch bypasses the Baota environment and PID file')
    monkeypatch.setattr(maintenance.subprocess, 'Popen', reject_direct_launch)
    monkeypatch.setattr(maintenance, 'urlopen', lambda *_args, **_kwargs: nullcontext(SimpleNamespace(status=200)))

    maintenance.start_backend('shanghai')
    assert len(calls) == (0 if already_running else 1)
    if calls:
        args, kwargs = calls[0]
        assert args[0] == ['/bin/bash', '/www/server/python_project/vhost/scripts/backend_cmd.sh']
        assert kwargs['user'] == kwargs['group'] == 1001
        assert kwargs['check'] and kwargs['timeout'] == 10
        assert kwargs['env']['HOME'] == '/home/www'
        assert kwargs['env']['MODELSCOPE_CACHE'] == '/configured/cache'
    assert maintenance.os.environ['HOME'] == '/root'


def test_maintenance_drain_attempts_each_asset_once_even_when_batches_take_minutes(
    tmp_path: Path, session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import nullcontext
    from app.db import session as database
    from scripts import voice_library_maintenance as maintenance
    cleanup = importlib.import_module('app.services.voice_clone_cleanup')
    session.add_all(VoiceCloneDeletionTask(kind='provider', resource_key=f'provider:old:{index}', provider='cosyvoice', target_model='old-model', voice_id=str(index)) for index in range(111))
    session.commit()
    monkeypatch.setattr(settings, 'voice_cloning_enabled', False)
    monkeypatch.setattr(database, 'SessionLocal', lambda: nullcontext(session))
    monkeypatch.setattr(maintenance, 'BACKEND', tmp_path)
    def fail(_task):
        raise TimeoutError('simulated slow provider')
    monkeypatch.setattr(cleanup, '_run_voice_clone_deletion_task', fail)
    real_drain = cleanup.drain_voice_clone_deletion_tasks
    calls = []
    start = datetime.now(timezone.utc)
    def slow_batch(current_session, *, limit, now=None):
        calls.append(now)
        assert len(calls) <= 2, 'failed assets were selected again within the same maintenance run'
        elapsed_now = start + timedelta(minutes=3 * len(calls))
        return real_drain(current_session, limit=limit, now=now or elapsed_now)
    monkeypatch.setattr(cleanup, 'drain_voice_clone_deletion_tasks', slow_batch)
    with pytest.raises(RuntimeError, match='pending deletion'):
        maintenance.retry_deletions()
    assert len(calls) == 2
    assert all(task.attempt_count == 1 for task in session.query(VoiceCloneDeletionTask))


def test_reset_rejects_replaced_orphan_audio(tmp_path, session):
    cleanup = importlib.import_module('app.services.voice_clone_cleanup')
    audio = settings.voice_clone_library_dir_path / 'sample.wav'
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b'old voice')
    plan = cleanup.build_voice_clone_cleanup_plan(session, reset_all=True)
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()))
    audio.write_bytes(b'new replacement voice')
    with pytest.raises(RuntimeError, match='过期'):
        cleanup.apply_voice_clone_cleanup_plan(session, plan, None, plan_path, no_backup=True)
    assert session.query(VoiceCloneSample).count() == 0
    assert session.query(VoiceCloneDeletionTask).count() == 0
    assert audio.read_bytes() == b'new replacement voice'


def test_reset_preserves_failed_pending_assets_and_late_project_reference(tmp_path, session, project, monkeypatch):
    from app.models.job import Job
    cleanup = importlib.import_module('app.services.voice_clone_cleanup')
    audio = settings.voice_clone_library_dir_path / 'later-project.wav'
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b'project media')
    pending = VoiceCloneDeletionTask(kind='provider', resource_key='provider:old', provider='cosyvoice', target_model='old', voice_id='old')
    session.add(pending)
    session.commit()
    pending_id = pending.id
    plan = cleanup.build_voice_clone_cleanup_plan(session, reset_all=True)
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan.as_dict()))
    cleanup.apply_voice_clone_cleanup_plan(session, plan, None, path, no_backup=True)
    assert session.get(VoiceCloneDeletionTask, pending_id) is not None
    session.add(Job(project_id=project.id, source_url='https://unowned.example/source', source_local_path=str(audio)))
    session.commit()
    def fail(*_args, **_kwargs):
        raise TimeoutError('mocked provider failure')
    monkeypatch.setattr(cleanup, '_delete_provider_voice', fail)
    result = cleanup.drain_voice_clone_deletion_tasks(session)
    assert result.failed_count == 1
    assert session.get(VoiceCloneDeletionTask, pending_id).attempt_count == 1
    assert audio.read_bytes() == b'project media'


@pytest.mark.parametrize('provider,status_code,code', [
    ('qwen', 400, 'BadRequest.VoiceNotFound'),
    ('cosyvoice', 400, 'BadRequest.ResourceNotExist'),
    ('cosyvoice', 416, 'BadRequest.ResourceNotExist'),
])
def test_duplicate_provider_deletion_accepts_only_documented_absence(session, monkeypatch, provider, status_code, code):
    cleanup = importlib.import_module('app.services.voice_clone_cleanup')
    calls = []
    def post(_url, **kwargs):
        calls.append(kwargs['json']['input'])
        return (httpx.Response(200, json={'output': {}}) if len(calls) == 1 else
                httpx.Response(status_code, json={'code': code, 'message': 'exact provider error'}))
    monkeypatch.setattr(httpx, 'post', post)
    for model in ('legacy-a', 'legacy-b'):
        session.add(VoiceCloneDeletionTask(kind='provider', resource_key=f'{provider}:{model}:same-id',
            provider=provider, target_model=model, voice_id='same-id'))
    session.commit()
    result = cleanup.drain_voice_clone_deletion_tasks(session)
    assert result.completed_count == 2
    assert result.failed_count == 0
    assert len(calls) == 2
    assert session.query(VoiceCloneDeletionTask).count() == 0


@pytest.mark.parametrize('provider,status_code,code', [
    ('qwen', 401, 'InvalidApiKey'),
    ('qwen', 403, 'BadRequest.VoiceNotFound'),
    ('qwen', 400, 'InvalidParameter'),
    ('qwen', 400, 'BadRequest.ResourceNotExist'),
    ('qwen', 200, 'BadRequest.VoiceNotFound'),
    ('cosyvoice', 403, 'BadRequest.ResourceNotExist'),
    ('cosyvoice', 200, 'BadRequest.ResourceNotExist'),
    ('cosyvoice', 200, 'InvalidApiKey'),
    ('cosyvoice', 400, 'BadRequest.VoiceNotFound'),
    ('cosyvoice', 500, 'InternalError'),
])
def test_provider_deletion_keeps_auth_and_other_failures_pending(session, monkeypatch, provider, status_code, code):
    cleanup = importlib.import_module('app.services.voice_clone_cleanup')
    # Even a misleading not-found message must not substitute for the exact contract.
    monkeypatch.setattr(httpx, 'post', lambda *_args, **_kwargs: httpx.Response(status_code,
        json={'code': code, 'message': "Voice 'old-id' not found."}))
    task = VoiceCloneDeletionTask(kind='provider', resource_key=f'{provider}:old-id',
        provider=provider, target_model='legacy', voice_id='old-id')
    session.add(task)
    session.commit()
    result = cleanup.drain_voice_clone_deletion_tasks(session)
    assert result.completed_count == 0
    assert result.failed_count == 1
    assert session.query(VoiceCloneDeletionTask).one().attempt_count == 1
    assert task.next_attempt_at is not None


def test_resume_refuses_unqueued_voice_exports(tmp_path, session, monkeypatch):
    from contextlib import nullcontext
    from app.db import session as database
    from scripts import voice_library_maintenance as maintenance
    monkeypatch.setattr(database, 'SessionLocal', lambda: nullcontext(session))
    monkeypatch.setattr(maintenance, 'BACKEND', tmp_path)
    export = settings.voice_clone_library_dir_path / 'identities-export.json'
    export.parent.mkdir(parents=True, exist_ok=True)
    export.write_text('{"old_embedding": [1, 2]}')
    with pytest.raises(RuntimeError, match='assets'):
        maintenance.verify_reset_complete()
    assert export.exists()


def test_real_maintenance_guards_block_multiline_identity_writers(tmp_path, monkeypatch):
    import ast
    import inspect
    from types import SimpleNamespace
    from scripts import voice_library_maintenance as maintenance
    monkeypatch.setattr(maintenance, 'STATE_DIR', tmp_path)
    (tmp_path / 'active').touch(mode=0o600)
    backend = Path(__file__).resolve().parents[1]
    for filename, (name, action) in maintenance.VOICE_GUARDS.items():
        source = (backend / filename).read_text()
        guarded = maintenance.with_voice_guard(source, name, action)
        compile(guarded, filename, 'exec')
        assert maintenance.without_middleware(guarded) == source
        function = next(node for node in ast.parse(guarded).body if isinstance(node, ast.FunctionDef) and node.name == name)
        scope = {'VoiceprintAutoLearningResult': lambda: SimpleNamespace(skipped=True), 'extract_voiceprint_embedding': None}
        exec('from __future__ import annotations\n' + ast.unparse(function), scope)
        kwargs = {name: None for name, param in inspect.signature(scope[name]).parameters.items() if param.default is inspect.Parameter.empty}
        if action.startswith('raise '):
            with pytest.raises(RuntimeError, match='maintenance'):
                scope[name](**kwargs)
        else:
            result = scope[name](**kwargs)
            assert result == {} or result.skipped


def test_reset_preview_never_overwrites_an_existing_output(tmp_path, session):
    import os
    destination = tmp_path / 'source.wav'
    destination.write_bytes(b'protected source')
    command = [sys.executable, 'scripts/cleanup_voice_clone_library.py', '--database-url', str(session.get_bind().url), '--reset-all', '--output-plan', str(destination)]
    env = {**os.environ, 'LOCAL_AUDIO_DIR': str(tmp_path / 'audio'), 'LOCAL_VOICEPRINT_LIBRARY_DIR': str(tmp_path / 'voiceprints'), 'LOCAL_VOICE_CLONE_LIBRARY_DIR': str(tmp_path / 'voice-cloning')}
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    assert result.returncode != 0
    assert destination.read_bytes() == b'protected source'



def test_reset_removes_orphan_generated_audio_but_preserves_other_audio_and_project_sources(tmp_path, session, monkeypatch):
    from contextlib import nullcontext
    from app.models.job import Job
    from scripts import voice_library_maintenance as maintenance
    cleanup = importlib.import_module('app.services.voice_clone_cleanup')
    audio = settings.audio_dir_path
    orphan = audio / 'voice-clone-preview-11111111-1111-1111-1111-111111111111.wav'
    source = audio / 'voice-clone-preview-11111111-1111-1111-1111-111111111111-source.mp3'
    protected = audio / 'voice-clone-preview-22222222-2222-2222-2222-222222222222.wav'
    unrelated = audio / 'subtitle-source.wav'
    document = audio / 'voice-clone-preview-notes.json'
    nested = audio / 'other-workflow' / 'voice-clone-preview-record.wav'
    nested.parent.mkdir()
    for path in (orphan, source, protected, unrelated, document, nested):
        path.write_bytes(b'keep unless owned orphan voice audio')
    session.add(Job(source_url='https://example.test/project-source.wav', source_local_path=str(protected)))
    session.commit()
    monkeypatch.setattr('app.db.session.SessionLocal', lambda: nullcontext(session))
    monkeypatch.setattr(maintenance, 'BACKEND', tmp_path)

    plan = cleanup.build_voice_clone_cleanup_plan(session, reset_all=True)
    assert {target.local_path for target in plan.deletion_targets} == {str(orphan), str(source)}
    with pytest.raises(RuntimeError, match='assets'):
        maintenance.verify_reset_complete()
    plan_path = tmp_path / 'reset-plan.json'
    plan_path.write_text(json.dumps(plan.as_dict()))
    cleanup.apply_voice_clone_cleanup_plan(session, plan, None, plan_path, no_backup=True)
    result = cleanup.drain_voice_clone_deletion_tasks(session)
    assert result.completed_count == 2 and result.failed_count == 0
    assert not orphan.exists() and not source.exists()
    assert all(path.exists() for path in (protected, unrelated, document, nested))
    maintenance.verify_reset_complete()


@pytest.mark.parametrize('variant', ['trusted', 'modified', 'crlf', 'wrong_path'])
def test_pause_accepts_only_exact_known_legacy_identity_helper(tmp_path, monkeypatch, variant):
    from scripts import voice_library_maintenance as maintenance
    backend = tmp_path / 'backend'
    backend.mkdir()
    main_path = backend / 'main.py'
    main_path.write_text('app = None\n')
    env_path = tmp_path / '.env'
    env_path.write_text('VOICE_CLONING_ENABLED=true\n')
    legacy = (Path(__file__).parent / 'fixtures/voice_clone_identity_legacy_20260907.py').read_bytes()
    filename = 'app/services/voiceprint_matching.py' if variant == 'wrong_path' else 'app/services/voice_clone_identity.py'
    target = backend / filename
    target.parent.mkdir(parents=True)
    source = legacy + b'\n# unreviewed change\n' if variant == 'modified' else legacy.replace(b'\n', b'\r\n') if variant == 'crlf' else legacy
    target.write_bytes(source)
    state_dir = tmp_path / 'maintenance-state'
    monkeypatch.setattr(maintenance, 'BACKEND', backend)
    monkeypatch.setattr(maintenance, 'STATE_DIR', state_dir)
    monkeypatch.setattr(maintenance.sys, 'platform', 'linux')
    monkeypatch.setattr(maintenance.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(maintenance, 'stop_backend', lambda _server: None)
    monkeypatch.setattr(maintenance, 'start_backend', lambda _server: None)
    monkeypatch.setattr(maintenance, 'verify_paused', lambda: None)
    monkeypatch.setattr(maintenance.sys, 'argv', ['maintenance', '--server', 'beijing', '--pause'])
    if variant == 'trusted':
        maintenance.main()
        assert (state_dir / 'active').exists()
        assert maintenance.BEGIN in main_path.read_text()
        assert maintenance.enabled_value(env_path.read_text()) is False
    else:
        with pytest.raises(RuntimeError, match='Voice function changed'):
            maintenance.main()
        assert not state_dir.exists()
        assert main_path.read_text() == 'app = None\n'
        assert maintenance.enabled_value(env_path.read_text()) is True
    assert target.read_bytes() == source
