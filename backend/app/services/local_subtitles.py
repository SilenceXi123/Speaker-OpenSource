"""Pure local subtitle commands. No media reads, service settings, database or network."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any
import unicodedata

from app.services.raw_asr_snapshot import build_raw_asr_snapshot


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 2**53:
        raise ValueError(f'{name} 必须是真实有效的整数')
    return value


def _span(item: dict, duration: int) -> tuple[int, int]:
    start = _integer(item.get('start_ms'), 'start_ms')
    end = _integer(item.get('end_ms'), 'end_ms')
    if not start <= end <= duration:
        raise ValueError('识别时间超出音频范围或顺序无效')
    return start, end


def _text(value: Any, name: str, *, empty: bool = False, limit: int = 100000) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f'{name} 无效')
    return value


def _match_text(text: str) -> str:
    return ''.join(char for char in text if not char.isspace() and not unicodedata.category(char).startswith('P'))


def recognition_to_payload(recognition: dict, speaker_name: str = '') -> dict:
    """Represent each Apple attributed run as one indivisible, coarsely timed token."""
    if not isinstance(recognition, dict) or recognition.get('engine') != 'apple_speech':
        raise ValueError('仅接受 Apple 本机识别结果')
    duration = _integer(recognition.get('duration_ms'), 'duration_ms', minimum=1)
    sample_rate = _integer(recognition.get('sample_rate_hz'), 'sample_rate_hz', minimum=1)
    channels = _integer(recognition.get('channel_count'), 'channel_count', minimum=1)
    locale = _text(recognition.get('locale'), 'locale', limit=64)
    system_version = _text(recognition.get('system_version'), 'system_version', limit=128)
    speaker_name = _text(speaker_name, 'speaker_name', empty=True, limit=255).strip()
    segments = recognition.get('segments')
    if not isinstance(segments, list) or not segments or len(segments) > 200000:
        raise ValueError('没有可用的本机语音识别结果')
    sentences = []
    previous_start = -1
    for segment_index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError('识别片段格式无效')
        text = _text(segment.get('text'), 'segment.text')
        start, end = _span(segment, duration)
        words = segment.get('words', [])
        if not isinstance(words, list):
            raise ValueError('Apple 时间范围格式无效')
        if words and _match_text(''.join(_text(word.get('text'), 'run.text') for word in words if isinstance(word, dict))) != _match_text(text):
            raise ValueError('Apple 片段文字与带时码的原文不一致')
        for run_index, run in enumerate(words or [segment]):
            if not isinstance(run, dict):
                raise ValueError('Apple 时间范围格式无效')
            run_text = _text(run.get('text'), 'run.text')
            run_start, run_end = _span(run, duration)
            if run_start < start or run_end > end or run_start < previous_start:
                raise ValueError('Apple 时间范围超出片段或发生倒序')
            previous_start = run_start
            sentence = {
                'sentence_id': len(sentences) + 1, 'text': run_text,
                'begin_time': run_start, 'end_time': run_end, 'words': [],
                'apple_segment_index': segment_index, 'apple_run_index': run_index,
            }
            if speaker_name:
                sentence['speaker_id'] = 0  # User label for this track; never diarization.
            sentences.append(sentence)
    return {'transcripts': [{'sentences': sentences}], '_apple_speech': {
        'engine': 'apple_speech', 'locale': locale, 'duration_ms': duration,
        'sample_rate_hz': sample_rate, 'channel_count': channels, 'system_version': system_version,
        'timing_granularity': 'apple_attributed_run', 'diarization': False,
        'speaker_assignment': 'user_assigned' if speaker_name else 'unknown', 'speaker_name': speaker_name,
    }}


def build_local_raw_snapshot(payload: dict, *, provider_task_id: str = 'local-apple'):
    metadata = payload.get('_apple_speech')
    if not isinstance(metadata, dict) or metadata.get('engine') != 'apple_speech':
        raise ValueError('缺少 Apple 原始识别元数据')
    return build_raw_asr_snapshot(
        payload, provider_model='apple_speech', provider_task_id=provider_task_id,
        language=str(metadata['locale']).split('_')[0].split('-')[0],
        request_metadata=metadata,
        preflight_metadata={
            'duration_ms': metadata['duration_ms'], 'sample_rate_hz': metadata['sample_rate_hz'],
            'channel_count': metadata['channel_count'], 'status': 'accepted',
            'timing_granularity': 'apple_attributed_run',
        },
    )


def _refresh(job: dict, *, source: str) -> dict:
    from app.services.pipeline_artifacts import build_pipeline_artifacts
    from app.services.normalize import normalize_funasr_payload
    from app.services.speakers import build_speaker_options
    from app.services.subtitles import build_subtitle_cues, render_subtitle_content

    previous = job.get('pipeline_artifacts') or {}
    profile = job['processing_metrics']['subtitle_length_profile']
    cues = build_subtitle_cues(job['normalized_segments'], job['speaker_mappings'], export_frame_rate=job['export_frame_rate'],
                               subtitle_length_mode=profile['mode'], subtitle_max_chars=profile['max_chars'])
    facade = SimpleNamespace(**{**job, 'kind': 'transcription'})
    artifacts = build_pipeline_artifacts(facade, raw_segments=normalize_funasr_payload(job['raw_payload']),
        semantic_utterances=job['normalized_segments'], normalized_segments=job['normalized_segments'], refreshed_by=source,
        user_edit_patches=job['processing_metrics'].get('user_edit_patches', []))
    for key in ('evidence_subtitle_v2', 'dialogue_broadcast_normalization'):
        if key in previous:
            artifacts[key] = deepcopy(previous[key])
    artifacts['generation_timeline_revision'] = previous.get('generation_timeline_revision') or previous.get('timeline_revision') or artifacts['timeline_revision']
    artifacts['local_processing'] = {'engine': 'apple_speech', 'cloud_ai_used': False, 'timing_granularity': 'apple_attributed_run', 'diarization': False}
    job['pipeline_artifacts'] = artifacts
    job['local_cues'] = [cue.model_dump(mode='json') for cue in cues]
    job['local_full_srt'] = render_subtitle_content(cues, 'srt', 'full')
    job['local_clean_srt'] = render_subtitle_content(cues, 'srt', 'clean')
    job['local_speakers'] = {
        'mappings': job['speaker_mappings'], 'program_name': job.get('program_name'), 'memory_suggestions': [],
        'voiceprint_learning': None,
        'speakers': [option.model_dump(mode='json') for option in build_speaker_options(job['normalized_segments'], job['speaker_mappings'])],
    }
    job['updated_at'] = datetime.now(timezone.utc).isoformat()
    job['status'] = 'completed_with_warnings' if artifacts['cue_validation_report']['issue_count'] else 'completed'
    return job


def _build(payload: dict) -> dict:
    from app.schemas.job import JobDetailResponse
    from app.services.script_correction_candidates import build_script_snapshot
    from app.services.subtitle_pipeline_v2 import build_evidence_subtitle_segments_v2
    from app.services.subtitle_global_planner import CuePlannerProfile
    from app.services.subtitles import build_subtitle_length_profile

    supplied = payload.get('job')
    if not isinstance(supplied, dict):
        raise ValueError('缺少本地任务')
    job_id = _text(supplied.get('id'), 'job.id', limit=128)
    if not job_id.startswith('local-'):
        raise ValueError('本机任务 ID 必须以 local- 开头')
    project_id = _text(supplied.get('project_id'), 'project_id', limit=128)
    source_path = _text(supplied.get('source_local_path'), 'source_local_path', limit=8192)
    if not Path(source_path).is_absolute():
        raise ValueError('本机素材必须使用绝对路径')
    frame_rate = str(supplied.get('export_frame_rate') or '25')
    if frame_rate not in {'25', '29.97', '50'}:
        raise ValueError('不支持的字幕帧率')
    kind = supplied.get('kind', 'transcription')
    if kind not in {'transcription', 'transcript'}:
        raise ValueError('不支持的任务类型')
    recognition = payload.get('recognition')
    provider_payload = recognition_to_payload(recognition, supplied.get('speaker_name') or '')
    for option, default in (('use_project_scripts', True), ('lyrics_correction_mode', False)):
        if not isinstance(supplied.get(option, default), bool):
            raise ValueError(f'{option} 必须是布尔值')
    raw = build_local_raw_snapshot(provider_payload, provider_task_id=job_id)
    profile = build_subtitle_length_profile(supplied.get('subtitle_length_mode'), supplied.get('subtitle_max_chars'))
    cue_profile = CuePlannerProfile(mode=profile['mode'], strict_max_display_units=profile['max_chars'] if profile['mode'] == 'strict' else None)
    segments, evidence = build_evidence_subtitle_segments_v2(raw_snapshot=raw, script_snapshot=build_script_snapshot(''), cue_profile=cue_profile)
    for segment in segments:
        segment['timingEvidence'] = 'apple_attributed_run'
    evidence.update({'timing_policy': 'apple_attributed_run', 'ai_policy': 'offline_rules_only', 'ai_total_windows': 0, 'ai_completed_windows': 0})
    now = datetime.now(timezone.utc)
    job = JobDetailResponse(id=job_id, kind=kind, status='completed', source_url='', source_local_path=source_path,
        source_export_path=supplied.get('source_export_path') or source_path, export_frame_rate=frame_rate,
        project_id=project_id, program_name=supplied.get('program_name'), subtitle_strategy='evidence',
        source_duration_seconds=recognition['duration_ms'] / 1000, created_at=now, updated_at=now,
        processing_progress=100, processing_message='本机识别与规则质检完成，未调用云端 AI',
        speaker_mappings={'speaker_0': provider_payload['_apple_speech']['speaker_name'] or '未区分说话人'}, normalized_segments=segments,
        raw_payload=provider_payload, pipeline_artifacts={'evidence_subtitle_v2': evidence},
        processing_metrics={'subtitle_length_profile': profile, 'user_edit_patches': []}).model_dump(mode='json')
    job.update({'local_only': True, 'local_recognition': deepcopy(recognition),
                'use_project_scripts': supplied.get('use_project_scripts', True),
                'lyrics_correction_mode': supplied.get('lyrics_correction_mode', False),
                'original_filename': supplied.get('original_filename') or Path(source_path).name,
                'client_source_path': supplied.get('client_source_path') or source_path})
    return _refresh(job, source='local_build')


def _validated_job(value: Any) -> dict:
    if not isinstance(value, dict) or value.get('local_only') is not True or not str(value.get('id', '')).startswith('local-'):
        raise ValueError('不是有效的本机任务')
    job = deepcopy(value)
    recognition_to_payload(job.get('local_recognition'))
    duration = job['local_recognition']['duration_ms']
    for segment in job.get('normalized_segments') or []:
        if not isinstance(segment, dict) or segment.get('v2FinalCue') is not True:
            raise ValueError('本机字幕缺少确定性时间证据')
        _span({'start_ms': segment.get('startMs'), 'end_ms': segment.get('endMs')}, duration)
        _text(segment.get('text'), 'subtitle.text', empty=True)
    return job


def _edit(payload: dict) -> dict:
    from app.schemas.speaker import SpeakerMappingItem
    from app.schemas.subtitle import SubtitleSpeakerOverrideUpdate, SubtitleTextOverrideUpdate
    from app.services.subtitles import apply_subtitle_speaker_overrides, apply_subtitle_text_overrides, build_standalone_filler_delete_overrides
    from app.services.user_edit_patches import build_segment_edit_patch

    job = _validated_job(payload.get('job'))
    changes = payload.get('changes') or []
    if not isinstance(changes, list) or len(changes) > 10000:
        raise ValueError('修改列表无效')
    action = payload.get('action')
    before = deepcopy(job['normalized_segments'])
    before_mappings = dict(job['speaker_mappings'])
    profile = job['processing_metrics']['subtitle_length_profile']
    kwargs = {'subtitle_length_mode': profile['mode'], 'subtitle_max_chars': profile['max_chars']}
    if action == 'speakers':
        mappings = [SpeakerMappingItem.model_validate(change) for change in changes]
        for mapping in mappings:
            if mapping.speaker_key not in job['speaker_mappings'] or not mapping.speaker_name.strip():
                raise ValueError('说话人映射无效')
            job['speaker_mappings'][mapping.speaker_key] = mapping.speaker_name.strip()
    elif action in {'subtitle_speakers', 'subtitle_text', 'delete_fillers'}:
        if action == 'delete_fillers':
            changes = build_standalone_filler_delete_overrides(before, job['speaker_mappings'], **kwargs)
        else:
            schema = SubtitleSpeakerOverrideUpdate if action == 'subtitle_speakers' else SubtitleTextOverrideUpdate
            changes = [schema.model_validate(change).model_dump() for change in changes]
            indexes = [change['index'] for change in changes]
            if len(set(indexes)) != len(indexes) or any(index < 1 or index > len(job['local_cues']) for index in indexes):
                raise ValueError('字幕索引过期或重复，请重新打开任务')
        apply = apply_subtitle_speaker_overrides if action == 'subtitle_speakers' else apply_subtitle_text_overrides
        job['normalized_segments'] = apply(before, changes, job['speaker_mappings'], **kwargs)
    else:
        raise ValueError('不支持的本机字幕修改')
    patch = build_segment_edit_patch(before, job['normalized_segments'], source=f'local_{action}')
    patch['requested_changes'] = changes
    patch['speaker_mappings_before'] = before_mappings
    patch['speaker_mappings_after'] = job['speaker_mappings']
    patches = job['processing_metrics'].get('user_edit_patches', [])
    job['processing_metrics']['user_edit_patches'] = (patches + [patch])[-20:]
    return _refresh(job, source=f'local_{action}')


def _export(payload: dict) -> dict:
    from app.services.premiere_xml import build_visual_aligned_subtitle_cues
    from app.services.subtitle_cue_validator import validate_subtitle_cues
    from app.services.subtitles import render_subtitle_content
    from app.schemas.subtitle import SubtitleCue
    from app.services.subtitle_interchange import convert_srt_bytes_to_premiere_xml

    job = _refresh(_validated_job(payload.get('job')), source='local_export_validation')
    fmt = payload.get('format', 'srt')
    variant = payload.get('variant', 'clean')
    if fmt not in {'srt', 'vtt', 'xml'} or variant not in {'clean', 'full'}:
        raise ValueError('不支持的字幕导出格式')
    cues = [SubtitleCue.model_validate(cue) for cue in job['local_cues']]
    if not cues:
        raise ValueError('没有可导出的字幕')
    for candidate_cues in (cues, build_visual_aligned_subtitle_cues(cues, frame_rate=job['export_frame_rate'])):
        report = validate_subtitle_cues(candidate_cues)
        errors = [issue.message for issue in report.issues if issue.code in {'invalid_timing', 'overlapping_time'}]
        if errors:
            raise ValueError('字幕时间无效或重叠，已禁止导出：' + '；'.join(errors[:5]))
    stem = re.sub(r'[/\\:\x00-\x1f]', '_', Path(job.get('original_filename') or '字幕').stem)[:160] or '字幕'
    if fmt == 'xml':
        content = convert_srt_bytes_to_premiere_xml(render_subtitle_content(cues, 'srt', variant).encode('utf-8'), source_name=stem,
                                             frame_rate=job['export_frame_rate']).xml_content
        mime = 'application/xml; charset=utf-8'
    else:
        content = render_subtitle_content(candidate_cues, fmt, variant)
        mime = 'text/vtt; charset=utf-8' if fmt == 'vtt' else 'application/x-subrip; charset=utf-8'
    return {'filename': f'{stem}-{variant}.{fmt}', 'mime_type': mime, 'content_base64': base64.b64encode(content.encode('utf-8')).decode('ascii')}


def handle_local_subtitle_command(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('本机字幕请求必须是 JSON 对象')
    operation = payload.get('operation')
    if operation == 'build':
        return _build(payload)
    if operation == 'edit':
        return _edit(payload)
    if operation == 'export':
        return _export(payload)
    raise ValueError('不支持的本机字幕操作')
