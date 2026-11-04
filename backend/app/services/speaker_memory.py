from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse, unquote

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.speaker_memory import SpeakerMemory


def normalize_program_name(value: Optional[str]) -> str:
    if not value:
        return ''
    return ' '.join(str(value).strip().split())


def build_program_key(program_name: Optional[str]) -> str:
    return normalize_program_name(program_name).casefold()


def derive_program_name(candidate: Optional[str], fallback_url: Optional[str] = None) -> str:
    normalized = normalize_program_name(candidate)
    if normalized:
        return normalized

    if fallback_url:
        parsed = urlparse(str(fallback_url))
        file_name = Path(unquote(parsed.path or '')).stem
        normalized = normalize_program_name(file_name.replace('_', ' '))
        if normalized:
            return normalized

    return ''


def get_program_speaker_memory(
    session: Session,
    program_key: Optional[str],
    *,
    project_id: Optional[str] = None,
) -> Optional[SpeakerMemory]:
    normalized_project_id = str(project_id or '').strip()
    if normalized_project_id:
        record, _has_conflict = _find_project_speaker_memory(session, normalized_project_id)
        return record

    normalized_key = build_program_key(program_key)
    if not normalized_key:
        return None
    return session.scalar(select(SpeakerMemory).where(SpeakerMemory.program_key == normalized_key))


def _find_project_speaker_memory(
    session: Session,
    project_id: str,
) -> tuple[Optional[SpeakerMemory], bool]:
    exact_records = list(session.scalars(
        select(SpeakerMemory)
        .where(SpeakerMemory.project_id == project_id)
        .order_by(SpeakerMemory.updated_at.desc(), SpeakerMemory.created_at.desc())
        .limit(2)
    ))
    if len(exact_records) == 1:
        return exact_records[0], False
    if len(exact_records) > 1:
        return None, True

    safe_aliases = _resolve_unambiguous_project_aliases(session, project_id)
    if not safe_aliases:
        return None, False
    candidates = list(session.scalars(
        select(SpeakerMemory)
        .where(
            SpeakerMemory.project_id.is_(None),
            or_(
                SpeakerMemory.program_key.in_(safe_aliases),
                SpeakerMemory.program_name.in_(safe_aliases),
            ),
        )
        .order_by(SpeakerMemory.updated_at.desc(), SpeakerMemory.created_at.desc())
    ))
    resolved_candidates = [
        memory
        for memory in candidates
        if resolve_speaker_memory_project_ids(session, memory) == {project_id}
    ]
    if len(resolved_candidates) == 1:
        return resolved_candidates[0], False
    return None, len(resolved_candidates) > 1


def ensure_project_speaker_memory(
    session: Session,
    *,
    project_id: str,
    program_name: Optional[str] = None,
    program_key: Optional[str] = None,
) -> Optional[SpeakerMemory]:
    normalized_project_id = str(project_id or '').strip()
    if not normalized_project_id:
        return None
    project = session.get(Project, normalized_project_id)
    if not project:
        return None

    canonical_name = normalize_program_name(project.name or program_name)
    canonical_key = build_program_key(project.legacy_program_key or project.name_key or program_key or canonical_name)
    record, has_conflict = _find_project_speaker_memory(session, normalized_project_id)
    if has_conflict:
        return None
    if record:
        record.project_id = normalized_project_id
        if canonical_name:
            record.program_name = canonical_name
        return record

    if not canonical_name or not canonical_key:
        return None
    record = SpeakerMemory(
        project_id=normalized_project_id,
        program_key=_available_program_key(session, canonical_key, normalized_project_id),
        program_name=canonical_name,
        speaker_names=[],
        lower_third_assets=[],
    )
    session.add(record)
    return record


def upsert_program_speaker_memory(
    session: Session,
    program_name: Optional[str],
    speaker_names: Iterable[str],
    *,
    project_id: Optional[str] = None,
) -> Optional[SpeakerMemory]:
    normalized_project_id = str(project_id or '').strip() or None
    project = session.get(Project, normalized_project_id) if normalized_project_id else None
    if normalized_project_id and (not project or project.status != 'active'):
        return None
    normalized_program_name = normalize_program_name(project.name if project else program_name)
    program_key = build_program_key(normalized_program_name)
    cleaned_names = sorted({normalize_program_name(item) for item in speaker_names if normalize_program_name(item)})
    if not program_key or not cleaned_names:
        return None

    if normalized_project_id:
        record = ensure_project_speaker_memory(
            session,
            project_id=normalized_project_id,
            program_name=normalized_program_name,
            program_key=program_key,
        )
    else:
        record = get_program_speaker_memory(session, program_key)
    if not record and normalized_project_id:
        return None
    if not record:
        record = SpeakerMemory(
            program_key=program_key,
            program_name=normalized_program_name,
            speaker_names=cleaned_names,
        )
        session.add(record)
    else:
        existing_names = set(record.speaker_names or [])
        record.speaker_names = sorted(existing_names.union(cleaned_names))
        if normalized_program_name:
            record.program_name = normalized_program_name

    session.commit()
    session.refresh(record)
    return record


def list_program_lower_third_assets(
    session: Session,
    program_key: Optional[str],
    *,
    project_id: Optional[str] = None,
) -> list[dict]:
    memory = get_program_speaker_memory(session, program_key, project_id=project_id)
    if not memory:
        return []
    return [dict(item) for item in (memory.lower_third_assets or []) if isinstance(item, dict)]


def upsert_program_lower_third_assets(
    session: Session,
    program_name: Optional[str],
    assets: Iterable[dict],
    *,
    project_id: Optional[str] = None,
) -> Optional[SpeakerMemory]:
    normalized_project_id = str(project_id or '').strip() or None
    project = session.get(Project, normalized_project_id) if normalized_project_id else None
    if normalized_project_id and (not project or project.status != 'active'):
        return None
    normalized_program_name = normalize_program_name(project.name if project else program_name)
    program_key = build_program_key(normalized_program_name)
    cleaned_assets = [_normalize_lower_third_asset(item) for item in assets]
    cleaned_assets = [item for item in cleaned_assets if item]
    if not program_key or not cleaned_assets:
        return None

    if normalized_project_id:
        record = ensure_project_speaker_memory(
            session,
            project_id=normalized_project_id,
            program_name=normalized_program_name,
            program_key=program_key,
        )
    else:
        record = get_program_speaker_memory(session, program_key)
    if not record and normalized_project_id:
        return None
    if not record:
        record = SpeakerMemory(
            program_key=program_key,
            program_name=normalized_program_name,
            speaker_names=[],
            lower_third_assets=[],
        )
        session.add(record)
    elif normalized_program_name:
        record.program_name = normalized_program_name

    merged: dict[str, dict] = {}
    for asset in record.lower_third_assets or []:
        normalized = _normalize_lower_third_asset(asset)
        if normalized:
            merged[_build_lower_third_asset_key(normalized)] = normalized
    for asset in cleaned_assets:
        merged[_build_lower_third_asset_key(asset)] = asset

    record.lower_third_assets = list(merged.values())
    session.commit()
    session.refresh(record)
    return record


def _resolve_unambiguous_project_aliases(session: Session, project_id: str) -> tuple[str, ...]:
    # Runtime import avoids a service import cycle while keeping one canonical
    # definition for legacy alias ownership during the M1 transition.
    from app.services.projects import resolve_unambiguous_project_scope_ids

    return resolve_unambiguous_project_scope_ids(session, project_id)


def resolve_speaker_memory_project_ids(
    session: Session,
    memory: SpeakerMemory,
) -> set[str]:
    aliases = {
        build_program_key(value)
        for value in (memory.program_key, memory.program_name)
        if build_program_key(value)
    }
    if not aliases:
        return set()
    matched_project_ids: set[str] = set()
    for project in session.scalars(select(Project)):
        project_aliases = {
            build_program_key(value)
            for value in (project.id, project.name, project.name_key, project.legacy_program_key)
            if build_program_key(value)
        }
        if aliases.intersection(project_aliases):
            matched_project_ids.add(project.id)
    return matched_project_ids


def _available_program_key(session: Session, preferred_key: str, project_id: str) -> str:
    existing = session.scalar(select(SpeakerMemory).where(SpeakerMemory.program_key == preferred_key))
    if not existing or existing.project_id == project_id:
        return preferred_key
    return f'project:{project_id}'


def build_program_memory_suggestions(
    memory: Optional[SpeakerMemory],
    current_mappings: Optional[dict[str, str]] = None,
) -> list[str]:
    if not memory:
        return []
    used_names = {normalize_program_name(name) for name in (current_mappings or {}).values() if normalize_program_name(name)}
    return [name for name in (memory.speaker_names or []) if normalize_program_name(name) not in used_names]


def _normalize_lower_third_asset(asset: dict) -> dict:
    if not isinstance(asset, dict):
        return {}
    copied = {key: value for key, value in dict(asset).items() if value is not None}
    speaker_name = normalize_program_name(copied.get('speaker_name'))
    file_name = normalize_program_name(copied.get('file_name'))
    object_key = normalize_program_name(copied.get('object_key'))
    if not speaker_name and file_name:
        speaker_name = Path(file_name).stem.strip()
    if not speaker_name and not object_key:
        return {}
    if speaker_name:
        copied['speaker_name'] = speaker_name
    if file_name:
        copied['file_name'] = file_name
    if object_key:
        copied['object_key'] = object_key
    copied['library_scope'] = 'program'
    return copied


def _build_lower_third_asset_key(asset: dict) -> str:
    speaker_name = normalize_program_name(asset.get('speaker_name')).casefold()
    if speaker_name:
        return f'speaker:{speaker_name}'
    return f"file:{normalize_program_name(asset.get('file_name')).casefold()}"
