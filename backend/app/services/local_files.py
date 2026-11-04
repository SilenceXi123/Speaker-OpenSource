from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, UploadFile

from app.core.config import settings


def ensure_storage_capacity(
    target_dir: Path,
    *,
    required_bytes: int = 0,
    operation: str = '写入文件',
) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(target_dir)
    required = max(0, int(required_bytes))
    minimum_free = max(0.0, float(settings.storage_min_free_gb)) * 1024**3
    configured_maximum = min(99.0, max(1.0, float(settings.storage_max_used_percent)))
    maximum_used_percent = (
        configured_maximum
        if settings.app_env.strip().lower() in {'prod', 'production'}
        else 99.0
    )
    projected_free = usage.free - required
    projected_used_percent = (
        ((usage.total - projected_free) / usage.total) * 100
        if usage.total
        else 0.0
    )
    if projected_free >= minimum_free and projected_used_percent <= maximum_used_percent:
        return
    free_gb = usage.free / 1024**3
    required_gb = required / 1024**3
    raise HTTPException(
        status_code=507,
        detail=(
            f'服务器存储保护已阻止{operation}：当前可用 {free_gb:.1f}GB，'
            f'本次至少还需 {required_gb:.1f}GB；系统必须保留 '
            f'{float(settings.storage_min_free_gb):.1f}GB 空间且磁盘使用率不得超过 '
            f'{maximum_used_percent:.0f}%。请先清理旧任务或联系管理员。'
        ),
    )


async def save_upload_file_locally(file: UploadFile, max_bytes: Optional[int] = None) -> Path:
    if not file.filename:
        raise HTTPException(status_code=400, detail='缺少文件名')

    safe_name = Path(file.filename).name.replace(' ', '_')
    target_path = settings.upload_dir_path / f'{uuid4()}-{safe_name}'
    total_bytes = 0
    exceeded_limit = False
    ensure_storage_capacity(settings.upload_dir_path, operation='开始上传')
    try:
        with target_path.open('wb') as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if max_bytes is not None and total_bytes > max_bytes:
                    exceeded_limit = True
                    break
                ensure_storage_capacity(
                    settings.upload_dir_path,
                    required_bytes=len(chunk),
                    operation='继续上传',
                )
                output.write(chunk)
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    if exceeded_limit:
        target_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail='当前素材超过阿里云百炼 Fun-ASR 非实时文件识别上限：单文件最长 12 小时 / 2GB。请先在剪辑软件里压缩或拆成多个音频文件后再上传。',
        )
    if not total_bytes:
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail='上传文件为空')
    return target_path


def copy_local_file_to_library(source_path: Path, target_dir: Path, original_name: Optional[str] = None) -> Path:
    if not source_path.exists():
        raise HTTPException(status_code=400, detail=f'本地文件不存在: {source_path}')
    safe_name = Path(original_name or source_path.name).name.replace(' ', '_')
    target_dir.mkdir(parents=True, exist_ok=True)
    digest = _sha256_file(source_path)
    target_path = target_dir / f'{digest}-{uuid4()}-{safe_name}'
    existing = next(
        (
            candidate
            for candidate in target_dir.glob(f'{digest}-*')
            if candidate.is_file() and candidate.stat().st_size == source_path.stat().st_size
        ),
        None,
    )
    if existing is not None:
        try:
            os.link(existing, target_path)
            return target_path
        except OSError:
            pass
    ensure_storage_capacity(
        target_dir,
        required_bytes=source_path.stat().st_size,
        operation='保存任务源文件',
    )
    shutil.copy2(source_path, target_path)
    return target_path


def remove_local_file(path: Optional[Path]) -> None:
    if path and path.exists():
        path.unlink()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
