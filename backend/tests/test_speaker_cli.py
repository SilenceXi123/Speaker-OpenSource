from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import wave

import httpx
import pytest

from scripts.speaker_cli import main, plan_chunks


def test_task_create_uses_locked_v2_project_and_prints_json(tmp_path, capsys) -> None:
    media = tmp_path / 'sample.wav'
    media.write_bytes(b'RIFF0000WAVE')
    script = tmp_path / 'script.txt'
    script.write_text('节目台本', encoding='utf-8')
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        seen.update(path=request.url.path, body=body, content_type=request.headers['content-type'])
        return httpx.Response(
            201,
            json={
                'job': {
                    'id': 'job-1',
                    'project_id': 'project-1',
                    'kind': 'transcription',
                    'status': 'pending',
                },
                'audio_upload': {'object_key': 'audio/sample.wav'},
            },
        )

    exit_code = main(
        [
            '--api-base',
            'http://speaker.test',
            'task',
            'create',
            '--project-id',
            'project-1',
            '--file',
            str(media),
            '--script',
            str(script),
            '--lyrics-correction',
        ],
        transport=httpx.MockTransport(handler),
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output['ok'] is True
    assert output['data']['job']['id'] == 'job-1'
    assert seen['path'] == '/api/v2/projects/project-1/transcriptions/media-jobs'
    assert str(seen['content_type']).startswith('multipart/form-data; boundary=')
    assert b'name="job_kind"' in seen['body']
    assert b'transcription' in seen['body']
    assert b'name="subtitle_strategy"' in seen['body']
    assert b'evidence' in seen['body']
    assert b'name="script_files"; filename="script.txt"' in seen['body']
    assert b'name="lyrics_correction_mode"' in seen['body']
    assert b'true' in seen['body']


def test_task_wait_drives_sync_until_terminal_and_checks_project(capsys) -> None:
    statuses = iter(['running', 'completed'])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                'id': 'job-1',
                'project_id': 'project-1',
                'kind': 'transcription',
                'status': next(statuses),
            },
        )

    exit_code = main(
        [
            '--api-base',
            'http://speaker.test',
            'task',
            'wait',
            '--project-id',
            'project-1',
            '--job-id',
            'job-1',
            '--interval',
            '0',
            '--timeout',
            '1',
        ],
        transport=httpx.MockTransport(handler),
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert calls == 2
    assert output == {
        'ok': True,
        'data': {
            'id': 'job-1',
            'project_id': 'project-1',
            'kind': 'transcription',
            'status': 'completed',
        },
    }


def test_task_status_returns_machine_readable_cross_project_error(capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                'id': 'job-1',
                'project_id': 'project-2',
                'kind': 'transcription',
                'status': 'running',
            },
        )

    exit_code = main(
        [
            '--api-base',
            'http://speaker.test',
            'task',
            'status',
            '--project-id',
            'project-1',
            '--job-id',
            'job-1',
        ],
        transport=httpx.MockTransport(handler),
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output['ok'] is False
    assert output['error']['code'] == 'project_mismatch'


def test_srt_to_xml_runs_offline_and_prints_integrity_json(tmp_path, capsys) -> None:
    source = tmp_path / '输入字幕.srt'
    target = tmp_path / '输出字幕.xml'
    source.write_text(
        '1\n00:00:00,000 --> 00:00:01,000\nAI & Speaker\n',
        encoding='utf-8',
    )

    def unexpected_http(_request: httpx.Request) -> httpx.Response:
        raise AssertionError('offline conversion must not make HTTP requests')

    exit_code = main(
        [
            '--api-base', 'not-a-url',
            'subtitle', 'srt-to-xml',
            '--input', str(source),
            '--output', str(target),
            '--frame-rate', '25',
        ],
        transport=httpx.MockTransport(unexpected_http),
    )

    output = json.loads(capsys.readouterr().out)
    payload = target.read_bytes()
    assert exit_code == 0
    assert output == {
        'ok': True,
        'data': {
            'input_path': str(source.resolve()),
            'output_path': str(target.resolve()),
            'cue_count': 1,
            'frame_rate': '25',
            'duration_ms': 1000,
            'sha256': hashlib.sha256(payload).hexdigest(),
        },
    }
    assert b'GraphicAndType' in payload


def test_srt_to_xml_refuses_existing_output_without_force(tmp_path, capsys) -> None:
    source = tmp_path / 'input.srt'
    target = tmp_path / 'output.xml'
    source.write_text('1\n00:00:00,000 --> 00:00:01,000\n字幕\n', encoding='utf-8')
    target.write_bytes(b'keep-me')

    exit_code = main(['subtitle', 'srt-to-xml', '--input', str(source), '--output', str(target)])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output['error']['code'] == 'output_exists'
    assert target.read_bytes() == b'keep-me'


def test_srt_to_xml_force_replaces_existing_output(tmp_path, capsys) -> None:
    source = tmp_path / 'input.srt'
    target = tmp_path / 'output.xml'
    source.write_text('1\n00:00:00,000 --> 00:00:01,000\n字幕\n', encoding='utf-8')
    target.write_bytes(b'old')

    exit_code = main([
        'subtitle', 'srt-to-xml', '--input', str(source), '--output', str(target), '--force',
    ])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)['ok'] is True
    assert target.read_text(encoding='utf-8').startswith('<?xml version="1.0"')


def test_srt_to_xml_parse_failure_leaves_no_output(tmp_path, capsys) -> None:
    source = tmp_path / 'input.srt'
    target = tmp_path / 'output.xml'
    source.write_text(
        '1\n00:00:00,000 --> 00:00:02,000\n一\n\n'
        '2\n00:00:01,900 --> 00:00:03,000\n二\n',
        encoding='utf-8',
    )

    exit_code = main(['subtitle', 'srt-to-xml', '--input', str(source), '--output', str(target)])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output['error']['code'] == 'overlapping_cues'
    assert not target.exists()
    assert list(tmp_path.glob(f'.{target.name}.*.tmp')) == []


def test_srt_to_xml_never_overwrites_its_input_even_with_force(tmp_path, capsys) -> None:
    source = tmp_path / 'input.srt'
    original = b'1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n'
    source.write_bytes(original)

    exit_code = main([
        'subtitle', 'srt-to-xml', '--input', str(source), '--output', str(source), '--force',
    ])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output['error']['code'] == 'invalid_arguments'
    assert source.read_bytes() == original


def test_srt_to_xml_rejects_hard_link_alias_even_with_force(tmp_path, capsys) -> None:
    source = tmp_path / 'input.srt'
    target = tmp_path / 'output.xml'
    original = b'1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n'
    source.write_bytes(original)
    os.link(source, target)

    exit_code = main([
        'subtitle', 'srt-to-xml', '--input', str(source), '--output', str(target), '--force',
    ])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 2
    assert captured.out.count('\n') == 1
    assert captured.err == ''
    assert output['error']['code'] == 'invalid_arguments'
    assert source.read_bytes() == original
    assert target.read_bytes() == original


def test_srt_to_xml_rejects_unverifiable_output_identity(tmp_path, capsys, monkeypatch) -> None:
    source = tmp_path / 'input.srt'
    target = tmp_path / 'output.xml'
    original = b'1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n'
    source.write_bytes(original)
    target.write_bytes(b'keep-me')
    original_samefile = Path.samefile

    def fail_target_samefile(path: Path, other_path: Path) -> bool:
        if path == target.resolve():
            raise OSError('identity check failed')
        return original_samefile(path, other_path)

    monkeypatch.setattr(Path, 'samefile', fail_target_samefile)

    exit_code = main([
        'subtitle', 'srt-to-xml', '--input', str(source), '--output', str(target), '--force',
    ])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 2
    assert captured.out.count('\n') == 1
    assert captured.err == ''
    assert output['error']['code'] == 'invalid_arguments'
    assert source.read_bytes() == original
    assert target.read_bytes() == b'keep-me'


def test_srt_to_xml_no_force_does_not_clobber_output_created_during_publish(
    tmp_path, capsys, monkeypatch,
) -> None:
    source = tmp_path / 'input.srt'
    target = tmp_path / 'output.xml'
    source.write_text('1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n', encoding='utf-8')
    original_link = os.link
    original_replace = os.replace

    def race_link(source_path: Path, output_path: Path) -> None:
        target.write_bytes(b'competitor')
        original_link(source_path, output_path)

    def race_replace(source_path: Path, output_path: Path) -> None:
        target.write_bytes(b'competitor')
        original_replace(source_path, output_path)

    monkeypatch.setattr('scripts.speaker_cli.os.link', race_link)
    monkeypatch.setattr('scripts.speaker_cli.os.replace', race_replace)

    exit_code = main(['subtitle', 'srt-to-xml', '--input', str(source), '--output', str(target)])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 2
    assert captured.out.count('\n') == 1
    assert captured.err == ''
    assert output['error']['code'] == 'output_exists'
    assert target.read_bytes() == b'competitor'
    assert list(tmp_path.glob(f'.{target.name}.*.tmp')) == []


def test_srt_to_xml_maps_post_replace_output_read_failure_to_json(tmp_path, capsys, monkeypatch) -> None:
    source = tmp_path / 'input.srt'
    target = tmp_path / 'output.xml'
    source.write_text('1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n', encoding='utf-8')
    original_read_bytes = Path.read_bytes

    def fail_output_read(path: Path) -> bytes:
        if path == target.resolve():
            raise OSError('cannot read installed XML')
        return original_read_bytes(path)

    monkeypatch.setattr(Path, 'read_bytes', fail_output_read)

    exit_code = main([
        'subtitle', 'srt-to-xml', '--input', str(source), '--output', str(target), '--force',
    ])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 2
    assert captured.out.count('\n') == 1
    assert captured.err == ''
    assert output['error']['code'] == 'output_write_failed'
    assert target.exists()


def test_srt_to_xml_reports_temp_cleanup_failure_as_json(tmp_path, capsys, monkeypatch) -> None:
    source = tmp_path / 'input.srt'
    target = tmp_path / 'output.xml'
    source.write_text('1\n00:00:00,000 --> 00:00:01,000\nsubtitle\n', encoding='utf-8')
    original_unlink = Path.unlink

    def fail_replace(*_args: object) -> None:
        raise OSError('replace failed')

    def fail_temp_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.parent == tmp_path and path.name.startswith(f'.{target.name}.') and path.suffix == '.tmp':
            raise OSError('cleanup failed')
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr('scripts.speaker_cli.os.replace', fail_replace)
    monkeypatch.setattr(Path, 'unlink', fail_temp_unlink)

    exit_code = main([
        'subtitle', 'srt-to-xml', '--input', str(source), '--output', str(target), '--force',
    ])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 2
    assert captured.out.count('\n') == 1
    assert captured.err == ''
    assert output['error']['code'] == 'output_write_failed'
    assert output['error']['message'] == '写入 XML 失败：replace failed'
    assert output['error']['details']['cleanup_error'] == 'cleanup failed'
    for temp_path in tmp_path.glob(f'.{target.name}.*.tmp'):
        original_unlink(temp_path)


def test_plan_chunks_stays_within_max_and_snaps_to_silence() -> None:
    chunks = plan_chunks(
        15000,
        [600.0, 7191.0, 10000.0],
        max_seconds=7200,
        overlap_seconds=8,
        snap_window_seconds=120,
    )

    assert [chunk['offset_ms'] for chunk in chunks] == [0, 7191000, 14383000]
    assert all(chunk['duration_seconds'] <= 7200 for chunk in chunks)
    assert chunks[0]['duration_seconds'] == 7199.0  # 边界吸附到 7191s 静音中点，末尾带 8s 重叠


def test_plan_chunks_never_snaps_back_to_previous_boundary() -> None:
    # 静音中点恰好在上一边界附近时，不得把边界吸附回原位置造成死循环
    chunks = plan_chunks(
        130,
        [70.0],
        max_seconds=60,
        overlap_seconds=8,
        snap_window_seconds=120,
    )

    assert [chunk['offset_ms'] for chunk in chunks] == [0, 52000, 70000, 122000]
    assert [chunk['duration_seconds'] for chunk in chunks] == [60.0, 26.0, 60.0, 8.0]


def _write_tone_wav(path: Path, seconds: float = 1.0) -> None:
    sample_rate = 8000
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = bytearray()
        for index in range(int(sample_rate * seconds)):
            frames += struct.pack('<h', int(0.4 * 32767 * math.sin(2 * math.pi * 440 * index / sample_rate)))
        handle.writeframes(bytes(frames))


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='需要本机 ffmpeg')
def test_task_create_auto_split_submits_chunk_batch(tmp_path, capsys) -> None:
    media = tmp_path / 'tone.wav'
    _write_tone_wav(media)
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        seen.update(path=request.url.path, body=body, content_type=request.headers['content-type'])
        return httpx.Response(
            201,
            json={
                'job': {
                    'id': 'job-1',
                    'project_id': 'project-1',
                    'kind': 'transcription',
                    'status': 'pending',
                },
            },
        )

    exit_code = main(
        [
            '--api-base',
            'http://speaker.test',
            'task',
            'create',
            '--project-id',
            'project-1',
            '--file',
            str(media),
            '--auto-split',
        ],
        transport=httpx.MockTransport(handler),
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output['ok'] is True
    assert output['data']['job']['id'] == 'job-1'
    assert output['data']['chunks'][0]['offset_ms'] == 0
    assert seen['path'] == '/api/v2/projects/project-1/transcriptions/media-jobs'
    assert b'name="chunk_files"' in seen['body']
    assert b'name="chunk_manifest"' in seen['body']
    assert b'name="file"' not in seen['body']
