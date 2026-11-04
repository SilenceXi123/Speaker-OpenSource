from __future__ import annotations

import subprocess
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.services.local_files import ensure_storage_capacity
from app.schemas.provider_cost import ProviderCallContext, ProviderCallObservation, ProviderMeterObservation
from app.schemas.subtitle import SubtitleCue
from app.services.provider_costs import record_legacy_provider_observation


logger = logging.getLogger(__name__)


def download_remote_file(
    url: str,
    directory: Path,
    file_name: Optional[str] = None,
    *,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    guessed_name = Path(parsed.path).name or f'{uuid4()}'
    target_path = directory / (file_name or guessed_name)
    ensure_storage_capacity(directory, operation='下载渲染素材')

    started_at = _utcnow()
    downloaded_bytes = 0
    request_id = None
    try:
        with httpx.stream('GET', url, timeout=120) as response:
            request_id = _response_request_id(response)
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail=f'下载远程文件失败: {url}')
            with target_path.open('wb') as output:
                for chunk in response.iter_bytes():
                    ensure_storage_capacity(
                        directory,
                        required_bytes=len(chunk),
                        operation='下载渲染素材',
                    )
                    downloaded_bytes += len(chunk)
                    output.write(chunk)
    except Exception as exc:
        target_path.unlink(missing_ok=True)
        _record_oss_download(
            url,
            provider_context,
            observation_key=observation_key,
            status='failed',
            request_id=request_id,
            started_at=started_at,
            error=exc,
        )
        raise
    _record_oss_download(
        url,
        provider_context,
        observation_key=observation_key,
        status='completed',
        request_id=request_id,
        downloaded_bytes=downloaded_bytes,
        started_at=started_at,
    )
    return target_path


def write_clean_srt(cues: list[SubtitleCue], target_path: Path) -> Path:
    blocks = []
    for cue in cues:
        blocks.append(
            '\n'.join(
                [
                    str(cue.index),
                    f'{_format_srt_time(cue.start_ms)} --> {_format_srt_time(cue.end_ms)}',
                    cue.clean_text,
                ]
            )
        )
    target_path.write_text('\n\n'.join(blocks) + ('\n' if blocks else ''), encoding='utf-8')
    return target_path


def build_overlay_intervals(cues: list[SubtitleCue]) -> list[dict]:
    intervals: list[dict] = []
    for cue in cues:
        if not cue.clean_text:
            continue
        start = cue.start_ms / 1000.0
        end = cue.end_ms / 1000.0
        intervals.append(
            {
                'speaker_name': cue.speaker_name,
                'start': start,
                'end': end,
            }
        )
    return intervals


def render_video_with_assets(
    video_path: Path,
    subtitle_path: Path,
    matched_assets: dict[str, dict],
    cues: list[SubtitleCue],
    working_dir: Path,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    provider_context: Optional[ProviderCallContext] = None,
) -> Path:
    working_dir.mkdir(parents=True, exist_ok=True)
    output_path = working_dir / f'{uuid4()}-rendered.mp4'
    intervals = build_overlay_intervals(cues)

    image_inputs: list[Path] = []
    speaker_input_index: dict[str, int] = {}
    command = ['ffmpeg', '-y', '-i', str(video_path)]

    for speaker_name, asset in matched_assets.items():
        image_path = resolve_asset_media_path(
            asset,
            working_dir,
            provider_context=provider_context,
            observation_key=f"oss:get:lower-third:{asset.get('id') or asset.get('file_name') or speaker_name}",
        )
        speaker_input_index[speaker_name] = len(image_inputs) + 1
        image_inputs.append(image_path)
        command.extend(['-loop', '1', '-i', str(image_path)])

    filter_parts: list[str] = []
    previous_label = '[0:v]'
    overlay_counter = 0
    for interval in intervals:
        input_index = speaker_input_index.get(interval['speaker_name'])
        if not input_index:
            continue
        overlay_counter += 1
        next_label = f'[v{overlay_counter}]'
        filter_parts.append(_build_overlay_filter(previous_label, input_index, interval, next_label))
        previous_label = next_label

    subtitles_label = '[vout]'
    escaped_subtitle = _escape_filter_path(subtitle_path)
    filter_parts.append(
        f"{previous_label}subtitles='{escaped_subtitle}':force_style='Fontsize=18,Outline=1,BorderStyle=3,MarginV=40'{subtitles_label}"
    )

    command.extend(
        [
            '-loglevel',
            'error',
            '-filter_complex',
            ';'.join(filter_parts),
            '-map',
            subtitles_label,
            '-map',
            '0:a?',
            '-c:v',
            'libx264',
            '-pix_fmt',
            'yuv420p',
            '-c:a',
            'copy',
            '-progress',
            'pipe:1',
            '-nostats',
            str(output_path),
        ]
    )
    duration_seconds = _get_media_duration_seconds(video_path)
    ensure_storage_capacity(
        working_dir,
        required_bytes=video_path.stat().st_size,
        operation='渲染视频',
    )
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    if progress_callback:
        progress_callback(48, 'FFmpeg 已启动，正在渲染视频')
    latest_out_seconds = 0.0
    assert process.stdout is not None
    for line in process.stdout:
        content = line.strip()
        if content.startswith('out_time_us='):
            latest_out_seconds = max(latest_out_seconds, int(content.split('=', 1)[1]) / 1_000_000.0)
        elif content.startswith('out_time_ms='):
            latest_out_seconds = max(latest_out_seconds, int(content.split('=', 1)[1]) / 1_000_000.0)
        elif content == 'progress=end' and progress_callback:
            progress_callback(88, 'FFmpeg 合成完成，准备上传成片')
        if duration_seconds > 0 and progress_callback and latest_out_seconds > 0:
            ratio = min(latest_out_seconds / duration_seconds, 1.0)
            progress = 48 + int(ratio * 36)
            progress_callback(progress, f'正在渲染视频 {int(ratio * 100)}%')
    return_code = process.wait()
    stderr_output = process.stderr.read() if process.stderr else ''
    if return_code != 0 or not output_path.exists():
        output_path.unlink(missing_ok=True)
        detail = stderr_output.strip() or 'FFmpeg 渲染失败'
        raise HTTPException(status_code=502, detail=detail[:1200])
    return output_path


def resolve_asset_media_path(
    asset: dict,
    working_dir: Path,
    *,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
) -> Path:
    local_path = str(asset.get('local_path') or '').strip()
    if local_path:
        candidate = Path(local_path)
        if candidate.exists():
            return candidate

    public_url = str(asset.get('public_url') or '').strip()
    if public_url:
        return download_remote_file(
            public_url,
            working_dir,
            file_name=asset.get('file_name'),
            provider_context=provider_context,
            observation_key=observation_key,
        )

    raise HTTPException(status_code=400, detail=f"人名条缺少可用文件路径：{asset.get('file_name') or 'unknown'}")


def _build_overlay_filter(previous_label: str, input_index: int, interval: dict, next_label: str) -> str:
    return (
        f"{previous_label}[{input_index}:v]"
        f"overlay=40:H-h-160:shortest=1:enable='between(t,{interval['start']:.3f},{interval['end']:.3f})'"
        f'{next_label}'
    )


def _escape_filter_path(path: Path) -> str:
    value = str(path)
    value = value.replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'")
    return value


def _get_media_duration_seconds(video_path: Path) -> float:
    command = [
        'ffprobe',
        '-v',
        'error',
        '-show_entries',
        'format=duration',
        '-of',
        'default=noprint_wrappers=1:nokey=1',
        str(video_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _format_srt_time(ms: int) -> str:
    total_ms = max(ms, 0)
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    seconds = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}'


def _record_oss_download(
    url: str,
    context: Optional[ProviderCallContext],
    *,
    observation_key: Optional[str],
    status: str,
    request_id: Optional[str] = None,
    downloaded_bytes: Optional[int] = None,
    started_at: Optional[datetime] = None,
    error: Optional[Exception] = None,
) -> None:
    if context is None or not observation_key or not _is_configured_oss_url(url):
        return
    usage = None
    meters: list[ProviderMeterObservation] = []
    if downloaded_bytes is not None:
        usage = {'egress_bytes': downloaded_bytes, 'request_count': 1}
        meters = [
            ProviderMeterObservation(meter='egress_bytes', quantity=downloaded_bytes, unit='byte'),
            ProviderMeterObservation(meter='request_count', quantity=1, unit='request'),
        ]
    try:
        record_legacy_provider_observation(
            context,
            provider='aliyun_oss',
            model=settings.oss_bucket or 'unknown-bucket',
            operation='get_object',
            observed_call=ProviderCallObservation(
                idempotency_key=observation_key,
                status=status,
                request_id=request_id,
                usage=usage,
                meters=meters,
                started_at=started_at,
                completed_at=_utcnow(),
                error_code=type(error).__name__ if error else None,
                error_summary=f'{type(error).__name__}: OSS download failed' if error else None,
            ),
        )
    except Exception as exc:  # pragma: no cover - observation must stay fail-open
        logger.warning('OSS download observation skipped (%s)', type(exc).__name__)


def _is_configured_oss_url(url: str) -> bool:
    configured_origin = urlparse(settings.oss_public_base_url or '').netloc.lower()
    candidate_origin = urlparse(url).netloc.lower()
    return bool(configured_origin and candidate_origin == configured_origin)


def _response_request_id(response) -> Optional[str]:
    headers = getattr(response, 'headers', {}) or {}
    return str(headers.get('x-oss-request-id') or headers.get('x-oss-requestid') or '').strip() or None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
