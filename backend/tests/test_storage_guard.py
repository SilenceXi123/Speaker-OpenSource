import asyncio
import os
from collections import namedtuple

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services import local_files
from app.services.local_files import (
    copy_local_file_to_library,
    ensure_storage_capacity,
    save_upload_file_locally,
)


DiskUsage = namedtuple('usage', 'total used free')


def test_storage_capacity_rejects_projected_high_watermark(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, 'app_env', 'production')
    monkeypatch.setattr(settings, 'storage_min_free_gb', 0.0)
    monkeypatch.setattr(settings, 'storage_max_used_percent', 85.0)
    monkeypatch.setattr(
        local_files.shutil,
        'disk_usage',
        lambda _path: DiskUsage(total=100, used=80, free=20),
    )

    with pytest.raises(HTTPException) as captured:
        ensure_storage_capacity(tmp_path, required_bytes=6, operation='测试写入')

    assert captured.value.status_code == 507
    assert '存储保护' in captured.value.detail


def test_upload_removes_partial_file_when_storage_guard_trips(monkeypatch, tmp_path) -> None:
    class FakeUpload:
        filename = 'large.wav'

        def __init__(self) -> None:
            self.calls = 0

        async def read(self, _size: int) -> bytes:
            self.calls += 1
            return b'payload' if self.calls == 1 else b''

    guard_calls = 0

    def fake_guard(*_args, **_kwargs) -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls > 1:
            raise HTTPException(status_code=507, detail='disk full')

    monkeypatch.setattr(settings, 'local_upload_dir', str(tmp_path))
    monkeypatch.setattr(local_files, 'ensure_storage_capacity', fake_guard)

    with pytest.raises(HTTPException) as captured:
        asyncio.run(save_upload_file_locally(FakeUpload()))

    assert captured.value.status_code == 507
    assert list(tmp_path.iterdir()) == []


def test_library_copy_streams_and_hardlinks_duplicate_content(tmp_path) -> None:
    source_one = tmp_path / 'one.wav'
    source_two = tmp_path / 'two.wav'
    library = tmp_path / 'library'
    payload = b'same-audio-content'
    source_one.write_bytes(payload)
    source_two.write_bytes(payload)

    first = copy_local_file_to_library(source_one, library, original_name='one.wav')
    second = copy_local_file_to_library(source_two, library, original_name='two.wav')

    assert first != second
    assert first.read_bytes() == payload
    assert second.read_bytes() == payload
    assert os.stat(first).st_ino == os.stat(second).st_ino
    assert os.stat(first).st_nlink >= 2
