from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import Base
from app.models.job import Job
from app.models.subtitle_pipeline_run import SubtitlePipelineRun
from app.services.job_events import list_job_events
from app.services.jobs import _build_job_script_snapshot, _load_job_runtime_lexicon_bundle, _pipeline_replay_decision_richness, abort_job, build_job_evidence_subtitle_segments, create_job, delete_job, save_transcription_result, update_job_normalized_segments, update_job_provider, update_job_source_export_path, update_subtitle_backplate_asset
from app.services.lyrics_alignment import attach_lyrics_reference, build_lyrics_reference
from app.services.deepseek_subtitle_contract import run_deepseek_plan_window
from app.services.subtitles import build_subtitle_cues
from app.services.user_edit_patches import build_segment_edit_patch


def _build_session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_replan_prefers_richest_saved_ai_decision_artifact() -> None:
    original = SimpleNamespace(
        run_sequence=1,
        artifacts={'checkpoints': {'ai_planning': {
            'accepted_candidate_ids': ['a', 'b', 'c'],
            'accepted_boundary_votes': ['vote-a'],
            'window_results': {'window-a': {}},
        }}},
    )
    later_replan = SimpleNamespace(
        run_sequence=2,
        artifacts={'checkpoints': {'ai_planning': {
            'accepted_candidate_ids': ['c'],
            'accepted_boundary_votes': [],
            'window_results': {'window-a': {}},
        }}},
    )

    selected = max(
        (later_replan, original),
        key=_pipeline_replay_decision_richness,
    )

    assert selected is original


def test_update_job_source_export_path_updates_audio_job() -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        source_local_path='/srv/source.wav',
        source_media_kind='audio',
        status='completed',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    updated = update_job_source_export_path(session, job.id, '/Volumes/Editor/source.wav')

    assert updated.source_export_path == '/Volumes/Editor/source.wav'
    assert updated.video_source_export_path is None


def test_create_job_persists_locked_export_frame_rate() -> None:
    session = _build_session()

    job = create_job(
        session=session,
        source_url='https://example.com/source.wav',
        export_frame_rate='29.97',
        source_media_kind='audio',
    )

    assert job.export_frame_rate == '29.97'
    assert job.processing_metrics['storage_policy_version'] == 2


def test_create_job_persists_optional_project_id_without_changing_legacy_fields() -> None:
    session = _build_session()

    job = create_job(
        session=session,
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
        project_id='project-uuid',
        program_key='legacy-key',
        program_name='旧项目名称',
    )

    assert job.project_id == 'project-uuid'
    assert job.program_key == 'legacy-key'
    assert job.program_name == '旧项目名称'


def test_job_runtime_lexicon_hard_protects_explicit_roster_names() -> None:
    session = _build_session()
    job = Job(
        kind='transcription',
        source_url='https://example.com/source.wav',
        status='completed',
        script_context={
            'people': [f'普通词{index}' for index in range(40)],
            'reference_lines': [
                '第六位是——张峻豪。',
                '有请首轮名人堂成员——艾热、rapeter、功夫胖。',
            ],
            'canonical_terms': [{
                'display_text': 'SHarK米尔艾力',
                'term_type': 'person',
                'protection_level': 'hard',
                'aliases': ['SHarK', '米尔艾力'],
            }],
        },
    )
    session.add(job)
    session.commit()

    bundle = _load_job_runtime_lexicon_bundle(session, job)
    protection_by_text = {
        item['display_text']: item['protection_level']
        for item in bundle['terms']
    }

    assert protection_by_text['张峻豪'] == 'hard'
    assert protection_by_text['rapeter'] == 'hard'
    shark = next(item for item in bundle['terms'] if item['display_text'] == 'SHarK米尔艾力')
    assert shark['aliases'] == ['SHarK', '米尔艾力']


def test_job_script_snapshot_includes_project_hard_term_alias_evidence() -> None:
    job = Job(
        kind='transcription',
        source_url='https://example.com/source.wav',
        status='completed',
    )
    bundle = {
        'terms': [{
            'term_id': 'term-ranzer',
            'display_text': 'RANZER叶润泽',
            'term_type': 'person',
            'scope_type': 'project',
            'protection_level': 'hard',
            'aliases': ['忍者', '叶师傅'],
        }],
    }

    snapshot = _build_job_script_snapshot(job, bundle)

    assert snapshot.lines[0].text == '项目强制词库人名——RANZER叶润泽；别名：忍者、叶师傅'


def test_job_script_snapshot_preserves_reference_source_groups() -> None:
    job = Job(
        kind='transcription',
        source_url='https://example.com/source.wav',
        status='completed',
        script_source_name='本次台本.txt + 历史台本.txt',
        script_context={
            'reference_lines': ['后面AI内容', '前面历史台本内容'],
            'reference_sources': [
                {
                    'source_name': '本次台本.txt',
                    'source_hash': '1' * 64,
                    'reference_lines': ['后面AI内容'],
                },
                {
                    'source_name': '历史台本.txt',
                    'source_hash': '2' * 64,
                    'reference_lines': ['前面历史台本内容'],
                },
            ],
            'canonical_terms': [],
        },
    )

    snapshot = _build_job_script_snapshot(job)

    assert [line.source_order for line in snapshot.lines] == [0, 1]
    assert [line.source_hash for line in snapshot.lines] == [
        '1' * 64,
        '2' * 64,
    ]


def test_create_job_persists_subtitle_strategy() -> None:
    session = _build_session()

    job = create_job(
        session=session,
        source_url='https://example.com/source.wav',
        subtitle_strategy='pro',
        source_media_kind='audio',
    )

    assert job.subtitle_strategy == 'pro'


def test_create_job_defaults_subtitle_strategy_to_enhanced() -> None:
    session = _build_session()

    job = create_job(
        session=session,
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
    )

    assert job.subtitle_strategy == 'enhanced'


def test_create_transcription_job_upgrades_standard_strategy_to_enhanced() -> None:
    session = _build_session()

    job = create_job(
        session=session,
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
        kind='transcription',
        subtitle_strategy='standard',
    )

    assert job.subtitle_strategy == 'enhanced'


def test_create_job_persists_kind_for_transcript_workflow() -> None:
    session = _build_session()

    job = create_job(
        session=session,
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
        kind='transcript',
    )

    assert job.kind == 'transcript'


def test_create_transcript_job_forces_standard_strategy() -> None:
    session = _build_session()

    job = create_job(
        session=session,
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
        kind='transcript',
        subtitle_strategy='enhanced',
    )

    assert job.kind == 'transcript'
    assert job.subtitle_strategy == 'standard'


def test_update_subtitle_backplate_asset_keeps_roles_independent() -> None:
    session = _build_session()
    job = create_job(
        session=session,
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
    )

    update_subtitle_backplate_asset(
        session,
        job.id,
        {'file_name': '唱词底.png', 'object_key': 'with.png', 'local_path': '/tmp/with.png'},
        role='with_speaker',
    )
    updated = update_subtitle_backplate_asset(
        session,
        job.id,
        {'file_name': '唱词底.png', 'object_key': 'without.png', 'local_path': '/tmp/without.png'},
        role='without_speaker',
    )

    assert updated.subtitle_backplate_asset['schema_version'] == 1
    assert updated.subtitle_backplate_asset['roles']['with_speaker']['object_key'] == 'with.png'
    assert updated.subtitle_backplate_asset['roles']['without_speaker']['object_key'] == 'without.png'

    updated = update_subtitle_backplate_asset(session, job.id, None, role='without_speaker')

    assert updated.subtitle_backplate_asset == {
        'schema_version': 1,
        'roles': {
            'with_speaker': {
                'file_name': '唱词底.png',
                'object_key': 'with.png',
                'local_path': '/tmp/with.png',
                'role': 'with_speaker',
            }
        },
    }


def test_create_job_persists_script_context_and_cleanup_flag() -> None:
    session = _build_session()

    job = create_job(
        session=session,
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
        script_context={'people': ['刘权'], 'terms': ['AI+'], 'summary': {'keyword_count': 2}},
        script_source_name='台本.txt',
        remove_standalone_fillers=True,
    )

    assert job.script_context['people'] == ['刘权']
    assert job.script_source_name == '台本.txt'
    assert job.remove_standalone_fillers is True


def test_update_job_provider_persists_provider_task_meta() -> None:
    session = _build_session()
    job = create_job(
        session=session,
        source_url='https://example.com/source.wav',
        source_media_kind='audio',
    )

    updated = update_job_provider(
        session=session,
        job_id=job.id,
        provider_task_id='task-1',
        provider_request_id='req-1',
        status='pending',
        provider_task_meta=[
            {'task_id': 'task-1', 'offset_ms': 0},
            {'task_id': 'task-2', 'offset_ms': 7200000},
        ],
    )

    assert updated.provider_task_id == 'task-1'
    assert updated.provider_task_meta[1]['task_id'] == 'task-2'


def test_save_transcription_result_falls_back_when_subtitle_enhancement_fails(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='enhanced',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 0,
                        'end_time': 1000,
                        'text': '大家好，欢迎来到节目。',
                        'words': [],
                    }
                ]
            }
        ]
    }

    monkeypatch.setattr(
        'app.services.jobs.apply_subtitle_strategy',
        lambda _job, segments, **_kwargs: (segments, False, '字幕增强请求失败'),
    )

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    assert saved.status == 'completed_with_warnings'
    assert saved.subtitle_enhanced is False
    assert saved.subtitle_enhancement_error == '字幕增强请求失败'
    assert '回退' in saved.processing_message
    assert saved.normalized_segments[0]['text'] == '大家好，欢迎来到节目。'
    assert saved.processing_metrics['normalize_seconds'] >= 0
    assert saved.processing_metrics['subtitle_strategy_seconds'] >= 0


def test_save_transcription_result_applies_timed_lyrics_after_subtitle_pipeline(monkeypatch) -> None:
    session = _build_session()
    reference = build_lyrics_reference(
        source_name='歌词.srt',
        extracted_text='',
        raw_payload=b'1\n00:00:10,000 --> 00:00:12,000\nWe never give up\n',
    )
    job = Job(
        kind='transcription',
        source_url='https://example.com/source.wav',
        source_duration_seconds=30,
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='standard',
        script_context=attach_lyrics_reference({}, reference),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    payload = {'transcripts': [{'sentences': [
        {
            'sentence_id': 1,
            'speaker_id': 0,
            'begin_time': 0,
            'end_time': 3_000,
            'text': '现在开始表演',
            'words': [],
        },
        {
            'sentence_id': 2,
            'speaker_id': 1,
            'begin_time': 10_000,
            'end_time': 12_000,
            'text': '为拿给发',
            'words': [],
        },
    ]}]}
    monkeypatch.setattr(
        'app.services.jobs.apply_subtitle_strategy',
        lambda _job, segments, **_kwargs: (segments, True, None),
    )

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    assert saved.status == 'completed'
    assert [segment['text'] for segment in saved.normalized_segments] == [
        '现在开始表演',
        'We never give up',
    ]
    assert saved.pipeline_artifacts['lyrics_correction']['status'] == 'applied'
    assert saved.processing_metrics['lyrics_applied_line_count'] == 1
    assert saved.processing_message == '转写与歌词校对已完成'


def test_save_transcription_result_uses_evidence_cues_in_existing_workflow(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        status='processing',
        kind='transcription',
        export_frame_rate='25',
        subtitle_strategy='evidence',
        source_duration_seconds=4.0,
        provider_task_id='task-evidence',
        project_id='project-evidence',
        script_context={
            'reference_lines': ['我们来到查济村现在参观这个特别漂亮的地方', '好的'],
        },
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    def sentence(words, speaker, start):
        provider_words = []
        cursor = start
        for text in words:
            provider_words.append({
                'text': text,
                'begin_time': cursor,
                'end_time': cursor + 160,
            })
            cursor += 180
        return {
            'speaker_id': speaker,
            'begin_time': start,
            'end_time': cursor,
            'text': ''.join(words),
            'words': provider_words,
        }

    payload = {'transcripts': [{'sentences': [
        sentence(
            ['我们', '来到', '扎记村', '现在', '参观', '这个', '特别', '漂亮', '的', '地方'],
            0,
            0,
        ),
        sentence(['好的'], 1, 2200),
    ]}]}
    monkeypatch.setattr(
        'app.services.jobs.apply_subtitle_strategy',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('evidence strategy must not call the legacy enhancer')
        ),
    )
    provider_calls: list[str] = []

    class FakePlannerClient:
        def plan_window(self, *, window, **_kwargs):
            provider_calls.append(window.window_id)
            return run_deepseek_plan_window(window, lambda _window: {
                'window_id': window.window_id,
                'boundary_votes': [],
                'correction_candidate_ids': [
                    candidate.candidate_id
                    for candidate in window.correction_candidates
                ],
            })

    monkeypatch.setattr(
        'app.services.deepseek_subtitle_client.DeepSeekSubtitlePlannerClient',
        FakePlannerClient,
    )

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    assert saved.status == 'completed'
    assert saved.subtitle_enhanced is True
    assert all(segment['v2FinalCue'] is True for segment in saved.normalized_segments)
    assert '查济村' in ''.join(segment['text'] for segment in saved.normalized_segments)
    cues = build_subtitle_cues(saved.normalized_segments, saved.speaker_mappings or {})
    assert max(len(cue.clean_text) for cue in cues) <= 28
    assert {cue.speaker_key for cue in cues} == {'speaker_0', 'speaker_1'}
    assert saved.pipeline_artifacts['evidence_subtitle_v2']['corrections'][0][
        'replacement_text'
    ] == '查济村'
    assert saved.pipeline_artifacts['evidence_subtitle_v2']['schema_version'] == (
        'evidence-subtitle.v2'
    )
    assert saved.pipeline_artifacts['dialogue_broadcast_normalization']['status'] == (
        'disabled'
    )
    run = session.query(SubtitlePipelineRun).filter_by(job_id=job.id).one()
    assert run.stage == 'ready'
    assert len(provider_calls) == run.ai_total_windows
    assert saved.processing_metrics['subtitle_v2_ai_total_windows'] == run.ai_total_windows
    assert saved.processing_metrics['subtitle_v2_ai_completed_windows'] == run.ai_completed_windows

    build_job_evidence_subtitle_segments(
        session,
        saved,
        raw_payload=payload,
    )
    assert len(provider_calls) == run.ai_total_windows


def test_save_transcription_result_updates_progress_during_subtitle_enhancement(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='enhanced',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 0,
                        'end_time': 2200,
                        'text': '大家好欢迎来到节目今天继续聊字幕断句体验',
                        'words': [],
                    }
                ]
            }
        ]
    }
    observed_progress: list[tuple[str, int, str]] = []

    def fake_apply_subtitle_strategy(_job, segments, progress_callback=None):
        assert progress_callback is not None
        progress_callback(2, 5, 'DeepSeek 字幕增强批次')
        refreshed = session.get(Job, job.id)
        observed_progress.append((
            refreshed.status,
            refreshed.processing_progress,
            refreshed.processing_message,
        ))
        return segments, False, None

    monkeypatch.setattr('app.services.jobs.apply_subtitle_strategy', fake_apply_subtitle_strategy)

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    assert saved.status == 'completed'
    assert observed_progress
    assert observed_progress[0][0] == 'subtitle_enhancing'
    assert observed_progress[0][1] > 92
    assert 'DeepSeek 字幕增强批次' in observed_progress[0][2]


def test_save_transcription_result_preserves_existing_processing_metrics(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='enhanced',
        processing_metrics={'fetch_payload_seconds': 1.25},
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 0,
                        'end_time': 1000,
                        'text': '大家好，欢迎来到节目。',
                        'words': [],
                    }
                ]
            }
        ]
    }

    monkeypatch.setattr(
        'app.services.jobs.apply_subtitle_strategy',
        lambda _job, segments, **_kwargs: (segments, False, None),
    )

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    assert saved.processing_metrics['fetch_payload_seconds'] == 1.25
    assert saved.processing_metrics['normalize_seconds'] >= 0
    assert saved.processing_metrics['subtitle_strategy_seconds'] >= 0


def test_save_transcription_result_records_time_drift_diagnostic(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='enhanced',
        source_duration_seconds=3600.0,
        provider_task_meta=[
            {
                'task_id': 'task-1',
                'file_url': 'https://oss.example.com/private-part-1.wav',
                'offset_ms': 0,
                'duration_seconds': 1800.0,
                'chunk_index': 0,
                'speaker_offset': 0,
            },
            {
                'task_id': 'task-2',
                'file_url': 'https://oss.example.com/private-part-2.wav',
                'offset_ms': 1800000,
                'duration_seconds': 1800.0,
                'chunk_index': 1,
                'speaker_offset': 1000,
            },
        ],
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 0,
                        'end_time': 3604000,
                        'text': '这是一个用于检查时间漂移的长音频任务',
                        'words': [],
                    }
                ]
            }
        ]
    }

    monkeypatch.setattr(
        'app.services.jobs.apply_subtitle_strategy',
        lambda _job, segments, **_kwargs: (segments, False, None),
    )

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    diagnostic = saved.processing_metrics['time_drift_diagnostic']
    assert diagnostic['status'] == 'warn'
    assert diagnostic['source_duration_ms'] == 3600000
    assert diagnostic['comparisons']['raw_asr']['delta_ms'] == 4000
    assert diagnostic['comparisons']['subtitle_cue']['delta_ms'] == 4000
    assert diagnostic['provider_chunks']['count'] == 2
    assert diagnostic['provider_chunks']['theoretical_end_ms'] == 3600000
    assert 'file_url' not in str(diagnostic)
    assert 'oss.example.com' not in str(diagnostic)
    assert saved.pipeline_artifacts['time_drift_diagnostic']['status'] == 'warn'

    events = list_job_events(session, saved.id)
    drift_event = next(event for event in events if event.event_type == 'time_drift_diagnostic')
    assert drift_event.details['status'] == 'warn'
    assert drift_event.details['subtitle_cue_delta_ms'] == 4000
    assert 'oss.example.com' not in str(drift_event.details)


def test_save_transcription_result_keeps_transcript_strategy_raw_segments() -> None:
    session = _build_session()
    job = Job(
        kind='transcript',
        source_url='https://example.com/source.wav',
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='standard',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 0,
                        'end_time': 1000,
                        'text': '大家好，欢迎来到节目。',
                        'words': [],
                    }
                ]
            }
        ]
    }

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    assert saved.subtitle_strategy == 'standard'
    assert saved.subtitle_enhanced is False
    assert saved.subtitle_enhancement_error is None
    assert saved.normalized_segments[0]['text'] == '大家好，欢迎来到节目。'
    assert saved.pipeline_artifacts['pipeline'] == 'transcript'
    assert 'subtitle_length_profile' not in saved.pipeline_artifacts
    assert 'subtitle_length_summary' not in saved.pipeline_artifacts


def test_save_transcription_result_rebuilds_utterances_before_subtitle_enhancement(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        kind='transcription',
        source_url='https://example.com/source.wav',
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='enhanced',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 19000,
                        'end_time': 22000,
                        'text': '这里就是沈阳的国际赛艇',
                        'words': [],
                    },
                    {
                        'sentence_id': 2,
                        'speaker_id': 1,
                        'begin_time': 22020,
                        'end_time': 22800,
                        'text': '中心哦',
                        'words': [],
                    },
                    {
                        'sentence_id': 3,
                        'speaker_id': 1,
                        'begin_time': 23000,
                        'end_time': 23800,
                        'text': '听说过',
                        'words': [],
                    },
                ]
            }
        ]
    }
    observed_segments: list[list[dict]] = []

    def fake_apply_subtitle_strategy(_job, segments, **_kwargs):
        observed_segments.append(segments)
        return segments, False, None

    monkeypatch.setattr('app.services.jobs.apply_subtitle_strategy', fake_apply_subtitle_strategy)

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    assert [(item['speakerKey'], item['text']) for item in observed_segments[0]] == [
        ('speaker_0', '这里就是沈阳的国际赛艇中心哦'),
        ('speaker_1', '听说过'),
    ]
    assert saved.normalized_segments[0]['sourceSegmentIds'] == ['seg-1', 'seg-2']
    assert saved.processing_metrics['utterance_rebuilder_version'] == 1.0
    assert saved.processing_metrics['utterance_rebuilder_enabled'] == 1.0
    assert saved.processing_metrics['utterance_rebuilder_input_segments'] == 3.0
    assert saved.processing_metrics['utterance_rebuilder_output_segments'] == 2.0
    assert saved.processing_metrics['utterance_rebuilder_repair_count'] == 1.0
    assert saved.processing_metrics['subtitle_cue_validator_version'] == 1.0
    assert saved.processing_metrics['subtitle_cue_validator_cue_count'] == 2.0
    assert saved.processing_metrics['subtitle_cue_validator_p0_count'] == 0.0
    assert saved.processing_metrics['subtitle_cue_validator_issue_count'] == 0.0


def test_save_transcription_result_writes_pipeline_artifacts(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        kind='transcription',
        source_url='https://example.com/source.wav',
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='enhanced',
        script_context={'terms': ['国际赛艇中心'], 'reference_lines': ['这里就是沈阳的国际赛艇中心哦']},
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 19000,
                        'end_time': 22000,
                        'text': '这里就是沈阳的国际赛艇',
                        'words': [
                            {'text': '这里', 'begin_time': 19000, 'end_time': 19400},
                            {'text': '就是', 'begin_time': 19400, 'end_time': 19800},
                            {'text': '沈阳', 'begin_time': 19800, 'end_time': 20400},
                            {'text': '的', 'begin_time': 20400, 'end_time': 20600},
                            {'text': '国际', 'begin_time': 20600, 'end_time': 21200},
                            {'text': '赛艇', 'begin_time': 21200, 'end_time': 22000},
                        ],
                    },
                    {
                        'sentence_id': 2,
                        'speaker_id': 1,
                        'begin_time': 22020,
                        'end_time': 22800,
                        'text': '中心哦',
                        'words': [
                            {'text': '中心', 'begin_time': 22020, 'end_time': 22600},
                            {'text': '哦', 'begin_time': 22600, 'end_time': 22800},
                        ],
                    },
                ]
            }
        ]
    }

    monkeypatch.setattr(
        'app.services.jobs.apply_subtitle_strategy',
        lambda _job, segments, **_kwargs: (segments, False, None),
    )

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    artifacts = saved.pipeline_artifacts
    assert artifacts['schema_version'] == 'pipeline_artifacts.v2-shadow'
    assert artifacts['shadow_mode'] is True
    assert artifacts['pipeline'] == 'transcription'
    assert artifacts['generated_at']
    assert artifacts['refreshed_by'] == 'save_transcription_result'
    assert artifacts['timeline_revision'].startswith('timeline-')
    assert artifacts['pipeline_version']['cue_builder']
    assert artifacts['pipeline_version']['cue_validator']
    assert artifacts['source_revision']['job_id'] == saved.id
    assert artifacts['source_revision']['raw_payload_hash']
    assert artifacts['source_revision']['normalized_segments_hash']
    assert artifacts['source_revision']['speaker_mappings_hash']
    assert artifacts['source_revision']['script_context_hash']
    assert artifacts['artifact_hashes']['asr_tokens_hash']
    assert artifacts['artifact_hashes']['subtitle_cues_hash'] == artifacts['export_manifest']['cue_hash']
    assert artifacts['artifact_hashes']['cue_validation_hash']
    assert artifacts['asr_tokens_snapshot']['count'] == 8
    assert artifacts['semantic_utterances_snapshot']['count'] == 1
    assert artifacts['user_edit_patches_snapshot']['count'] == 0
    assert any(item['text'] == '国际赛艇中心' for item in artifacts['protected_terms_snapshot']['items'])
    assert artifacts['subtitle_cues_snapshot']['count'] >= 1
    assert artifacts['cue_validation_report']['p0_count'] == 0
    assert artifacts['export_manifest']['schema_version'] == 2
    assert len(artifacts['export_manifest']['cue_hash']) == 64


def test_save_transcription_result_records_strict_subtitle_length_profile(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        kind='transcription',
        source_url='https://example.com/source.wav',
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='enhanced',
        processing_metrics={'subtitle_length_profile': {'mode': 'strict', 'max_chars': 9}},
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 0,
                        'end_time': 4200,
                        'text': '社区学院看一看看他们是如何打通邻里之间',
                        'words': [],
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(
        'app.services.jobs.apply_subtitle_strategy',
        lambda _job, segments, **_kwargs: (segments, False, None),
    )

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    artifacts = saved.pipeline_artifacts
    assert artifacts['subtitle_length_profile'] == {'mode': 'strict', 'max_chars': 9}
    assert artifacts['subtitle_cues_snapshot']['count'] >= 2
    assert all(len(item['clean_text']) <= 9 for item in artifacts['subtitle_cues_snapshot']['items'])


def test_update_job_normalized_segments_refreshes_pipeline_artifact_hash(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        kind='transcription',
        source_url='https://example.com/source.wav',
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='enhanced',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 0,
                        'end_time': 1000,
                        'text': '大家好',
                        'words': [],
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(
        'app.services.jobs.apply_subtitle_strategy',
        lambda _job, segments, **_kwargs: (segments, False, None),
    )

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )
    original_hash = saved.pipeline_artifacts['export_manifest']['cue_hash']
    original_lyrics_report = saved.pipeline_artifacts['lyrics_correction']
    original_dialogue_report = saved.pipeline_artifacts['dialogue_broadcast_normalization']
    original_revision = saved.pipeline_artifacts['timeline_revision']

    updated = update_job_normalized_segments(
        session=session,
        job_id=job.id,
        normalized_segments=[
            {
                'segmentId': 'seg-1',
                'speakerKey': 'speaker_0',
                'speakerId': 0,
                'startMs': 0,
                'endMs': 1000,
                'text': '大家好欢迎回来',
                'words': [],
            }
        ],
    )

    assert updated.pipeline_artifacts['export_manifest']['cue_hash'] != original_hash
    assert updated.pipeline_artifacts['subtitle_cues_snapshot']['items'][0]['clean_text'] == '大家好欢迎回来'
    assert updated.pipeline_artifacts['lyrics_correction'] == original_lyrics_report
    assert updated.pipeline_artifacts['dialogue_broadcast_normalization'] == original_dialogue_report
    assert updated.pipeline_artifacts['generation_timeline_revision'] == original_revision
    assert updated.pipeline_artifacts['cue_validation_report']['timeline_revision'] == updated.pipeline_artifacts['timeline_revision']
    updated = update_job_normalized_segments(session=session, job_id=job.id, normalized_segments=[])
    assert updated.pipeline_artifacts['subtitle_cues_snapshot']['count'] == 0
    assert updated.pipeline_artifacts['generation_timeline_revision'] == original_revision
    assert updated.pipeline_artifacts['dialogue_broadcast_normalization'] == original_dialogue_report


def test_update_job_normalized_segments_records_user_edit_patch(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        kind='transcription',
        source_url='https://example.com/source.wav',
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='enhanced',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 0,
                        'end_time': 1000,
                        'text': '扎记村',
                        'words': [],
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(
        'app.services.jobs.apply_subtitle_strategy',
        lambda _job, segments, **_kwargs: (segments, False, None),
    )

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )
    updated_segments = [
        {
            'segmentId': 'seg-1',
            'speakerKey': 'speaker_0',
            'speakerId': 0,
            'startMs': 0,
            'endMs': 1000,
            'text': '查济村',
            'words': [],
        }
    ]
    edit_patch = build_segment_edit_patch(
        saved.normalized_segments or [],
        updated_segments,
        source='script_text_correction',
    )

    updated = update_job_normalized_segments(
        session=session,
        job_id=job.id,
        normalized_segments=updated_segments,
        edit_patch=edit_patch,
    )

    assert updated.processing_metrics['user_edit_patch_count'] == 1
    assert updated.pipeline_artifacts['user_edit_patches_snapshot']['count'] == 1
    assert updated.pipeline_artifacts['user_edit_patches_snapshot']['items'][0]['source'] == 'script_text_correction'


def test_save_transcription_result_keeps_transcript_workflow_out_of_utterance_rebuilder() -> None:
    session = _build_session()
    job = Job(
        kind='transcript',
        source_url='https://example.com/source.wav',
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='standard',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {'sentence_id': 1, 'speaker_id': 0, 'begin_time': 19000, 'end_time': 22000, 'text': '这里就是沈阳的国际赛艇', 'words': []},
                    {'sentence_id': 2, 'speaker_id': 1, 'begin_time': 22020, 'end_time': 22800, 'text': '中心哦', 'words': []},
                ]
            }
        ]
    }

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    assert [(item['speakerKey'], item['text']) for item in saved.normalized_segments] == [
        ('speaker_0', '这里就是沈阳的国际赛艇'),
        ('speaker_1', '中心哦'),
    ]
    assert saved.processing_metrics['utterance_rebuilder_enabled'] == 0.0
    assert saved.processing_metrics['utterance_rebuilder_repair_count'] == 0.0


def test_save_transcription_result_upgrades_legacy_transcription_standard_strategy(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        kind='transcription',
        source_url='https://example.com/source.wav',
        status='processing',
        export_frame_rate='25',
        subtitle_strategy='standard',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 0,
                        'end_time': 1000,
                        'text': '大家好，欢迎来到节目。',
                        'words': [],
                    }
                ]
            }
        ]
    }
    observed_strategy: list[str] = []

    def fake_apply_subtitle_strategy(job_arg, segments, **_kwargs):
        observed_strategy.append(job_arg.subtitle_strategy)
        return segments, False, None

    monkeypatch.setattr('app.services.jobs.apply_subtitle_strategy', fake_apply_subtitle_strategy)

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    assert observed_strategy == ['enhanced']
    assert saved.subtitle_strategy == 'enhanced'


def test_update_job_source_export_path_updates_video_job_paths_together() -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        source_local_path='/srv/source.wav',
        source_media_kind='video',
        video_source_url='https://example.com/source.mp4',
        video_source_local_path='/srv/source.mp4',
        status='completed',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    updated = update_job_source_export_path(session, job.id, '/Users/editor/Desktop/source.mp4')

    assert updated.source_export_path == '/Users/editor/Desktop/source.mp4'
    assert updated.video_source_export_path == '/Users/editor/Desktop/source.mp4'


def test_delete_job_removes_record_and_local_files(tmp_path) -> None:
    session = _build_session()
    source_file = tmp_path / 'source.wav'
    video_file = tmp_path / 'source.mp4'
    lower_third_file = tmp_path / 'host.png'
    source_file.write_bytes(b'audio')
    video_file.write_bytes(b'video')
    lower_third_file.write_bytes(b'image')

    job = Job(
        source_url='https://example.com/source.wav',
        source_local_path=str(source_file),
        source_media_kind='audio',
        video_source_url='https://example.com/source.mp4',
        video_source_local_path=str(video_file),
        lower_third_assets=[
            {
                'speaker_name': '主持人',
                'file_name': '主持人.png',
                'object_key': 'host.png',
                'local_path': str(lower_third_file),
            }
        ],
        status='completed',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    preview_target = settings.audio_dir_path / f'job-{job.id}-{Path(job.source_local_path).stem}-preview.wav'
    preview_target.write_bytes(b'preview')

    delete_job(session, job.id)

    assert session.get(Job, job.id) is None
    assert not source_file.exists()
    assert not video_file.exists()
    assert not lower_third_file.exists()
    assert not preview_target.exists()


def test_delete_job_rejects_running_job() -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        status='running',
        render_status='idle',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    try:
        delete_job(session, job.id)
    except RuntimeError as error:
        assert str(error) == '任务仍在处理中，暂不允许删除'
    else:
        raise AssertionError('expected RuntimeError')


def test_delete_job_rejects_finalizing_job() -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        status='finalizing',
        render_status='idle',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    try:
        delete_job(session, job.id)
    except RuntimeError as error:
        assert str(error) == '任务仍在处理中，暂不允许删除'
    else:
        raise AssertionError('expected RuntimeError')


def test_abort_job_marks_active_job_as_aborted() -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        status='running',
        render_status='idle',
        processing_progress=42,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    aborted = abort_job(session, job.id, message='任务已由用户中止')

    assert aborted.status == 'aborted'
    assert aborted.processing_progress == 100
    assert aborted.processing_message == '任务已由用户中止'
    assert aborted.error_message == '任务已由用户中止'


def test_abort_job_rejects_completed_job() -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        status='completed',
        render_status='idle',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    try:
        abort_job(session, job.id)
    except RuntimeError as error:
        assert str(error) == '任务已经完成，无需中止'
    else:
        raise AssertionError('expected RuntimeError')


def test_save_transcription_result_does_not_override_aborted_job(monkeypatch) -> None:
    session = _build_session()
    job = Job(
        source_url='https://example.com/source.wav',
        status='aborted',
        export_frame_rate='25',
        subtitle_strategy='enhanced',
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    payload = {
        'transcripts': [
            {
                'sentences': [
                    {
                        'sentence_id': 1,
                        'speaker_id': 0,
                        'begin_time': 0,
                        'end_time': 1000,
                        'text': '大家好，欢迎来到节目。',
                        'words': [],
                    }
                ]
            }
        ]
    }

    monkeypatch.setattr(
        'app.services.jobs.apply_subtitle_strategy',
        lambda _job, segments, **_kwargs: (segments, False, None),
    )

    saved = save_transcription_result(
        session=session,
        job_id=job.id,
        transcription_url='https://example.com/transcription.json',
        raw_payload=payload,
    )

    assert saved.status == 'aborted'
    assert saved.transcription_url is None
