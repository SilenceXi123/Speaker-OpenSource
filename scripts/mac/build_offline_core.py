#!/usr/bin/env python3
"""Build the client-only subtitle worker without copying backend data or secrets."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]


def source_digest() -> str:
    digest = hashlib.sha256()
    for path in sorted((ROOT / 'backend/app').rglob('*.py')) + [ROOT / 'backend/scripts/speaker_offline.py']:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def verify_worker(directory: Path, temporary: Path) -> None:
    """Exercise frozen imports and dictionary/XML resources before replacing a usable bundle."""
    def call(payload: dict) -> dict:
        process = subprocess.run([str(directory / 'speaker-offline')], input=json.dumps(payload),
                                 text=True, capture_output=True, timeout=90, cwd=temporary)
        try:
            response = json.loads(process.stdout)
        except ValueError as error:
            raise RuntimeError('Packaged offline worker returned invalid JSON') from error
        if process.returncode or response.get('ok') is not True:
            raise RuntimeError(f"Packaged offline worker verification failed: {response.get('error')}")
        return response['data']
    # This is an explicit contract fixture, not a claimed speech-recognition result.
    job = call({'operation': 'build', 'job': {
        'id': 'local-00000000-0000-4000-8000-000000000001', 'project_id': 'package-verification',
        'source_local_path': '/speaker-offline-package-fixture.wav', 'export_frame_rate': '25',
        'kind': 'transcription', 'subtitle_length_mode': 'standard', 'subtitle_max_chars': 20,
    }, 'recognition': {'engine': 'apple_speech', 'locale': 'zh_CN', 'system_version': '26.0',
        'duration_ms': 2000, 'sample_rate_hz': 24000, 'channel_count': 1,
        'segments': [{'text': '离线字幕测试。', 'start_ms': 0, 'end_ms': 2000, 'words': [
            {'text': '离线字幕', 'start_ms': 0, 'end_ms': 1000},
            {'text': '测试。', 'start_ms': 1000, 'end_ms': 2000}]}]}})
    if not job.get('local_cues'):
        raise RuntimeError('Packaged worker produced no subtitle cues')
    for fmt in ('srt', 'vtt', 'xml'):
        result = call({'operation': 'export', 'job': job, 'format': fmt, 'variant': 'clean'})
        content = base64.b64decode(result['content_base64'], validate=True)
        if not content:
            raise RuntimeError(f'Packaged {fmt} export is empty')
        if fmt == 'xml':
            ET.fromstring(content)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', required=True, help='Python environment containing the subtitle dependencies')
    parser.add_argument('--pyinstaller-path', default='/private/tmp/speaker-pyinstaller')
    args = parser.parse_args()
    python = str(Path(args.python).resolve())
    architecture = subprocess.check_output([args.python, '-c', 'import platform; print(platform.machine())'], text=True).strip()
    if architecture not in ('arm64', 'x86_64'):
        raise SystemExit(f'Unsupported build architecture: {architecture}')
    entry = ROOT / 'backend/scripts/speaker_offline.py'
    if not entry.is_file():
        raise SystemExit(f'Offline worker entry does not exist: {entry}')
    initial_digest = source_digest()
    # Build with the supplied venv entry point; resolving its symlink would bypass its dependencies.
    env = {**os.environ, 'PYTHONPATH': args.pyinstaller_path, 'PYTHONDONTWRITEBYTECODE': '1'}
    with tempfile.TemporaryDirectory(prefix='speaker-offline-build-') as temporary:
        build = Path(temporary)
        env['PYINSTALLER_CONFIG_DIR'] = str(build / 'cache')
        hook = build / 'network_disabled.py'
        hook.write_text('''import socket
def _offline_only(*args, **kwargs):
    raise OSError("Speaker offline subtitle worker does not allow network access")
class _OfflineSocket(socket.socket):
    connect = _offline_only
    connect_ex = _offline_only
    sendto = _offline_only
socket.socket = _OfflineSocket
socket.create_connection = _offline_only
socket.getaddrinfo = _offline_only
''')
        subprocess.run([
            args.python, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
            '--name', 'speaker-offline', '--target-architecture', architecture,
            '--paths', str(ROOT / 'backend'),
            '--collect-data', 'jieba', '--collect-data', 'opencc', '--collect-data', 'pypinyin',
            '--runtime-hook', str(hook),
            '--specpath', str(build), '--workpath', str(build / 'work'),
            '--distpath', str(build / 'dist'), str(entry),
        ], check=True, env=env, cwd=ROOT)
        result = build / 'dist/speaker-offline'
        forbidden = [path.relative_to(result).as_posix() for path in result.rglob('*')
                     if path.is_file() and (path.name == '.env' or path.name.startswith('.env.')
                                           or path.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.wav', '.mp3', '.m4a', '.mp4'})]
        if forbidden:
            raise SystemExit(f'Unexpected private data or media in worker bundle: {forbidden}')
        verify_worker(result, build)
        if source_digest() != initial_digest:
            raise SystemExit('Backend source changed during packaging; rebuild to produce a consistent worker')
        (result / 'runtime.json').write_text(json.dumps({
            'architectures': ['aarch64' if architecture == 'arm64' else architecture],
            'python_version': subprocess.check_output([args.python, '-c', 'import platform; print(platform.python_version())'], text=True).strip(),
            'network_access': False,
            'entry': 'speaker-offline',
            'source_digest': initial_digest,
            'verified_exports': ['srt', 'vtt', 'xml'],
        }, indent=2) + '\n')
        destination = ROOT / 'src-tauri/resources/offline-core'
        staging = destination.with_name('offline-core.building')
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(result, staging, symlinks=True)
        if destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)
        print(json.dumps({'directory': str(destination), 'architecture': architecture, 'python': python}))


if __name__ == '__main__':
    main()
