from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.job import Job
from app.models.person import Person
from app.models.project import Project
from app.models.speaker_memory import SpeakerMemory
from app.models.voiceprint_match import VoiceprintMatch
from app.models.voiceprint_sample import VoiceprintSample
from app.schemas.voiceprint import PersonCreateRequest, PersonResponse, ProgramDeleteResponse, ProgramSummaryResponse, VoiceprintSampleResponse
from app.services.local_files import copy_local_file_to_library, remove_local_file
from app.services.media import get_media_duration_seconds, prepare_recognition_audio, prepare_voice_clone_reference_audio
from app.services.projects import (
    ensure_legacy_project,
    get_project,
    get_project_by_name,
    resolve_unambiguous_project_scope_ids,
)
from app.services.speaker_memory import build_program_key, get_program_speaker_memory, normalize_program_name
from app.services.voice_sample_analysis import VOICE_SAMPLE_USAGE_TYPES, analyze_clone_readiness, ensure_sample_public_url
from app.services.voice_retention import voice_retention_cutoff, voice_is_expired, voice_expires_at
from app.services.voiceprint_provider import extract_voiceprint_embedding
from app.services.voiceprint_policy import (
    enforce_voiceprint_sample_limit,
    remove_pruned_voiceprint_files,
)


@dataclass(frozen=True)
class VoiceprintSampleAssets:
    voiceprint_audio_path: Path
    clone_audio_path: Path


def list_people(
    session: Session,
    *,
    project_id: Optional[str] = None,
    include_global: bool = False,
) -> list[Person]:
    statement = select(Person)
    if project_id:
        scope_ids = resolve_unambiguous_project_scope_ids(session, project_id)
        predicates = [Person.project_id == project_id]
        if scope_ids:
            predicates.append(
                and_(
                    Person.project_id.is_(None),
                    or_(Person.program_key.in_(scope_ids), Person.program_name.in_(scope_ids)),
                )
            )
        if include_global:
            predicates.append(_global_person_predicate())
        statement = statement.where(or_(*predicates))
    return list(session.scalars(statement.order_by(Person.updated_at.desc(), Person.created_at.desc())))


def get_person(session: Session, person_id: str) -> Optional[Person]:
    return session.get(Person, person_id)


def create_person(
    session: Session,
    payload: PersonCreateRequest,
    *,
    project_id: Optional[str] = None,
) -> Person:
    requested_project_id = str(project_id or payload.project_id or '').strip() or None
    project: Project | None = None
    if requested_project_id:
        project = get_project(session, requested_project_id)
        if not project:
            raise LookupError('项目不存在')
        if project.status != 'active':
            raise RuntimeError('项目已归档，不能新增人物')
    elif normalize_program_name(payload.program_name):
        project = ensure_legacy_project(session, payload.program_name)

    program_name = project.name if project else normalize_program_name(payload.program_name) or None
    program_key = (
        project.legacy_program_key or project.name_key
        if project
        else build_program_key(payload.program_name) or None
    )
    person = Person(
        name=normalize_program_name(payload.name),
        project_id=project.id if project else None,
        program_name=program_name,
        program_key=program_key,
        aliases=[normalize_program_name(item) for item in payload.aliases if normalize_program_name(item)] or None,
        notes=(payload.notes or '').strip() or None,
    )
    session.add(person)
    session.commit()
    session.refresh(person)
    return person


def list_program_summaries(session: Session) -> list[ProgramSummaryResponse]:
    program_map: dict[str, ProgramSummaryResponse] = {}

    people = list(session.scalars(select(Person)))
    for person in people:
        normalized_name = normalize_program_name(person.program_name)
        if not normalized_name:
            continue
        program_key = build_program_key(normalized_name)
        summary = program_map.setdefault(
            program_key,
            ProgramSummaryResponse(program_key=program_key, program_name=normalized_name),
        )
        summary.person_count += 1

    samples = list(session.scalars(select(VoiceprintSample).where(VoiceprintSample.created_at > voice_retention_cutoff())))
    person_lookup = {person.id: person for person in people}
    for sample in samples:
        person = person_lookup.get(sample.person_id)
        normalized_name = normalize_program_name(person.program_name if person else None)
        if not normalized_name:
            continue
        summary = program_map.get(build_program_key(normalized_name))
        if summary:
            summary.sample_count += 1

    for job in session.scalars(select(Job)):
        normalized_name = normalize_program_name(job.program_name)
        if not normalized_name:
            continue
        program_key = build_program_key(normalized_name)
        summary = program_map.setdefault(
            program_key,
            ProgramSummaryResponse(program_key=program_key, program_name=normalized_name),
        )
        summary.job_count += 1

    for memory in session.scalars(select(SpeakerMemory)):
        normalized_name = normalize_program_name(memory.program_name)
        if not normalized_name:
            continue
        program_key = build_program_key(normalized_name)
        program_map.setdefault(
            program_key,
            ProgramSummaryResponse(program_key=program_key, program_name=normalized_name),
        )

    return sorted(program_map.values(), key=lambda item: item.program_name)


def create_program_summary(session: Session, program_name: str) -> ProgramSummaryResponse:
    normalized_name = normalize_program_name(program_name)
    program_key = build_program_key(normalized_name)
    if not normalized_name or not program_key:
        raise ValueError('项目名称不能为空')

    project = ensure_legacy_project(session, normalized_name)
    memory = get_program_speaker_memory(session, program_key, project_id=project.id)
    if not memory:
        raise RuntimeError('项目记忆初始化失败')

    return ProgramSummaryResponse(program_key=program_key, program_name=normalized_name)


def delete_empty_program(session: Session, program_key: str) -> ProgramDeleteResponse:
    normalized_key = build_program_key(program_key)
    if not normalized_key:
        raise ValueError('项目标识不能为空')

    summaries = list_program_summaries(session)
    summary = next((item for item in summaries if item.program_key == normalized_key), None)
    if not summary:
        raise LookupError('项目不存在')
    if summary.person_count or summary.sample_count or summary.job_count:
        raise RuntimeError('该项目已被任务或声纹数据使用，暂不允许删除')

    project = get_project_by_name(session, normalized_key)
    memory = get_program_speaker_memory(
        session,
        normalized_key,
        project_id=project.id if project else None,
    )
    if not memory:
        raise LookupError('项目不存在')
    if list(memory.speaker_names or []) or list(memory.lower_third_assets or []):
        raise RuntimeError('该项目已保存说话人姓名或人名条，暂不允许删除')

    deleted_name = memory.program_name or summary.program_name
    session.delete(memory)
    session.commit()
    return ProgramDeleteResponse(program_key=normalized_key, program_name=deleted_name, deleted=True)


def save_voiceprint_sample(
    session: Session,
    person: Person,
    audio_local_path: Path,
    original_filename: str,
    clone_audio_local_path: Optional[Path] = None,
) -> VoiceprintSample:
    clone_path = clone_audio_local_path or audio_local_path
    clone_sample_rate_hz = _clone_sample_rate_hz(has_clone_reference=clone_audio_local_path is not None)
    clone_channel_count = _clone_channel_count(has_clone_reference=clone_audio_local_path is not None)
    duration_ms = int(get_media_duration_seconds(clone_path) * 1000) or None
    clone_readiness = analyze_clone_readiness(
        duration_ms=duration_ms,
        sample_rate_hz=clone_sample_rate_hz,
        channel_count=clone_channel_count,
    )
    audio_public_url = None
    public_url_error = None
    try:
        audio_public_url = ensure_sample_public_url(clone_path)
    except Exception as error:
        public_url_error = str(error)
    sample = VoiceprintSample(
        person_id=person.id,
        original_filename=Path(original_filename).name,
        audio_local_path=str(audio_local_path),
        duration_ms=duration_ms,
        sample_rate_hz=clone_sample_rate_hz,
        channel_count=clone_channel_count,
        usage_types=list(VOICE_SAMPLE_USAGE_TYPES),
        embedding_status='pending_provider',
        provider_name=None,
        provider_model_id=None,
        provider_message='样本已入库，等待声纹 provider 生成 embedding。',
        clone_readiness_status=clone_readiness.status,
        clone_readiness_message=clone_readiness.message,
        audio_public_url=audio_public_url,
        clone_audio_local_path=str(clone_path),
        clone_audio_public_url=audio_public_url,
        clone_sample_rate_hz=clone_sample_rate_hz,
        clone_channel_count=clone_channel_count,
        waveform_ready=False,
    )

    try:
        embedding_result = extract_voiceprint_embedding(audio_local_path)
    except Exception as error:
        sample.embedding_status = 'pending_provider'
        sample.provider_message = str(error)
    else:
        sample.embedding = embedding_result.embedding
        sample.embedding_status = 'ready'
        sample.provider_name = embedding_result.provider_name
        sample.provider_model_id = embedding_result.provider_model_id
        sample.provider_message = embedding_result.provider_message

    if public_url_error:
        warning = f'样本公网地址未准备好：{public_url_error}'
        if sample.clone_readiness_message:
            sample.clone_readiness_message = f'{sample.clone_readiness_message}；{warning}'
        else:
            sample.clone_readiness_message = warning

    session.add(sample)
    session.flush()
    retention = enforce_voiceprint_sample_limit(session, person.id)
    session.commit()
    remove_pruned_voiceprint_files(session, retention)
    if sample.id in retention.removed_sample_ids:
        raise RuntimeError('新上传声纹未达到保留条件，请换一段更清晰的音频')
    session.refresh(sample)
    session.refresh(person)
    return sample


def list_person_samples(session: Session, person_id: str) -> list[VoiceprintSample]:
    return list(session.scalars(select(VoiceprintSample).where(VoiceprintSample.person_id == person_id, VoiceprintSample.created_at > voice_retention_cutoff()).order_by(VoiceprintSample.created_at.desc())))


def get_voiceprint_sample(session: Session, sample_id: str) -> Optional[VoiceprintSample]:
    sample = session.get(VoiceprintSample, sample_id)
    return sample if sample and not voice_is_expired(sample.created_at) else None


def delete_voiceprint_sample(session: Session, sample: VoiceprintSample) -> None:
    sample_paths = _sample_local_paths(sample)
    person = get_person(session, sample.person_id)
    session.delete(sample)
    if person:
        person.updated_at = datetime.utcnow()
    session.commit()
    for sample_path in sample_paths:
        remove_local_file(sample_path)


def confirm_voiceprint_candidate(session: Session, sample: VoiceprintSample) -> VoiceprintSample:
    if voice_is_expired(sample.created_at):
        raise LookupError('声纹样本已到期，请重新创建')
    if sample.source_type == 'auto_candidate':
        sample.source_type = 'auto_confirmed'
    sample.review_status = 'confirmed'
    person = get_person(session, sample.person_id)
    if person:
        person.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(sample)
    return sample


def delete_person(session: Session, person: Person) -> None:
    samples = list(session.scalars(select(VoiceprintSample).where(VoiceprintSample.person_id == person.id)))
    sample_paths = [path for sample in samples for path in _sample_local_paths(sample)]

    for sample in samples:
        session.delete(sample)

    for match in session.scalars(select(VoiceprintMatch).where(VoiceprintMatch.person_id == person.id)):
        session.delete(match)

    session.delete(person)
    session.commit()

    for sample_path in sample_paths:
        remove_local_file(sample_path)


def build_person_response(person: Person, samples: Optional[list[VoiceprintSample]] = None) -> PersonResponse:
    sample_records = [sample for sample in (samples or []) if not voice_is_expired(sample.created_at)]
    return PersonResponse(
        id=person.id,
        name=person.name,
        project_id=person.project_id,
        program_name=person.program_name,
        aliases=person.aliases or [],
        notes=person.notes,
        sample_count=len(sample_records),
        ready_sample_count=sum(
            1
            for item in sample_records
            if item.embedding_status == 'ready' and item.review_status == 'confirmed'
        ),
        created_at=person.created_at,
        updated_at=person.updated_at,
        samples=[build_sample_response(item) for item in sample_records],
    )


def _global_person_predicate():
    return and_(
        Person.project_id.is_(None),
        or_(Person.program_key.is_(None), Person.program_key == ''),
        or_(Person.program_name.is_(None), Person.program_name == ''),
    )


def build_sample_response(sample: VoiceprintSample) -> VoiceprintSampleResponse:
    return VoiceprintSampleResponse(
        id=sample.id,
        original_filename=sample.original_filename,
        duration_ms=sample.duration_ms,
        sample_rate_hz=sample.sample_rate_hz,
        channel_count=sample.channel_count,
        quality_score=sample.quality_score,
        confidence_score=sample.confidence_score,
        usage_types=sample.usage_types or [],
        embedding_status=sample.embedding_status,
        provider_name=sample.provider_name,
        provider_model_id=sample.provider_model_id,
        provider_message=sample.provider_message,
        clone_readiness_status=sample.clone_readiness_status,
        clone_readiness_message=sample.clone_readiness_message,
        audio_public_url=sample.audio_public_url,
        clone_audio_local_path=sample.clone_audio_local_path,
        clone_audio_public_url=sample.clone_audio_public_url,
        clone_sample_rate_hz=sample.clone_sample_rate_hz,
        clone_channel_count=sample.clone_channel_count,
        waveform_ready=bool(sample.waveform_ready),
        source_type=sample.source_type or 'manual_upload',
        review_status=sample.review_status or 'confirmed',
        source_job_id=sample.source_job_id,
        source_speaker_key=sample.source_speaker_key,
        source_start_ms=sample.source_start_ms,
        source_end_ms=sample.source_end_ms,
        duplicate_of_sample_id=sample.duplicate_of_sample_id,
        created_at=sample.created_at,
        expires_at=voice_expires_at(sample.created_at),
    )


def prepare_voiceprint_sample_assets(source_path: Path, original_name: str) -> VoiceprintSampleAssets:
    voiceprint_audio_path = normalize_voiceprint_sample(source_path, original_name)
    clone_temp_path: Path | None = None
    try:
        clone_temp_path = prepare_voice_clone_reference_audio(source_path)
        clone_audio_path = copy_local_file_to_library(
            source_path=clone_temp_path,
            target_dir=settings.voiceprint_library_dir_path,
            original_name=f'{Path(original_name).stem}-clone.wav',
        )
    except Exception:
        remove_local_file(voiceprint_audio_path)
        raise
    finally:
        remove_local_file(clone_temp_path)
    return VoiceprintSampleAssets(
        voiceprint_audio_path=voiceprint_audio_path,
        clone_audio_path=clone_audio_path,
    )


def normalize_voiceprint_sample(source_path: Path, original_name: str) -> Path:
    normalized_path = prepare_recognition_audio(source_path, media_kind='audio')
    library_path = copy_local_file_to_library(
        source_path=normalized_path,
        target_dir=settings.voiceprint_library_dir_path,
        original_name=f'{Path(original_name).stem}.wav',
    )
    try:
        normalized_path.unlink()
    except OSError:
        pass
    return library_path


def _clone_sample_rate_hz(*, has_clone_reference: bool) -> int:
    if not has_clone_reference:
        return 16000
    return max(16000, int(settings.voice_cloning_sample_target_rate_hz or 24000))


def _clone_channel_count(*, has_clone_reference: bool) -> int:
    if not has_clone_reference:
        return 1
    return 1 if int(settings.voice_cloning_sample_channel_count or 1) <= 1 else 2


def _sample_local_paths(sample: VoiceprintSample) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for value in [sample.audio_local_path, sample.clone_audio_local_path]:
        if not value:
            continue
        path = Path(value)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths
