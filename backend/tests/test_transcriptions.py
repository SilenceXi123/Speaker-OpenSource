from io import BytesIO
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree.ElementTree import fromstring
import zipfile

from fastapi import HTTPException, UploadFile

from app.api.v1.transcriptions import (
    _build_abort_message,
    _build_job_premiere_xml_content,
    _build_premiere_package_archive,
    _build_transcript_docx,
    _build_transcript_lines,
    _build_provider_task_entry,
    _build_provider_failure_message,
    _estimate_provider_poll_progress,
    _handle_dashscope_finish_event,
    _assert_exportable_subtitle_cues,
    _persist_job_provider_task_meta,
    _detect_media_kind,
    _fetch_remote_asset_bytes,
    _normalize_export_frame_rate,
    _normalize_subtitle_strategy,
    _parse_client_paths_payload,
    _parse_language_hints,
    _parse_vocabulary_id,
    _resolve_job_export_frame_rate,
    _resolve_client_export_path,
    _resolve_provider_task_meta,
    _resolve_script_context_inputs,
    _query_provider_tasks,
    apply_job_script_text_corrections,
    delete_job_subtitle_fillers,
    download_job_premiere_xml,
    download_job_transcript,
    download_job_subtitles,
    get_job_pipeline_artifacts,
    get_job_speakers,
    preview_job_subtitles,
    put_job_subtitle_text,
    put_job_speakers,
    submit_media_transcription_job,
    sync_transcription_job,
    update_job_script_context,
)
from app.core.config import settings
from app.models.job import Job
from app.schemas.speaker import SpeakerMappingItem, SpeakerMappingRequest
from app.schemas.subtitle import SubtitleCue, SubtitleTextOverrideRequest, SubtitleTextOverrideUpdate
from app.services.jobs import create_job, update_job_normalized_segments
from app.services.local_files import save_upload_file_locally
from app.services.media import (
    ASR_RECORDED_FILE_MAX_BYTES,
    ASR_RECORDED_FILE_MAX_SECONDS,
    DIARIZATION_SEGMENT_SECONDS,
    extract_recognition_audio,
    split_recognition_audio,
    validate_recognition_source_constraints,
)
from app.services.normalize import merge_funasr_payloads


def test_parse_language_hints_splits_csv() -> None:
    result = _parse_language_hints('zh, en ,ja')

    assert result == ['zh']


def test_parse_vocabulary_id_trims_whitespace() -> None:
    assert _parse_vocabulary_id('  vocab-123  ') == 'vocab-123'


def test_resolve_script_context_inputs_combines_multiple_files() -> None:
    files = [
        UploadFile(filename='第一份.txt', file=BytesIO('SHARK 的作品是冰棍儿'.encode())),
        UploadFile(filename='第二份.txt', file=BytesIO('RENZER 的舞台很有 vibe'.encode())),
    ]

    context, source_name, metrics, sources = asyncio.run(_resolve_script_context_inputs(script_files=files))

    assert context is not None
    assert source_name == '第一份.txt + 第二份.txt'
    assert context['source_names'] == ['第一份.txt', '第二份.txt']
    assert context['summary']['source_count'] == 2
    assert metrics['script_source_count'] == 2
    assert [source['source_name'] for source in sources] == ['第一份.txt', '第二份.txt']


def test_resolve_script_context_inputs_marks_selected_files_as_lyrics_only_when_enabled() -> None:
    lyrics = UploadFile(
        filename='歌词.srt',
        file=BytesIO(b'1\n00:00:10,000 --> 00:00:12,000\nWe never give up\n'),
    )

    context, _, metrics, sources = asyncio.run(_resolve_script_context_inputs(
        script_files=[lyrics],
        lyrics_correction_mode=True,
    ))

    assert context is not None
    assert context['lyrics_correction_mode'] is True
    assert context['lyrics_references'][0]['timing_mode'] == 'timed_srt'
    assert sources[0]['script_context']['lyrics_references'][0]['line_count'] == 1
    assert metrics['script_source_count'] == 1


def test_v2_project_route_forwards_multiple_scripts(monkeypatch) -> None:
    from app.api.v2.transcriptions import submit_project_media_transcription_job

    received = {}

    async def fake_submit(**kwargs):
        received.update(kwargs)
        return {'job': {'id': 'job-1'}}

    monkeypatch.setattr('app.api.v2.transcriptions.submit_media_transcription_job', fake_submit)
    media = UploadFile(filename='audio.wav', file=BytesIO(b'audio'))
    scripts = [UploadFile(filename='台本一.txt', file=BytesIO(b'one'))]

    asyncio.run(submit_project_media_transcription_job(
        project_id='project-1',
        file=media,
        script_file=None,
        script_files=scripts,
        lyrics_correction_mode=True,
        script_text=None,
        use_project_scripts=False,
    ))

    assert received['script_files'] == scripts
    assert received['lyrics_correction_mode'] is True
    assert received['use_project_scripts'] is False
    assert _parse_vocabulary_id('   ') is None


def test_submit_media_job_prioritizes_current_script_over_project_history(monkeypatch, tmp_path) -> None:
    from uuid import uuid4

    from app.db.session import SessionLocal
    from app.schemas.project import ProjectCreateRequest
    from app.schemas.upload import UploadResponse
    from app.services.lexicon_imports import save_project_script_evidence
    from app.services.projects import create_project

    media_path = tmp_path / 'audio.mp3'
    media_path.write_bytes(b'audio')
    session = SessionLocal()
    try:
        project = create_project(
            session,
            ProjectCreateRequest(name=f'台本优先级 {uuid4().hex[:8]}'),
        )
        project_id = project.id
        save_project_script_evidence(
            session,
            tenant_id='default',
            project_id=project_id,
            source_name='历史台本.txt',
            text='欢迎使用 AI 创作。',
        )
        session.commit()
    finally:
        session.close()

    upload = UploadResponse(
        public_url='https://example.com/audio.mp3',
        object_key='uploads/audio.mp3',
        content_type='audio/mpeg',
        size_bytes=5,
    )
    chunk = SimpleNamespace(index=0, path=media_path, offset_ms=0, duration_seconds=1.0)

    async def fake_save_upload(*_args, **_kwargs):
        return media_path

    monkeypatch.setattr(
        'app.api.v1.transcriptions.save_upload_file_locally',
        fake_save_upload,
    )
    monkeypatch.setattr('app.api.v1.transcriptions.copy_local_file_to_library', lambda **_kwargs: media_path)
    monkeypatch.setattr('app.api.v1.transcriptions.validate_recognition_source_constraints', lambda *_args: None)
    monkeypatch.setattr('app.api.v1.transcriptions.prepare_recognition_audio', lambda *_args, **_kwargs: media_path)
    monkeypatch.setattr('app.api.v1.transcriptions.split_recognition_audio', lambda *_args: [chunk])
    monkeypatch.setattr('app.api.v1.transcriptions.get_media_duration_seconds', lambda *_args: 1.0)
    monkeypatch.setattr('app.api.v1.transcriptions.upload_local_file_to_oss', lambda **_kwargs: upload)
    monkeypatch.setattr(
        'app.api.v1.transcriptions.FunAsrClient',
        lambda: SimpleNamespace(
            submit_transcription=lambda **_kwargs: {'task_id': 'task-1', 'request_id': 'request-1'},
        ),
    )

    response = asyncio.run(
        submit_media_transcription_job(
            file=UploadFile(filename='audio.mp3', file=BytesIO(b'audio')),
            language_hints=None,
            speaker_count=None,
            vocabulary_id=None,
            project_id=project_id,
            program_name=None,
            client_source_path=None,
            job_kind='transcription',
            export_frame_rate='25',
            subtitle_strategy='enhanced',
            script_file=None,
            script_files=None,
            script_text='欢迎使用 ai 创作。',
            use_project_scripts=True,
            lyrics_correction_mode=False,
            remove_standalone_fillers=False,
            subtitle_length_mode='smart',
            subtitle_max_chars=None,
        )
    )

    assert response.job.script_context is not None
    assert response.job.script_context['reference_lines'] == ['欢迎使用 ai 创作。']
    assert response.job.processing_metrics['script_context_current_source_precedence'] == 1


def test_submit_media_job_accepts_chunk_batch_and_preserves_offsets(monkeypatch, tmp_path) -> None:
    from uuid import uuid4

    from fastapi import UploadFile

    from app.db.session import SessionLocal
    from app.schemas.project import ProjectCreateRequest
    from app.schemas.upload import UploadResponse
    from app.services.projects import create_project

    session = SessionLocal()
    try:
        project = create_project(session, ProjectCreateRequest(name=f'分片批次 {uuid4().hex[:8]}'))
        project_id = project.id
    finally:
        session.close()

    chunk_paths = [tmp_path / 'chunk-000.wav', tmp_path / 'chunk-001.wav']
    for path in chunk_paths:
        path.write_bytes(b'RIFFxxxxWAVE')
    upload = UploadResponse(
        public_url='https://example.com/chunk.wav',
        object_key='uploads/chunk.wav',
        content_type='audio/wav',
        size_bytes=1,
    )

    async def fake_save_upload(*args, **_kwargs):
        upload_file = next(arg for arg in args if isinstance(arg, UploadFile))
        return next(path for path in chunk_paths if path.name == upload_file.filename)

    monkeypatch.setattr('app.api.v1.transcriptions.save_upload_file_locally', fake_save_upload)
    monkeypatch.setattr('app.api.v1.transcriptions.get_media_duration_seconds', lambda *_args: 100.0)
    monkeypatch.setattr('app.api.v1.transcriptions.ensure_diarization_friendly_duration', lambda *_args: None)
    monkeypatch.setattr('app.api.v1.transcriptions.upload_local_file_to_oss', lambda **_kwargs: upload)
    monkeypatch.setattr(
        'app.api.v1.transcriptions.FunAsrClient',
        lambda: SimpleNamespace(
            submit_transcription=lambda **_kwargs: {'task_id': 'task-1', 'request_id': 'request-1'},
        ),
    )

    response = asyncio.run(
        submit_media_transcription_job(
            file=None,
            language_hints=None,
            speaker_count=None,
            vocabulary_id=None,
            project_id=project_id,
            program_name=None,
            client_source_path=None,
            job_kind='transcription',
            export_frame_rate='25',
            subtitle_strategy='evidence',
            script_file=None,
            script_files=None,
            script_text=None,
            use_project_scripts=True,
            lyrics_correction_mode=False,
            remove_standalone_fillers=False,
            subtitle_length_mode='smart',
            subtitle_max_chars=None,
            chunk_files=[
                UploadFile(filename='chunk-000.wav', file=BytesIO(b'RIFFxxxxWAVE')),
                UploadFile(filename='chunk-001.wav', file=BytesIO(b'RIFFxxxxWAVE')),
            ],
            chunk_manifest=json.dumps([
                {'file': 'chunk-001.wav', 'offset_ms': 100000},
                {'file': 'chunk-000.wav', 'offset_ms': 0},
            ]),
        )
    )

    assert response.job.kind == 'transcription'
    assert response.job.source_duration_seconds == 200.0
    assert [entry['offset_ms'] for entry in response.job.provider_task_meta] == [0, 100000]


def test_normalize_export_frame_rate_accepts_supported_values() -> None:
    assert _normalize_export_frame_rate('25') == '25'
    assert _normalize_export_frame_rate('29.97') == '29.97'
    assert _normalize_export_frame_rate('50') == '50'


def test_normalize_subtitle_strategy_accepts_supported_values() -> None:
    assert _normalize_subtitle_strategy('standard') == 'standard'
    assert _normalize_subtitle_strategy('enhanced') == 'enhanced'
    assert _normalize_subtitle_strategy('pro') == 'pro'


def test_normalize_subtitle_strategy_defaults_to_enhanced_for_missing_or_unknown() -> None:
    assert _normalize_subtitle_strategy(None) == 'enhanced'
    assert _normalize_subtitle_strategy('unknown') == 'enhanced'


def test_sync_completed_with_warnings_is_terminal(monkeypatch) -> None:
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        job = create_job(session, source_url='https://example.com/source.wav')
        job.status = 'completed_with_warnings'
        job.provider_task_id = 'task-done'
        job.provider_task_meta = [{'task_id': 'task-done'}]
        session.commit()

        class UnexpectedClient:
            def query_task(self, task_id: str) -> dict:
                raise AssertionError('terminal jobs must not query or finalize the provider again')

        monkeypatch.setattr(
            'app.api.v1.transcriptions.FunAsrClient',
            lambda: UnexpectedClient(),
        )

        response = sync_transcription_job(job.id)

        assert response.status == 'completed_with_warnings'
    finally:
        session.close()


def test_resolve_job_export_frame_rate_prefers_locked_job_value() -> None:
    job = SimpleNamespace(export_frame_rate='29.97')

    assert _resolve_job_export_frame_rate(job, '25') == '29.97'


def test_detect_media_kind_supports_audio_and_video_uploads() -> None:
    assert _detect_media_kind(UploadFile(filename='demo.wav', file=None, headers={'content-type': 'audio/wav'})) == 'audio'
    assert _detect_media_kind(UploadFile(filename='demo.mp4', file=None, headers={'content-type': 'video/mp4'})) == 'video'


def test_assert_exportable_subtitle_cues_blocks_p0_validation_errors() -> None:
    job = SimpleNamespace(script_context={'terms': ['沈阳']})
    cues = [
        SubtitleCue(
            index=1,
            start_ms=0,
            end_ms=1000,
            speaker_key='speaker_0',
            speaker_name='主持人',
            full_text='主持人：沈',
            clean_text='沈',
        ),
        SubtitleCue(
            index=2,
            start_ms=1000,
            end_ms=2000,
            speaker_key='speaker_0',
            speaker_name='主持人',
            full_text='主持人：阳赛艇中心',
            clean_text='阳赛艇中心',
        ),
    ]

    try:
        _assert_exportable_subtitle_cues(job, cues)
    except HTTPException as exc:
        assert exc.status_code == 409
        assert 'P0' in exc.detail
        assert '沈阳' in exc.detail
    else:
        raise AssertionError('expected HTTPException')


def test_assert_exportable_subtitle_cues_allows_standalone_filler_particle() -> None:
    job = SimpleNamespace(script_context=None, pipeline_artifacts={})
    cues = [
        SubtitleCue(
            index=1,
            start_ms=0,
            end_ms=320,
            speaker_key='speaker_0',
            speaker_name='主持人',
            full_text='主持人：哦',
            clean_text='哦',
        ),
        SubtitleCue(
            index=2,
            start_ms=320,
            end_ms=1200,
            speaker_key='speaker_0',
            speaker_name='主持人',
            full_text='主持人：欢迎大家',
            clean_text='欢迎大家',
        ),
    ]

    _assert_exportable_subtitle_cues(job, cues)


def test_assert_exportable_subtitle_cues_does_not_reject_validated_v2_language_boundaries() -> None:
    def cue(index: int, start_ms: int, end_ms: int, text: str) -> SubtitleCue:
        return SubtitleCue(
            index=index,
            start_ms=start_ms,
            end_ms=end_ms,
            speaker_key='speaker_0',
            speaker_name='嘉宾',
            full_text=f'嘉宾：{text}',
            clean_text=text,
        )

    job = SimpleNamespace(
        script_context=None,
        pipeline_artifacts={},
        normalized_segments=[{'v2FinalCue': True}] * 3,
    )
    cues = [
        cue(1, 0, 400, '哟'),
        cue(2, 400, 1400, '非常优雅'),
        cue(3, 1400, 2600, '好听的小旋律'),
    ]

    _assert_exportable_subtitle_cues(job, cues)

    try:
        _assert_exportable_subtitle_cues(job, [cue(1, 400, 400, '哟')])
    except HTTPException as exc:
        assert exc.status_code == 409
        assert '时间范围无效' in exc.detail
    else:
        raise AssertionError('expected HTTPException for invalid V2 timing')


def test_extract_recognition_audio_raises_for_missing_file() -> None:
    try:
        extract_recognition_audio(Path('/tmp/not-exists-video.mp4'))
    except HTTPException as exc:
        assert exc.status_code == 400
        assert '不存在' in exc.detail
    else:
        raise AssertionError('expected HTTPException')


def test_split_recognition_audio_returns_single_chunk_within_limit(monkeypatch, tmp_path) -> None:
    audio_path = tmp_path / 'demo.wav'
    audio_path.write_bytes(b'audio')
    monkeypatch.setattr('app.services.media.get_media_duration_seconds', lambda _path: 120)

    chunks = split_recognition_audio(audio_path)

    assert len(chunks) == 1
    assert chunks[0].path == audio_path
    assert chunks[0].offset_ms == 0


def test_validate_recognition_source_constraints_rejects_over_official_duration(monkeypatch, tmp_path) -> None:
    audio_path = tmp_path / 'too-long.wav'
    audio_path.write_bytes(b'audio')
    monkeypatch.setattr('app.services.media.get_media_duration_seconds', lambda _path: ASR_RECORDED_FILE_MAX_SECONDS + 1)

    try:
        validate_recognition_source_constraints(audio_path)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert '12 小时' in exc.detail
        assert '2GB' in exc.detail
    else:
        raise AssertionError('expected HTTPException')


def test_validate_recognition_source_constraints_rejects_over_official_size(monkeypatch, tmp_path) -> None:
    audio_path = tmp_path / 'too-large.wav'
    audio_path.write_bytes(b'audio')
    monkeypatch.setattr('app.services.media.get_media_duration_seconds', lambda _path: 60)
    original_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        if self == audio_path:
            return SimpleNamespace(st_size=ASR_RECORDED_FILE_MAX_BYTES + 1)
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', fake_stat)

    try:
        validate_recognition_source_constraints(audio_path)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert '2GB' in exc.detail
    else:
        raise AssertionError('expected HTTPException')


def test_validate_recognition_source_constraints_rejects_missing_audio_track(monkeypatch, tmp_path) -> None:
    audio_path = tmp_path / 'no-audio.wav'
    audio_path.write_bytes(b'audio')
    monkeypatch.setattr('app.services.media.get_media_duration_seconds', lambda _path: 15)

    def fake_run(_command, capture_output=True, text=True):
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr('app.services.media.subprocess.run', fake_run)

    try:
        validate_recognition_source_constraints(audio_path)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert '没有检测到可识别的音轨' in exc.detail
    else:
        raise AssertionError('expected HTTPException')


def test_validate_recognition_source_constraints_rejects_silent_audio(monkeypatch, tmp_path) -> None:
    audio_path = tmp_path / 'silent.wav'
    audio_path.write_bytes(b'audio')
    monkeypatch.setattr('app.services.media.get_media_duration_seconds', lambda _path: 15)

    def fake_run(command, capture_output=True, text=True):
        if command and command[0] == 'ffprobe':
            return SimpleNamespace(returncode=0, stdout='audio\n', stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='[Parsed_volumedetect_0] max_volume: -inf dB\n')

    monkeypatch.setattr('app.services.media.subprocess.run', fake_run)

    try:
        validate_recognition_source_constraints(audio_path)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert '没有检测到有效声音' in exc.detail
    else:
        raise AssertionError('expected HTTPException')


def test_save_upload_file_locally_rejects_when_stream_exceeds_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, 'local_upload_dir', str(tmp_path))

    class FakeUpload:
        filename = 'too-large.wav'

        def __init__(self):
            self._chunks = iter([b'ab', b'cd', b''])

        async def read(self, _size: int = -1):
            return next(self._chunks)

    try:
        asyncio.run(save_upload_file_locally(FakeUpload(), max_bytes=3))
    except HTTPException as exc:
        assert exc.status_code == 413
        assert '2GB' in exc.detail
    else:
        raise AssertionError('expected HTTPException')
    assert not list(tmp_path.iterdir())


def test_validate_recognition_source_constraints_allows_diarization_chunking_range(monkeypatch, tmp_path) -> None:
    audio_path = tmp_path / 'chunkable.wav'
    audio_path.write_bytes(b'audio')
    monkeypatch.setattr('app.services.media.get_media_duration_seconds', lambda _path: DIARIZATION_SEGMENT_SECONDS + 60)

    def fake_run(command, capture_output=True, text=True):
        if command and command[0] == 'ffprobe':
            return SimpleNamespace(returncode=0, stdout='audio\n', stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='[Parsed_volumedetect_0] max_volume: -12.0 dB\n')

    monkeypatch.setattr('app.services.media.subprocess.run', fake_run)

    validate_recognition_source_constraints(audio_path)


def test_media_job_stores_strict_subtitle_length_profile(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient
    from main import app
    from app.schemas.upload import UploadResponse

    media_path = tmp_path / 'sample.wav'
    media_path.write_bytes(b'RIFF0000WAVE')

    async def fake_save_upload_file_locally(*args, **kwargs):
        return media_path

    class FakeClient:
        def submit_transcription(self, *args, **kwargs):
            return {'task_id': 'task-1', 'request_id': 'request-1'}

    upload = UploadResponse(
        object_key='audio/sample.wav',
        public_url='https://example.test/audio.wav',
        content_type='audio/wav',
        size_bytes=media_path.stat().st_size,
    )
    chunk = SimpleNamespace(path=media_path, offset_ms=0, duration_seconds=1.0, index=0)

    monkeypatch.setattr('app.api.v1.transcriptions.save_upload_file_locally', fake_save_upload_file_locally)
    monkeypatch.setattr('app.api.v1.transcriptions.validate_recognition_source_constraints', lambda *args, **kwargs: None)
    monkeypatch.setattr('app.api.v1.transcriptions.copy_local_file_to_library', lambda *args, **kwargs: media_path)
    monkeypatch.setattr('app.api.v1.transcriptions.prepare_recognition_audio', lambda *args, **kwargs: media_path)
    monkeypatch.setattr('app.api.v1.transcriptions.split_recognition_audio', lambda *args, **kwargs: [chunk])
    monkeypatch.setattr('app.api.v1.transcriptions.upload_local_file_to_oss', lambda *args, **kwargs: upload)
    monkeypatch.setattr('app.api.v1.transcriptions.get_media_duration_seconds', lambda *args, **kwargs: 1.0)
    monkeypatch.setattr('app.api.v1.transcriptions.FunAsrClient', lambda: FakeClient())

    client = TestClient(app)
    response = client.post(
        '/api/v1/transcriptions/media-jobs',
        data={
            'program_name': '测试项目',
            'job_kind': 'transcription',
            'subtitle_length_mode': 'strict',
            'subtitle_max_chars': '9',
        },
        files={'file': ('sample.wav', b'RIFF0000WAVE', 'audio/wav')},
    )

    assert response.status_code == 201
    job = response.json()['job']
    assert job['processing_metrics']['subtitle_length_profile'] == {'mode': 'strict', 'max_chars': 9}


def test_resolve_provider_task_meta_falls_back_to_single_task() -> None:
    job = SimpleNamespace(
        provider_task_meta=None,
        provider_task_id='task-1',
        provider_request_id='req-1',
        source_url='https://example.com/source.wav',
        source_duration_seconds=123.0,
    )

    result = _resolve_provider_task_meta(job)

    assert result == [
        _build_provider_task_entry(
            task_id='task-1',
            request_id='req-1',
            file_url='https://example.com/source.wav',
            offset_ms=0,
            duration_seconds=123.0,
            chunk_index=0,
            speaker_offset=0,
        )
    ]


def test_build_abort_message_distinguishes_provider_cancel_result() -> None:
    assert '已取消 2 个仍在排队中的百炼分段任务' in _build_abort_message(2, 0)
    assert '本地将不再继续整理结果' in _build_abort_message(1, 3)
    assert _build_abort_message(0, 2) == '任务已中止。当前云端分段已开始或已结束，本地将不再继续整理结果'


def test_query_provider_tasks_preserves_input_order() -> None:
    class FakeClient:
        def query_task(self, task_id: str) -> dict:
            return {'output': {'task_id': task_id, 'task_status': 'SUCCEEDED'}}

    result = _query_provider_tasks(FakeClient(), [
        {'task_id': 'task-1'},
        {'task_id': 'task-2'},
        {'task_id': 'task-3'},
    ])

    assert [item['output']['task_id'] for item in result] == ['task-1', 'task-2', 'task-3']


def test_build_provider_failure_message_includes_dashscope_diagnostics() -> None:
    message = _build_provider_failure_message(
        {
            'request_id': 'req-123',
            'output': {
                'task_id': 'task-123',
                'task_status': 'FAILED',
                'code': 'InvalidFile.DownloadFailed',
                'message': 'failed to download file',
                'results': [
                    {
                        'subtask_status': 'FAILED',
                        'code': 'DownloadFailed',
                        'message': 'source url is not reachable',
                    }
                ],
            },
        },
        {'chunk_index': 0, 'file_url': 'https://example.com/source.wav'},
    )

    assert '百炼任务状态异常: FAILED' in message
    assert 'InvalidFile.DownloadFailed' in message
    assert 'failed to download file' in message
    assert 'DownloadFailed' in message
    assert 'source url is not reachable' in message
    assert 'task-123' in message
    assert 'req-123' in message
    assert 'source.wav' in message


def test_estimate_provider_poll_progress_advances_single_running_task() -> None:
    job = SimpleNamespace(created_at=SimpleNamespace(timestamp=lambda: 1000.0))

    assert _estimate_provider_poll_progress(job, index=0, total_parts=1, task_status='RUNNING', now_seconds=1000.0) == 8
    assert _estimate_provider_poll_progress(job, index=0, total_parts=1, task_status='RUNNING', now_seconds=1120.0) > 8
    assert _estimate_provider_poll_progress(job, index=0, total_parts=1, task_status='RUNNING', now_seconds=4000.0) <= 60


def test_sync_retries_dashscope_instance_pool_exhausted(monkeypatch) -> None:
    from app.db.session import SessionLocal

    session = SessionLocal()
    submitted: dict[str, object] = {}
    try:
        job = create_job(session, source_url='https://example.com/source.wav')
        provider_meta = [
            _build_provider_task_entry(
                task_id='task-old',
                request_id='req-old',
                file_url='https://example.com/source.wav',
                offset_ms=0,
                duration_seconds=120.0,
                chunk_index=0,
                speaker_offset=0,
                language_hints=['zh'],
                speaker_count=2,
                vocabulary_id='vocab-123',
            )
        ]
        _persist_job_provider_task_meta(session, job.id, provider_meta)
        job.provider_task_id = 'task-old'
        job.provider_request_id = 'req-old'
        job.status = 'running'
        session.commit()

        class FakeClient:
            def query_task(self, task_id: str) -> dict:
                assert task_id == 'task-old'
                return {
                    'request_id': 'req-failed',
                    'output': {
                        'task_id': task_id,
                        'task_status': 'FAILED',
                        'code': 'INSTANCE_POOL_EXHAUSTED',
                        'message': '[asr]Instance pool exhausted!',
                        'results': [
                            {
                                'subtask_status': 'FAILED',
                                'code': 'INSTANCE_POOL_EXHAUSTED',
                                'message': '[asr]Instance pool exhausted!',
                            }
                        ],
                    },
                }

            def submit_transcription(self, file_url, language_hints, speaker_count, vocabulary_id):
                submitted.update(
                    file_url=file_url,
                    language_hints=language_hints,
                    speaker_count=speaker_count,
                    vocabulary_id=vocabulary_id,
                )
                return {'task_id': 'task-retry', 'request_id': 'req-retry'}

        monkeypatch.setattr(settings, 'dashscope_asr_transient_retry_limit', 3)
        monkeypatch.setattr('app.api.v1.transcriptions.FunAsrClient', lambda: FakeClient())

        response = sync_transcription_job(job.id)

        session.refresh(job)
        assert response.status == 'processing'
        assert response.error_message is None
        assert '百炼资源繁忙' in (response.processing_message or '')
        assert job.provider_task_id == 'task-retry'
        assert job.provider_request_id == 'req-retry'
        assert job.provider_task_meta[0]['task_id'] == 'task-retry'
        assert job.provider_task_meta[0]['provider_retry_count'] == 1
        assert job.provider_task_meta[0]['previous_task_ids'] == ['task-old']
        assert submitted == {
            'file_url': 'https://example.com/source.wav',
            'language_hints': ['zh'],
            'speaker_count': 2,
            'vocabulary_id': 'vocab-123',
        }
    finally:
        session.close()


def test_handle_dashscope_finish_event_updates_partial_progress(monkeypatch) -> None:
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        job = create_job(session, source_url='https://example.com/source.wav')
        provider_meta = [
            _build_provider_task_entry('task-1', 'req-1', 'https://example.com/1.wav', 0, 100.0, 0, 0),
            _build_provider_task_entry('task-2', 'req-2', 'https://example.com/2.wav', 1000, 100.0, 1, 1000),
        ]
        _persist_job_provider_task_meta(session, job.id, provider_meta)
        job.provider_task_id = 'task-1'
        job.provider_request_id = 'req-1'
        job.status = 'running'
        session.commit()
        session.refresh(job)

        class FakeClient:
            def query_task(self, task_id: str) -> dict:
                assert task_id == 'task-1'
                return {
                    'output': {
                        'task_status': 'SUCCEEDED',
                        'results': [{'subtask_status': 'SUCCEEDED', 'transcription_url': 'https://example.com/1.json'}],
                    }
                }

        result = _handle_dashscope_finish_event(
            session=session,
            client=FakeClient(),
            job_id=job.id,
            task_id='task-1',
            task_status='SUCCEEDED',
            event_data={'request_id': 'req-1'},
        )

        session.refresh(job)
        assert result['started_finalize'] is False
        assert job.status == 'running'
        assert job.processing_message == '已收到百炼完成通知，当前 1/2 段已完成'
        assert job.provider_task_meta[0]['transcription_url'] == 'https://example.com/1.json'
    finally:
        session.close()


def test_handle_dashscope_finish_event_starts_finalize_when_all_done(monkeypatch) -> None:
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        job = create_job(session, source_url='https://example.com/source.wav')
        provider_meta = [
            {
                **_build_provider_task_entry('task-1', 'req-1', 'https://example.com/1.wav', 0, 100.0, 0, 0),
                'transcription_url': 'https://example.com/1.json',
            },
            _build_provider_task_entry('task-2', 'req-2', 'https://example.com/2.wav', 1000, 100.0, 1, 1000),
        ]
        _persist_job_provider_task_meta(session, job.id, provider_meta)
        job.provider_task_id = 'task-1'
        job.provider_request_id = 'req-1'
        job.status = 'running'
        session.commit()
        session.refresh(job)

        started: dict[str, object] = {}

        class FakeClient:
            def query_task(self, task_id: str) -> dict:
                assert task_id == 'task-2'
                return {
                    'output': {
                        'task_status': 'SUCCEEDED',
                        'results': [{'subtask_status': 'SUCCEEDED', 'transcription_url': 'https://example.com/2.json'}],
                    }
                }

        monkeypatch.setattr('app.api.v1.transcriptions.is_finalize_transcription_job_running', lambda _job_id: False)
        monkeypatch.setattr(
            'app.api.v1.transcriptions.start_finalize_transcription_job',
            lambda job_id, provider_task_meta: started.update({'job_id': job_id, 'count': len(provider_task_meta)}),
        )

        result = _handle_dashscope_finish_event(
            session=session,
            client=FakeClient(),
            job_id=job.id,
            task_id='task-2',
            task_status='SUCCEEDED',
            event_data={'request_id': 'req-2'},
        )

        session.refresh(job)
        assert result['started_finalize'] is True
        assert job.status == 'finalizing'
        assert started == {'job_id': job.id, 'count': 2}
    finally:
        session.close()


def test_delete_job_subtitle_fillers_updates_normalized_segments() -> None:
    from app.db.session import SessionLocal
    from app.services.subtitles import build_subtitle_cues

    session = SessionLocal()
    try:
        job = create_job(session, source_url='https://example.com/source.wav')
        job.status = 'completed'
        job.normalized_segments = [
            {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 300, 'text': '嗯'},
            {'speakerKey': 'speaker_0', 'startMs': 500, 'endMs': 1600, 'text': '正式内容'},
            {'speakerKey': 'speaker_0', 'startMs': 1800, 'endMs': 2400, 'text': '啊这个不能删'},
        ]
        job.speaker_mappings = {'speaker_0': '主持人'}
        session.commit()

        response = delete_job_subtitle_fillers(job.id)

        session.refresh(job)
        cues = build_subtitle_cues(job.normalized_segments, job.speaker_mappings)
        assert response.deleted_count == 2
        assert [cue.clean_text for cue in cues] == ['正式内容', '这个不能删']
    finally:
        session.close()


def test_put_job_speakers_can_return_voiceprint_learning_result(monkeypatch) -> None:
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        job = create_job(session, source_url='https://example.com/source.wav')
        job.status = 'completed'
        job.source_local_path = '/tmp/source.wav'
        job.normalized_segments = [
            {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 3000, 'text': '大家好欢迎来到节目'},
        ]
        session.commit()

        monkeypatch.setattr(
            'app.api.v1.transcriptions.learn_voiceprint_candidates_for_job',
            lambda _session, _job: SimpleNamespace(
                created_count=2,
                skipped_count=1,
                duplicate_count=0,
                conflict_count=0,
                error_count=0,
                warnings=[],
                candidate_sample_ids=['sample-1', 'sample-2'],
            ),
        )

        response = put_job_speakers(
            job.id,
            SpeakerMappingRequest(mappings=[SpeakerMappingItem(speaker_key='speaker_0', speaker_name='刘权')]),
            learn_voiceprints=True,
        )

        assert response.voiceprint_learning is not None
        assert response.voiceprint_learning.created_count == 2
        assert response.voiceprint_learning.skipped_count == 1
        assert response.voiceprint_learning.candidate_sample_ids == ['sample-1', 'sample-2']
    finally:
        session.close()


def test_job_project_uuid_controls_speaker_memory_even_when_legacy_names_disagree() -> None:
    from uuid import uuid4

    from app.db.session import SessionLocal
    from app.schemas.project import ProjectCreateRequest
    from app.services.projects import create_project
    from app.services.speaker_memory import get_program_speaker_memory, upsert_program_speaker_memory

    session = SessionLocal()
    try:
        suffix = uuid4().hex[:8]
        project_a = create_project(session, ProjectCreateRequest(name=f'任务记忆 A {suffix}'))
        project_b = create_project(session, ProjectCreateRequest(name=f'任务记忆 B {suffix}'))
        upsert_program_speaker_memory(
            session,
            project_a.name,
            ['A 项目旧姓名'],
            project_id=project_a.id,
        )
        upsert_program_speaker_memory(
            session,
            project_b.name,
            ['B 项目旧姓名'],
            project_id=project_b.id,
        )
        job = create_job(
            session,
            source_url='https://example.com/source.wav',
            project_id=project_a.id,
            program_key=project_b.name_key,
            program_name=project_b.name,
        )
        job.status = 'completed'
        job.normalized_segments = [
            {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1200, 'text': '测试内容'},
        ]
        session.commit()

        before = get_job_speakers(job.id)
        assert before.memory_suggestions == ['A 项目旧姓名']

        put_job_speakers(
            job.id,
            SpeakerMappingRequest(
                mappings=[SpeakerMappingItem(speaker_key='speaker_0', speaker_name='A 项目新姓名')]
            ),
        )

        memory_a = get_program_speaker_memory(session, project_a.name, project_id=project_a.id)
        memory_b = get_program_speaker_memory(session, project_b.name, project_id=project_b.id)
        session.refresh(memory_a)
        session.refresh(memory_b)
        assert memory_a.speaker_names == ['A 项目新姓名', 'A 项目旧姓名']
        assert memory_b.speaker_names == ['B 项目旧姓名']
    finally:
        session.close()


def test_update_job_script_context_allows_review_stage_script_binding() -> None:
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        job = create_job(session, source_url='https://example.com/source.wav')
        job.status = 'completed'
        job.normalized_segments = [
            {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1000, 'text': '小赢每一个项目能坚'},
        ]
        session.commit()

        response = asyncio.run(update_job_script_context(job.id, script_text='小赢每一个项目能坚持\nI got the power in my soul'))

        session.refresh(job)
        assert response.script_context is not None
        assert response.script_context['source_name'] == '粘贴台本'
        assert '小赢每一个项目能坚持' in response.script_context['reference_lines']
        assert job.script_context == response.script_context

        response = asyncio.run(
            update_job_script_context(
                job.id,
                script_text='当前任务写作是 ai\nRENZER 的舞台很有 vibe',
            )
        )

        session.refresh(job)
        assert response.script_context is not None
        assert response.script_context['summary']['source_count'] == 1
        assert response.script_context['reference_lines'][0] == '当前任务写作是 ai'
        assert '小赢每一个项目能坚持' in response.script_context['reference_lines']
        assert 'RENZER 的舞台很有 vibe' in response.script_context['reference_lines']
        assert job.script_context == response.script_context
    finally:
        session.close()


def test_apply_job_script_text_corrections_updates_normalized_segments() -> None:
    from app.db.session import SessionLocal
    from app.services.subtitles import build_subtitle_cues

    session = SessionLocal()
    try:
        job = create_job(session, source_url='https://example.com/source.wav')
        job.status = 'completed'
        job.normalized_segments = [
            {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1400, 'text': '扎记村现在留存下来的很多建筑'},
        ]
        job.speaker_mappings = {'speaker_0': '主持人'}
        job.script_context = {
            'places': ['查济村'],
            'reference_lines': ['安徽省宣城市泾县查济村台本', '查济村现在留存下来的很多建筑'],
            'summary': {'keyword_count': 1, 'reference_line_count': 2, 'text_length': 20},
        }
        session.commit()

        response = apply_job_script_text_corrections(job.id)

        session.refresh(job)
        cues = build_subtitle_cues(job.normalized_segments, job.speaker_mappings)
        assert response.updated_count == 1
        assert [cue.clean_text for cue in cues] == ['查济村现在留存下来的很多建筑']
    finally:
        session.close()


def test_apply_job_script_text_corrections_does_not_rewrite_evidence_job() -> None:
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        job = create_job(
            session,
            source_url='https://example.com/source.wav',
            subtitle_strategy='evidence',
        )
        job.status = 'completed'
        job.normalized_segments = [{
            'speakerKey': 'speaker_0',
            'startMs': 0,
            'endMs': 1400,
            'text': '我们巅峰财团的174位成员',
        }]
        job.script_context = {
            'terms': ['巅峰裁团'],
            'reference_lines': ['我们巅峰裁团的174位成员'],
        }
        session.commit()

        response = apply_job_script_text_corrections(job.id)

        session.refresh(job)
        assert response.updated_count == 0
        assert job.normalized_segments[0]['text'] == '我们巅峰财团的174位成员'
        assert not (job.processing_metrics or {}).get('user_edit_patches')
    finally:
        session.close()


def test_subtitle_text_override_records_user_edit_patch() -> None:
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        job = create_job(session, source_url='https://example.com/source.wav')
        job.kind = 'transcription'
        job.status = 'completed'
        job.normalized_segments = [
            {
                'segmentId': 'seg-1',
                'speakerKey': 'speaker_0',
                'speakerId': 0,
                'startMs': 0,
                'endMs': 1000,
                'text': '扎记村',
                'words': [],
            }
        ]
        job.speaker_mappings = {'speaker_0': '主持人'}
        session.commit()

        response = put_job_subtitle_text(
            job.id,
            SubtitleTextOverrideRequest(cues=[SubtitleTextOverrideUpdate(index=1, clean_text='查济村')]),
        )

        session.refresh(job)
        assert response.cues[0].clean_text == '查济村'
        assert job.pipeline_artifacts['user_edit_patches_snapshot']['count'] == 1
        assert job.pipeline_artifacts['user_edit_patches_snapshot']['items'][0]['source'] == 'subtitle_text_override'
    finally:
        session.close()


def test_get_job_pipeline_artifacts_returns_summary() -> None:
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        job = create_job(session, source_url='https://example.com/source.wav')
        cue_hash = 'a' * 64
        job.pipeline_artifacts = {
            'schema_version': 'pipeline_artifacts.v2-shadow',
            'shadow_mode': True,
            'timeline_revision': 'timeline-abc',
            'source_revision': {'job_id': job.id},
            'artifact_hashes': {'subtitle_cues_hash': cue_hash},
            'asr_tokens_snapshot': {'count': 0, 'truncated': False, 'items': []},
            'semantic_utterances_snapshot': {'count': 0, 'truncated': False, 'items': []},
            'lexical_atoms_snapshot': {'count': 0, 'truncated': False, 'items': []},
            'subtitle_cues_snapshot': {'count': 2, 'truncated': False, 'items': []},
            'user_edit_patches_snapshot': {'count': 0, 'truncated': False, 'items': []},
            'cue_validation_report': {
                'valid': True,
                'has_p0': False,
                'p0_count': 0,
                'issue_count': 0,
                'issues': [],
            },
            'export_manifest': {
                'schema_version': 2,
                'frame_rate': '25',
                'cue_count': 2,
                'cue_hash': cue_hash,
            },
        }
        session.commit()

        response = get_job_pipeline_artifacts(job.id)

        assert response.summary.schema_version == 'pipeline_artifacts.v2-shadow'
        assert response.summary.subtitle_cue_count == 2
        assert response.summary.p0_count == 0
        assert response.summary.cue_hash == cue_hash
    finally:
        session.close()


def test_job_preview_hash_matches_pipeline_export_manifest() -> None:
    session, job = _create_completed_subtitle_job_with_artifacts()
    try:
        response = preview_job_subtitles(job.id, subtitle_format='srt', variant='full')

        session.refresh(job)
        assert response.cue_hash == job.pipeline_artifacts['export_manifest']['cue_hash']
    finally:
        session.close()


def test_subtitle_download_header_matches_pipeline_export_manifest() -> None:
    session, job = _create_completed_subtitle_job_with_artifacts()
    try:
        response = download_job_subtitles(job.id, subtitle_format='srt', variant='full')

        session.refresh(job)
        assert response.headers['X-Speaker-Cue-Hash'] == job.pipeline_artifacts['export_manifest']['cue_hash']
    finally:
        session.close()


def test_premiere_xml_download_header_matches_pipeline_export_manifest() -> None:
    session, job = _create_completed_subtitle_job_with_artifacts()
    try:
        response = download_job_premiere_xml(job.id, frame_rate='25', subtitle_mode='sidecar')

        session.refresh(job)
        assert response.headers['X-Speaker-Cue-Hash'] == job.pipeline_artifacts['export_manifest']['cue_hash']
    finally:
        session.close()


def _create_completed_subtitle_job_with_artifacts():
    from app.db.session import SessionLocal

    session = SessionLocal()
    job = create_job(session, source_url='https://example.com/source.wav', export_frame_rate='25')
    job.kind = 'transcription'
    job.status = 'completed'
    job.normalized_segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'speakerId': 0,
            'startMs': 0,
            'endMs': 1000,
            'text': '大家好',
            'words': [],
        }
    ]
    job.speaker_mappings = {'speaker_0': '主持人'}
    session.commit()
    job = update_job_normalized_segments(session, job.id, job.normalized_segments)
    return session, job


def test_merge_funasr_payloads_offsets_time_and_speaker_ids() -> None:
    merged = merge_funasr_payloads([
        ({
            'transcripts': [{
                'sentences': [{
                    'sentence_id': 1,
                    'speaker_id': 0,
                    'begin_time': 0,
                    'end_time': 500,
                    'text': '第一段',
                    'words': [{'begin_time': 0, 'end_time': 500, 'text': '第一段'}],
                }]
            }]
        }, 0, 0),
        ({
            'transcripts': [{
                'sentences': [{
                    'sentence_id': 1,
                    'speaker_id': 0,
                    'begin_time': 0,
                    'end_time': 700,
                    'text': '第二段',
                    'words': [{'begin_time': 0, 'end_time': 700, 'text': '第二段'}],
                }]
            }]
        }, DIARIZATION_SEGMENT_SECONDS * 1000, 1000),
    ])

    sentences = merged['transcripts'][0]['sentences']
    assert len(sentences) == 2
    assert sentences[0]['speaker_id'] == 0
    assert sentences[1]['speaker_id'] == 1000
    assert sentences[1]['begin_time'] == DIARIZATION_SEGMENT_SECONDS * 1000


def test_build_premiere_package_archive_contains_xml_srt_and_readme() -> None:
    payload = _build_premiere_package_archive(
        job_id='job-123',
        xml_content='<xmeml version="4"></xmeml>',
        clean_srt_content='1\n00:00:00,000 --> 00:00:01,000\n大家好\n',
        full_srt_content='1\n00:00:00,000 --> 00:00:01,000\n主持人：大家好\n',
        frame_rate=25,
    )

    with zipfile.ZipFile(BytesIO(payload), 'r') as archive:
        names = set(archive.namelist())
        assert 'job-123/job-123-premiere.xml' in names
        assert 'job-123/job-123-clean.srt' in names
        assert 'job-123/job-123-full.srt' in names
        assert 'job-123/README.txt' in names
        assert 'job-123/job-123-graphic-text.csv' not in names
        assert '大家好' in archive.read('job-123/job-123-clean.srt').decode('utf-8')
        readme = archive.read('job-123/README.txt').decode('utf-8')
        assert '导入同目录下的 XML 文件' in readme
        assert '导入 clean.srt' in readme
        assert '升级为图形字幕' in readme


def test_build_transcript_lines_uses_speaker_mappings_and_timestamps() -> None:
    job = SimpleNamespace(
        normalized_segments=[
            {'speakerKey': 'speaker_0', 'startMs': 1250, 'endMs': 3000, 'text': '大家好'},
            {'speakerKey': 'speaker_1', 'startMs': 61000, 'endMs': 64000, 'text': '欢迎来到节目'},
        ],
        speaker_mappings={'speaker_0': '主持人', 'speaker_1': '嘉宾'},
        export_frame_rate='25',
        remove_standalone_fillers=True,
    )

    lines = _build_transcript_lines(job)

    assert lines == [
        {'index': 1, 'start_ms': 1250, 'end_ms': 3000, 'speaker_name': '主持人', 'text': '大家好'},
        {'index': 2, 'start_ms': 61000, 'end_ms': 64000, 'speaker_name': '嘉宾', 'text': '欢迎来到节目'},
    ]


def test_build_transcript_docx_contains_transcript_text() -> None:
    payload = _build_transcript_docx([
        {'index': 1, 'start_ms': 0, 'end_ms': 1000, 'speaker_name': '主持人', 'text': '大家好'},
        {'index': 2, 'start_ms': 1100, 'end_ms': 2200, 'speaker_name': '嘉宾', 'text': '欢迎来到节目'},
    ])

    with zipfile.ZipFile(BytesIO(payload), 'r') as archive:
        document_xml = archive.read('word/document.xml').decode('utf-8')
        assert '[00:00:00] 主持人：大家好' in document_xml
        assert '[00:00:01] 嘉宾：欢迎来到节目' in document_xml


def test_download_job_transcript_supports_txt_srt_and_docx() -> None:
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        job = create_job(
            session,
            source_url='https://example.com/划稿测试.wav',
            source_media_kind='audio',
            program_name='划稿测试',
        )
        job.status = 'completed'
        job.normalized_segments = [
            {'speakerKey': 'speaker_0', 'startMs': 0, 'endMs': 1200, 'text': '嗯大家好'},
            {'speakerKey': 'speaker_1', 'startMs': 1500, 'endMs': 3000, 'text': '这里是划稿内容'},
        ]
        job.speaker_mappings = {'speaker_0': '主持人', 'speaker_1': '嘉宾'}
        session.commit()

        txt_response = download_job_transcript(job.id, transcript_format='txt')
        assert txt_response.media_type == 'text/plain; charset=utf-8'
        assert '[00:00:00] 主持人：嗯大家好' in txt_response.body.decode('utf-8')

        srt_response = download_job_transcript(job.id, transcript_format='srt')
        srt_text = srt_response.body.decode('utf-8')
        assert '00:00:00,000 --> 00:00:01,200' in srt_text
        assert '主持人：嗯大家好' in srt_text

        docx_response = download_job_transcript(job.id, transcript_format='docx')
        assert docx_response.media_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        assert docx_response.body.startswith(b'PK')
    finally:
        session.close()


def test_parse_client_paths_payload_returns_ordered_items() -> None:
    payload = '[{"file_name":"主持人.png","path":"/Volumes/Editor/主持人.png"},{"file_name":"嘉宾.png","path":""}]'

    result = _parse_client_paths_payload(payload)

    assert result == [
        {"file_name": "主持人.png", "path": "/Volumes/Editor/主持人.png"},
        {"file_name": "嘉宾.png", "path": None},
    ]


def test_resolve_client_export_path_prefers_upload_order_for_duplicate_names() -> None:
    client_paths = [
        {"file_name": "嘉宾.png", "path": "/Volumes/A/嘉宾.png"},
        {"file_name": "嘉宾.png", "path": "/Volumes/B/嘉宾.png"},
    ]

    assert _resolve_client_export_path(client_paths, 0, "嘉宾.png") == "/Volumes/A/嘉宾.png"
    assert _resolve_client_export_path(client_paths, 1, "嘉宾.png") == "/Volumes/B/嘉宾.png"


def test_build_job_premiere_xml_content_prefers_client_export_paths() -> None:
    cues = [
        SimpleNamespace(
            index=1,
            speaker_key="speaker_0",
            speaker_name="主持人",
            start_ms=0,
            end_ms=1000,
            full_text="主持人：欢迎大家",
            clean_text="欢迎大家",
        )
    ]
    job = SimpleNamespace(
        id="job-client-path",
        video_source_export_path="/Volumes/ClientMedia/source.mp4",
        video_source_local_path="/Users/server/media/source.mp4",
        source_export_path=None,
        source_local_path="/Users/server/media/source.wav",
        video_source_url="https://example.com/source.mp4",
        source_url="https://example.com/source.wav",
        source_media_kind="video",
        lower_third_assets=[
            {
                "speaker_name": "主持人",
                "file_name": "主持人.png",
                "public_url": "https://example.com/host.png",
                "local_path": "/Users/server/assets/主持人.png",
                "export_path": "/Volumes/ClientAssets/主持人.png",
            }
        ],
    )

    content = _build_job_premiere_xml_content(job, cues, frame_rate=25, subtitle_mode="graphicandtype")

    assert "file:///Volumes/ClientMedia/source.mp4" in content
    assert "file:///Volumes/ClientAssets/%E4%B8%BB%E6%8C%81%E4%BA%BA.png" in content


def test_build_job_premiere_xml_content_aligns_lower_thirds_to_subtitle_frames() -> None:
    cues = [
        SimpleNamespace(
            index=1,
            speaker_key="speaker_0",
            speaker_name="主持人",
            start_ms=5180,
            end_ms=5390,
            full_text="主持人：欢迎大家",
            clean_text="欢迎大家",
        )
    ]
    job = SimpleNamespace(
        id="job-frame-align",
        video_source_export_path="/Volumes/ClientMedia/source.mp4",
        video_source_local_path=None,
        source_export_path=None,
        source_local_path=None,
        video_source_url=None,
        source_url=None,
        source_media_kind="video",
        lower_third_assets=[
            {
                "speaker_name": "主持人",
                "file_name": "主持人.png",
                "export_path": "/Volumes/ClientAssets/主持人.png",
            }
        ],
    )

    content = _build_job_premiere_xml_content(job, cues, frame_rate=25, subtitle_mode="graphicandtype")
    xml_body = content.split("<xmeml", 1)[1]
    root = fromstring(f"<xmeml{xml_body}")
    subtitle_clip = root.find(".//clipitem[@id='clipitem-subtitle-1']")
    overlay_clip = root.find(".//clipitem[@id='clip-overlay-1']")

    assert subtitle_clip is not None
    assert overlay_clip is not None
    assert overlay_clip.findtext("start") == subtitle_clip.findtext("start")
    assert overlay_clip.findtext("end") == subtitle_clip.findtext("end")


def test_build_job_premiere_xml_content_includes_subtitle_backplate_export_path() -> None:
    cues = [
        SimpleNamespace(
            index=1,
            speaker_key="speaker_0",
            speaker_name="主持人",
            start_ms=1950,
            end_ms=3190,
            full_text="主持人：做米果去了",
            clean_text="做米果去了",
        )
    ]
    job = SimpleNamespace(
        id="job-backplate-path",
        video_source_export_path="/Volumes/ClientMedia/source.mp4",
        video_source_local_path=None,
        source_export_path=None,
        source_local_path=None,
        video_source_url=None,
        source_url=None,
        source_media_kind="video",
        lower_third_assets=[],
        subtitle_backplate_asset={
            "file_name": "唱词条底.png",
            "object_key": "caption-backplate.png",
            "local_path": "/Users/server/assets/caption-backplate.png",
            "export_path": "/Volumes/ClientAssets/唱词条底.png",
        },
    )

    content = _build_job_premiere_xml_content(job, cues, frame_rate=25, subtitle_mode="graphicandtype")
    xml_body = content.split("<xmeml", 1)[1]
    root = fromstring(f"<xmeml{xml_body}")
    subtitle_clip = root.find(".//clipitem[@id='clipitem-subtitle-1']")
    backplate_clip = root.find(".//clipitem[@id='clip-backplate-1']")

    assert "file:///Volumes/ClientAssets/%E5%94%B1%E8%AF%8D%E6%9D%A1%E5%BA%95.png" in content
    assert "file:///Users/server/assets/caption-backplate.png" not in content
    assert subtitle_clip is not None
    assert backplate_clip is not None
    assert backplate_clip.findtext("start") == subtitle_clip.findtext("start")
    assert backplate_clip.findtext("end") == subtitle_clip.findtext("end")


def test_fetch_remote_asset_bytes_prefers_response_content_type(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/png; charset=binary"}
        content = b"png"

    def fake_get(url: str, timeout: int):
        assert url == "https://example.com/host.png"
        assert timeout == 60
        return FakeResponse()

    monkeypatch.setattr("app.api.v1.transcriptions.httpx.get", fake_get)

    content, media_type = _fetch_remote_asset_bytes("https://example.com/host.png", "host.png")

    assert content == b"png"
    assert media_type == "image/png"


def test_fetch_remote_asset_bytes_falls_back_to_guessed_media_type(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {}
        content = b"jpg"

    monkeypatch.setattr("app.api.v1.transcriptions.httpx.get", lambda *_args, **_kwargs: FakeResponse())

    content, media_type = _fetch_remote_asset_bytes("https://example.com/guest.jpg", "guest.jpg")

    assert content == b"jpg"
    assert media_type == "image/jpeg"


def test_fetch_remote_asset_bytes_raises_for_remote_failure(monkeypatch) -> None:
    class FakeResponse:
        status_code = 404
        headers = {}
        content = b""

    monkeypatch.setattr("app.api.v1.transcriptions.httpx.get", lambda *_args, **_kwargs: FakeResponse())

    try:
        _fetch_remote_asset_bytes("https://example.com/missing.png", "missing.png")
    except HTTPException as error:
        assert error.status_code == 502
        assert error.detail == "读取远端人名条失败"
    else:
        raise AssertionError("expected HTTPException")
