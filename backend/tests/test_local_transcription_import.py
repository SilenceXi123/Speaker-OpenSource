from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import math
import struct
import wave

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.job import Job
from app.models.project import Project
from main import app


def recognition():
    return {
        'engine': 'apple_speech', 'locale': 'zh_CN', 'system_version': '26.6.2',
        'duration_ms': 2000, 'sample_rate_hz': 22050, 'channel_count': 1,
        'segments': [{'text': '预算三百元', 'start_ms': 100, 'end_ms': 1700,
                      'words': [{'text': '预算', 'start_ms': 100, 'end_ms': 500},
                                {'text': '三百元', 'start_ms': 550, 'end_ms': 1700}]}],
    }


def audio_bytes():
    output = BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(b''.join(struct.pack('<h', int(6000 * math.sin(i / 22050 * 440 * 2 * math.pi))) for i in range(44100)))
    return output.getvalue()


@pytest.fixture()
def local_import_client(monkeypatch, tmp_path):
    from app.api.v2 import transcriptions
    from app.api.v1 import transcriptions as v1
    from app.services import jobs, transcription_tasks
    from app.core.config import settings

    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(transcriptions, 'SessionLocal', factory)
    monkeypatch.setattr(v1, 'SessionLocal', factory)
    monkeypatch.setattr(jobs, '_ensure_schema_once', lambda: None)
    monkeypatch.setattr(settings, 'local_upload_dir', str(tmp_path / 'upload'))
    monkeypatch.setattr(settings, 'local_media_library_dir', str(tmp_path / 'media'))
    monkeypatch.setattr(settings, 'storage_min_free_gb', 0)
    monkeypatch.setattr('app.services.local_files.ensure_storage_capacity', lambda *_args, **_kwargs: None)
    started = []
    monkeypatch.setattr(transcription_tasks, 'start_finalize_transcription_job', lambda *args: started.append(args) or True)
    monkeypatch.setattr(v1, 'FunAsrClient', lambda: pytest.fail('Apple import must not call cloud ASR'))
    with factory() as session:
        session.add(Project(id='11111111-1111-4111-8111-111111111111', name='本地识别测试', name_key='本地识别测试', status='active'))
        session.commit()
    yield TestClient(app), factory, started
    engine.dispose()


def submit(client, *, data=None, project='11111111-1111-4111-8111-111111111111'):
    fields = {
        'recognition': json.dumps(recognition(), ensure_ascii=False),
        'client_job_id': '22222222-2222-4222-8222-222222222222',
        'speaker_name': '主持人', 'job_kind': 'transcription', 'export_frame_rate': '25',
        'use_project_scripts': 'false',
    }
    fields.update(data or {})
    return client.post(f'/api/v2/projects/{project}/transcriptions/local-jobs', data=fields,
                       files={'file': ('speech.wav', audio_bytes(), 'audio/wav')})


def test_import_saves_apple_evidence_and_retries_without_duplicate_job(local_import_client):
    client, factory, started = local_import_client
    response = submit(client)
    assert response.status_code == 201, response.text
    job_id = response.json()['job']['id']
    repeated = submit(client)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()['job']['id'] == job_id
    with factory() as session:
        all_jobs = list(session.scalars(select(Job)))
        assert len(all_jobs) == 1
        job = all_jobs[0]
        assert job.project_id == '11111111-1111-4111-8111-111111111111'
        assert job.raw_payload['_apple_speech']['engine'] == 'apple_speech'
        assert job.speaker_mappings == {'speaker_0': '主持人'}
        assert job.source_duration_seconds == pytest.approx(2)
        assert job.provider_task_id is None
        assert job.source_local_path.endswith('speech.wav')
    assert started


def test_import_rejects_changed_evidence_under_same_request_identity(local_import_client):
    client, factory, _ = local_import_client
    assert submit(client).status_code == 201
    altered = deepcopy(recognition())
    altered['segments'][0]['words'][1]['text'] = '三千元'
    altered['segments'][0]['text'] = '预算三千元'
    response = submit(client, data={'recognition': json.dumps(altered)})
    assert response.status_code == 409
    with factory() as session:
        assert len(list(session.scalars(select(Job)))) == 1


def test_import_validates_project_and_audio_duration(local_import_client):
    client, _, started = local_import_client
    assert submit(client, project='33333333-3333-4333-8333-333333333333').status_code == 404
    altered = recognition()
    altered['duration_ms'] = 20000
    response = submit(client, data={'recognition': json.dumps(altered)})
    assert response.status_code == 422
    assert not started


def test_sync_local_import_never_queries_funasr(local_import_client):
    client, _, _ = local_import_client
    response = submit(client)
    assert response.status_code == 201, response.text
    job_id = response.json()['job']['id']
    synced = client.post(f'/api/v1/transcriptions/jobs/{job_id}/sync')
    assert synced.status_code == 200, synced.text
    assert synced.json()['raw_payload']['_apple_speech']['engine'] == 'apple_speech'


def test_local_import_finalizer_uses_existing_planner_and_actual_run_times(local_import_client, monkeypatch):
    from app.services import transcription_tasks
    from app.services.deepseek_subtitle_contract import run_deepseek_plan_window
    client, factory, _ = local_import_client
    calls = []

    class Planner:
        def plan_window(self, *, window, **kwargs):
            calls.append(window.window_id)
            return run_deepseek_plan_window(window, lambda _: {
                'window_id': window.window_id, 'boundary_votes': [], 'correction_candidate_ids': [],
            })

    monkeypatch.setattr('app.services.deepseek_subtitle_client.DeepSeekSubtitlePlannerClient', Planner)
    monkeypatch.setattr(transcription_tasks, 'SessionLocal', factory)
    monkeypatch.setattr(transcription_tasks, 'FunAsrClient', lambda: pytest.fail('must not run cloud ASR'))
    monkeypatch.setattr(transcription_tasks, 'match_job_voiceprints', lambda *_: pytest.fail('must not infer Apple speaker identity'))
    response = submit(client)
    assert response.status_code == 201, response.text
    job_id = response.json()['job']['id']
    transcription_tasks._run_finalize_transcription_job(job_id, [])
    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status in {'completed', 'completed_with_warnings'}, job.error_message
        assert calls
        assert job.speaker_mappings == {'speaker_0': '主持人'}
        assert job.pipeline_artifacts['evidence_subtitle_v2']['timing_policy'] == 'apple_attributed_run'
        assert all(row['timingEvidence'] == 'apple_attributed_run' for row in job.normalized_segments)
        assert job.normalized_segments[0]['startMs'] == 100
        assert job.normalized_segments[-1]['endMs'] == 1700


def test_retry_without_script_recovers_original_job_but_explicit_changed_script_is_rejected(local_import_client):
    client, factory, _ = local_import_client
    response = submit(client, data={'script_text': '预算三百元'})
    assert response.status_code == 201, response.text
    original = response.json()['job']
    repeated = submit(client)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()['job']['id'] == original['id']
    assert repeated.json()['job']['script_context'] == original['script_context']
    assert submit(client, data={'script_text': '预算三百元'}).status_code == 201
    assert submit(client, data={'script_text': '预算三千元'}).status_code == 409
    assert submit(client, data={'export_frame_rate': '50'}).status_code == 409
    altered = recognition()
    altered['segments'][0]['text'] = '预算三千元'
    altered['segments'][0]['words'][1]['text'] = '三千元'
    assert submit(client, data={'recognition': json.dumps(altered)}).status_code == 409
    with factory() as session:
        assert len(list(session.scalars(select(Job)))) == 1


def test_apple_lyrics_option_preserves_actual_times_and_reports_skipped_interpolation(local_import_client, monkeypatch):
    from app.services import transcription_tasks
    from app.services.deepseek_subtitle_contract import run_deepseek_plan_window
    client, factory, _ = local_import_client

    class Planner:
        def plan_window(self, *, window, **kwargs):
            return run_deepseek_plan_window(window, lambda _: {
                'window_id': window.window_id, 'boundary_votes': [], 'correction_candidate_ids': [],
            })

    monkeypatch.setattr('app.services.deepseek_subtitle_client.DeepSeekSubtitlePlannerClient', Planner)
    monkeypatch.setattr(transcription_tasks, 'SessionLocal', factory)
    response = submit(client, data={'lyrics_correction_mode': 'true', 'script_text': '预算三百元'})
    assert response.status_code == 201, response.text
    job_id = response.json()['job']['id']
    transcription_tasks._run_finalize_transcription_job(job_id, [])
    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == 'completed_with_warnings', job.error_message
        assert job.normalized_segments[0]['startMs'] == 100
        assert job.normalized_segments[-1]['endMs'] == 1700
        assert all(item['sourceTokenIds'] and item['timingEvidence'] == 'apple_attributed_run' for item in job.normalized_segments)
        report = job.pipeline_artifacts['lyrics_correction']
        assert report['reason'] == 'apple_timing_preserved'
        assert report['status'] != 'applied'
        assert 'Apple' in job.subtitle_enhancement_error
        assert job.script_context['reference_lines']


def test_apple_pipeline_failure_retains_whole_run_instead_of_estimating_character_times(local_import_client, monkeypatch):
    from app.services import jobs, transcription_tasks
    from app.services.subtitles import build_subtitle_cues
    client, factory, _ = local_import_client
    long_text = '我们今天来到这里是为了向大家介绍这个非常重要的字幕节目测试项目'
    source = recognition()
    source['segments'] = [{'text': long_text, 'start_ms': 100, 'end_ms': 1700, 'words': []}]
    monkeypatch.setattr(transcription_tasks, 'SessionLocal', factory)
    monkeypatch.setattr(jobs, 'build_job_evidence_subtitle_segments', lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError('V2 test failure')))
    response = submit(client, data={'recognition': json.dumps(source)})
    assert response.status_code == 201, response.text
    job_id = response.json()['job']['id']
    transcription_tasks._run_finalize_transcription_job(job_id, [])
    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == 'completed_with_warnings', job.error_message
        cues = build_subtitle_cues(job.normalized_segments, job.speaker_mappings)
        assert [(cue.start_ms, cue.end_ms, cue.clean_text) for cue in cues] == [(100, 1700, long_text)]
        assert all(item['v2FinalCue'] and item['timingEvidence'] == 'apple_attributed_run' for item in job.normalized_segments)
        assert job.pipeline_artifacts['evidence_subtitle_v2']['ai_policy'] == 'failed_apple_run_fallback'
        assert 'Apple' in job.subtitle_enhancement_error
