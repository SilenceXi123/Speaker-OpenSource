from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import quote

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.subtitle_interchange import SrtConversionError, convert_srt_bytes_to_premiere_xml


TERMINAL_STATUSES = {'completed', 'completed_with_warnings', 'failed', 'aborted'}


class CliError(Exception):
    def __init__(self, code: str, message: str, *, details: Any = None, exit_code: int = 2) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.exit_code = exit_code


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliError('invalid_arguments', message)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog='speaker-cli', description='Speaker 2.0 JSON CLI')
    parser.add_argument(
        '--api-base',
        default=os.getenv('SPEAKER_API_BASE', 'http://127.0.0.1:18787'),
        help='Speaker 后端地址；也可使用 SPEAKER_API_BASE',
    )
    parser.add_argument('--request-timeout', type=float, default=120, help='单次 HTTP 请求超时秒数（auto-split 自动抬升到至少 1800s）')
    groups = parser.add_subparsers(dest='group', required=True)

    project = groups.add_parser('project', help='项目操作')
    project_commands = project.add_subparsers(dest='command', required=True)
    project_list = project_commands.add_parser('list', help='列出项目')
    project_list.add_argument('--include-archived', action='store_true')
    project_create = project_commands.add_parser('create', help='创建项目')
    project_create.add_argument('--name', required=True)
    project_create.add_argument('--owner')
    project_create.add_argument('--language', default='zh')
    project_create.add_argument('--frame-rate', choices=('25', '29.97', '50'), default='25')

    task = groups.add_parser('task', help='任务操作')
    task_commands = task.add_subparsers(dest='command', required=True)
    task_list = task_commands.add_parser('list', help='列出项目任务')
    task_list.add_argument('--project-id', required=True)
    task_status = task_commands.add_parser('status', help='查询任务')
    _add_task_identity(task_status)
    task_wait = task_commands.add_parser('wait', help='同步并等待任务完成')
    _add_task_identity(task_wait)
    _add_wait_options(task_wait)

    task_create = task_commands.add_parser('create', help='上传音频并创建任务')
    task_create.add_argument('--project-id', required=True)
    task_create.add_argument('--file', required=True)
    task_create.add_argument('--kind', choices=('subtitle', 'transcript'), default='subtitle')
    task_create.add_argument('--script', action='append', default=[], help='台本/唱词文件，可重复传入')
    task_create.add_argument('--lyrics-correction', action='store_true')
    task_create.add_argument('--no-project-scripts', action='store_true')
    task_create.add_argument('--language-hints')
    task_create.add_argument('--speaker-count', type=int)
    task_create.add_argument('--vocabulary-id')
    task_create.add_argument('--frame-rate', choices=('25', '29.97', '50'), default='25')
    task_create.add_argument('--subtitle-length', choices=('smart', 'strict'), default='smart')
    task_create.add_argument('--max-chars', type=int, default=14)
    task_create.add_argument('--wait', action='store_true')
    _add_wait_options(task_create)
    task_create.add_argument('--auto-split', action='store_true', help='本地按静音/2 小时切分后批量提交，支持超过 2GB 的原始文件')
    task_create.add_argument('--chunk-seconds', type=float, default=7200)
    task_create.add_argument('--overlap-seconds', type=float, default=8)
    task_create.add_argument('--silence-db', type=float, default=-35)
    task_create.add_argument('--min-silence-seconds', type=float, default=1.5)

    subtitle = groups.add_parser('subtitle', help='字幕文件操作')
    subtitle_commands = subtitle.add_subparsers(dest='command', required=True)
    srt_to_xml = subtitle_commands.add_parser('srt-to-xml', help='离线将 SRT 转为 Premiere 图形字幕 XML')
    srt_to_xml.add_argument('--input', required=True)
    srt_to_xml.add_argument('--output', required=True)
    srt_to_xml.add_argument('--frame-rate', choices=('25', '29.97', '50'), default='25')
    srt_to_xml.add_argument('--force', action='store_true')
    return parser


def _add_task_identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--job-id', required=True)


def _add_wait_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--interval', type=float, default=3, help='同步间隔秒数')
    parser.add_argument('--timeout', type=float, default=3600, help='总等待秒数，0 表示不限制')


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, path, **kwargs)
    if response.status_code == 204:
        return None
    try:
        payload = response.json()
    except ValueError:
        payload = {'body': response.text}
    if response.is_error:
        message = payload.get('detail') if isinstance(payload, dict) else None
        raise CliError(
            f'http_{response.status_code}',
            str(message or f'Speaker API 返回 HTTP {response.status_code}'),
            details={'status': response.status_code, 'response': payload},
        )
    return payload


def _normalize_api_base(value: str) -> str:
    base = str(value or '').strip().rstrip('/')
    for suffix in ('/api/v1', '/api/v2'):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    if not base.startswith(('http://', 'https://')):
        raise CliError('invalid_api_base', 'API 地址必须以 http:// 或 https:// 开头')
    return base


def _existing_file(raw_path: str, label: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise CliError('file_not_found', f'{label}不存在或不是文件：{path}')
    return path


def _assert_distinct_files(input_path: Path, output_path: Path) -> None:
    if output_path == input_path:
        raise CliError('invalid_arguments', '输入 SRT 和输出 XML 不能是同一个文件')
    try:
        output_path.stat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise CliError(
            'invalid_arguments',
            f'无法确认输入 SRT 和输出 XML 是否为同一个文件：{error}',
            details={'input_path': str(input_path), 'output_path': str(output_path)},
        ) from error
    try:
        same_file = output_path.samefile(input_path)
    except OSError as error:
        raise CliError(
            'invalid_arguments',
            f'无法确认输入 SRT 和输出 XML 是否为同一个文件：{error}',
            details={'input_path': str(input_path), 'output_path': str(output_path)},
        ) from error
    if same_file:
        raise CliError('invalid_arguments', '输入 SRT 和输出 XML 不能是同一个文件')


def _assert_project(job: dict[str, Any], project_id: str) -> dict[str, Any]:
    actual = str(job.get('project_id') or '')
    if actual != project_id:
        raise CliError(
            'project_mismatch',
            '任务不属于指定项目，已停止操作',
            details={'expected_project_id': project_id, 'actual_project_id': actual, 'job_id': job.get('id')},
        )
    return job


def _path_id(value: str) -> str:
    return quote(str(value), safe='')


def _wait_for_job(client: httpx.Client, project_id: str, job_id: str, interval: float, timeout: float) -> dict[str, Any]:
    if interval < 0 or timeout < 0:
        raise CliError('invalid_arguments', 'interval 和 timeout 不能小于 0')
    started_at = time.monotonic()
    while True:
        job = _assert_project(
            _request(client, 'POST', f'/api/v1/transcriptions/jobs/{_path_id(job_id)}/sync'),
            project_id,
        )
        status = str(job.get('status') or '').lower()
        if status in TERMINAL_STATUSES:
            if status in {'failed', 'aborted'}:
                raise CliError(
                    'job_failed',
                    str(job.get('error_message') or job.get('processing_message') or f'任务状态为 {status}'),
                    details=job,
                    exit_code=3,
                )
            return job
        if timeout and time.monotonic() - started_at >= timeout:
            raise CliError('wait_timeout', f'等待任务超过 {timeout:g} 秒', details=job, exit_code=4)
        time.sleep(interval)


def _create_task(client: httpx.Client, args: argparse.Namespace) -> Any:
    media_path = _existing_file(args.file, '音频')
    script_paths = [_existing_file(path, '台本/唱词') for path in args.script]
    job_kind = 'transcript' if args.kind == 'transcript' else 'transcription'
    if job_kind == 'transcript' and (script_paths or args.lyrics_correction):
        raise CliError('invalid_arguments', 'script 和 lyrics-correction 仅用于字幕任务')
    if args.auto_split:
        if args.overlap_seconds < 0 or args.chunk_seconds <= args.overlap_seconds:
            raise CliError('invalid_arguments', 'chunk-seconds 必须大于 overlap-seconds（均非负）')
        if args.silence_db >= 0:
            raise CliError('invalid_arguments', 'silence-db 必须是负数（dB）')
    data: dict[str, str] = {
        'job_kind': job_kind,
        'export_frame_rate': args.frame_rate,
        'subtitle_strategy': 'standard' if job_kind == 'transcript' else 'evidence',
    }
    if args.language_hints:
        data['language_hints'] = args.language_hints
    if args.speaker_count is not None:
        data['speaker_count'] = str(args.speaker_count)
    if args.vocabulary_id:
        data['vocabulary_id'] = args.vocabulary_id
    if job_kind == 'transcription':
        data.update(
            use_project_scripts=str(not args.no_project_scripts).lower(),
            lyrics_correction_mode=str(args.lyrics_correction).lower(),
            subtitle_length_mode=args.subtitle_length,
        )
        if args.subtitle_length == 'strict':
            data['subtitle_max_chars'] = str(args.max_chars)
    split = None
    try:
        with ExitStack() as stack:
            if args.auto_split:
                split = _split_for_transcription(media_path, args)
                if split['skipped']:
                    return {'skipped': split['skipped']}
                data['chunk_manifest'] = json.dumps(split['manifest'], ensure_ascii=False)
                files: list[tuple[str, tuple[str, Any, str]]] = [
                    (
                        'chunk_files',
                        (
                            chunk_path.name,
                            stack.enter_context(chunk_path.open('rb')),
                            'audio/wav',
                        ),
                    )
                    for chunk_path in split['paths']
                ]
            else:
                files = [
                    (
                        'file',
                        (
                            media_path.name,
                            stack.enter_context(media_path.open('rb')),
                            mimetypes.guess_type(media_path.name)[0] or 'application/octet-stream',
                        ),
                    ),
                ]
            for script_path in script_paths:
                files.append(
                    (
                        'script_files',
                        (
                            script_path.name,
                            stack.enter_context(script_path.open('rb')),
                            mimetypes.guess_type(script_path.name)[0] or 'application/octet-stream',
                        ),
                    ),
                )
            result = _request(
                client,
                'POST',
                f'/api/v2/projects/{_path_id(args.project_id)}/transcriptions/media-jobs',
                data=data,
                files=files,
            )
        if split is not None and split['skipped'] is None:
            result = {**result, 'chunks': split['manifest']}
    finally:
        if split is not None and split['skipped'] is None:
            shutil.rmtree(split['temp_dir'], ignore_errors=True)
    job = result.get('job', result)
    _assert_project(job, args.project_id)
    if args.wait:
        return _wait_for_job(client, args.project_id, str(job['id']), args.interval, args.timeout)
    return result


def plan_chunks(
    duration_seconds: float,
    silence_midpoints: list[float],
    max_seconds: float = 7200,
    overlap_seconds: float = 8,
    snap_window_seconds: float = 120,
) -> list[dict[str, Any]]:
    if duration_seconds <= 0 or max_seconds <= overlap_seconds:
        return []
    bounds = [0.0]
    while bounds[-1] < duration_seconds:
        target = min(bounds[-1] + max_seconds - overlap_seconds, duration_seconds)
        if target < duration_seconds:
            # 只吸附当前边界之后的静音中点，避免吸附回已有边界导致死循环
            candidates = [
                midpoint
                for midpoint in silence_midpoints
                if bounds[-1] < midpoint <= target and midpoint >= target - snap_window_seconds
            ]
            if candidates:
                target = max(candidates)
        bounds.append(target)
    chunks: list[dict[str, Any]] = []
    for index, start in enumerate(bounds[:-1]):
        end = bounds[index + 1]
        chunks.append({
            'offset_ms': int(round(start * 1000)),
            'duration_seconds': round(min(end + overlap_seconds, duration_seconds) - start, 3),
        })
    return chunks


def _media_duration_seconds(media_path: Path) -> float:
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        raise CliError('ffprobe_missing', '--auto-split 需要本机 ffprobe')
    result = subprocess.run(
        [ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(media_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CliError('probe_failed', (result.stderr or result.stdout).strip()[-300:])
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise CliError('probe_failed', f'无法解析媒体时长：{result.stdout.strip()[:100]}')


def _scan_audio(media_path: Path, ffmpeg: str, silence_db: float, min_silence_seconds: float) -> tuple[Optional[float], list[float]]:
    result = subprocess.run(
        [
            ffmpeg,
            '-i',
            str(media_path),
            '-af',
            f'volumedetect,silencedetect=noise={silence_db:g}dB:d={min_silence_seconds:g}',
            '-f',
            'null',
            '-',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CliError('audio_scan_failed', (result.stderr or '').strip()[-500:])
    max_volume_db: Optional[float] = None
    midpoints: list[float] = []
    silence_start: Optional[float] = None
    for line in result.stderr.splitlines():
        stripped = line.strip()
        if 'max_volume:' in stripped:
            value = stripped.split('max_volume:', 1)[1].split('dB', 1)[0].strip()
            try:
                max_volume_db = float(value)
            except ValueError:
                pass
        if 'silence_start:' in stripped:
            try:
                silence_start = float(stripped.split('silence_start:', 1)[1].strip())
            except ValueError:
                silence_start = None
        if 'silence_end:' in stripped and silence_start is not None:
            try:
                silence_end = float(stripped.split('silence_end:', 1)[1].split('|', 1)[0].strip())
                if silence_end > silence_start:
                    midpoints.append((silence_start + silence_end) / 2)
            except ValueError:
                pass
            silence_start = None
    return max_volume_db, sorted(midpoints)


def _cut_chunks(media_path: Path, chunks: list[dict[str, Any]], out_dir: Path, ffmpeg: str) -> list[Path]:
    paths: list[Path] = []
    for index, chunk in enumerate(chunks):
        output_path = out_dir / f'chunk-{index:03d}.wav'
        command = [
            ffmpeg,
            '-y',
            '-ss',
            f"{chunk['offset_ms'] / 1000:.3f}",
            '-i',
            str(media_path),
            '-t',
            f"{chunk['duration_seconds']:.3f}",
            '-vn',
            '-acodec',
            'pcm_s16le',
            '-ar',
            '16000',
            '-ac',
            '1',
            str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0 or not output_path.exists():
            raise CliError('chunk_cut_failed', (result.stderr or '').strip()[-500:])
        paths.append(output_path)
    return paths


def _split_for_transcription(media_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise CliError('ffmpeg_missing', '--auto-split 需要本机 ffmpeg，可用 brew install ffmpeg 安装')
    duration_seconds = _media_duration_seconds(media_path)
    if duration_seconds <= 0:
        raise CliError('probe_failed', f'无法读取音频时长：{media_path.name}')
    max_volume_db, silence_midpoints = _scan_audio(media_path, ffmpeg, args.silence_db, args.min_silence_seconds)
    if max_volume_db is None or max_volume_db < args.silence_db:
        return {
            'skipped': {
                'source_file': media_path.name,
                'reason': '全轨静音或低于阈值',
                'max_volume_db': max_volume_db,
                'duration_seconds': duration_seconds,
                'silence_db': args.silence_db,
            },
        }
    chunks = plan_chunks(
        duration_seconds,
        silence_midpoints,
        max_seconds=args.chunk_seconds,
        overlap_seconds=args.overlap_seconds,
    )
    temp_dir = Path(tempfile.mkdtemp(prefix='speaker-split-'))
    try:
        paths = _cut_chunks(media_path, chunks, temp_dir, ffmpeg)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return {
        'skipped': None,
        'manifest': [
            {'file': path.name, 'offset_ms': chunk['offset_ms'], 'duration_seconds': chunk['duration_seconds']}
            for chunk, path in zip(chunks, paths)
        ],
        'paths': paths,
        'temp_dir': temp_dir,
        'source_duration_seconds': duration_seconds,
    }


def _convert_srt_to_xml(args: argparse.Namespace) -> dict[str, Any]:
    input_path = _existing_file(args.input, 'SRT')
    output_path = Path(args.output).expanduser().resolve()
    _assert_distinct_files(input_path, output_path)
    if output_path.exists() and not args.force:
        raise CliError('output_exists', f'输出文件已存在：{output_path}')
    if not output_path.parent.is_dir():
        raise CliError('output_write_failed', f'输出目录不存在：{output_path.parent}')

    try:
        converted = convert_srt_bytes_to_premiere_xml(
            input_path.read_bytes(),
            source_name=input_path.name,
            frame_rate=args.frame_rate,
        )
    except SrtConversionError as error:
        raise CliError(error.code, error.message, details=error.details) from error
    except OSError as error:
        raise CliError('output_write_failed', f'读取 SRT 失败：{error}') from error

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', newline='\n',
            dir=output_path.parent, prefix=f'.{output_path.name}.', suffix='.tmp', delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(converted.xml_content)
            handle.flush()
            os.fsync(handle.fileno())
        if args.force:
            _assert_distinct_files(input_path, output_path)
            os.replace(temp_path, output_path)
        else:
            try:
                os.link(temp_path, output_path)
            except FileExistsError as error:
                raise CliError('output_exists', f'输出文件已存在：{output_path}') from error
            temp_path.unlink()
        temp_path = None
    except CliError:
        raise
    except OSError as error:
        raise CliError('output_write_failed', f'写入 XML 失败：{error}') from error
    finally:
        pending_error = sys.exc_info()[1]
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                cleanup_details = {'cleanup_error': str(cleanup_error)}
                if isinstance(pending_error, CliError):
                    if isinstance(pending_error.details, dict):
                        pending_error.details = {**pending_error.details, **cleanup_details}
                    elif pending_error.details is None:
                        pending_error.details = cleanup_details
                    else:
                        pending_error.details = {'original_details': pending_error.details, **cleanup_details}
                else:
                    raise CliError(
                        'output_write_failed',
                        f'清理临时 XML 失败：{cleanup_error}',
                        details=cleanup_details,
                    ) from cleanup_error

    try:
        payload = output_path.read_bytes()
    except OSError as error:
        raise CliError('output_write_failed', f'读取 XML 失败：{error}') from error
    return {
        'input_path': str(input_path),
        'output_path': str(output_path),
        'cue_count': converted.cue_count,
        'frame_rate': converted.frame_rate,
        'duration_ms': converted.duration_ms,
        'sha256': hashlib.sha256(payload).hexdigest(),
    }


def _execute(client: httpx.Client, args: argparse.Namespace) -> Any:
    if args.group == 'project':
        if args.command == 'list':
            return _request(client, 'GET', '/api/v2/projects', params={'include_archived': args.include_archived})
        payload = {
            'name': args.name,
            'default_language': args.language,
            'default_frame_rate': args.frame_rate,
        }
        if args.owner:
            payload['owner_name'] = args.owner
        return _request(client, 'POST', '/api/v2/projects', json=payload)
    if args.command == 'list':
        jobs = _request(client, 'GET', '/api/v1/transcriptions/jobs')
        return [job for job in jobs if str(job.get('project_id') or '') == args.project_id]
    if args.command == 'create':
        return _create_task(client, args)
    if args.command == 'status':
        return _assert_project(
            _request(client, 'GET', f'/api/v1/transcriptions/jobs/{_path_id(args.job_id)}'),
            args.project_id,
        )
    return _wait_for_job(client, args.project_id, args.job_id, args.interval, args.timeout)


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(',', ':'), default=str))


def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.group == 'subtitle':
            result = _convert_srt_to_xml(args)
        else:
            api_base = _normalize_api_base(args.api_base)
            request_timeout = max(args.request_timeout, 1800) if getattr(args, 'auto_split', False) else args.request_timeout
            with httpx.Client(base_url=api_base, timeout=request_timeout, transport=transport) as client:
                result = _execute(client, args)
        _print({'ok': True, 'data': result})
        return 0
    except CliError as error:
        payload: dict[str, Any] = {'code': error.code, 'message': error.message}
        if error.details is not None:
            payload['details'] = error.details
        _print({'ok': False, 'error': payload})
        return error.exit_code
    except httpx.RequestError as error:
        _print({'ok': False, 'error': {'code': 'connection_error', 'message': str(error)}})
        return 5
    except KeyboardInterrupt:
        _print({'ok': False, 'error': {'code': 'interrupted', 'message': '操作已中止'}})
        return 130


if __name__ == '__main__':
    sys.exit(main())
