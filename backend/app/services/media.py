from __future__ import annotations

from dataclasses import dataclass
import subprocess
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException

from app.core.config import settings
from app.services.local_files import ensure_storage_capacity

ASR_RECORDED_FILE_MAX_SECONDS = 12 * 60 * 60
ASR_RECORDED_FILE_MAX_BYTES = 2 * 1024 * 1024 * 1024
DIARIZATION_RECOMMENDED_MAX_SECONDS = 2 * 60 * 60
DIARIZATION_SEGMENT_SECONDS = DIARIZATION_RECOMMENDED_MAX_SECONDS
AUDIO_ACTIVITY_SAMPLE_SECONDS = 8
SILENT_AUDIO_MAX_VOLUME_DB = -90.0


@dataclass
class RecognitionAudioChunk:
    index: int
    path: Path
    offset_ms: int
    duration_seconds: float


def _run_audio_normalize_command(media_path: Path, output_path: Path, media_kind: str = 'audio') -> Path:
    if not media_path.exists():
        raise HTTPException(status_code=400, detail='待处理媒体不存在')

    duration_seconds = get_media_duration_seconds(media_path)
    ensure_storage_capacity(
        output_path.parent,
        required_bytes=_estimate_pcm_bytes(duration_seconds, sample_rate=16000, channels=1),
        operation='生成识别音频',
    )
    command = [
        'ffmpeg',
        '-y',
        '-i',
        str(media_path),
    ]
    if media_kind == 'video':
        command.append('-vn')
    command.extend([
        '-acodec',
        'pcm_s16le',
        '-ar',
        '16000',
        '-ac',
        '1',
        str(output_path),
    ])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not output_path.exists():
        output_path.unlink(missing_ok=True)
        detail = result.stderr.strip() or 'FFmpeg 预处理识别音频失败'
        raise HTTPException(status_code=502, detail=detail[:1000])
    return output_path


def prepare_recognition_audio(media_path: Path, media_kind: str = 'audio') -> Path:
    output_path = settings.audio_dir_path / f'{uuid4()}-{media_path.stem}.wav'
    return _run_audio_normalize_command(media_path, output_path, media_kind=media_kind)


def prepare_voice_clone_reference_audio(media_path: Path) -> Path:
    if not media_path.exists():
        raise HTTPException(status_code=400, detail='待处理媒体不存在')

    target_rate = max(16000, int(settings.voice_cloning_sample_target_rate_hz or 24000))
    channel_count = 1 if int(settings.voice_cloning_sample_channel_count or 1) <= 1 else 2
    output_path = settings.audio_dir_path / f'{uuid4()}-{media_path.stem}-clone.wav'
    ensure_storage_capacity(
        output_path.parent,
        required_bytes=_estimate_pcm_bytes(
            get_media_duration_seconds(media_path),
            sample_rate=target_rate,
            channels=channel_count,
        ),
        operation='生成声音复刻样本',
    )
    command = [
        'ffmpeg',
        '-y',
        '-i',
        str(media_path),
        '-vn',
        '-acodec',
        'pcm_s16le',
        '-ar',
        str(target_rate),
        '-ac',
        str(channel_count),
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not output_path.exists():
        output_path.unlink(missing_ok=True)
        detail = result.stderr.strip() or 'FFmpeg 预处理复刻样本失败'
        raise HTTPException(status_code=502, detail=detail[:1000])
    return output_path


def normalize_voice_clone_generated_audio(media_path: Path, output_path: Path) -> Path:
    if not media_path.exists():
        raise RuntimeError('待处理生成音频不存在')

    ensure_storage_capacity(
        output_path.parent,
        required_bytes=_estimate_pcm_bytes(
            get_media_duration_seconds(media_path),
            sample_rate=48000,
            channels=1,
        ),
        operation='标准化声音复刻音频',
    )
    command = [
        'ffmpeg',
        '-y',
        '-i',
        str(media_path),
        '-vn',
        '-map_metadata',
        '-1',
        '-acodec',
        'pcm_s16le',
        '-ar',
        '48000',
        '-ac',
        '1',
        '-f',
        'wav',
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not output_path.exists():
        output_path.unlink(missing_ok=True)
        detail = result.stderr.strip() or 'FFmpeg 标准化声音复刻生成音频失败'
        raise RuntimeError(detail[:1000])
    return output_path


def prepare_preview_audio(media_path: Path, cache_key: str, media_kind: str = 'audio') -> Path:
    if not media_path.exists():
        raise HTTPException(status_code=400, detail='待处理媒体不存在')
    safe_key = ''.join(char if char.isalnum() or char in {'-', '_'} else '-' for char in cache_key)
    output_path = settings.audio_dir_path / f'{safe_key}-{media_path.stem}-preview.wav'
    if output_path.exists():
        return output_path
    return _run_audio_normalize_command(media_path, output_path, media_kind=media_kind)


def extract_recognition_audio(video_path: Path) -> Path:
    return prepare_recognition_audio(video_path, media_kind='video')


def get_media_duration_seconds(media_path: Path) -> float:
    command = [
        'ffprobe',
        '-v',
        'error',
        '-show_entries',
        'format=duration',
        '-of',
        'default=noprint_wrappers=1:nokey=1',
        str(media_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _has_audio_stream(media_path: Path) -> bool:
    command = [
        'ffprobe',
        '-v',
        'error',
        '-select_streams',
        'a:0',
        '-show_entries',
        'stream=codec_type',
        '-of',
        'default=noprint_wrappers=1:nokey=1',
        str(media_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return False
    return any(line.strip() == 'audio' for line in result.stdout.splitlines())


def _parse_max_volume_db(stderr: str) -> Optional[float]:
    for line in stderr.splitlines():
        marker = 'max_volume:'
        if marker not in line:
            continue
        value = line.split(marker, 1)[1].strip().split(' ', 1)[0]
        if value == '-inf':
            return float('-inf')
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _detect_sample_max_volume_db(media_path: Path, start_seconds: float = 0.0) -> Optional[float]:
    command = [
        'ffmpeg',
        '-hide_banner',
        '-nostats',
    ]
    if start_seconds > 0:
        command.extend(['-ss', f'{start_seconds:.3f}'])
    command.extend(
        [
            '-t',
            str(AUDIO_ACTIVITY_SAMPLE_SECONDS),
            '-i',
            str(media_path),
            '-map',
            '0:a:0',
            '-af',
            'volumedetect',
            '-f',
            'null',
            '-',
        ]
    )
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return _parse_max_volume_db(result.stderr)


def _has_effective_audio_signal(media_path: Path, duration_seconds: float) -> Optional[bool]:
    sample_starts = [0.0]
    if duration_seconds > 60:
        sample_starts.append(max(0.0, duration_seconds / 2))
    if duration_seconds > 120:
        sample_starts.append(max(0.0, duration_seconds - AUDIO_ACTIVITY_SAMPLE_SECONDS))

    observed_volumes = [
        volume
        for volume in (_detect_sample_max_volume_db(media_path, start_seconds) for start_seconds in sample_starts)
        if volume is not None
    ]
    if not observed_volumes:
        return None
    return any(volume > SILENT_AUDIO_MAX_VOLUME_DB for volume in observed_volumes)


def validate_recognition_source_constraints(media_path: Path) -> None:
    if media_path.exists() and media_path.stat().st_size > ASR_RECORDED_FILE_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail='当前素材超过阿里云百炼 Fun-ASR 非实时文件识别上限：单文件最长 12 小时 / 2GB。请先在剪辑软件里压缩或拆成多个音频文件后再上传。',
        )
    duration_seconds = get_media_duration_seconds(media_path)
    if duration_seconds and duration_seconds > ASR_RECORDED_FILE_MAX_SECONDS:
        raise HTTPException(
            status_code=400,
            detail='当前素材超过阿里云百炼 Fun-ASR 非实时文件识别上限：单文件最长 12 小时 / 2GB。请先在剪辑软件里拆成多个音频文件后再上传。',
        )
    if not _has_audio_stream(media_path):
        raise HTTPException(
            status_code=400,
            detail='当前文件没有检测到可识别的音轨。请确认选择的是有声音的音频文件，或从剪辑软件重新导出包含声音的 WAV/MP3 后再上传。',
        )
    if _has_effective_audio_signal(media_path, duration_seconds) is False:
        raise HTTPException(
            status_code=400,
            detail='当前音频没有检测到有效声音，可能是全静音、导出错轨或音量接近 0。请先在本机播放确认有声音，或从剪辑软件重新导出有声版本后再上传。',
        )


def ensure_diarization_friendly_duration(audio_path: Path) -> None:
    duration_seconds = get_media_duration_seconds(audio_path)
    if duration_seconds and duration_seconds > DIARIZATION_RECOMMENDED_MAX_SECONDS:
        raise HTTPException(
            status_code=400,
            detail='当前素材超过 2 小时。阿里云官方建议在启用说话人分离时将单段音频控制在 2 小时内；请先切分后再上传，以提高说话人识别稳定性。',
        )


def split_recognition_audio(audio_path: Path, max_seconds: int = DIARIZATION_SEGMENT_SECONDS) -> list[RecognitionAudioChunk]:
    duration_seconds = get_media_duration_seconds(audio_path)
    if not duration_seconds or duration_seconds <= max_seconds:
        return [
            RecognitionAudioChunk(
                index=0,
                path=audio_path,
                offset_ms=0,
                duration_seconds=duration_seconds,
            )
        ]

    chunk_dir = settings.audio_dir_path / f'{uuid4()}-{audio_path.stem}-chunks'
    chunk_dir.mkdir(parents=True, exist_ok=True)
    ensure_storage_capacity(
        chunk_dir,
        required_bytes=audio_path.stat().st_size,
        operation='切分识别音频',
    )
    pattern = chunk_dir / 'chunk-%03d.wav'
    command = [
        'ffmpeg',
        '-y',
        '-i',
        str(audio_path),
        '-f',
        'segment',
        '-segment_time',
        str(max_seconds),
        '-acodec',
        'pcm_s16le',
        '-ar',
        '16000',
        '-ac',
        '1',
        str(pattern),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        for partial_path in chunk_dir.glob('chunk-*.wav'):
            partial_path.unlink(missing_ok=True)
        chunk_dir.rmdir()
        detail = result.stderr.strip() or 'FFmpeg 自动切分音频失败'
        raise HTTPException(status_code=502, detail=detail[:1000])

    offset_ms = 0
    chunks: list[RecognitionAudioChunk] = []
    for index, chunk_path in enumerate(sorted(chunk_dir.glob('chunk-*.wav'))):
        chunk_duration = get_media_duration_seconds(chunk_path)
        if not chunk_duration:
            continue
        chunks.append(
            RecognitionAudioChunk(
                index=index,
                path=chunk_path,
                offset_ms=offset_ms,
                duration_seconds=chunk_duration,
            )
        )
        offset_ms += int(chunk_duration * 1000)
    if not chunks:
        raise HTTPException(status_code=502, detail='自动切分音频后未生成任何分片')
    return chunks


def _estimate_pcm_bytes(duration_seconds: float, *, sample_rate: int, channels: int) -> int:
    if duration_seconds <= 0:
        return 1024 * 1024
    return int(duration_seconds * sample_rate * channels * 2) + 1024 * 1024
