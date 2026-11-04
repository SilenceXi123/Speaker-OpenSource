from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Optional

from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.models.person import Person
from app.models.project import Project
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_identity import VoiceCloneIdentity
from app.models.voice_clone_sample import VoiceCloneSample
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
from app.models.voiceprint_sample import VoiceprintSample
from app.schemas.voice_cloning import (
    VoiceCloneLibraryGroupResponse,
    VoiceCloneLibraryPageResponse,
    VoiceCloneLibraryProfileResponse,
    VoiceCloneSamplePageItemResponse,
    VoiceCloneSamplePageResponse,
)
from app.services.voice_clone_identity import normalize_voice_clone_name, current_voice_clone_sample_clause
from app.services import voice_retention
from app.services.voice_cloning import (
    _build_model_options,
    build_voice_clone_sample_response,
    get_active_voice_clone_project,
    get_voice_clone_profile_availability,
    current_voice_clone_profile_clause,
    voice_clone_asset_expires_at,
)


DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100


def list_voice_clone_library(
    session: Session,
    project_id: str,
    query: Optional[str] = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: Optional[str] = None,
) -> VoiceCloneLibraryPageResponse:
    get_active_voice_clone_project(session, project_id)
    _register_sqlite_library_functions(session)
    normalized_query = normalize_voice_clone_name(query or '')
    offset = _parse_cursor(cursor)
    page_size = _clamp_limit(limit)
    ranked = _ranked_library_profiles(project_id, normalized_query)
    group_filter = ranked.c.row_number == 1
    if normalized_query:
        group_filter = and_(group_filter, ranked.c.group_query_match == 1)
    total, available = session.execute(
        select(func.count(), func.coalesce(func.sum(ranked.c.selectable), 0))
        .select_from(ranked).where(group_filter)
    ).one()
    page_rows = list(session.execute(
        select(
            ranked.c.profile_id,
            ranked.c.group_key,
            ranked.c.version_count,
            ranked.c.scope_rank,
        )
        .where(group_filter)
        .order_by(
            ranked.c.scope_rank.desc(),
            ranked.c.last_used_at.desc(),
            ranked.c.updated_at.desc(),
            ranked.c.created_at.desc(),
            ranked.c.profile_id.desc(),
            ranked.c.group_key.desc(),
        )
        .offset(offset)
        .limit(page_size + 1)
    ))
    has_more = len(page_rows) > page_size
    page_rows = page_rows[:page_size]
    profile_ids = [row.profile_id for row in page_rows if row.profile_id]
    profiles_by_id = {
        profile.id: profile
        for profile in session.scalars(
            select(VoiceCloneProfile).where(VoiceCloneProfile.id.in_(profile_ids))
        )
    } if profile_ids else {}
    profiles = [profiles_by_id[profile_id] for profile_id in profile_ids]
    clone_samples, voiceprint_samples, people, projects, identities = (
        _source_maps_for_profiles(session, profiles)
    )
    missing_identity_ids = {row.group_key for row in page_rows if row.profile_id is None}
    if missing_identity_ids:
        identities.update({identity.id: identity for identity in session.scalars(
            select(VoiceCloneIdentity).where(VoiceCloneIdentity.id.in_(missing_identity_ids))
        )})
    used_profile_ids = _used_profile_ids(session, project_id, set(profile_ids))
    page_groups = [
        _build_group(
            session,
            row.group_key,
            [profiles_by_id[row.profile_id]] if row.profile_id else [],
            clone_samples,
            voiceprint_samples,
            people,
            projects,
            identities,
            project_id,
            used_profile_ids,
            version_count=row.version_count,
        )
        for row in page_rows
    ]
    reusable_sample_count = session.scalar(
        select(func.count()).select_from(VoiceCloneSample).where(
            current_voice_clone_sample_clause(),
            VoiceCloneSample.clone_readiness_status != 'rejected'
        )
    ) or 0
    return VoiceCloneLibraryPageResponse(
        groups=page_groups,
        next_cursor=str(offset + page_size) if has_more else None,
        total=total,
        available_group_count=available,
        exception_group_count=total - available,
        reusable_sample_count=reusable_sample_count,
    )


def list_voice_clone_sample_page(
    session: Session,
    project_id: str,
    query: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: Optional[str] = None,
) -> VoiceCloneSamplePageResponse:
    get_active_voice_clone_project(session, project_id)
    normalized_query = normalize_voice_clone_name(query or '')
    normalized_status = str(status or '').strip().lower()
    offset = _parse_cursor(cursor)
    page_size = _clamp_limit(limit)
    filters = [current_voice_clone_sample_clause()]
    if normalized_query:
        filters.append(or_(
            func.instr(
                func.voice_clone_normalize_name(VoiceCloneSample.display_name),
                normalized_query,
            ) > 0,
            func.instr(
                func.voice_clone_normalize_name(VoiceCloneSample.original_filename),
                normalized_query,
            ) > 0,
        ))
    if normalized_status != 'all':
        filters.append(
            VoiceCloneSample.clone_readiness_status == normalized_status
            if normalized_status
            else VoiceCloneSample.clone_readiness_status != 'rejected'
        )
    _register_sqlite_library_functions(session)
    filtered = select(VoiceCloneSample)
    count_statement = select(func.count()).select_from(VoiceCloneSample)
    if filters:
        filtered = filtered.where(*filters)
        count_statement = count_statement.where(*filters)
    total = session.scalar(count_statement) or 0
    profile_count = (
        select(func.count())
        .select_from(VoiceCloneProfile)
        .where(VoiceCloneProfile.source_clone_sample_id == VoiceCloneSample.id, current_voice_clone_profile_clause())
        .correlate(VoiceCloneSample)
        .scalar_subquery()
    )
    page_rows = list(session.execute(
        filtered
        .add_columns(profile_count.label('profile_count'))
        .order_by(VoiceCloneSample.created_at.desc(), VoiceCloneSample.id.desc())
        .offset(offset)
        .limit(page_size + 1)
    ))
    has_more = len(page_rows) > page_size
    page_rows = page_rows[:page_size]
    return VoiceCloneSamplePageResponse(
        items=[
            VoiceCloneSamplePageItemResponse(
                **build_voice_clone_sample_response(sample).model_dump(),
                in_use=profile_count > 0,
                profile_count=profile_count,
            )
            for sample, profile_count in page_rows
        ],
        next_cursor=str(offset + page_size) if has_more else None,
        total=total,
    )


def list_voice_clone_group_profiles(
    session: Session,
    project_id: str,
    group_key: str,
) -> list[VoiceCloneLibraryProfileResponse]:
    get_active_voice_clone_project(session, project_id)
    _register_sqlite_library_functions(session)
    group_filter = _group_profile_filter(group_key)
    profiles = list(session.scalars(
        select(VoiceCloneProfile).where(group_filter, current_voice_clone_profile_clause())
    ))
    clone_samples, voiceprint_samples, people, projects, identities = (
        _source_maps_for_profiles(session, profiles)
    )
    used_profile_ids = _used_profile_ids(
        session,
        project_id,
        {profile.id for profile in profiles},
    )
    profiles.sort(
        key=lambda profile: (get_voice_clone_profile_availability(session, profile).selectable,
                             profile.status == 'creating',
                             *_profile_rank(profile, project_id, used_profile_ids)),
        reverse=True,
    )
    return [
        _build_profile_summary(
            session,
            profile,
            clone_samples,
            voiceprint_samples,
            people,
            projects,
            identities,
            project_id,
            used_profile_ids,
        )
        for profile in profiles
    ]



def _group_profile_filter(group_key: str):
    if group_key.startswith('legacy:'):
        return and_(
            func.trim(func.coalesce(VoiceCloneProfile.identity_id, '')) == '',
            func.voice_clone_normalize_name(VoiceCloneProfile.voice_name)
            == group_key.removeprefix('legacy:'),
        )
    return VoiceCloneProfile.identity_id == group_key


def list_voice_clone_group_samples(
    session: Session, group_key: str, project_id: Optional[str] = None,
) -> list[VoiceCloneSamplePageItemResponse]:
    if project_id is not None:
        get_active_voice_clone_project(session, project_id)
    _register_sqlite_library_functions(session)
    group_filter = (
        VoiceCloneSample.id.in_(select(VoiceCloneProfile.source_clone_sample_id).where(
            _group_profile_filter(group_key), current_voice_clone_profile_clause()
        )) if group_key.startswith('legacy:') else VoiceCloneSample.identity_id == group_key
    )
    profile_count = (select(func.count()).select_from(VoiceCloneProfile).where(
        VoiceCloneProfile.source_clone_sample_id == VoiceCloneSample.id, current_voice_clone_profile_clause()
    ).correlate(VoiceCloneSample).scalar_subquery())
    return [VoiceCloneSamplePageItemResponse(
        **build_voice_clone_sample_response(sample).model_dump(),
        in_use=count > 0, profile_count=count,
    ) for sample, count in session.execute(
        select(VoiceCloneSample, profile_count).where(group_filter, current_voice_clone_sample_clause())
        .order_by(VoiceCloneSample.created_at.desc(), VoiceCloneSample.id.desc())
    )]


def list_creating_voice_clone_profiles(
    session: Session,
    project_id: str,
) -> list[VoiceCloneLibraryProfileResponse]:
    get_active_voice_clone_project(session, project_id)
    profiles = list(session.scalars(
        select(VoiceCloneProfile).where(
            VoiceCloneProfile.project_id == project_id,
            VoiceCloneProfile.status == 'creating',
            current_voice_clone_profile_clause(),
            VoiceCloneProfile.updated_at >= datetime.utcnow() - timedelta(hours=1),
        )
    ))
    clone_samples, voiceprint_samples, people, projects, identities = (
        _source_maps_for_profiles(session, profiles)
    )
    used_profile_ids = _used_profile_ids(
        session,
        project_id,
        {profile.id for profile in profiles},
    )
    profiles.sort(
        key=lambda profile: _profile_rank(profile, project_id, used_profile_ids),
        reverse=True,
    )
    return [
        _build_profile_summary(
            session,
            profile,
            clone_samples,
            voiceprint_samples,
            people,
            projects,
            identities,
            project_id,
            used_profile_ids,
        )
        for profile in profiles
    ]


def _ranked_library_profiles(project_id: str, normalized_query: str):
    clone_sample = aliased(VoiceCloneSample)
    voiceprint_sample = aliased(VoiceprintSample)
    person = aliased(Person)
    source_project = aliased(Project)
    identity = aliased(VoiceCloneIdentity)
    used_by_project = exists(
        select(1).where(
            VoiceCloneSynthesisRecord.project_id == project_id,
            VoiceCloneSynthesisRecord.status == 'ready',
            VoiceCloneSynthesisRecord.voice_clone_profile_id
            == VoiceCloneProfile.id,
        )
    )
    scope_rank = case(
        (VoiceCloneProfile.project_id == project_id, 2),
        (used_by_project, 1),
        else_=0,
    )
    normalized_provider = case(
        (
            func.lower(func.trim(func.coalesce(VoiceCloneProfile.provider, ''))).in_(
                ('qwen3', 'qwen3-tts', 'qwen-tts')
            ),
            'qwen',
        ),
        (
            func.trim(func.coalesce(VoiceCloneProfile.provider, '')) == '',
            'qwen',
        ),
        else_=func.lower(func.trim(VoiceCloneProfile.provider)),
    )
    supported_provider = or_(*(
        and_(normalized_provider == option.provider,
             func.trim(func.coalesce(VoiceCloneProfile.target_model, '')) == option.model)
        for option in _build_model_options()
    ))
    clone_path = func.trim(func.coalesce(clone_sample.audio_local_path, ''))
    clone_available = and_(
        func.lower(func.coalesce(clone_sample.clone_readiness_status, '')) == 'ready',
        func.voice_clone_nonempty_file_size(clone_path) > 0,
        func.voice_clone_valid_sha256(clone_sample.content_sha256) == 1,
        clone_sample.file_size_bytes > 0,
        clone_sample.file_size_bytes
        == func.voice_clone_nonempty_file_size(clone_path),
        clone_sample.duration_ms > 0,
        clone_sample.sample_rate_hz > 0,
        clone_sample.channel_count > 0,
    )
    voiceprint_clone_path = func.trim(
        func.coalesce(voiceprint_sample.clone_audio_local_path, '')
    )
    voiceprint_path = case(
        (voiceprint_clone_path != '', voiceprint_clone_path),
        else_=func.trim(func.coalesce(voiceprint_sample.audio_local_path, '')),
    )
    voiceprint_sample_rate = case(
        (voiceprint_clone_path != '', voiceprint_sample.clone_sample_rate_hz),
        else_=voiceprint_sample.sample_rate_hz,
    )
    voiceprint_channel_count = case(
        (voiceprint_clone_path != '', voiceprint_sample.clone_channel_count),
        else_=voiceprint_sample.channel_count,
    )
    voiceprint_available = and_(
        func.lower(func.coalesce(voiceprint_sample.clone_readiness_status, ''))
        == 'ready',
        func.voice_clone_nonempty_file_size(voiceprint_path) > 0,
        voiceprint_sample.duration_ms > 0,
        voiceprint_sample_rate > 0,
        voiceprint_channel_count > 0,
    )
    has_clone_source_id = (
        func.trim(func.coalesce(VoiceCloneProfile.source_clone_sample_id, '')) != ''
    )
    source_available = or_(
        and_(has_clone_source_id, clone_available),
        and_(
            ~has_clone_source_id,
            func.trim(func.coalesce(VoiceCloneProfile.source_sample_id, '')) != '',
            voiceprint_available,
        ),
    )
    selectable = and_(
        VoiceCloneProfile.status == 'ready',
        func.trim(func.coalesce(VoiceCloneProfile.voice_id, '')) != '',
        supported_provider,
        source_available,
    )
    group_key = case(
        (
            func.trim(func.coalesce(VoiceCloneProfile.identity_id, '')) != '',
            VoiceCloneProfile.identity_id,
        ),
        else_=literal('legacy:')
        + func.voice_clone_normalize_name(VoiceCloneProfile.voice_name),
    )
    source_display_name = case(
        (clone_sample.id.is_not(None), clone_sample.display_name),
        else_=person.name,
    )
    source_original_filename = case(
        (clone_sample.id.is_not(None), clone_sample.original_filename),
        else_=voiceprint_sample.original_filename,
    )
    source_project_id = case(
        (clone_sample.id.is_not(None), clone_sample.project_id),
        else_=person.project_id,
    )
    query_match = or_(
        func.instr(
            func.voice_clone_normalize_name(VoiceCloneProfile.voice_name),
            normalized_query,
        ) > 0,
        func.instr(
            func.voice_clone_normalize_name(source_display_name),
            normalized_query,
        ) > 0,
        func.instr(
            func.voice_clone_normalize_name(source_original_filename),
            normalized_query,
        ) > 0,
        func.instr(
            func.voice_clone_normalize_name(source_project.name),
            normalized_query,
        ) > 0,
        func.instr(
            func.voice_clone_normalize_name(identity.display_name),
            normalized_query,
        ) > 0,
        func.voice_clone_aliases_match(identity.aliases, normalized_query) == 1,
        _identity_samples_match_query(identity.id, normalized_query),
    )
    profile_rows = (
        select(
            VoiceCloneProfile.id.label('profile_id'), group_key.label('group_key'),
            scope_rank.label('scope_rank'),
            VoiceCloneProfile.last_used_at.label('last_used_at'),
            VoiceCloneProfile.updated_at.label('updated_at'),
            VoiceCloneProfile.created_at.label('created_at'),
            case((selectable, 1), else_=0).label('selectable'),
            case((VoiceCloneProfile.status == 'creating', 1), else_=0).label('creating'),
            case((query_match, 1), else_=0).label('query_match'),
        )
        .select_from(VoiceCloneProfile)
        .outerjoin(clone_sample, clone_sample.id == VoiceCloneProfile.source_clone_sample_id)
        .outerjoin(voiceprint_sample, voiceprint_sample.id == VoiceCloneProfile.source_sample_id)
        .outerjoin(person, person.id == voiceprint_sample.person_id)
        .outerjoin(source_project, source_project.id == source_project_id)
        .outerjoin(identity, identity.id == VoiceCloneProfile.identity_id)
        .where(current_voice_clone_profile_clause())
    )
    # Identities without profiles still need their existing recordings managed.
    identity_samples = select(1).select_from(VoiceCloneSample).where(
        VoiceCloneSample.identity_id == VoiceCloneIdentity.id, current_voice_clone_sample_clause()
    )
    identity_query_match = or_(
        func.instr(func.voice_clone_normalize_name(VoiceCloneIdentity.display_name), normalized_query) > 0,
        func.voice_clone_aliases_match(VoiceCloneIdentity.aliases, normalized_query) == 1,
        _identity_samples_match_query(VoiceCloneIdentity.id, normalized_query),
    )
    identity_rows = select(
        literal(None).label('profile_id'), VoiceCloneIdentity.id.label('group_key'),
        case((exists(identity_samples.where(VoiceCloneSample.project_id == project_id)), 2), else_=0).label('scope_rank'),
        literal(None).label('last_used_at'), VoiceCloneIdentity.updated_at.label('updated_at'),
        VoiceCloneIdentity.created_at.label('created_at'), literal(0).label('selectable'),
        literal(0).label('creating'), case((identity_query_match, 1), else_=0).label('query_match'),
    ).where(VoiceCloneIdentity.created_at > voice_retention.voice_retention_cutoff(),
            ~exists(select(1).where(VoiceCloneProfile.identity_id == VoiceCloneIdentity.id, current_voice_clone_profile_clause())))
    candidates = profile_rows.union_all(identity_rows).subquery()
    columns = candidates.c
    return select(
        *candidates.c,
        func.count(columns.profile_id).over(partition_by=columns.group_key).label('version_count'),
        func.row_number().over(partition_by=columns.group_key, order_by=(
            columns.selectable.desc(), columns.creating.desc(), columns.scope_rank.desc(),
            columns.last_used_at.desc(), columns.updated_at.desc(), columns.created_at.desc(),
            columns.profile_id.desc(),
        )).label('row_number'),
        func.max(columns.query_match).over(partition_by=columns.group_key).label('group_query_match'),
    ).subquery()


def _identity_samples_match_query(identity_id, normalized_query: str):
    return exists(
        select(1).select_from(VoiceCloneSample)
        .outerjoin(Project, Project.id == VoiceCloneSample.project_id)
        .where(
            VoiceCloneSample.identity_id == identity_id,
            current_voice_clone_sample_clause(),
            or_(
                func.instr(func.voice_clone_normalize_name(VoiceCloneSample.display_name), normalized_query) > 0,
                func.instr(func.voice_clone_normalize_name(VoiceCloneSample.original_filename), normalized_query) > 0,
                func.instr(func.voice_clone_normalize_name(Project.name), normalized_query) > 0,
            ),
        )
    )


def _register_sqlite_library_functions(session: Session) -> None:
    if session.bind is None or session.bind.dialect.name != 'sqlite':
        return
    driver_connection = session.connection().connection.driver_connection

    def nonempty_file_size(value: object) -> int:
        normalized_path = str(value or '').strip()
        if not normalized_path:
            return 0
        try:
            path = Path(normalized_path)
            return path.stat().st_size if path.is_file() else 0
        except OSError:
            return 0

    def valid_sha256(value: object) -> int:
        fingerprint = str(value or '').strip().lower()
        return int(
            len(fingerprint) == 64
            and all(character in '0123456789abcdef' for character in fingerprint)
        )

    def aliases_match(value: object, query: object) -> int:
        try:
            aliases = json.loads(str(value or '[]'))
        except (TypeError, ValueError, json.JSONDecodeError):
            return 0
        if not isinstance(aliases, list):
            return 0
        normalized_query = str(query or '')
        return int(any(
            normalized_query in normalize_voice_clone_name(alias)
            for alias in aliases
        ))

    driver_connection.create_function(
        'voice_clone_normalize_name', 1, normalize_voice_clone_name,
        deterministic=True,
    )
    driver_connection.create_function(
        'voice_clone_nonempty_file_size', 1, nonempty_file_size,
        deterministic=True,
    )
    driver_connection.create_function(
        'voice_clone_valid_sha256', 1, valid_sha256,
        deterministic=True,
    )
    driver_connection.create_function(
        'voice_clone_aliases_match', 2, aliases_match,
        deterministic=True,
    )


def _build_group(
    session: Session,
    group_key: str,
    profiles: list[VoiceCloneProfile],
    clone_samples: dict[str, VoiceCloneSample],
    voiceprint_samples: dict[str, VoiceprintSample],
    people: dict[str, Person],
    projects: dict[str, Project],
    identities: dict[str, VoiceCloneIdentity],
    project_id: str,
    used_profile_ids: set[str],
    *,
    version_count: Optional[int] = None,
) -> VoiceCloneLibraryGroupResponse:
    if not profiles:
        identity = identities[group_key]
        return VoiceCloneLibraryGroupResponse(
            expires_at=voice_clone_asset_expires_at(identity, session),
            group_key=group_key, identity_id=identity.id, identity_display_name=identity.display_name,
            identity_aliases=list(identity.aliases or []), display_name=identity.display_name,
            version_count=0, health_status='draft', recommended_profile=None,
        )
    recommended = max(
        profiles,
        key=lambda profile: _profile_rank(profile, project_id, used_profile_ids),
    )
    summary = _build_profile_summary(
        session,
        recommended,
        clone_samples,
        voiceprint_samples,
        people,
        projects,
        identities,
        project_id,
        used_profile_ids,
    )
    return VoiceCloneLibraryGroupResponse(
        expires_at=voice_clone_asset_expires_at(identities.get(recommended.identity_id) or recommended, session),
        group_key=group_key,
        identity_id=summary.identity_id,
        identity_display_name=summary.identity_display_name,
        identity_aliases=summary.identity_aliases,
        display_name=summary.identity_display_name or recommended.voice_name.strip(),
        version_count=version_count if version_count is not None else len(profiles),
        health_status=_group_health(profiles, recommended) if summary.selectable or recommended.status != 'ready' else 'unavailable',
        recommended_profile=summary,
    )


def _build_profile_summary(
    session: Session,
    profile: VoiceCloneProfile,
    clone_samples: dict[str, VoiceCloneSample],
    voiceprint_samples: dict[str, VoiceprintSample],
    people: dict[str, Person],
    projects: dict[str, Project],
    identities: dict[str, VoiceCloneIdentity],
    project_id: str,
    used_profile_ids: set[str],
) -> VoiceCloneLibraryProfileResponse:
    clone_sample = clone_samples.get(profile.source_clone_sample_id or '')
    voiceprint_sample = voiceprint_samples.get(profile.source_sample_id or '')
    source_sample = clone_sample or voiceprint_sample
    person = people.get(voiceprint_sample.person_id) if voiceprint_sample else None
    source_project_id = (
        clone_sample.project_id if clone_sample else person.project_id if person else None
    )
    source_project = projects.get(str(source_project_id or '').strip())
    availability = get_voice_clone_profile_availability(session, profile)
    identity = identities.get(profile.identity_id or '')
    return VoiceCloneLibraryProfileResponse(
        expires_at=voice_clone_asset_expires_at(profile, session),
        id=profile.id,
        identity_id=profile.identity_id,
        identity_display_name=(
            identity.display_name if identity else None
        ),
        identity_aliases=list(identity.aliases or []) if identity else [],
        voice_name=profile.voice_name,
        provider=profile.provider,
        target_model=profile.target_model,
        status=profile.status,
        voice_id=profile.voice_id,
        source_sample_id=profile.source_sample_id,
        source_clone_sample_id=profile.source_clone_sample_id,
        selectable=availability.selectable,
        project_scope=(
            'current_project'
            if profile.project_id == project_id
            else 'used_in_current_project'
            if profile.id in used_profile_ids
            else 'shared'
        ),
        project_id=profile.project_id,
        source_display_name=(
            clone_sample.display_name
            if clone_sample
            else person.name
            if person
            else None
        ),
        source_project_name=source_project.name if source_project else None,
        source_original_filename=source_sample.original_filename if source_sample else None,
        source_sample_ready=bool(
            source_sample
            and str(source_sample.clone_readiness_status or '').lower() == 'ready'
        ),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        last_used_at=profile.last_used_at,
    )


def _group_health(
    profiles: list[VoiceCloneProfile],
    recommended: VoiceCloneProfile,
) -> str:
    if recommended.status == 'ready' and recommended.voice_id:
        return 'ready'
    if any(profile.status == 'creating' for profile in profiles):
        return 'creating'
    if any(profile.status == 'failed' for profile in profiles):
        return 'failed'
    return recommended.status


def _profile_rank(
    profile: VoiceCloneProfile,
    project_id: str,
    used_profile_ids: set[str],
) -> tuple[int, float, float, float, str]:
    project_scope_rank = (
        2
        if profile.project_id == project_id
        else 1
        if profile.id in used_profile_ids
        else 0
    )
    return (
        project_scope_rank,
        _timestamp(profile.last_used_at),
        _timestamp(profile.updated_at),
        _timestamp(profile.created_at),
        profile.id,
    )


def _used_profile_ids(
    session: Session,
    project_id: str,
    profile_ids: Optional[set[str]] = None,
) -> set[str]:
    statement = select(VoiceCloneSynthesisRecord.voice_clone_profile_id).where(
        VoiceCloneSynthesisRecord.project_id == project_id,
        VoiceCloneSynthesisRecord.status == 'ready',
        VoiceCloneSynthesisRecord.voice_clone_profile_id.is_not(None),
    )
    if profile_ids is not None:
        if not profile_ids:
            return set()
        statement = statement.where(
            VoiceCloneSynthesisRecord.voice_clone_profile_id.in_(profile_ids)
        )
    return set(session.scalars(statement))


def _source_maps_for_profiles(
    session: Session,
    profiles: list[VoiceCloneProfile],
) -> tuple[
    dict[str, VoiceCloneSample],
    dict[str, VoiceprintSample],
    dict[str, Person],
    dict[str, Project],
    dict[str, VoiceCloneIdentity],
]:
    clone_ids = {
        profile.source_clone_sample_id
        for profile in profiles
        if profile.source_clone_sample_id
    }
    voiceprint_ids = {
        profile.source_sample_id
        for profile in profiles
        if profile.source_sample_id
    }
    identity_ids = {
        profile.identity_id for profile in profiles if profile.identity_id
    }
    clone_samples = {
        sample.id: sample for sample in session.scalars(
            select(VoiceCloneSample).where(VoiceCloneSample.id.in_(clone_ids))
        )
    } if clone_ids else {}
    voiceprint_samples = {
        sample.id: sample for sample in session.scalars(
            select(VoiceprintSample).where(VoiceprintSample.id.in_(voiceprint_ids))
        )
    } if voiceprint_ids else {}
    person_ids = {sample.person_id for sample in voiceprint_samples.values()}
    people = {
        person.id: person for person in session.scalars(
            select(Person).where(Person.id.in_(person_ids))
        )
    } if person_ids else {}
    project_ids = {
        sample.project_id for sample in clone_samples.values() if sample.project_id
    } | {
        person.project_id for person in people.values() if person.project_id
    }
    projects = {
        project.id: project for project in session.scalars(
            select(Project).where(Project.id.in_(project_ids))
        )
    } if project_ids else {}
    identities = {
        identity.id: identity for identity in session.scalars(
            select(VoiceCloneIdentity).where(VoiceCloneIdentity.id.in_(identity_ids))
        )
    } if identity_ids else {}
    return clone_samples, voiceprint_samples, people, projects, identities


def _timestamp(value: Optional[datetime]) -> float:
    return value.timestamp() if value else 0.0


def _parse_cursor(cursor: Optional[str]) -> int:
    try:
        return max(0, int(str(cursor or '0')))
    except ValueError:
        return 0


def _clamp_limit(limit: int) -> int:
    return max(1, min(MAX_PAGE_SIZE, int(limit or DEFAULT_PAGE_SIZE)))
