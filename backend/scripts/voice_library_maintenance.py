"""Explicit administrator maintenance for the documented Beijing/Shanghai backends."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import urlopen

BACKEND = Path('/www/wwwroot/speaker/backend_app/backend')
STATE_DIR = BACKEND.parent / '.voice-library-maintenance'
# Verified Beijing/Shanghai legacy file: hash/name helpers only, no identity writer.
LEGACY_IDENTITY_HELPER_SHA256 = '479b5d1b5157315a2b6eb0cfcff545dd553a3b1b969b2254aa59d5092e00c85a'
VOICE_GUARDS = {
    'app/services/voiceprint_auto_learning.py': ('learn_voiceprint_candidates_for_job', 'return VoiceprintAutoLearningResult()'),
    'app/services/voiceprint_matching.py': ('match_job_voiceprints', 'return {}'),
    'app/services/voice_clone_identity.py': ('resolve_voice_clone_identity', "raise RuntimeError('Voice library maintenance is active')"),
    'app/services/voice_clone_identity_review.py': ('resolve_identity_review', "raise RuntimeError('Voice library maintenance is active')"),
    'scripts/backfill_voice_clone_identities.py': ('run_backfill', "raise RuntimeError('Voice library maintenance is active')"),
}

BEGIN = '# BEGIN SPEAKER VOICE LIBRARY MAINTENANCE'
END = '# END SPEAKER VOICE LIBRARY MAINTENANCE'
MIDDLEWARE = '''
# BEGIN SPEAKER VOICE LIBRARY MAINTENANCE
import re as _voice_maintenance_re
from pathlib import Path as _VoiceMaintenancePath
from fastapi.responses import JSONResponse as _VoiceMaintenanceResponse

if _VoiceMaintenancePath('/www/wwwroot/speaker/backend_app/.voice-library-maintenance/active').is_file():
    def start_storage_maintenance_worker(*args, **kwargs):
        return False

@app.middleware('http')
async def _speaker_voice_library_maintenance(request, call_next):
    if (_VoiceMaintenancePath('/www/wwwroot/speaker/backend_app/.voice-library-maintenance/active').is_file()
            and request.method not in {'GET', 'HEAD', 'OPTIONS'}
            and _voice_maintenance_re.fullmatch(
                r'/api/(?:v1|v2/projects/[^/]+)/voiceprints(?:/.*)?|/api/v1/admin/voice-clone-identities(?:/.*)?', request.url.path)):
        return _VoiceMaintenanceResponse({'detail': '声纹库正在维护，请稍后再试'}, status_code=503,
                                         headers={'Access-Control-Allow-Origin': '*'})
    if ((_VoiceMaintenancePath('/www/wwwroot/speaker/backend_app/.voice-library-maintenance/active').is_file()
            or not settings.voice_cloning_enabled) and request.method != 'OPTIONS'
            and _voice_maintenance_re.fullmatch(
                r'/api/(?:v1|v2/projects/[^/]+)/voice-cloning(?:/.*)?', request.url.path)):
        message = '声音复刻正在升级维护，请更新客户端后再使用'
        headers = {'Access-Control-Allow-Origin': '*'}
        if request.url.path.endswith('/provider-status') and request.method == 'GET':
            return _VoiceMaintenanceResponse({
                'enabled': False, 'available': False, 'message': message,
                'provider': 'cosyvoice', 'region': settings.voice_cloning_region,
                'target_model': 'cosyvoice-v3.5-plus', 'providers': [], 'models': [],
            }, headers=headers)
        return _VoiceMaintenanceResponse({'detail': message}, status_code=503, headers=headers)
    return await call_next(request)
# END SPEAKER VOICE LIBRARY MAINTENANCE
'''


def enabled_value(content: str) -> bool | None:
    values = re.findall(r'^\s*VOICE_CLONING_ENABLED\s*=\s*[\"\']?(true|false|1|0)[\"\']?\s*(?:#.*)?$', content, re.M | re.I)
    assignments = re.findall(r'^\s*VOICE_CLONING_ENABLED\s*=', content, re.M | re.I)
    if len(values) != len(assignments) or len(values) > 1:
        raise RuntimeError('VOICE_CLONING_ENABLED must be one unambiguous boolean')
    return values[0].lower() in {'true', '1'} if values else None


def with_enabled(content: str, enabled: bool | None) -> str:
    pattern = r'^\s*VOICE_CLONING_ENABLED\s*=.*(?:\n|$)'
    replacement = '' if enabled is None else f'VOICE_CLONING_ENABLED={str(enabled).lower()}\n'
    if re.search(pattern, content, re.M | re.I):
        return re.sub(pattern, replacement, content, flags=re.M | re.I)
    return content.rstrip('\n') + '\n' + replacement


def without_middleware(content: str) -> str:
    if BEGIN not in content and END not in content:
        return content
    if content.count(BEGIN) != 1 or content.count(END) != 1:
        raise RuntimeError('Unexpected maintenance markers; manual review required')
    return re.sub(r'^[ \t]*' + re.escape(BEGIN) + r'.*?^[ \t]*' + re.escape(END) + r'\n?', '', content, flags=re.S | re.M)


def with_voice_guard(content: str, function: str, action: str) -> str:
    content = without_middleware(content)
    matches = [node for node in ast.parse(content).body
               if isinstance(node, ast.FunctionDef) and node.name == function]
    if len(matches) != 1:
        raise RuntimeError('Voice function changed; maintenance patch requires review')
    node = matches[0]
    first = node.body[0]
    # Keep an existing docstring as the first statement.
    line = first.end_lineno if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str) else first.lineno - 1
    guard = (f'    {BEGIN}\n'
             f'    from pathlib import Path as _VoiceMaintenancePath\n'
             f'    if _VoiceMaintenancePath({str(STATE_DIR / "active")!r}).is_file():\n'
             f'        {action}\n'
             f'    {END}\n')
    lines = content.splitlines(keepends=True)
    lines.insert(line, guard)
    return ''.join(lines)


def atomic_write(path: Path, content: str) -> None:
    stat = path.stat()
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        staged = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.chmod(staged, stat.st_mode & 0o777)
            os.chown(staged, stat.st_uid, stat.st_gid)
            os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)


def shanghai_pids() -> list[int]:
    result = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / 'cmdline').read_bytes().split(b'\0')
            if (b'uvicorn' in command and b'main:app' in command and b'8787' in command
                    and (entry / 'cwd').resolve() == BACKEND
                    and entry.stat().st_uid == pwd.getpwnam('www').pw_uid):
                result.append(int(entry.name))
        except (OSError, RuntimeError):
            continue
    return result


def stop_backend(server: str) -> None:
    if server == 'beijing':
        subprocess.run(['systemctl', 'stop', 'speaker-backend.service'], check=True)
        return
    pids = shanghai_pids()
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 45
    while shanghai_pids():
        if time.monotonic() > deadline:
            raise RuntimeError('Shanghai backend did not stop; no SIGKILL or reset performed')
        time.sleep(0.5)


def start_backend(server: str) -> None:
    if server == 'beijing':
        subprocess.run(['systemctl', 'start', 'speaker-backend.service'], check=True)
    elif not shanghai_pids():
        account = pwd.getpwnam('www')
        # The existing launcher loads Baota's environment and updates backend.pid.
        subprocess.run(
            ['/bin/bash', '/www/server/python_project/vhost/scripts/backend_cmd.sh'],
            cwd=BACKEND, stdin=subprocess.DEVNULL, check=True, timeout=10,
            env={**os.environ, 'HOME': account.pw_dir},
            user=account.pw_uid, group=account.pw_gid, extra_groups=[], start_new_session=True,
        )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            with urlopen('http://127.0.0.1:8787/api/v1/health', timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, HTTPError):
            time.sleep(0.5)
    raise RuntimeError('Backend health did not recover; inspect service logs')


def verify_paused() -> None:
    if enabled_value((BACKEND.parent / '.env').read_text()) is not False:
        raise RuntimeError('VOICE_CLONING_ENABLED is not explicitly false')
    if not (STATE_DIR / 'active').is_file():
        raise RuntimeError('Voiceprint maintenance marker is absent')
    with urlopen('http://127.0.0.1:8787/api/v1/voice-cloning/provider-status', timeout=5) as response:
        status = json.load(response)
    if status.get('available') is not False or status.get('enabled') is not False:
        raise RuntimeError('Voice cloning is not paused')
    for path in ('/api/v1/voice-cloning/samples', '/api/v2/projects/_maintenance_probe_/voice-cloning/samples'):
        try:
            urlopen('http://127.0.0.1:8787' + path, timeout=5).close()
        except HTTPError as error:
            if error.code == 503:
                continue
        raise RuntimeError('Voice clone routes are not fully paused')


def perform_reset() -> None:
    # This worker runs only while the parent has stopped the backend.
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 8787)) == 0:
            raise RuntimeError('Backend still listens on 8787; reset refused')
    sys.path.insert(0, str(BACKEND))
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.voice_clone_cleanup import build_voice_clone_cleanup_plan, apply_voice_clone_cleanup_plan
    if settings.voice_cloning_enabled:
        raise RuntimeError('Configured voice cloning is still enabled')
    if not (STATE_DIR / 'active').is_file():
        raise RuntimeError('Maintenance marker is absent; reset refused')
    with SessionLocal() as session:
        plan = build_voice_clone_cleanup_plan(session, reset_all=True)
        with tempfile.TemporaryDirectory(prefix='speaker-voice-reset-') as temporary:
            path = Path(temporary) / 'plan.json'
            path.write_text(json.dumps(plan.as_dict()), encoding='utf-8')
            apply_voice_clone_cleanup_plan(session, plan, backup_path=None, plan_path=path, no_backup=True)
        print(json.dumps({'mode': 'reset', 'removed_rows': dict(plan.reset_table_counts)}, ensure_ascii=False))


def retry_deletions() -> None:
    sys.path.insert(0, str(BACKEND))
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.models.voice_clone_deletion_task import VoiceCloneDeletionTask
    from app.services.voice_clone_cleanup import drain_voice_clone_deletion_tasks
    if settings.voice_cloning_enabled:
        raise RuntimeError('Voice cloning must stay paused until deletion finishes')
    with SessionLocal() as session:
        totals = {'attempted': 0, 'completed': 0, 'failed': 0}
        cutoff = datetime.now(timezone.utc)
        while True:
            batch = drain_voice_clone_deletion_tasks(session, limit=100, now=cutoff)
            totals['attempted'] += batch.attempted_count
            totals['completed'] += batch.completed_count
            totals['failed'] += batch.failed_count
            if batch.attempted_count < 100:
                break
        pending = session.query(VoiceCloneDeletionTask).count()
        print(json.dumps({'mode': 'asset-deletions', 'asset_deletions': totals, 'pending_asset_deletions': pending}, ensure_ascii=False))
        if pending:
            raise RuntimeError('Some assets are pending deletion; retry after next_attempt_at')


def verify_reset_complete() -> None:
    sys.path.insert(0, str(BACKEND))
    from app.db.session import SessionLocal
    from app.services.voice_clone_cleanup import _RESET_MODELS, build_voice_clone_cleanup_plan
    from app.models.voice_clone_deletion_task import VoiceCloneDeletionTask
    with SessionLocal() as session:
        if any(session.query(model).count() for model in (*_RESET_MODELS, VoiceCloneDeletionTask)):
            raise RuntimeError('Voice rows or pending deletions remain; resume refused')
        if build_voice_clone_cleanup_plan(session, reset_all=True).deletion_targets:
            raise RuntimeError('Unqueued voice assets remain; resume refused')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True, choices=('beijing', 'shanghai'))
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--pause', action='store_true')
    action.add_argument('--reset-all', dest='reset', action='store_true')
    parser.add_argument('--no-backup', action='store_true', help='Required for reset: no database or voice media backup')
    action.add_argument('--resume', action='store_true')
    action.add_argument('--retry-deletions', action='store_true')
    action.add_argument('--reset-worker', action='store_true', help=argparse.SUPPRESS)
    action.add_argument('--deletion-worker', action='store_true', help=argparse.SUPPRESS)
    action.add_argument('--resume-check', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if bool(args.reset or args.reset_worker) != args.no_backup:
        parser.error('--reset-all requires --no-backup; other actions must omit it')
    if sys.platform != 'linux' or os.geteuid() != 0 or not BACKEND.is_dir():
        parser.error('Run on the selected documented server through interactive sudo')
    if args.reset_worker:
        perform_reset()
        return
    if args.deletion_worker:
        retry_deletions()
        return
    if args.resume_check:
        verify_reset_complete()
        return
    env_path, main_path = BACKEND.parent / '.env', BACKEND / 'main.py'
    if args.pause:
        original_env = env_path.read_text()
        original_main = main_path.read_text()
        enabled = enabled_value(original_env)
        patched_sources = {}
        for filename, (function, result) in VOICE_GUARDS.items():
            path = BACKEND / filename
            if not path.exists() and not path.is_symlink():
                continue  # Older deployments do not yet have every writer.
            original = path.read_text()
            if (filename == 'app/services/voice_clone_identity.py'
                    and hashlib.sha256(path.read_bytes()).hexdigest() == LEGACY_IDENTITY_HELPER_SHA256):
                continue
            patched = with_voice_guard(original, function, result)
            compile(patched, str(path), 'exec')
            patched_sources[path] = (original, patched)
        fresh_pause = not (STATE_DIR / 'active').exists()
        if not STATE_DIR.exists():
            STATE_DIR.mkdir(mode=0o711)
        if STATE_DIR.is_symlink() or STATE_DIR.stat().st_uid != os.getuid():
            raise RuntimeError('Maintenance state must be a directory owned by the administrator')
        STATE_DIR.chmod(0o711)
        for path in STATE_DIR.iterdir():
            if path.is_symlink() or not path.is_file():
                raise RuntimeError('Unexpected maintenance state entry')
            path.chmod(0o600)
        if fresh_pause:
            contents = {
                'state.json': json.dumps({'previous_enabled': enabled}),
                'main-before.py': original_main,
                **{path.name: original for path, (original, _patched) in patched_sources.items()},
            }
            for filename, content in contents.items():
                path = STATE_DIR / filename
                if path.exists():
                    atomic_write(path, content)
                else:
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                        handle.write(content)
        (STATE_DIR / 'active').touch(mode=0o600)
        patched_main = without_middleware(original_main).rstrip() + '\n' + MIDDLEWARE
        compile(patched_main, str(main_path), 'exec')
        atomic_write(main_path, patched_main)
        for path, (_original, patched) in patched_sources.items():
            atomic_write(path, patched)
        atomic_write(env_path, with_enabled(original_env, False))
        stop_backend(args.server)
        start_backend(args.server)
        verify_paused()
        print(json.dumps({'mode': 'pause', 'voice_cloning_enabled': False, 'voice_routes_status': 503}))
    elif args.resume:
        state = json.loads((STATE_DIR / 'state.json').read_text())
        if not state.get('reset_applied'):
            raise RuntimeError('No completed reset recorded; resume refused')
        stop_backend(args.server)
        try:
            subprocess.run([str(BACKEND / '.venv/bin/python'), str(Path(__file__).resolve()), '--server', args.server, '--resume-check'], cwd=BACKEND, check=True)
        except Exception:
            start_backend(args.server)
            raise
        atomic_write(main_path, without_middleware(main_path.read_text()))
        for filename in VOICE_GUARDS:
            path = BACKEND / filename
            if not path.exists() and not path.is_symlink():
                continue
            atomic_write(path, without_middleware(path.read_text()))
        atomic_write(env_path, with_enabled(env_path.read_text(), state['previous_enabled']))
        (STATE_DIR / 'active').unlink()
        start_backend(args.server)
        print(json.dumps({'mode': 'resume', 'restored_enabled': state['previous_enabled']}))
    elif args.retry_deletions:
        verify_paused()
        subprocess.run([str(BACKEND / '.venv/bin/python'), str(Path(__file__).resolve()), '--server', args.server, '--deletion-worker'], cwd=BACKEND, check=True)
    else:
        verify_paused()
        stop_backend(args.server)
        try:
            subprocess.run([str(BACKEND / '.venv/bin/python'), str(Path(__file__).resolve()), '--server', args.server, '--reset-worker', '--no-backup'], cwd=BACKEND, check=True)
            state_path = STATE_DIR / 'state.json'
            state = json.loads(state_path.read_text())
            state['reset_applied'] = True
            atomic_write(state_path, json.dumps(state))
        finally:
            start_backend(args.server)
        verify_paused()
        subprocess.run([str(BACKEND / '.venv/bin/python'), str(Path(__file__).resolve()), '--server', args.server, '--deletion-worker'], cwd=BACKEND, check=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Provider exceptions and subprocess output can contain sensitive values.
        print(f'Maintenance stopped: {type(error).__name__}. Inspect the aggregate result and server logs.', file=sys.stderr)
        raise SystemExit(1)
