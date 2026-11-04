#!/usr/bin/env python3
"""One stdin/stdout JSON command; this process cannot use network, DB or .env files."""
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys


def _offline_guard(event, args):
    if event in {'socket.__new__', 'socket.connect', 'socket.getaddrinfo', 'sqlite3.connect', 'subprocess.Popen'}:
        raise PermissionError('离线字幕进程禁止网络、数据库与外部进程')
    if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.fsdecode(args[0]).rsplit('/', 1)[-1].startswith('.env'):
            raise PermissionError('离线字幕进程禁止读取服务端环境文件')
    if event == 'import' and args and args[0] in {'app.core.config', 'app.db.session', 'app.services.deepseek_subtitle_client'}:
        raise PermissionError('离线字幕进程禁止载入服务端配置、数据库或云端客户端')


def main() -> int:
    sys.addaudithook(_offline_guard)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        source = sys.stdin.buffer.read(128 * 1024 * 1024 + 1)
        if len(source) > 128 * 1024 * 1024:
            raise ValueError('本机字幕请求过大')
        payload = json.loads(source, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'无效数字 {value}')))
        with redirect_stdout(sys.stderr):
            from app.services.local_subtitles import handle_local_subtitle_command
            data = handle_local_subtitle_command(payload)
        result = {'ok': True, 'data': data}
        code = 0
    except Exception as error:
        result = {'ok': False, 'error': {'code': 'invalid_local_subtitle_request', 'message': str(error)}}
        code = 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
