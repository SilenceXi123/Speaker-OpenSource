from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest


def _recognition():
    return {
        'engine': 'apple_speech', 'locale': 'zh_CN', 'duration_ms': 3000,
        'sample_rate_hz': 44100, 'channel_count': 2, 'system_version': '26.6.2',
        'segments': [{'text': '你好，这是三到四厘米。', 'start_ms': 100, 'end_ms': 2500, 'words': [
            {'text': '你好，', 'start_ms': 100, 'end_ms': 500},
            {'text': '这是三到四厘米。', 'start_ms': 700, 'end_ms': 2500},
        ]}],
    }


def _command():
    return {'operation': 'build', 'job': {
        'id': 'local-test', 'project_id': 'project-test', 'program_name': '节目',
        'source_local_path': '/素材/节目.wav', 'source_export_path': '/素材/节目.wav',
        'export_frame_rate': '25', 'kind': 'transcription',
    }, 'recognition': _recognition()}


def test_adapter_preserves_apple_run_times_and_unknown_person():
    from app.services.local_subtitles import recognition_to_payload, build_local_raw_snapshot
    recognition = _recognition()
    payload = recognition_to_payload(recognition)
    raw = build_local_raw_snapshot(payload, provider_task_id='local-test')
    assert raw.provider_model == 'apple_speech'
    assert raw.preflight_metadata.sample_rate_hz == 44100
    assert raw.preflight_metadata.channel_count == 2
    assert [(token.text, token.start_ms, token.end_ms, token.timing_precision) for token in raw.tokens] == [
        ('你好，', 100, 500, 'coarse_shared_span'), ('这是三到四厘米。', 700, 2500, 'coarse_shared_span'),
    ]
    assert all(token.speaker_id is None for token in raw.tokens)
    assert payload['_apple_speech']['diarization'] is False
    assert payload['_apple_speech']['speaker_assignment'] == 'unknown'
    assert recognition == _recognition()


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True, -1, 3001])
def test_adapter_rejects_untrustworthy_timing(bad):
    from app.services.local_subtitles import recognition_to_payload
    recognition = _recognition()
    recognition['segments'][0]['words'][0]['end_ms'] = bad
    with pytest.raises(ValueError):
        recognition_to_payload(recognition)


def test_build_edit_and_exports_preserve_raw_recognition_without_network(monkeypatch):
    import socket
    import sqlite3
    monkeypatch.setattr(socket.socket, 'connect', lambda *_args: pytest.fail('network forbidden'))
    monkeypatch.setattr(sqlite3, 'connect', lambda *_args, **_kw: pytest.fail('database forbidden'))
    from app.services.local_subtitles import handle_local_subtitle_command
    from app.schemas.job import JobDetailResponse
    job = handle_local_subtitle_command(_command())
    JobDetailResponse.model_validate(job)
    assert job['local_only'] is True
    assert job['local_recognition'] == _recognition()
    assert all(cue['speaker_name'] == '未区分说话人' for cue in job['local_cues'])
    assert 'Fun-ASR' not in json.dumps(job, ensure_ascii=False)
    raw = deepcopy(job['raw_payload'])
    edited = handle_local_subtitle_command({'operation': 'edit', 'job': job, 'action': 'subtitle_text', 'changes': [{'index': 1, 'clean_text': '人工校正的文字'}]})
    assert edited['local_cues'][0]['clean_text'] == '人工校正的文字'
    assert edited['raw_payload'] == raw and edited['local_recognition'] == _recognition()
    assert edited['pipeline_artifacts']['user_edit_patches_snapshot']['count'] == 1
    named = handle_local_subtitle_command({'operation': 'edit', 'job': edited, 'action': 'speakers', 'changes': [{'speaker_key': 'speaker_0', 'speaker_name': '主持人'}]})
    assert named['local_cues'][0]['speaker_name'] == '主持人'
    for fmt in ('srt', 'vtt', 'xml'):
        result = handle_local_subtitle_command({'operation': 'export', 'job': named, 'format': fmt, 'variant': 'full'})
        content = base64.b64decode(result['content_base64']).decode('utf-8')
        assert result['filename'].endswith('.' + fmt)
        assert ('xmeml' in content) if fmt == 'xml' else ('主持人' in content)


def test_long_apple_run_is_never_split_into_fabricated_character_times():
    from app.services.local_subtitles import handle_local_subtitle_command
    command = _command()
    text = '这是一条没有内部时间证据的很长的识别片段必须原样保留时间不能均分到每个字'
    command['recognition']['segments'] = [{'text': text, 'start_ms': 200, 'end_ms': 2900, 'words': []}]
    job = handle_local_subtitle_command(command)
    assert [(cue['start_ms'], cue['end_ms'], cue['clean_text']) for cue in job['local_cues']] == [(200, 2900, text)]


def test_offline_script_outputs_one_json_and_does_not_load_server_modules():
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'speaker_offline.py'
    result = subprocess.run([sys.executable, str(script)], input=json.dumps(_command()), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
    response = json.loads(result.stdout)
    assert response['ok'] is True
    assert response['data']['local_only'] is True


def test_export_refuses_overlapping_current_cues():
    from app.services.local_subtitles import handle_local_subtitle_command
    job = handle_local_subtitle_command(_command())
    duplicate = deepcopy(job['normalized_segments'][0])
    duplicate['segmentId'] = 'overlap'
    job['normalized_segments'].append(duplicate)
    with pytest.raises(ValueError, match='重叠|overlap'):
        handle_local_subtitle_command({'operation': 'export', 'job': job, 'format': 'srt'})


def test_local_build_retains_selected_cloud_options_and_manual_label():
    from app.services.local_subtitles import handle_local_subtitle_command
    command = _command()
    command['job'].update(use_project_scripts=False, lyrics_correction_mode=True, speaker_name=' 主持人 ')
    job = handle_local_subtitle_command(command)
    assert job['use_project_scripts'] is False
    assert job['lyrics_correction_mode'] is True
    assert job['raw_payload']['_apple_speech']['speaker_assignment'] == 'user_assigned'
    assert job['speaker_mappings'] == {'speaker_0': '主持人'}
    assert job['pipeline_artifacts']['local_processing']['cloud_ai_used'] is False


@pytest.mark.parametrize('operation', ['edit', 'srt', 'vtt', 'xml'])
def test_offline_subprocess_can_edit_and_export_without_server_imports(operation):
    from app.services.local_subtitles import handle_local_subtitle_command
    job = handle_local_subtitle_command(_command())
    command = {'operation': 'export', 'job': job, 'format': operation, 'variant': 'clean'}
    if operation == 'edit':
        command = {'operation': 'edit', 'job': job, 'action': 'subtitle_speakers', 'changes': [{'index': 1, 'speaker_name': '嘉宾'}]}
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'speaker_offline.py'
    result = subprocess.run([sys.executable, str(script)], input=json.dumps(command), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
    response = json.loads(result.stdout)
    assert response['ok'] is True
    if operation == 'edit':
        assert response['data']['local_cues'][0]['speaker_name'] == '嘉宾'
    else:
        assert response['data']['content_base64']


def test_offline_guard_rejects_network_database_env_and_provider_imports():
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'speaker_offline.py'
    source = '''import runpy, sys, socket, sqlite3
guard = runpy.run_path(sys.argv[1])['_offline_guard']
sys.addaudithook(guard)
attempts = [lambda: socket.socket(), lambda: sqlite3.connect(':memory:'),
            lambda: open('/tmp/.env'), lambda: __import__('app.core.config')]
for attempt in attempts:
    try:
        attempt()
    except PermissionError:
        continue
    raise AssertionError('offline boundary allowed a forbidden capability')
'''
    result = subprocess.run([sys.executable, '-c', source, str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_deleting_last_cue_refreshes_qc_and_preserves_generation_history():
    from app.services.local_subtitles import handle_local_subtitle_command
    command = _command()
    command['recognition']['segments'] = [{'text': '嗯', 'start_ms': 100, 'end_ms': 250, 'words': []}]
    job = handle_local_subtitle_command(command)
    history = deepcopy(job['pipeline_artifacts']['evidence_subtitle_v2'])
    edited = handle_local_subtitle_command({'operation': 'edit', 'job': job, 'action': 'delete_fillers', 'changes': []})
    assert edited['local_cues'] == []
    assert edited['local_clean_srt'] == ''
    assert edited['pipeline_artifacts']['cue_validation_report']['issue_count'] == 0
    assert edited['pipeline_artifacts']['evidence_subtitle_v2'] == history
    assert edited['local_recognition'] == command['recognition']
    assert edited['pipeline_artifacts']['timeline_revision'] != job['pipeline_artifacts']['timeline_revision']


def test_adapter_rejects_lost_text_or_out_of_order_runs():
    from app.services.local_subtitles import recognition_to_payload
    recognition = _recognition()
    recognition['segments'][0]['words'][0]['text'] = '漏字'
    with pytest.raises(ValueError, match='原文不一致'):
        recognition_to_payload(recognition)
    recognition = _recognition()
    recognition['segments'][0]['words'][1]['start_ms'] = 99
    with pytest.raises(ValueError, match='倒序|超出'):
        recognition_to_payload(recognition)
