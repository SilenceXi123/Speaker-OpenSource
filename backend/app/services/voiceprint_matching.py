from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import tempfile
import math
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.person import Person
from app.models.project import Project
from app.models.voiceprint_match import VoiceprintMatch
from app.services.media import prepare_preview_audio
from app.models.voiceprint_sample import VoiceprintSample
from app.services.voice_retention import voice_retention_cutoff
from app.services.projects import resolve_unambiguous_project_scope_ids
from app.services.voiceprint_provider import extract_voiceprint_embedding, get_voiceprint_provider_status

logger = logging.getLogger(__name__)

AUTO_MATCH_THRESHOLD = 0.72
REVIEW_MATCH_THRESHOLD = 0.55
MIN_MATCH_SEGMENT_MS = 800
TARGET_MATCH_AUDIO_MS = 4500
MAX_MATCH_SEGMENTS = 4
MATCH_CONTEXT_PAD_MS = 400


def match_job_voiceprints(session: Session, job: Job) -> dict[str, str]:
    speaker_keys = sorted({str(item.get('speakerKey') or '').strip() for item in (job.normalized_segments or []) if item.get('speakerKey')})
    _clear_job_matches(session, job.id)
    if not speaker_keys:
        session.commit()
        return {}

    reference_people = _list_reference_people(session, job)
    if not reference_people:
        _save_placeholder_matches(session, job.id, speaker_keys, 'no_reference')
        return {}

    provider_status = get_voiceprint_provider_status()
    if not provider_status.available:
        _save_placeholder_matches(session, job.id, speaker_keys, 'pending_provider')
        return {}

    source_path = _resolve_job_audio_path(job)
    if not source_path:
        _save_placeholder_matches(session, job.id, speaker_keys, 'source_missing')
        return {}

    auto_mappings: dict[str, str] = {}
    for speaker_key in speaker_keys:
        segments = _pick_matching_segments(job.normalized_segments or [], speaker_key)
        if not segments:
            session.add(VoiceprintMatch(job_id=job.id, speaker_key=speaker_key, status='segment_missing'))
            continue

        try:
            with tempfile.TemporaryDirectory(prefix=f'voiceprint-{speaker_key}-') as tmp_dir:
                clip_path = _extract_speaker_clip(source_path, segments, Path(tmp_dir))
                embedding_result = extract_voiceprint_embedding(clip_path)
        except Exception:
            logger.exception(
                'Voiceprint embedding failed for job=%s speaker=%s source=%s segments=%s',
                job.id,
                speaker_key,
                source_path,
                _format_segment_ranges(segments),
            )
            session.add(VoiceprintMatch(job_id=job.id, speaker_key=speaker_key, status='embedding_failed'))
            continue

        candidates = _score_candidates(embedding_result.embedding, reference_people)[:3]
        top_candidate = candidates[0] if candidates else None
        match = VoiceprintMatch(
            job_id=job.id,
            speaker_key=speaker_key,
            person_id=top_candidate['person_id'] if top_candidate else None,
            score=top_candidate['score'] if top_candidate else None,
            top_candidates=candidates or None,
            status='unmatched',
        )
        if top_candidate:
            if top_candidate['score'] >= AUTO_MATCH_THRESHOLD:
                match.status = 'auto_matched'
                auto_mappings[speaker_key] = top_candidate['person_name']
            elif top_candidate['score'] >= REVIEW_MATCH_THRESHOLD:
                match.status = 'needs_review'
        session.add(match)

    session.commit()
    return auto_mappings


def build_voiceprint_match_lookup(session: Session, job_id: str) -> dict[str, dict[str, object]]:
    matches = list(session.scalars(select(VoiceprintMatch).where(VoiceprintMatch.job_id == job_id, VoiceprintMatch.created_at > voice_retention_cutoff())))
    return {
        item.speaker_key: {
            'status': item.status,
            'person_id': item.person_id,
            'score': item.score,
            'candidates': item.top_candidates or [],
        }
        for item in matches
    }


def _clear_job_matches(session: Session, job_id: str) -> None:
    session.execute(delete(VoiceprintMatch).where(VoiceprintMatch.job_id == job_id))


def _save_placeholder_matches(session: Session, job_id: str, speaker_keys: list[str], status: str) -> None:
    for speaker_key in speaker_keys:
        session.add(VoiceprintMatch(job_id=job_id, speaker_key=speaker_key, status=status))
    session.commit()


def _resolve_job_audio_path(job: Job) -> Path | None:
    source_path = (job.source_local_path or '').strip()
    if not source_path:
        return None
    path = Path(source_path)
    if not path.exists():
        return None
    if (job.source_media_kind or '').lower() == 'audio':
        try:
            return prepare_preview_audio(path, cache_key=f'job-{job.id}-voiceprint', media_kind='audio')
        except Exception:
            return path
    return path


def _pick_matching_segments(normalized_segments: list[dict], speaker_key: str) -> list[dict]:
    candidates = []
    for segment in normalized_segments:
        if str(segment.get('speakerKey') or '') != speaker_key:
            continue
        start_ms = int(segment.get('startMs') or 0)
        end_ms = int(segment.get('endMs') or 0)
        duration_ms = max(0, end_ms - start_ms)
        if duration_ms <= 0:
            continue
        candidates.append(
            {
                **segment,
                '_duration_ms': duration_ms,
                '_start_ms': start_ms,
            }
        )
    if not candidates:
        return []

    preferred = [item for item in candidates if int(item['_duration_ms']) >= MIN_MATCH_SEGMENT_MS]
    pool = preferred or candidates

    selected: list[dict] = []
    selected_keys: set[tuple[int, int]] = set()
    total_duration_ms = 0
    for segment in sorted(pool, key=lambda item: int(item['_duration_ms']), reverse=True):
        start_ms = int(segment['_start_ms'])
        end_ms = start_ms + int(segment['_duration_ms'])
        segment_key = (start_ms, end_ms)
        if segment_key in selected_keys:
            continue
        selected.append(segment)
        selected_keys.add(segment_key)
        total_duration_ms += int(segment['_duration_ms'])
        if total_duration_ms >= TARGET_MATCH_AUDIO_MS or len(selected) >= MAX_MATCH_SEGMENTS:
            break

    if total_duration_ms < MIN_MATCH_SEGMENT_MS:
        for segment in sorted(candidates, key=lambda item: int(item['_duration_ms']), reverse=True):
            start_ms = int(segment['_start_ms'])
            end_ms = start_ms + int(segment['_duration_ms'])
            segment_key = (start_ms, end_ms)
            if segment_key in selected_keys:
                continue
            selected.append(segment)
            selected_keys.add(segment_key)
            total_duration_ms += int(segment['_duration_ms'])
            if total_duration_ms >= MIN_MATCH_SEGMENT_MS or len(selected) >= MAX_MATCH_SEGMENTS:
                break

    return sorted(selected, key=lambda item: int(item['_start_ms']))


def _format_segment_ranges(segments: list[dict]) -> str:
    ranges = []
    for segment in segments:
        start_ms = int(segment.get('startMs') or 0)
        end_ms = int(segment.get('endMs') or 0)
        ranges.append(f'{start_ms}-{end_ms}')
    return ','.join(ranges)


def _extract_speaker_clip(source_path: Path, segments: list[dict], output_dir: Path) -> Path:
    if not segments:
        raise RuntimeError('缺少可用于声纹匹配的片段')
    output_dir.mkdir(parents=True, exist_ok=True)
    clip_paths = [_extract_segment_clip(source_path, segment, output_dir, index) for index, segment in enumerate(segments)]
    if len(clip_paths) == 1:
        return clip_paths[0]
    output_path = output_dir / f"{segments[0].get('speakerKey', 'speaker')}.wav"
    concat_list_path = output_dir / 'concat.txt'
    concat_list_path.write_text(
        ''.join(f"file '{path.as_posix()}'\n" for path in clip_paths),
        encoding='utf-8',
    )
    command = [
        'ffmpeg',
        '-y',
        '-f',
        'concat',
        '-safe',
        '0',
        '-i',
        str(concat_list_path),
        '-acodec',
        'pcm_s16le',
        '-ar',
        '16000',
        '-ac',
        '1',
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not output_path.exists():
        detail = (result.stderr or result.stdout or 'FFmpeg 拼接说话人片段失败').strip()
        raise RuntimeError(detail[:500])
    return output_path


def _extract_segment_clip(source_path: Path, segment: dict, output_dir: Path, index: int) -> Path:
    start_ms = max(0, int(segment.get('startMs') or 0) - MATCH_CONTEXT_PAD_MS)
    end_ms = int(segment.get('endMs') or 0) + MATCH_CONTEXT_PAD_MS
    duration_seconds = max(0.8, (end_ms - start_ms) / 1000)
    output_path = output_dir / f"{segment.get('speakerKey', 'speaker')}-{index}.wav"
    command = [
        'ffmpeg',
        '-y',
        '-ss',
        f'{start_ms / 1000:.3f}',
        '-t',
        f'{duration_seconds:.3f}',
        '-i',
        str(source_path),
        '-acodec',
        'pcm_s16le',
        '-ar',
        '16000',
        '-ac',
        '1',
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not output_path.exists():
        detail = (result.stderr or result.stdout or 'FFmpeg 抽取说话人片段失败').strip()
        raise RuntimeError(detail[:500])
    return output_path


def _list_reference_people(session: Session, job: Job) -> list[dict[str, object]]:
    statement = (
        select(VoiceprintSample, Person)
        .join(Person, Person.id == VoiceprintSample.person_id)
        .where(
            VoiceprintSample.created_at > voice_retention_cutoff(),
            VoiceprintSample.embedding_status == 'ready',
            VoiceprintSample.review_status == 'confirmed',
        )
    )
    preferred_predicate = None
    if job.project_id:
        project = session.get(Project, job.project_id)
        if not project or project.status != 'active':
            return []
        aliases = set(resolve_unambiguous_project_scope_ids(session, project.id))
        preferred_predicate = or_(
            Person.project_id == project.id,
            and_(
                Person.project_id.is_(None),
                or_(Person.program_key.in_(aliases), Person.program_name.in_(aliases)),
            ),
        )
        # A truly global person has passed cross-project consistency checks and
        # is safe to reuse in every active project. Project-specific people are
        # still isolated from one another.
        allowed_predicates = [preferred_predicate, _global_person_predicate()]
        statement = statement.where(or_(*allowed_predicates))
    else:
        aliases = {
            str(value or '').strip()
            for value in (job.program_key, job.program_name)
            if str(value or '').strip()
        }
        if aliases:
            preferred_predicate = or_(
                Person.program_key.in_(aliases),
                Person.program_name.in_(aliases),
            )
            statement = statement.where(or_(preferred_predicate, _global_person_predicate()))
        else:
            statement = statement.where(_global_person_predicate())

    rows = session.execute(statement).all()
    if not rows:
        return []

    preferred_rows = [
        (sample, person)
        for sample, person in rows
        if preferred_predicate is not None and not _is_global_person(person)
    ]
    # UUID-scoped jobs have already been restricted to the current project and,
    # when explicitly enabled, truly global people. Keep both candidate groups.
    effective_rows = rows if job.project_id else (preferred_rows or rows)

    people: list[dict[str, object]] = []
    for sample, person in effective_rows:
        if not sample.embedding:
            continue
        people.append(
            {
                'person_id': person.id,
                'person_name': person.name,
                'embedding': sample.embedding,
            }
        )
    return people


def _global_person_predicate():
    return and_(
        Person.project_id.is_(None),
        or_(Person.program_key.is_(None), Person.program_key == ''),
        or_(Person.program_name.is_(None), Person.program_name == ''),
    )


def _is_global_person(person: Person) -> bool:
    return not person.project_id and not str(person.program_key or '').strip() and not str(person.program_name or '').strip()


def _score_candidates(cluster_embedding: list[float], reference_people: list[dict[str, object]]) -> list[dict[str, object]]:
    best_by_person: dict[str, dict[str, object]] = {}
    for item in reference_people:
        score = _cosine_similarity(cluster_embedding, item['embedding'])
        person_id = str(item['person_id'])
        current = best_by_person.get(person_id)
        if current and float(current['score']) >= score:
            continue
        best_by_person[person_id] = {
            'person_id': person_id,
            'person_name': str(item['person_name']),
            'score': round(score, 4),
        }
    return sorted(best_by_person.values(), key=lambda item: float(item['score']), reverse=True)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    try:
        left_vector = [float(value) for value in left]
        right_vector = [float(value) for value in right]
    except (TypeError, ValueError):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left_vector))
    right_norm = math.sqrt(sum(value * value for value in right_vector))
    denominator = left_norm * right_norm
    if denominator == 0:
        return 0.0
    numerator = sum(left_value * right_value for left_value, right_value in zip(left_vector, right_vector))
    return float(numerator / denominator)
