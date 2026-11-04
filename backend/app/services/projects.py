from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete, func, inspect, or_, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.cost_event import CostEvent
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.lexicon_candidate import LexiconCandidate
from app.models.lexicon_import import LexiconImport
from app.models.lexicon_occurrence import LexiconOccurrence
from app.models.lexicon_term import LexiconTerm
from app.models.lexicon_term_alias import LexiconTermAlias
from app.models.lexicon_term_override import LexiconTermOverride
from app.models.person import Person
from app.models.project import Project
from app.models.provider_call import ProviderCall
from app.models.speaker_memory import SpeakerMemory
from app.models.subtitle_pipeline_run import SubtitlePipelineRun
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_sample import VoiceCloneSample
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
from app.models.voiceprint_match import VoiceprintMatch
from app.models.voiceprint_sample import VoiceprintSample
from app.schemas.project import ProjectCreateRequest, ProjectResponse, ProjectUpdateRequest
from app.services.jobs import (
    ACTIVE_JOB_STATUSES,
    count_subtitle_backplate_assets,
    normalize_subtitle_backplate_config,
)
from app.services.oss_storage import delete_oss_object, owned_oss_object_key
from app.services.speaker_memory import (
    build_program_key,
    ensure_project_speaker_memory,
    normalize_program_name,
    resolve_speaker_memory_project_ids,
)


DEFAULT_TENANT_ID = 'default'
VALID_PROJECT_STATUSES = {'active', 'archived'}
logger = logging.getLogger(__name__)


def normalize_project_name(value: Optional[str]) -> str:
    return normalize_program_name(value)


def build_project_name_key(value: Optional[str]) -> str:
    return build_program_key(value)


def create_project(
    session: Session,
    payload: ProjectCreateRequest,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> Project:
    name = normalize_project_name(payload.name)
    name_key = build_project_name_key(name)
    if not name or not name_key:
        raise ValueError('项目名称不能为空')
    existing = session.scalar(
        select(Project).where(Project.tenant_id == tenant_id, Project.name_key == name_key)
    )
    if existing:
        raise RuntimeError('同名项目已存在')
    project = Project(
        tenant_id=tenant_id,
        name=name,
        name_key=name_key,
        owner_name=normalize_project_name(payload.owner_name) or None,
        default_language=str(payload.default_language or 'zh').strip() or 'zh',
        default_frame_rate=payload.default_frame_rate,
        default_subtitle_profile_id=str(payload.default_subtitle_profile_id or '').strip() or None,
        default_voiceprint_scope=payload.default_voiceprint_scope,
        knowledge_domain=payload.knowledge_domain,
        legacy_program_key=name_key,
    )
    session.add(project)
    try:
        session.flush()
        _ensure_legacy_program_memory(session, project)
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise RuntimeError('同名项目已存在') from error
    session.refresh(project)
    return project


def get_project(session: Session, project_id: str, tenant_id: str = DEFAULT_TENANT_ID) -> Optional[Project]:
    return session.scalar(
        select(Project).where(Project.id == str(project_id), Project.tenant_id == tenant_id)
    )


def get_project_by_name(
    session: Session,
    name: Optional[str],
    tenant_id: str = DEFAULT_TENANT_ID,
) -> Optional[Project]:
    name_key = build_project_name_key(name)
    if not name_key:
        return None
    return session.scalar(
        select(Project).where(Project.tenant_id == tenant_id, Project.name_key == name_key)
    )


def resolve_project_scope_ids(
    session: Session,
    project_id: Optional[str],
    tenant_id: str = DEFAULT_TENANT_ID,
) -> tuple[str, ...]:
    normalized_id = str(project_id or '').strip()
    if not normalized_id:
        return tuple()
    project = get_project(session, normalized_id, tenant_id)
    if not project:
        return (normalized_id,)
    return tuple(dict.fromkeys((project.id, *sorted(_project_aliases(project)))))


def resolve_unambiguous_project_scope_ids(
    session: Session,
    project_id: Optional[str],
    tenant_id: str = DEFAULT_TENANT_ID,
) -> tuple[str, ...]:
    project = get_project(session, str(project_id or '').strip(), tenant_id)
    if not project:
        return tuple()
    projects = list(session.scalars(select(Project).where(Project.tenant_id == tenant_id)))
    alias_owners: dict[str, set[str]] = {}
    for candidate in projects:
        for alias in {candidate.id, *_project_aliases(candidate)}:
            normalized_alias = build_project_name_key(alias)
            if normalized_alias:
                alias_owners.setdefault(normalized_alias, set()).add(candidate.id)

    safe_aliases = {project.id}
    for alias in _project_aliases(project):
        normalized_alias = build_project_name_key(alias)
        if normalized_alias and alias_owners.get(normalized_alias) == {project.id}:
            safe_aliases.update((alias, normalized_alias))
    return tuple(sorted(safe_aliases))


def ensure_legacy_project(
    session: Session,
    name: Optional[str],
    tenant_id: str = DEFAULT_TENANT_ID,
) -> Project:
    normalized_name = normalize_project_name(name)
    if not normalized_name:
        raise ValueError('项目名称不能为空')
    existing = get_project_by_name(session, normalized_name, tenant_id)
    if existing:
        if existing.status == 'archived':
            existing.status = 'active'
        _ensure_legacy_program_memory(session, existing)
        session.commit()
        session.refresh(existing)
        return existing
    project = Project(
        tenant_id=tenant_id,
        name=normalized_name,
        name_key=build_project_name_key(normalized_name),
        legacy_program_key=build_project_name_key(normalized_name),
    )
    session.add(project)
    try:
        session.flush()
        _ensure_legacy_program_memory(session, project)
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = get_project_by_name(session, normalized_name, tenant_id)
        if existing:
            return existing
        raise
    session.refresh(project)
    return project


def list_projects(
    session: Session,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
    include_archived: bool = False,
) -> list[Project]:
    statement = select(Project).where(Project.tenant_id == tenant_id)
    if not include_archived:
        statement = statement.where(Project.status == 'active')
    statement = statement.order_by(Project.status.asc(), Project.updated_at.desc(), Project.name.asc())
    return list(session.scalars(statement))


def update_project(
    session: Session,
    project_id: str,
    payload: ProjectUpdateRequest,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> Project:
    project = get_project(session, project_id, tenant_id)
    if not project:
        raise LookupError('项目不存在')
    updates = payload.model_dump(exclude_unset=True)
    if 'name' in updates:
        name = normalize_project_name(updates['name'])
        if not name:
            raise ValueError('项目名称不能为空')
        if name != project.name:
            raise ValueError('Speaker 2.0 M1 暂不支持项目改名，请先完成旧资产迁移')
    if 'owner_name' in updates:
        project.owner_name = normalize_project_name(updates['owner_name']) or None
    for key in ('default_language', 'default_frame_rate', 'default_voiceprint_scope', 'knowledge_domain'):
        if key in updates:
            value = updates[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{key} 不能为空')
            value = value.strip()
            setattr(project, key, value)
    if 'default_subtitle_profile_id' in updates:
        project.default_subtitle_profile_id = str(updates['default_subtitle_profile_id'] or '').strip() or None
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise RuntimeError('同名项目已存在') from error
    session.refresh(project)
    return project


def set_project_status(
    session: Session,
    project_id: str,
    status: str,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> Project:
    if status not in VALID_PROJECT_STATUSES:
        raise ValueError('项目状态无效')
    project = get_project(session, project_id, tenant_id)
    if not project:
        raise LookupError('项目不存在')
    if status == 'archived':
        usage = _project_usage_counts(session, project)
        if usage['job_count'] or usage['asset_count']:
            raise RuntimeError('项目已有任务、词库或声音资产，当前版本只允许归档空项目')
        for memory in _project_speaker_memories(session, project):
            session.delete(memory)
    else:
        _ensure_legacy_program_memory(session, project)
    project.status = status
    session.commit()
    session.refresh(project)
    return project


def purge_project(
    session: Session,
    project_id: str,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> str:
    """Permanently remove one project's owned records and managed files."""
    project = get_project(session, project_id, tenant_id)
    if not project:
        raise LookupError('项目不存在')

    safe_scope_ids = set(resolve_unambiguous_project_scope_ids(session, project.id, tenant_id))
    safe_scope_ids.add(project.id)
    jobs = list(session.scalars(
        select(Job).where(_purge_project_job_predicate(project, safe_scope_ids))
    ))
    job_ids = {job.id for job in jobs}

    person_predicate = _project_asset_predicate(Person, project, safe_scope_ids)
    people = list(session.scalars(select(Person).where(person_predicate)))
    person_ids = {person.id for person in people}

    voiceprint_sample_predicates = []
    if person_ids:
        voiceprint_sample_predicates.append(VoiceprintSample.person_id.in_(person_ids))
    if job_ids:
        voiceprint_sample_predicates.append(VoiceprintSample.source_job_id.in_(job_ids))
    voiceprint_samples = list(session.scalars(
        select(VoiceprintSample).where(or_(*voiceprint_sample_predicates))
    )) if voiceprint_sample_predicates else []

    clone_samples = list(session.scalars(
        select(VoiceCloneSample).where(
            _project_asset_predicate(VoiceCloneSample, project, safe_scope_ids)
        )
    ))
    profile_predicates = [
        _project_asset_predicate(VoiceCloneProfile, project, safe_scope_ids),
    ]
    if person_ids:
        profile_predicates.append(VoiceCloneProfile.person_id.in_(person_ids))
    clone_profiles = list(session.scalars(
        select(VoiceCloneProfile).where(or_(*profile_predicates))
    ))
    profile_ids = {profile.id for profile in clone_profiles}

    record_predicates = [
        _project_asset_predicate(VoiceCloneSynthesisRecord, project, safe_scope_ids),
    ]
    if profile_ids:
        record_predicates.append(VoiceCloneSynthesisRecord.voice_clone_profile_id.in_(profile_ids))
    if person_ids:
        record_predicates.append(VoiceCloneSynthesisRecord.person_id.in_(person_ids))
    clone_records = list(session.scalars(
        select(VoiceCloneSynthesisRecord).where(or_(*record_predicates))
    ))

    memories = _project_speaker_memories(session, project)
    local_paths, object_keys = _collect_project_storage(
        project=project,
        jobs=jobs,
        memories=memories,
        voiceprint_samples=voiceprint_samples,
        clone_samples=clone_samples,
        clone_profiles=clone_profiles,
        clone_records=clone_records,
    )

    term_predicates = [
        (LexiconTerm.scope_type == 'project') & (LexiconTerm.scope_id.in_(safe_scope_ids)),
    ]
    if job_ids:
        term_predicates.append(
            (LexiconTerm.scope_type == 'job') & (LexiconTerm.scope_id.in_(job_ids))
        )
    term_ids = set(session.scalars(select(LexiconTerm.id).where(or_(*term_predicates))))
    import_ids = set(session.scalars(
        select(LexiconImport.id).where(LexiconImport.project_id.in_(safe_scope_ids))
    ))
    candidate_ids = set(session.scalars(
        select(LexiconCandidate.id).where(LexiconCandidate.project_id.in_(safe_scope_ids))
    ))

    occurrence_predicates = [LexiconOccurrence.project_id.in_(safe_scope_ids)]
    if job_ids:
        occurrence_predicates.append(LexiconOccurrence.job_id.in_(job_ids))
    if term_ids:
        occurrence_predicates.append(LexiconOccurrence.term_id.in_(term_ids))
    if import_ids:
        occurrence_predicates.append(LexiconOccurrence.import_id.in_(import_ids))
    if candidate_ids:
        occurrence_predicates.append(LexiconOccurrence.candidate_id.in_(candidate_ids))

    _delete_where(session, LexiconOccurrence, or_(*occurrence_predicates))
    if term_ids:
        _delete_where(session, LexiconTermAlias, LexiconTermAlias.term_id.in_(term_ids))
    override_predicates = [
        (LexiconTermOverride.scope_type == 'project')
        & (LexiconTermOverride.scope_id.in_(safe_scope_ids)),
    ]
    if job_ids:
        override_predicates.append(
            (LexiconTermOverride.scope_type == 'job')
            & (LexiconTermOverride.scope_id.in_(job_ids))
        )
    if term_ids:
        override_predicates.append(LexiconTermOverride.term_id.in_(term_ids))
    _delete_where(session, LexiconTermOverride, or_(*override_predicates))
    _delete_where(session, LexiconCandidate, LexiconCandidate.project_id.in_(safe_scope_ids))
    _delete_where(session, LexiconImport, LexiconImport.project_id.in_(safe_scope_ids))
    _delete_where(session, LexiconTerm, or_(*term_predicates))

    if job_ids or person_ids:
        match_predicates = []
        if job_ids:
            match_predicates.append(VoiceprintMatch.job_id.in_(job_ids))
        if person_ids:
            match_predicates.append(VoiceprintMatch.person_id.in_(person_ids))
        _delete_where(session, VoiceprintMatch, or_(*match_predicates))
    if job_ids:
        _delete_where(session, JobEvent, JobEvent.job_id.in_(job_ids))
    _delete_where(session, CostEvent, CostEvent.project_id == project.id)
    _delete_where(session, ProviderCall, ProviderCall.project_id == project.id)
    _delete_where(session, SubtitlePipelineRun, SubtitlePipelineRun.project_id == project.id)

    if clone_records:
        _delete_where(
            session,
            VoiceCloneSynthesisRecord,
            VoiceCloneSynthesisRecord.id.in_({record.id for record in clone_records}),
        )
    if clone_profiles:
        _delete_where(
            session,
            VoiceCloneProfile,
            VoiceCloneProfile.id.in_(profile_ids),
        )
    if voiceprint_samples:
        _delete_where(
            session,
            VoiceprintSample,
            VoiceprintSample.id.in_({sample.id for sample in voiceprint_samples}),
        )
    if clone_samples:
        _delete_where(
            session,
            VoiceCloneSample,
            VoiceCloneSample.id.in_({sample.id for sample in clone_samples}),
        )
    for memory in memories:
        session.delete(memory)
    if people:
        _delete_where(session, Person, Person.id.in_(person_ids))
    if jobs:
        _delete_where(session, Job, Job.id.in_(job_ids))

    deleted_name = project.name
    session.delete(project)
    session.commit()

    _delete_project_storage(project.id, local_paths, object_keys)
    return deleted_name


def _purge_project_job_predicate(project: Project, safe_scope_ids: set[str]):
    predicates = [Job.project_id == project.id]
    if safe_scope_ids:
        predicates.append(
            Job.project_id.is_(None)
            & or_(Job.program_key.in_(safe_scope_ids), Job.program_name.in_(safe_scope_ids))
        )
    return or_(*predicates)


def _delete_where(session: Session, model, predicate) -> None:
    session.execute(
        delete(model).where(predicate).execution_options(synchronize_session=False)
    )


def _collect_project_storage(
    *,
    project: Project,
    jobs: list[Job],
    memories: list[SpeakerMemory],
    voiceprint_samples: list[VoiceprintSample],
    clone_samples: list[VoiceCloneSample],
    clone_profiles: list[VoiceCloneProfile],
    clone_records: list[VoiceCloneSynthesisRecord],
) -> tuple[set[str], set[str]]:
    local_paths: set[str] = set()
    object_keys: set[str] = set()

    def add_local(value: Optional[str]) -> None:
        normalized = str(value or '').strip()
        if normalized:
            local_paths.add(normalized)

    def add_url(value: Optional[str]) -> None:
        object_key = owned_oss_object_key(value)
        if object_key:
            object_keys.add(object_key)

    _collect_asset_storage(project.subtitle_backplate_assets, local_paths, object_keys)
    for memory in memories:
        _collect_asset_storage(memory.lower_third_assets, local_paths, object_keys)
    for job in jobs:
        add_local(job.source_local_path)
        add_local(job.video_source_local_path)
        if job.source_local_path:
            source_path = Path(job.source_local_path)
            add_local(str(settings.audio_dir_path / f'job-{job.id}-{source_path.stem}-preview.wav'))
        _collect_asset_storage(job.lower_third_assets, local_paths, object_keys)
        _collect_asset_storage(job.subtitle_backplate_asset, local_paths, object_keys)
        for value in (
            job.source_url,
            job.video_source_url,
            job.transcription_url,
            job.render_output_url,
        ):
            add_url(value)
    for sample in voiceprint_samples:
        add_local(sample.audio_local_path)
        add_local(sample.clone_audio_local_path)
        add_url(sample.audio_public_url)
        add_url(sample.clone_audio_public_url)
    for sample in clone_samples:
        add_local(sample.audio_local_path)
        add_url(sample.audio_public_url)
    for profile in clone_profiles:
        add_local(profile.source_audio_local_path)
        add_local(profile.preview_audio_local_path)
    for record in clone_records:
        add_local(record.output_audio_local_path)
        add_url(record.output_audio_public_url)
    return local_paths, object_keys


def _collect_asset_storage(payload: Any, local_paths: set[str], object_keys: set[str]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == 'local_path' and str(value or '').strip():
                local_paths.add(str(value).strip())
            elif key == 'object_key' and str(value or '').strip():
                object_keys.add(str(value).strip())
            elif key.endswith('_url'):
                object_key = owned_oss_object_key(value)
                if object_key:
                    object_keys.add(object_key)
            _collect_asset_storage(value, local_paths, object_keys)
    elif isinstance(payload, list):
        for item in payload:
            _collect_asset_storage(item, local_paths, object_keys)

def _delete_project_storage(project_id: str, local_paths: set[str], object_keys: set[str]) -> None:
    roots = tuple(
        path.resolve()
        for path in (
            settings.upload_dir_path,
            settings.audio_dir_path,
            settings.render_dir_path,
            settings.media_library_dir_path,
            settings.lower_third_library_dir_path,
            settings.voiceprint_library_dir_path,
            settings.voice_clone_library_dir_path,
        )
    )
    for path_value in sorted(local_paths):
        target = Path(path_value)
        try:
            resolved_target = target.resolve()
            if not any(resolved_target == root or resolved_target.is_relative_to(root) for root in roots):
                logger.warning('skip unmanaged project file during purge: %s', target)
                continue
            if resolved_target.exists() and resolved_target.is_file():
                resolved_target.unlink()
        except OSError:
            logger.exception('failed to remove project file during purge: %s', target)

    project_asset_dir = (
        settings.lower_third_library_dir_path / 'projects' / str(project_id) / 'subtitle-backplates'
    )
    try:
        if project_asset_dir.exists() and project_asset_dir.is_dir() and not project_asset_dir.is_symlink():
            shutil.rmtree(project_asset_dir)
        project_root = project_asset_dir.parent
        if project_root.exists() and not any(project_root.iterdir()):
            project_root.rmdir()
    except OSError:
        logger.exception('failed to remove project asset directory during purge: %s', project_asset_dir)

    for object_key in sorted(object_keys):
        try:
            delete_oss_object(object_key)
        except Exception:
            logger.exception('failed to remove project OSS object during purge: %s', object_key)


def build_project_response(session: Session, project: Project) -> ProjectResponse:
    project_job_predicate = _project_job_predicate(project)
    usage = _project_usage_counts(session, project)
    active_job_count = session.scalar(
        select(func.count(Job.id)).where(project_job_predicate, Job.status.in_(ACTIVE_JOB_STATUSES))
    ) or 0
    return ProjectResponse(
        id=project.id,
        tenant_id=project.tenant_id,
        name=project.name,
        status=project.status,
        owner_name=project.owner_name,
        default_language=project.default_language,
        default_frame_rate=project.default_frame_rate,
        default_subtitle_profile_id=project.default_subtitle_profile_id,
        default_voiceprint_scope=project.default_voiceprint_scope,
        subtitle_backplate_assets=normalize_subtitle_backplate_config(
            project.subtitle_backplate_assets
        ),
        legacy_program_key=project.legacy_program_key,
        job_count=usage['job_count'],
        active_job_count=int(active_job_count),
        person_count=usage['person_count'],
        sample_count=usage['sample_count'],
        asset_count=usage['asset_count'],
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _project_job_predicate(project: Project):
    aliases = _project_aliases(project)
    predicates = [Job.project_id == project.id]
    if aliases:
        predicates.append((Job.project_id.is_(None)) & (Job.program_key.in_(aliases)))
        predicates.append((Job.project_id.is_(None)) & (Job.program_name.in_(aliases)))
    return or_(*predicates)


def _project_aliases(project: Project) -> set[str]:
    return {
        str(value or '').strip()
        for value in (project.legacy_program_key, project.name_key, project.name)
        if str(value or '').strip()
    }


def _legacy_asset_predicate(model, aliases: set[str]):
    return or_(model.program_key.in_(aliases), model.program_name.in_(aliases))


def _project_asset_predicate(model, project: Project, aliases: set[str]):
    predicates = [model.project_id == project.id]
    if aliases:
        predicates.append(
            model.project_id.is_(None) & _legacy_asset_predicate(model, aliases)
        )
    return or_(*predicates)


def _count(session: Session, model, *predicates) -> int:
    return int(session.scalar(select(func.count(model.id)).where(*predicates)) or 0)


def _project_usage_counts(session: Session, project: Project) -> dict[str, int]:
    aliases = _project_aliases(project)
    safe_aliases = set(resolve_unambiguous_project_scope_ids(session, project.id))
    scope_ids = {project.id, *aliases}
    job_count = int(session.scalar(select(func.count(Job.id)).where(_project_job_predicate(project))) or 0)
    person_predicate = _project_asset_predicate(Person, project, safe_aliases)
    person_count = _count(session, Person, person_predicate)
    voiceprint_sample_count = int(session.scalar(
        select(func.count(VoiceprintSample.id))
        .join(Person, VoiceprintSample.person_id == Person.id)
        .where(person_predicate)
    ) or 0)
    clone_sample_count = _count(
        session,
        VoiceCloneSample,
        _project_asset_predicate(VoiceCloneSample, project, safe_aliases),
    )
    meaningful_memory_count = sum(
        1
        for memory in _project_speaker_memories(session, project)
        if list(memory.speaker_names or []) or list(memory.lower_third_assets or [])
    )
    other_asset_count = sum((
        count_subtitle_backplate_assets(project.subtitle_backplate_assets),
        meaningful_memory_count,
        _count(
            session,
            VoiceCloneProfile,
            _project_asset_predicate(VoiceCloneProfile, project, safe_aliases),
        ),
        _count(session, LexiconTerm, LexiconTerm.scope_type == 'project', LexiconTerm.scope_id.in_(scope_ids)),
        _count(
            session,
            LexiconTermOverride,
            LexiconTermOverride.scope_type == 'project',
            LexiconTermOverride.scope_id.in_(scope_ids),
        ),
        _count(session, LexiconImport, LexiconImport.project_id.in_(scope_ids)),
        _count(session, LexiconCandidate, LexiconCandidate.project_id.in_(scope_ids)),
        _count(session, LexiconOccurrence, LexiconOccurrence.project_id.in_(scope_ids)),
    ))
    sample_count = voiceprint_sample_count + clone_sample_count
    return {
        'job_count': job_count,
        'person_count': person_count,
        'sample_count': sample_count,
        'asset_count': person_count + sample_count + other_asset_count,
    }


def _project_speaker_memories(session: Session, project: Project) -> list[SpeakerMemory]:
    aliases = set(resolve_unambiguous_project_scope_ids(session, project.id))
    predicates = [SpeakerMemory.project_id == project.id]
    if aliases:
        predicates.append(
            SpeakerMemory.project_id.is_(None) & _legacy_asset_predicate(SpeakerMemory, aliases)
        )
    candidates = list(session.scalars(
        select(SpeakerMemory).where(or_(*predicates))
    ))
    return [
        memory
        for memory in candidates
        if memory.project_id == project.id
        or resolve_speaker_memory_project_ids(session, memory) == {project.id}
    ]


def _ensure_legacy_program_memory(session: Session, project: Project) -> None:
    program_key = project.legacy_program_key or project.name_key
    ensure_project_speaker_memory(
        session,
        project_id=project.id,
        program_key=program_key,
        program_name=project.name,
    )


def migrate_legacy_projects(session: Session, *, apply: bool = False) -> dict[str, Any]:
    candidates = _collect_legacy_candidates(session)
    existing_projects = list(session.scalars(select(Project).where(Project.tenant_id == DEFAULT_TENANT_ID)))
    by_key = {
        build_project_name_key(project.legacy_program_key or project.name_key): project
        for project in existing_projects
    }
    created_count = 0
    mapped_job_count = 0
    planned_projects: list[dict[str, Any]] = []

    for key, candidate in sorted(candidates.items(), key=lambda item: item[1]['name']):
        project = by_key.get(key)
        if not project:
            planned_projects.append({'name': candidate['name'], 'legacy_program_key': key})
            if apply:
                project = Project(
                    tenant_id=DEFAULT_TENANT_ID,
                    name=candidate['name'],
                    name_key=build_project_name_key(candidate['name']),
                    legacy_program_key=key,
                )
                session.add(project)
                session.flush()
                by_key[key] = project
                created_count += 1
        elif apply and not project.legacy_program_key:
            project.legacy_program_key = key

        if not apply or not project:
            continue
        job_ids = list(candidate['job_ids'])
        if job_ids:
            result = session.execute(
                update(Job)
                .where(Job.id.in_(job_ids), Job.project_id.is_(None))
                .values(project_id=project.id)
                .execution_options(synchronize_session=False)
            )
            mapped_job_count += int(result.rowcount or 0)

    unresolved_jobs = list(
        session.scalars(
            select(Job).where(
                Job.project_id.is_(None),
                func.trim(func.coalesce(Job.program_key, '')) == '',
                func.trim(func.coalesce(Job.program_name, '')) == '',
            )
        )
    )
    if apply:
        session.commit()
    return {
        'mode': 'apply' if apply else 'dry-run',
        'candidate_project_count': len(candidates),
        'existing_project_count': len(existing_projects),
        'planned_projects': planned_projects,
        'created_project_count': created_count,
        'mapped_job_count': mapped_job_count,
        'unresolved_job_count': len(unresolved_jobs),
        'legacy_fields_preserved': True,
    }


def preview_legacy_project_migration(engine: Engine) -> dict[str, Any]:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    candidates: dict[str, str] = {}

    def collect_from_table(table_name: str) -> None:
        if table_name not in table_names:
            return
        columns = {column['name'] for column in inspector.get_columns(table_name)}
        available = [column for column in ('program_name', 'program_key') if column in columns]
        if not available:
            return
        selected = ', '.join(available)
        with engine.connect() as connection:
            rows = connection.execute(text(f'SELECT {selected} FROM {table_name}')).mappings()
            for row in rows:
                name = row.get('program_name') or row.get('program_key')
                key = build_project_name_key(row.get('program_key') or name)
                normalized_name = normalize_project_name(name)
                if key and normalized_name:
                    candidates.setdefault(key, normalized_name)

    for table_name in ('jobs', 'people', 'speaker_memories', 'voice_clone_profiles', 'voice_clone_samples'):
        collect_from_table(table_name)

    existing_keys: set[str] = set()
    existing_project_count = 0
    if 'projects' in table_names:
        with engine.connect() as connection:
            rows = connection.execute(
                text('SELECT name_key, legacy_program_key FROM projects WHERE tenant_id = :tenant_id'),
                {'tenant_id': DEFAULT_TENANT_ID},
            ).mappings()
            for row in rows:
                existing_project_count += 1
                key = build_project_name_key(row.get('legacy_program_key') or row.get('name_key'))
                if key:
                    existing_keys.add(key)

    unresolved_job_count = 0
    if 'jobs' in table_names:
        columns = {column['name'] for column in inspector.get_columns('jobs')}
        if {'program_name', 'program_key'}.issubset(columns):
            with engine.connect() as connection:
                unresolved_job_count = int(connection.execute(text(
                    "SELECT COUNT(*) FROM jobs "
                    "WHERE TRIM(COALESCE(program_name, '')) = '' "
                    "AND TRIM(COALESCE(program_key, '')) = ''"
                )).scalar_one())

    planned_projects = [
        {'name': name, 'legacy_program_key': key}
        for key, name in sorted(candidates.items(), key=lambda item: item[1])
        if key not in existing_keys
    ]
    return {
        'mode': 'dry-run',
        'candidate_project_count': len(candidates),
        'existing_project_count': existing_project_count,
        'planned_projects': planned_projects,
        'created_project_count': 0,
        'mapped_job_count': 0,
        'unresolved_job_count': unresolved_job_count,
        'legacy_fields_preserved': True,
        'schema_mutated': False,
    }


def _collect_legacy_candidates(session: Session) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}

    def add(name: Optional[str], key: Optional[str] = None, job_id: Optional[str] = None) -> None:
        normalized_name = normalize_project_name(name or key)
        normalized_key = build_project_name_key(key or normalized_name)
        if not normalized_name or not normalized_key:
            return
        entry = candidates.setdefault(normalized_key, {'name': normalized_name, 'job_ids': []})
        if job_id is not None:
            entry['job_ids'].append(str(job_id))

    for job_id, program_name, program_key in session.execute(
        select(Job.id, Job.program_name, Job.program_key)
    ):
        add(program_name, program_key, job_id)
    for program_name, program_key in session.execute(
        select(Person.program_name, Person.program_key)
    ):
        add(program_name, program_key)
    for program_name, program_key in session.execute(
        select(SpeakerMemory.program_name, SpeakerMemory.program_key)
    ):
        add(program_name, program_key)
    for program_name, program_key in session.execute(
        select(VoiceCloneProfile.program_name, VoiceCloneProfile.program_key)
    ):
        add(program_name, program_key)
    for program_name, program_key in session.execute(
        select(VoiceCloneSample.program_name, VoiceCloneSample.program_key)
    ):
        add(program_name, program_key)
    return candidates
