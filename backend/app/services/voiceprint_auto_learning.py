from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import subprocess
from typing import Optional
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.job import Job
from app.models.person import Person
from app.models.project import Project
from app.models.voiceprint_sample import VoiceprintSample
from app.services.voice_retention import voice_retention_cutoff
from app.services.local_files import remove_local_file
from app.services.projects import resolve_unambiguous_project_scope_ids
from app.services.speaker_memory import build_program_key, normalize_program_name
from app.services.voiceprint_global import promote_consistent_person_to_global
from app.services.voiceprint_provider import extract_voiceprint_embedding
from app.services.voiceprint_policy import (
    MAX_VOICEPRINT_SAMPLES_PER_PERSON,
    auto_confirm_consistent_samples,
    enforce_voiceprint_sample_limit,
    remove_pruned_voiceprint_files,
)

MIN_CANDIDATE_MS = 2000
MAX_CANDIDATE_MS = 8000
MAX_NEW_CANDIDATES_PER_SAVE = 5
MAX_CANDIDATES_PER_PERSON = MAX_VOICEPRINT_SAMPLES_PER_PERSON
DUPLICATE_SIMILARITY_THRESHOLD = 0.985

_DEFAULT_SPEAKER_PATTERN = re.compile(r'^speaker[_\-\s]*\d+$', re.IGNORECASE)
_FILLER_CHARS = set('嗯啊哦哎唉呃额呀哇呢嘛哈呵嘿')
_FILLER_WORDS = {
    '嗯',
    '啊',
    '哦',
    '哎',
    '唉',
    '呃',
    '额',
    '呀',
    '哇',
    '呢',
    '嘛',
    '哈',
    '哈哈',
    '呵',
    '呵呵',
    '嘿',
    '嘿嘿',
}


@dataclass
class VoiceprintAutoLearningResult:
    created_count: int = 0
    skipped_count: int = 0
    duplicate_count: int = 0
    auto_confirmed_count: int = 0
    global_promoted_count: int = 0
    pruned_count: int = 0
    conflict_count: int = 0
    error_count: int = 0
    warnings: list[str] = field(default_factory=list)
    candidate_sample_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _CandidateSegment:
    speaker_key: str
    speaker_name: str
    text: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


def learn_voiceprint_candidates_for_job(session: Session, job: Job) -> VoiceprintAutoLearningResult:
    result = VoiceprintAutoLearningResult()
    source_path = _resolve_job_audio_path(job)
    if not source_path:
        return _warn(result, '源音频不存在，已跳过声纹自动学习')

    selected, skipped_count = _select_candidate_segments(job)
    result.skipped_count += skipped_count
    if not selected:
        return result

    for candidate in selected[:MAX_NEW_CANDIDATES_PER_SAVE]:
        try:
            person = _get_or_create_person(session, job, candidate.speaker_name)
        except RuntimeError as error:
            result.error_count += 1
            result.warnings.append(str(error))
            continue
        if _confirmed_sample_count_for_person(session, person.id) >= MAX_CANDIDATES_PER_PERSON:
            result.skipped_count += 1
            continue

        clip_path: Optional[Path] = None
        try:
            clip_path = _extract_candidate_clip(
                source_path=source_path,
                start_ms=candidate.start_ms,
                end_ms=candidate.end_ms,
                output_dir=settings.voiceprint_library_dir_path,
                file_stem=f'auto-{person.name}-{candidate.speaker_key}-{candidate.start_ms}',
            )
            embedding_result = extract_voiceprint_embedding(clip_path)
        except Exception as error:
            result.error_count += 1
            result.warnings.append(str(error))
            remove_local_file(clip_path)
            continue

        duplicate_sample = _find_duplicate_sample(
            session,
            person.id,
            embedding_result.embedding,
            source_job_id=job.id,
        )
        if duplicate_sample:
            result.duplicate_count += 1
            remove_local_file(clip_path)
            continue

        sample = VoiceprintSample(
            person_id=person.id,
            original_filename=clip_path.name,
            audio_local_path=str(clip_path),
            duration_ms=candidate.duration_ms,
            sample_rate_hz=16000,
            channel_count=1,
            confidence_score=0.5,
            usage_types=['voiceprint'],
            embedding=embedding_result.embedding,
            embedding_status='ready',
            provider_name=embedding_result.provider_name,
            provider_model_id=embedding_result.provider_model_id,
            provider_message=embedding_result.provider_message,
            source_type='auto_candidate',
            review_status='candidate',
            source_job_id=job.id,
            source_speaker_key=candidate.speaker_key,
            source_start_ms=candidate.start_ms,
            source_end_ms=candidate.end_ms,
            clone_readiness_status='unknown',
            waveform_ready=False,
        )
        session.add(sample)
        session.flush()
        auto_confirmed_count = auto_confirm_consistent_samples(session, sample)
        result.auto_confirmed_count += auto_confirmed_count
        person.updated_at = getattr(job, 'updated_at', None) or person.updated_at
        promotion = (
            promote_consistent_person_to_global(session, person.id)
            if auto_confirmed_count
            else None
        )
        if promotion and promotion.promoted:
            result.global_promoted_count += 1
            result.pruned_count += promotion.pruned_sample_count
        else:
            retention = enforce_voiceprint_sample_limit(session, person.id)
            session.commit()
            remove_pruned_voiceprint_files(session, retention)
            result.pruned_count += retention.removed_count
        if session.get(VoiceprintSample, sample.id) is None:
            result.skipped_count += 1
            continue
        session.refresh(sample)
        result.created_count += 1
        result.candidate_sample_ids.append(sample.id)

    return result


def _select_candidate_segments(job: Job) -> tuple[list[_CandidateSegment], int]:
    best_by_speaker: dict[str, _CandidateSegment] = {}
    skipped_count = 0
    for segment in job.normalized_segments or []:
        candidate = _build_candidate_segment(job, segment)
        if not candidate:
            skipped_count += 1
            continue
        current = best_by_speaker.get(candidate.speaker_name)
        if current and current.duration_ms >= candidate.duration_ms:
            skipped_count += 1
            continue
        best_by_speaker[candidate.speaker_name] = candidate
    return sorted(best_by_speaker.values(), key=lambda item: item.duration_ms, reverse=True), skipped_count


def _build_candidate_segment(job: Job, segment: dict) -> Optional[_CandidateSegment]:
    speaker_key = str(segment.get('speakerKey') or '').strip()
    speaker_name = _resolve_confirmed_speaker_name(job, segment, speaker_key)
    if _is_default_speaker_name(speaker_name):
        return None

    text = str(segment.get('text') or '').strip()
    if _is_filler_only_text(text):
        return None

    start_ms = int(segment.get('startMs') or 0)
    end_ms = int(segment.get('endMs') or 0)
    if end_ms <= start_ms:
        return None
    start_ms, end_ms = _clamp_candidate_window(start_ms, end_ms)
    if end_ms - start_ms < MIN_CANDIDATE_MS:
        return None
    return _CandidateSegment(
        speaker_key=speaker_key,
        speaker_name=speaker_name,
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
    )


def _resolve_confirmed_speaker_name(job: Job, segment: dict, speaker_key: str) -> str:
    override_names = []
    for item in segment.get('speakerOverrides') or []:
        if not isinstance(item, dict):
            continue
        name = normalize_program_name(item.get('speakerName'))
        if name:
            override_names.append(name)
    unique_override_names = sorted(set(override_names))
    if len(unique_override_names) == 1:
        return unique_override_names[0]
    if len(unique_override_names) > 1:
        return ''

    mappings = job.speaker_mappings or {}
    return normalize_program_name(mappings.get(speaker_key) or speaker_key)


def _clamp_candidate_window(start_ms: int, end_ms: int) -> tuple[int, int]:
    duration_ms = end_ms - start_ms
    if duration_ms <= MAX_CANDIDATE_MS:
        return start_ms, end_ms
    center_ms = start_ms + duration_ms // 2
    next_start = max(start_ms, center_ms - MAX_CANDIDATE_MS // 2)
    next_end = min(end_ms, next_start + MAX_CANDIDATE_MS)
    return next_start, next_end


def _resolve_job_audio_path(job: Job) -> Optional[Path]:
    raw_path = str(job.source_local_path or '').strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_file():
        return None
    return path


def _get_or_create_person(session: Session, job: Job, speaker_name: str) -> Person:
    normalized_name = normalize_program_name(speaker_name)
    project = session.get(Project, job.project_id) if job.project_id else None
    if job.project_id and (not project or project.status != 'active'):
        raise RuntimeError('任务所属项目不存在或已归档，已跳过声纹自动学习')
    program_name = project.name if project else normalize_program_name(job.program_name)
    program_key = (
        project.legacy_program_key or project.name_key
        if project
        else build_program_key(job.program_key or program_name) or None
    )

    if job.project_id:
        person = session.scalar(
            select(Person).where(
                Person.name == normalized_name,
                Person.project_id == job.project_id,
            )
        )
        if person:
            return person

        safe_aliases = resolve_unambiguous_project_scope_ids(session, job.project_id)
        legacy_people = list(session.scalars(
            select(Person).where(
                Person.name == normalized_name,
                Person.project_id.is_(None),
                or_(Person.program_key.in_(safe_aliases), Person.program_name.in_(safe_aliases)),
            )
        )) if safe_aliases else []
        if len(legacy_people) == 1:
            person = legacy_people[0]
            person.project_id = job.project_id
            person.program_name = program_name or person.program_name
            person.program_key = program_key or person.program_key
            session.commit()
            session.refresh(person)
            return person
    else:
        person = session.scalar(
            select(Person).where(
                Person.name == normalized_name,
                Person.project_id.is_(None),
                Person.program_key == program_key,
            )
        )
        if person:
            return person

    person = Person(
        name=normalized_name,
        project_id=job.project_id,
        program_name=program_name or None,
        program_key=program_key,
        aliases=None,
        notes='自动学习声纹候选创建的人物',
    )
    session.add(person)
    session.commit()
    session.refresh(person)
    return person


def _candidate_count_for_person(session: Session, person_id: str) -> int:
    return int(
        session.query(VoiceprintSample)
        .filter(
            VoiceprintSample.person_id == person_id,
            VoiceprintSample.created_at > voice_retention_cutoff(),
            VoiceprintSample.source_type == 'auto_candidate',
            VoiceprintSample.review_status == 'candidate',
        )
        .count()
    )


def _confirmed_sample_count_for_person(session: Session, person_id: str) -> int:
    return int(
        session.query(VoiceprintSample)
        .filter(
            VoiceprintSample.person_id == person_id,
            VoiceprintSample.created_at > voice_retention_cutoff(),
            VoiceprintSample.review_status == 'confirmed',
        )
        .count()
    )


def _find_duplicate_sample(
    session: Session,
    person_id: str,
    embedding: list[float],
    *,
    source_job_id: str | None = None,
) -> Optional[VoiceprintSample]:
    samples = list(
        session.scalars(
            select(VoiceprintSample).where(
                VoiceprintSample.person_id == person_id,
                VoiceprintSample.created_at > voice_retention_cutoff(),
                VoiceprintSample.embedding_status == 'ready',
            )
        )
    )
    for sample in samples:
        if not sample.embedding:
            continue
        independent_jobs = bool(
            source_job_id
            and sample.source_job_id
            and sample.source_job_id != source_job_id
        )
        if (
            not independent_jobs
            and _cosine_similarity(embedding, sample.embedding) >= DUPLICATE_SIMILARITY_THRESHOLD
        ):
            return sample
    return None


def _extract_candidate_clip(
    *,
    source_path: Path,
    start_ms: int,
    end_ms: int,
    output_dir: Path,
    file_stem: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = ''.join(char if char.isalnum() or char in {'-', '_'} else '-' for char in file_stem)[:120]
    output_path = output_dir / f'{safe_stem}-{uuid4().hex[:8]}.wav'
    duration_seconds = max(0.8, (end_ms - start_ms) / 1000)
    command = [
        'ffmpeg',
        '-y',
        '-ss',
        f'{start_ms / 1000:.3f}',
        '-t',
        f'{duration_seconds:.3f}',
        '-i',
        str(source_path),
        '-vn',
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
        detail = (result.stderr or result.stdout or 'FFmpeg 抽取声纹候选片段失败').strip()
        raise RuntimeError(detail[:500])
    return output_path


def _is_default_speaker_name(name: str) -> bool:
    value = normalize_program_name(name)
    if not value:
        return True
    return bool(_DEFAULT_SPEAKER_PATTERN.match(value))


def _is_filler_only_text(text: str) -> bool:
    value = re.sub(r'[，。、！？；：、“”‘’（）()\[\]【】《》〈〉…,.!?;:\s-]+', '', str(text or '').strip())
    if not value:
        return True
    if value in _FILLER_WORDS:
        return True
    return len(value) <= 8 and all(char in _FILLER_CHARS for char in value)


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


def _warn(result: VoiceprintAutoLearningResult, message: str) -> VoiceprintAutoLearningResult:
    result.warnings.append(message)
    return result
