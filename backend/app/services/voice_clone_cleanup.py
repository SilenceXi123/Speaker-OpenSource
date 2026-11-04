from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Optional

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.job import Job
from app.models.voice_clone_deletion_task import VoiceCloneDeletionTask
from app.models.voice_clone_identity import VoiceCloneIdentity
from app.models.voice_clone_identity_review import VoiceCloneIdentityReview
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_sample import VoiceCloneSample
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
from app.models.voiceprint_match import VoiceprintMatch
from app.models.voiceprint_sample import VoiceprintSample
from app.services.media import get_media_duration_seconds
from app.services.oss_storage import delete_oss_object, owned_oss_object_key
from app.services.voice_cloning import (
    SUPPORTED_AUDIO_SUFFIXES,
    _delete_provider_voice,
    _normalize_provider,
    get_voice_clone_profile_availability,
)
from app.services.voice_clone_identity import (
    hash_voice_clone_audio,
    normalize_voice_clone_name,
)
from app.services.voice_sample_analysis import analyze_clone_readiness


MAX_SAMPLES_PER_NAME = 3


@dataclass(frozen=True)
class VoiceCloneCleanupGroup:
    name_key: str
    content_sha256: str
    canonical_sample_id: str
    duplicate_sample_ids: tuple[str, ...]
    duplicate_file_paths: tuple[str, ...]
    releasable_bytes: int


@dataclass(frozen=True)
class VoiceCloneNameCapGroup:
    name_key: str
    retained_sample_ids: tuple[str, ...]
    removed_sample_ids: tuple[str, ...]
    removed_file_paths: tuple[str, ...]
    sample_hashes: tuple[tuple[str, str], ...]
    releasable_bytes: int


@dataclass(frozen=True)
class VoiceCloneCleanupIssue:
    sample_id: str
    path: str
    reason: str


@dataclass(frozen=True)
class VoiceCloneInvalidProfile:
    profile_id: str
    reason: str


@dataclass(frozen=True)
class VoiceCloneInvalidSample:
    sample_id: str
    path: str
    reason: str


@dataclass(frozen=True)
class VoiceCloneReadinessUpdate:
    sample_id: str
    status: str
    message: Optional[str]
    duration_ms: int
    content_sha256: str
    file_size_bytes: int


@dataclass(frozen=True)
class VoiceCloneForeignKeyReference:
    table: str
    row_id: str
    column: str
    target_id: Optional[str]


@dataclass(frozen=True)
class VoiceCloneDeletionTarget:
    project_id: Optional[str]
    kind: str
    resource_key: str
    provider: Optional[str] = None
    target_model: Optional[str] = None
    voice_id: Optional[str] = None
    oss_object_key: Optional[str] = None
    local_path: Optional[str] = None


@dataclass(frozen=True)
class VoiceCloneCleanupPlan:
    scanned_sample_count: int
    hashed_sample_count: int
    duplicate_groups: tuple[VoiceCloneCleanupGroup, ...] = field(default_factory=tuple)
    name_cap_groups: tuple[VoiceCloneNameCapGroup, ...] = field(default_factory=tuple)
    missing_files: tuple[VoiceCloneCleanupIssue, ...] = field(default_factory=tuple)
    conflicts: tuple[VoiceCloneCleanupIssue, ...] = field(default_factory=tuple)
    invalid_profiles: tuple[VoiceCloneInvalidProfile, ...] = field(default_factory=tuple)
    invalid_samples: tuple[VoiceCloneInvalidSample, ...] = field(default_factory=tuple)
    readiness_updates: tuple[VoiceCloneReadinessUpdate, ...] = field(default_factory=tuple)
    detach_record_ids: tuple[str, ...] = field(default_factory=tuple)
    delete_record_ids: tuple[str, ...] = field(default_factory=tuple)
    sample_fingerprints: tuple[tuple[str, str, str, str, int], ...] = field(default_factory=tuple)
    foreign_key_baseline: tuple[VoiceCloneForeignKeyReference, ...] = field(default_factory=tuple)
    deletion_targets: tuple[VoiceCloneDeletionTarget, ...] = field(default_factory=tuple)
    plan_version: int = 1
    plan_fingerprint: str = ''
    reset_all: bool = False
    reset_table_counts: tuple[tuple[str, int], ...] = ()
    reset_data_fingerprint: str = ''
    reset_file_fingerprints: tuple[tuple[str, int, int, int, int], ...] = ()

    @property
    def duplicate_sample_count(self) -> int:
        return self.exact_duplicate_sample_count

    @property
    def exact_duplicate_sample_count(self) -> int:
        return sum(len(group.duplicate_sample_ids) for group in self.duplicate_groups)

    @property
    def name_cap_sample_count(self) -> int:
        return sum(len(group.removed_sample_ids) for group in self.name_cap_groups)

    @property
    def scheduled_sample_count(self) -> int:
        return len({
            *(entry.sample_id for entry in self.invalid_samples),
            *(
                sample_id
                for group in self.duplicate_groups
                for sample_id in group.duplicate_sample_ids
            ),
            *(
                sample_id
                for group in self.name_cap_groups
                for sample_id in group.removed_sample_ids
            ),
        })

    @property
    def releasable_bytes(self) -> int:
        if self.reset_all:
            return sum(max(0, entry[3]) for entry in self.reset_file_fingerprints)
        return (
            sum(group.releasable_bytes for group in self.duplicate_groups)
            + sum(group.releasable_bytes for group in self.name_cap_groups)
            + _estimate_paths_bytes([
                Path(entry.path) for entry in self.invalid_samples
            ])
        )

    def as_dict(self) -> dict[str, object]:
        return {
            **({
                'reset_all': True,
                'reset_table_counts': [list(entry) for entry in self.reset_table_counts],
                'reset_data_fingerprint': self.reset_data_fingerprint,
                'reset_file_fingerprints': [list(entry) for entry in self.reset_file_fingerprints],
            } if self.reset_all else {}),
            'scanned_sample_count': self.scanned_sample_count,
            'hashed_sample_count': self.hashed_sample_count,
            'duplicate_group_count': len(self.duplicate_groups),
            'duplicate_sample_count': self.duplicate_sample_count,
            'exact_duplicate_sample_count': self.exact_duplicate_sample_count,
            'name_cap_group_count': len(self.name_cap_groups),
            'name_cap_sample_count': self.name_cap_sample_count,
            'scheduled_sample_count': self.scheduled_sample_count,
            'releasable_bytes': self.releasable_bytes,
            'duplicate_groups': [asdict(group) for group in self.duplicate_groups],
            'name_cap_groups': [asdict(group) for group in self.name_cap_groups],
            'missing_files': [asdict(issue) for issue in self.missing_files],
            'conflicts': [asdict(issue) for issue in self.conflicts],
            'invalid_profiles': [asdict(entry) for entry in self.invalid_profiles],
            'invalid_samples': [asdict(entry) for entry in self.invalid_samples],
            'readiness_updates': [asdict(entry) for entry in self.readiness_updates],
            'detach_record_ids': list(self.detach_record_ids),
            'delete_record_ids': list(self.delete_record_ids),
            'sample_fingerprints': [list(entry) for entry in self.sample_fingerprints],
            'foreign_key_baseline': [
                asdict(entry) for entry in self.foreign_key_baseline
            ],
            'deletion_targets': [
                asdict(target) for target in self.deletion_targets
            ],
            'plan_version': self.plan_version,
            'plan_fingerprint': self.plan_fingerprint,
        }


@dataclass(frozen=True)
class VoiceCloneCleanupResult:
    merged_sample_count: int
    trimmed_sample_count: int
    relinked_profile_count: int
    detached_profile_count: int
    removed_file_count: int
    removed_file_bytes: int
    archived_file_count: int
    archive_dir: Optional[str]
    file_errors: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class VoiceCloneDeletionTaskDrainResult:
    attempted_count: int = 0
    completed_count: int = 0
    failed_count: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def build_voice_clone_cleanup_plan(
    session: Session, *, reset_all: bool = False,
) -> VoiceCloneCleanupPlan:
    if reset_all:
        return _build_voice_library_reset_plan(session)
    samples = list(session.scalars(
        select(VoiceCloneSample).order_by(
            VoiceCloneSample.created_at,
            VoiceCloneSample.id,
        )
    ))
    profiles = list(session.scalars(
        select(VoiceCloneProfile).order_by(
            VoiceCloneProfile.created_at,
            VoiceCloneProfile.id,
        )
    ))
    records = list(session.scalars(
        select(VoiceCloneSynthesisRecord).order_by(
            VoiceCloneSynthesisRecord.created_at,
            VoiceCloneSynthesisRecord.id,
        )
    ))
    profile_states: dict[str, list[str]] = defaultdict(list)
    for profile in profiles:
        if profile.source_clone_sample_id:
            profile_states[str(profile.source_clone_sample_id)].append(
                str(profile.status or '')
            )

    actual_hashes: dict[str, str] = {}
    missing_files: list[VoiceCloneCleanupIssue] = []
    conflicts: list[VoiceCloneCleanupIssue] = []
    invalid_samples: list[VoiceCloneInvalidSample] = []
    invalid_sample_reasons: dict[str, str] = {}
    readiness_updates: list[VoiceCloneReadinessUpdate] = []
    exact_candidates: dict[tuple[str, str], list[VoiceCloneSample]] = defaultdict(list)

    for sample in samples:
        name_key = normalize_voice_clone_name(sample.display_name)
        path = Path(_canonical_local_path(sample.audio_local_path))
        if not path.is_file():
            missing_files.append(VoiceCloneCleanupIssue(
                sample_id=sample.id,
                path=str(path),
                reason='missing_file',
            ))
            invalid_samples.append(VoiceCloneInvalidSample(
                sample_id=sample.id,
                path=str(path),
                reason='sample_file_missing',
            ))
            invalid_sample_reasons[sample.id] = 'sample_file_missing'
            continue
        if path.stat().st_size == 0:
            invalid_samples.append(VoiceCloneInvalidSample(
                sample_id=sample.id,
                path=str(path),
                reason='sample_file_empty',
            ))
            invalid_sample_reasons[sample.id] = 'sample_file_empty'
            continue
        try:
            identity = hash_voice_clone_audio(path)
        except OSError:
            invalid_samples.append(VoiceCloneInvalidSample(
                sample_id=sample.id,
                path=str(path),
                reason='sample_identity_untrusted',
            ))
            invalid_sample_reasons[sample.id] = 'sample_identity_untrusted'
            continue
        actual_hashes[sample.id] = identity.content_sha256
        hash_mismatch = bool(
            sample.content_sha256
            and sample.content_sha256 != identity.content_sha256
        )
        if hash_mismatch:
            conflicts.append(VoiceCloneCleanupIssue(
                sample_id=sample.id,
                path=str(path),
                reason='stored_hash_mismatch',
            ))
        readiness_status = str(sample.clone_readiness_status or '').lower()
        if readiness_status == 'rejected':
            invalid_samples.append(VoiceCloneInvalidSample(
                sample_id=sample.id,
                path=str(path),
                reason='source_sample_rejected',
            ))
            invalid_sample_reasons[sample.id] = 'source_sample_rejected'
            continue
        try:
            duration_ms = int(get_media_duration_seconds(path) * 1000)
        except (OSError, RuntimeError, TypeError, ValueError, OverflowError):
            duration_ms = 0
        if duration_ms <= 0:
            invalid_samples.append(VoiceCloneInvalidSample(
                sample_id=sample.id,
                path=str(path),
                reason='source_sample_rejected',
            ))
            invalid_sample_reasons[sample.id] = 'source_sample_rejected'
            continue
        if readiness_status in {'warning', 'unknown', ''}:
            reprobe = analyze_clone_readiness(
                duration_ms=duration_ms,
                sample_rate_hz=sample.sample_rate_hz,
                channel_count=sample.channel_count,
            )
            if reprobe.status != 'ready':
                invalid_samples.append(VoiceCloneInvalidSample(
                    sample_id=sample.id,
                    path=str(path),
                    reason='source_sample_rejected',
                ))
                invalid_sample_reasons[sample.id] = 'source_sample_rejected'
                continue
            readiness_updates.append(VoiceCloneReadinessUpdate(
                sample_id=sample.id,
                status='ready',
                message=reprobe.message,
                duration_ms=duration_ms,
                content_sha256=identity.content_sha256,
                file_size_bytes=identity.file_size_bytes,
            ))
        elif hash_mismatch:
            invalid_samples.append(VoiceCloneInvalidSample(
                sample_id=sample.id,
                path=str(path),
                reason='sample_identity_untrusted',
            ))
            invalid_sample_reasons[sample.id] = 'sample_identity_untrusted'
            continue
        exact_candidates[(name_key, identity.content_sha256)].append(sample)

    invalid_profiles: list[VoiceCloneInvalidProfile] = []
    invalid_profile_ids: set[str] = set()
    creating_cutoff = datetime.utcnow() - timedelta(hours=1)
    for profile in profiles:
        status = str(profile.status or '').lower()
        reason = None
        if status == 'failed':
            reason = 'profile_failed'
        elif status == 'deleted':
            reason = 'profile_deleted'
        elif (
            status == 'creating'
            and (profile.updated_at or profile.created_at) <= creating_cutoff
        ):
            reason = 'creating_timeout'
        elif status == 'ready' and not str(profile.voice_id or '').strip():
            reason = 'missing_voice_id'
        elif profile.source_clone_sample_id in invalid_sample_reasons:
            reason = invalid_sample_reasons[profile.source_clone_sample_id]
        elif (
            not str(profile.provider or '').strip()
            or not str(profile.target_model or '').strip()
        ):
            reason = 'unsupported_provider_model'
        else:
            availability = get_voice_clone_profile_availability(session, profile)
            if availability.reason in {
                'source_sample_unavailable',
                'unsupported_provider_model',
            }:
                reason = availability.reason
        if reason:
            invalid_profiles.append(VoiceCloneInvalidProfile(
                profile_id=profile.id,
                reason=reason,
            ))
            invalid_profile_ids.add(profile.id)

    for sample in samples:
        if sample.id in invalid_sample_reasons:
            continue
        linked_profiles = [
            profile
            for profile in profiles
            if profile.source_clone_sample_id == sample.id
        ]
        if not any(
            profile.id in invalid_profile_ids for profile in linked_profiles
        ):
            continue
        if any(
            profile.id not in invalid_profile_ids
            for profile in linked_profiles
        ):
            continue
        invalid_samples.append(VoiceCloneInvalidSample(
            sample_id=sample.id,
            path=_canonical_local_path(sample.audio_local_path),
            reason='source_sample_orphaned',
        ))
        invalid_sample_reasons[sample.id] = 'source_sample_orphaned'
        for profile in linked_profiles:
            if profile.id in invalid_profile_ids:
                continue
            invalid_profiles.append(VoiceCloneInvalidProfile(
                profile_id=profile.id,
                reason='source_sample_orphaned',
            ))
            invalid_profile_ids.add(profile.id)

    duplicate_groups: list[VoiceCloneCleanupGroup] = []
    duplicate_ids: set[str] = set()
    for (name_key, content_sha256), candidates in exact_candidates.items():
        members = [
            sample
            for sample in candidates
            if sample.id not in invalid_sample_reasons
        ]
        if len(members) < 2:
            continue
        ranked = sorted(
            members,
            key=lambda sample: _retention_rank(
                sample,
                profile_states.get(sample.id, []),
            ),
            reverse=True,
        )
        canonical = ranked[0]
        duplicates = sorted(ranked[1:], key=lambda sample: (sample.created_at, sample.id))
        duplicate_ids.update(sample.id for sample in duplicates)
        canonical_path = Path(_canonical_local_path(canonical.audio_local_path))
        duplicate_paths = tuple(
            _canonical_local_path(sample.audio_local_path)
            for sample in duplicates
            if Path(_canonical_local_path(sample.audio_local_path))
            != canonical_path
        )
        duplicate_groups.append(VoiceCloneCleanupGroup(
            name_key=name_key,
            content_sha256=content_sha256,
            canonical_sample_id=canonical.id,
            duplicate_sample_ids=tuple(sample.id for sample in duplicates),
            duplicate_file_paths=duplicate_paths,
            releasable_bytes=_estimate_releasable_bytes(
                canonical_path,
                [Path(path) for path in duplicate_paths],
            ),
        ))

    survivors_by_name: dict[str, list[VoiceCloneSample]] = defaultdict(list)
    for sample in samples:
        if sample.id not in duplicate_ids and sample.id not in invalid_sample_reasons:
            survivors_by_name[normalize_voice_clone_name(sample.display_name)].append(sample)

    name_cap_groups: list[VoiceCloneNameCapGroup] = []
    for name_key, members in survivors_by_name.items():
        if len(members) <= MAX_SAMPLES_PER_NAME:
            continue
        ranked = sorted(
            members,
            key=lambda sample: _retention_rank(
                sample,
                profile_states.get(sample.id, []),
            ),
            reverse=True,
        )
        retained = ranked[:MAX_SAMPLES_PER_NAME]
        removed = ranked[MAX_SAMPLES_PER_NAME:]
        retained_paths = {
            Path(_canonical_local_path(sample.audio_local_path))
            for sample in retained
        }
        removed_paths = tuple(
            _canonical_local_path(sample.audio_local_path)
            for sample in removed
            if Path(_canonical_local_path(sample.audio_local_path)).is_file()
            and Path(_canonical_local_path(sample.audio_local_path))
            not in retained_paths
        )
        name_cap_groups.append(VoiceCloneNameCapGroup(
            name_key=name_key,
            retained_sample_ids=tuple(sample.id for sample in retained),
            removed_sample_ids=tuple(sample.id for sample in removed),
            removed_file_paths=removed_paths,
            sample_hashes=tuple(
                (sample.id, actual_hashes.get(sample.id, ''))
                for sample in members
            ),
            releasable_bytes=_estimate_paths_bytes(
                [Path(path) for path in removed_paths]
            ),
        ))

    duplicate_groups.sort(
        key=lambda group: (
            group.name_key,
            group.content_sha256,
            group.canonical_sample_id,
        )
    )
    name_cap_groups.sort(key=lambda group: group.name_key)
    name_cap_sample_ids = {
        sample_id
        for group in name_cap_groups
        for sample_id in group.removed_sample_ids
    }
    duplicate_canonical_ids = {
        duplicate_sample_id: group.canonical_sample_id
        for group in duplicate_groups
        for duplicate_sample_id in group.duplicate_sample_ids
    }
    for profile in profiles:
        effective_sample_id = duplicate_canonical_ids.get(
            profile.source_clone_sample_id,
            profile.source_clone_sample_id,
        )
        if (
            effective_sample_id not in name_cap_sample_ids
            or profile.id in invalid_profile_ids
        ):
            continue
        invalid_profiles.append(VoiceCloneInvalidProfile(
            profile_id=profile.id,
            reason='name_cap',
        ))
        invalid_profile_ids.add(profile.id)

    detach_record_ids: list[str] = []
    delete_record_ids: list[str] = []
    for record in records:
        if record.voice_clone_profile_id not in invalid_profile_ids:
            continue
        has_output = bool(
            str(record.output_audio_local_path or '').strip()
            or str(record.output_audio_public_url or '').strip()
        )
        if record.status in {'failed', 'processing'} and not has_output:
            delete_record_ids.append(record.id)
        else:
            detach_record_ids.append(record.id)

    foreign_key_baseline = tuple(sorted(
        [
            VoiceCloneForeignKeyReference(
                table='voice_clone_profiles',
                row_id=profile.id,
                column='source_sample_id',
                target_id=profile.source_sample_id,
            )
            for profile in profiles
        ]
        + [
            VoiceCloneForeignKeyReference(
                table='voice_clone_profiles',
                row_id=profile.id,
                column='source_clone_sample_id',
                target_id=profile.source_clone_sample_id,
            )
            for profile in profiles
        ]
        + [
            VoiceCloneForeignKeyReference(
                table='voice_clone_synthesis_records',
                row_id=record.id,
                column='voice_clone_profile_id',
                target_id=record.voice_clone_profile_id,
            )
            for record in records
        ],
        key=lambda entry: (
            entry.table,
            entry.row_id,
            entry.column,
            entry.target_id or '',
        ),
    ))
    scheduled_sample_ids = (
        set(invalid_sample_reasons)
        | {
            sample_id
            for group in duplicate_groups
            for sample_id in group.duplicate_sample_ids
        }
        | {
            sample_id
            for group in name_cap_groups
            for sample_id in group.removed_sample_ids
        }
    )
    preserved_local_identities = {
        _local_path_identity(record.output_audio_local_path)
        for record in records
        if str(record.output_audio_local_path or '').strip()
    } | {
        _local_path_identity(sample.audio_local_path)
        for sample in samples
        if sample.id not in scheduled_sample_ids
        and str(sample.audio_local_path or '').strip()
    } | {
        _local_path_identity(profile.preview_audio_local_path)
        for profile in profiles
        if profile.id not in invalid_profile_ids
        and str(profile.preview_audio_local_path or '').strip()
    }
    preserved_oss_object_keys = {
        object_key
        for sample in samples
        if sample.id not in scheduled_sample_ids
        if (object_key := owned_oss_object_key(sample.audio_public_url))
    } | {
        object_key
        for record in records
        if (object_key := owned_oss_object_key(record.output_audio_public_url))
    }
    deletion_targets = {
        target.resource_key: target
        for target in _profile_deletion_targets(
            [
                profile
                for profile in profiles
                if profile.id in invalid_profile_ids
            ],
            preserved_local_identities=preserved_local_identities,
        )
    }
    for sample in samples:
        if sample.id not in scheduled_sample_ids:
            continue
        local_path = _canonical_local_path(sample.audio_local_path)
        if (
            local_path
            and _local_path_identity(local_path)
            not in preserved_local_identities
        ):
            resource_key = f'local:{local_path}'
            deletion_targets[resource_key] = VoiceCloneDeletionTarget(
                project_id=sample.project_id,
                kind='local',
                resource_key=resource_key,
                local_path=local_path,
            )
        oss_object_key = owned_oss_object_key(sample.audio_public_url)
        if (
            oss_object_key
            and oss_object_key not in preserved_oss_object_keys
        ):
            resource_key = f'oss:{oss_object_key}'
            deletion_targets[resource_key] = VoiceCloneDeletionTarget(
                project_id=sample.project_id,
                kind='oss',
                resource_key=resource_key,
                oss_object_key=oss_object_key,
            )
    plan = VoiceCloneCleanupPlan(
        scanned_sample_count=len(samples),
        hashed_sample_count=len(actual_hashes),
        duplicate_groups=tuple(duplicate_groups),
        name_cap_groups=tuple(name_cap_groups),
        missing_files=tuple(missing_files),
        conflicts=tuple(conflicts),
        invalid_profiles=tuple(invalid_profiles),
        invalid_samples=tuple(invalid_samples),
        readiness_updates=tuple(readiness_updates),
        detach_record_ids=tuple(detach_record_ids),
        delete_record_ids=tuple(delete_record_ids),
        sample_fingerprints=tuple(
            (
                sample.id,
                normalize_voice_clone_name(sample.display_name),
                _canonical_local_path(sample.audio_local_path),
                actual_hashes.get(sample.id, ''),
                int(sample.file_size_bytes or 0),
            )
            for sample in samples
        ),
        foreign_key_baseline=foreign_key_baseline,
        deletion_targets=tuple(
            deletion_targets[key] for key in sorted(deletion_targets)
        ),
    )
    return replace(plan, plan_fingerprint=_plan_fingerprint(plan))


def _plan_fingerprint(plan: VoiceCloneCleanupPlan) -> str:
    canonical_plan_inputs = {
        key: value
        for key, value in plan.as_dict().items()
        if key != 'plan_fingerprint'
    }
    return hashlib.sha256(
        json.dumps(
            canonical_plan_inputs,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        ).encode('utf-8')
    ).hexdigest()


_RESET_MODELS = (
    VoiceprintSample, VoiceprintMatch, VoiceCloneSample,
    VoiceCloneProfile, VoiceCloneSynthesisRecord,
    VoiceCloneIdentity, VoiceCloneIdentityReview,
)


def _project_media_references(session: Session) -> tuple[set[str], set[str]]:
    paths: set[str] = set()
    keys: set[str] = set()
    for row in session.execute(select(
        Job.source_local_path, Job.source_export_path,
        Job.video_source_local_path, Job.video_source_export_path,
        Job.source_url, Job.video_source_url,
        Job.transcription_url, Job.render_output_url,
    )):
        paths.update(_local_path_identity(value) for value in row[:4] if value)
        keys.update(key for value in row[4:] if (key := owned_oss_object_key(value)))
    return paths, keys


def _build_voice_library_reset_plan(session: Session) -> VoiceCloneCleanupPlan:
    rows = {
        model.__tablename__: list(session.scalars(select(model).order_by(model.id)))
        for model in _RESET_MODELS
    }
    snapshot = hashlib.sha256(str(session.get_bind().url).encode())
    for model in _RESET_MODELS:
        snapshot.update(model.__tablename__.encode())
        for row in rows[model.__tablename__]:
            snapshot.update(json.dumps(
                {column.name: getattr(row, column.name) for column in model.__table__.columns},
                sort_keys=True, default=str, separators=(',', ':'),
            ).encode())
    protected_paths, protected_keys = _project_media_references(session)
    profiles = rows['voice_clone_profiles']
    targets = {
        target.resource_key: target
        for target in _profile_deletion_targets(profiles, preserved_local_identities=protected_paths)
    }

    def add_assets(local_path: object, public_url: object = None, project_id: Optional[str] = None) -> None:
        path = _canonical_local_path(local_path)
        if path and _local_path_identity(path) not in protected_paths:
            targets[f'local:{path}'] = VoiceCloneDeletionTarget(
                project_id=project_id, kind='local', resource_key=f'local:{path}', local_path=path,
            )
        key = owned_oss_object_key(public_url)
        if key and key not in protected_keys:
            targets[f'oss:{key}'] = VoiceCloneDeletionTarget(
                project_id=project_id, kind='oss', resource_key=f'oss:{key}', oss_object_key=key,
            )

    for sample in rows['voiceprint_samples']:
        add_assets(sample.audio_local_path, sample.audio_public_url)
        add_assets(sample.clone_audio_local_path, sample.clone_audio_public_url)
    for sample in rows['voice_clone_samples']:
        add_assets(sample.audio_local_path, sample.audio_public_url, sample.project_id)
    for profile in profiles:
        add_assets(profile.source_audio_local_path, project_id=profile.project_id)
    for record in rows['voice_clone_synthesis_records']:
        add_assets(record.output_audio_local_path, record.output_audio_public_url, record.project_id)
    # Dedicated voice libraries can contain unreferenced samples and exports.
    for root in (settings.voiceprint_library_dir_path, settings.voice_clone_library_dir_path):
        for path in sorted(root.rglob('*')):
            if path.is_file() or path.is_symlink():
                add_assets(path)
    # Generated voice previews share the audio directory with project media.
    for path in sorted(settings.audio_dir_path.glob('voice-clone-preview-*')):
        if path.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES and (path.is_file() or path.is_symlink()):
            add_assets(path)
    file_fingerprints = []
    for target in sorted(targets.values(), key=lambda item: item.resource_key):
        if target.kind == 'local':
            try:
                stat = Path(target.local_path).lstat()
                metadata = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            except FileNotFoundError:
                metadata = (-1, -1, -1, -1)
            file_fingerprints.append((target.local_path, *metadata))
    plan = VoiceCloneCleanupPlan(
        scanned_sample_count=len(rows['voice_clone_samples']), hashed_sample_count=0,
        reset_all=True,
        reset_table_counts=tuple((table, len(items)) for table, items in rows.items()),
        reset_data_fingerprint=snapshot.hexdigest(),
        reset_file_fingerprints=tuple(file_fingerprints),
        invalid_profiles=tuple(VoiceCloneInvalidProfile(profile.id, 'reset_all') for profile in profiles),
        invalid_samples=tuple(VoiceCloneInvalidSample(sample.id, sample.audio_local_path, 'reset_all') for sample in rows['voice_clone_samples']),
        delete_record_ids=tuple(record.id for record in rows['voice_clone_synthesis_records']),
        deletion_targets=tuple(targets[key] for key in sorted(targets)),
    )
    return replace(plan, plan_fingerprint=_plan_fingerprint(plan))


def load_voice_clone_cleanup_plan(plan_path: Path) -> VoiceCloneCleanupPlan:
    try:
        payload = json.loads(Path(plan_path).read_text(encoding='utf-8'))
        plan = VoiceCloneCleanupPlan(
            scanned_sample_count=payload['scanned_sample_count'],
            hashed_sample_count=payload['hashed_sample_count'],
            duplicate_groups=tuple(
                VoiceCloneCleanupGroup(
                    **{
                        **entry,
                        'duplicate_sample_ids': tuple(
                            entry['duplicate_sample_ids']
                        ),
                        'duplicate_file_paths': tuple(
                            entry['duplicate_file_paths']
                        ),
                    }
                )
                for entry in payload['duplicate_groups']
            ),
            name_cap_groups=tuple(
                VoiceCloneNameCapGroup(
                    **{
                        **entry,
                        'retained_sample_ids': tuple(
                            entry['retained_sample_ids']
                        ),
                        'removed_sample_ids': tuple(
                            entry['removed_sample_ids']
                        ),
                        'removed_file_paths': tuple(
                            entry['removed_file_paths']
                        ),
                        'sample_hashes': tuple(
                            tuple(item) for item in entry['sample_hashes']
                        ),
                    }
                )
                for entry in payload['name_cap_groups']
            ),
            missing_files=tuple(
                VoiceCloneCleanupIssue(**entry)
                for entry in payload['missing_files']
            ),
            conflicts=tuple(
                VoiceCloneCleanupIssue(**entry)
                for entry in payload['conflicts']
            ),
            invalid_profiles=tuple(
                VoiceCloneInvalidProfile(**entry)
                for entry in payload['invalid_profiles']
            ),
            invalid_samples=tuple(
                VoiceCloneInvalidSample(**entry)
                for entry in payload['invalid_samples']
            ),
            readiness_updates=tuple(
                VoiceCloneReadinessUpdate(**entry)
                for entry in payload['readiness_updates']
            ),
            detach_record_ids=tuple(payload['detach_record_ids']),
            delete_record_ids=tuple(payload['delete_record_ids']),
            sample_fingerprints=tuple(
                tuple(entry) for entry in payload['sample_fingerprints']
            ),
            foreign_key_baseline=tuple(
                VoiceCloneForeignKeyReference(**entry)
                for entry in payload['foreign_key_baseline']
            ),
            deletion_targets=tuple(
                VoiceCloneDeletionTarget(**entry)
                for entry in payload['deletion_targets']
            ),
            plan_version=payload['plan_version'],
            plan_fingerprint=payload['plan_fingerprint'],
            reset_all=payload.get('reset_all', False),
            reset_table_counts=tuple(tuple(entry) for entry in payload.get('reset_table_counts', [])),
            reset_data_fingerprint=payload.get('reset_data_fingerprint', ''),
            reset_file_fingerprints=tuple(tuple(entry) for entry in payload.get('reset_file_fingerprints', [])),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise RuntimeError('保存的清理计划无法读取') from error
    normalized_plan = json.loads(json.dumps(plan.as_dict(), ensure_ascii=False))
    if plan.plan_version != 1 or normalized_plan != payload:
        raise RuntimeError('保存的清理计划格式或版本无效')
    return plan


def _canonical_local_path(value: object) -> str:
    raw = str(value or '').strip()
    return str(Path(os.path.abspath(raw))) if raw else ''


def _local_path_identity(value: object) -> str:
    canonical_path = _canonical_local_path(value)
    if not canonical_path:
        return ''
    try:
        return str(Path(canonical_path).resolve(strict=False))
    except (OSError, RuntimeError):
        return canonical_path


def _canonicalize_deletion_target(
    target: VoiceCloneDeletionTarget,
) -> VoiceCloneDeletionTarget:
    if target.kind != 'local':
        return target
    local_path = _canonical_local_path(target.local_path)
    return replace(
        target,
        resource_key=f'local:{local_path}',
        local_path=local_path,
    )


def _profile_deletion_targets(
    profiles: list[VoiceCloneProfile],
    *,
    preserved_local_identities: set[str] | frozenset[str] = frozenset(),
) -> tuple[VoiceCloneDeletionTarget, ...]:
    targets: dict[str, VoiceCloneDeletionTarget] = {}
    for profile in profiles:
        provider = str(profile.provider or '').strip().lower()
        target_model = str(profile.target_model or '').strip()
        voice_id = str(profile.voice_id or '').strip()
        if provider and target_model and voice_id:
            resource_key = f'provider:{provider}:{target_model}:{voice_id}'
            targets[resource_key] = VoiceCloneDeletionTarget(
                project_id=profile.project_id,
                kind='provider',
                resource_key=resource_key,
                provider=provider,
                target_model=target_model,
                voice_id=voice_id,
            )
        preview_path = _canonical_local_path(profile.preview_audio_local_path)
        if (
            preview_path
            and _local_path_identity(preview_path)
            not in preserved_local_identities
        ):
            resource_key = f'local:{preview_path}'
            targets[resource_key] = VoiceCloneDeletionTarget(
                project_id=profile.project_id,
                kind='local',
                resource_key=resource_key,
                local_path=preview_path,
            )
    return tuple(targets[key] for key in sorted(targets))


def hard_delete_voice_clone_profiles(
    session: Session,
    profile_ids: tuple[str, ...] | list[str],
    *,
    deletion_targets: tuple[VoiceCloneDeletionTarget, ...] = (),
) -> None:
    profiles = [
        profile
        for profile_id in dict.fromkeys(profile_ids)
        if (profile := session.get(VoiceCloneProfile, profile_id)) is not None
    ]
    profile_by_id = {profile.id: profile for profile in profiles}
    records = (
        list(session.scalars(
            select(VoiceCloneSynthesisRecord).where(
                VoiceCloneSynthesisRecord.voice_clone_profile_id.in_(
                    profile_by_id
                )
            )
        ))
        if profile_by_id
        else []
    )
    records_to_delete: list[VoiceCloneSynthesisRecord] = []
    for record in records:
        profile = profile_by_id[str(record.voice_clone_profile_id)]
        has_output = bool(
            str(record.output_audio_local_path or '').strip()
            or str(record.output_audio_public_url or '').strip()
        )
        if str(record.status or '').lower() in {'failed', 'processing'} and not has_output:
            records_to_delete.append(record)
            continue
        record.voice_name_snapshot = (
            record.voice_name_snapshot or profile.voice_name
        )
        record.provider_snapshot = record.provider_snapshot or profile.provider
        record.target_model_snapshot = (
            record.target_model_snapshot or profile.target_model
        )
        record.voice_clone_profile_id = None

    preserved_output_identities = {
        _local_path_identity(path)
        for path in session.scalars(
            select(VoiceCloneSynthesisRecord.output_audio_local_path)
        )
        if str(path or '').strip()
    }
    other_preview_identities = {
        _local_path_identity(path)
        for path in session.scalars(
            select(VoiceCloneProfile.preview_audio_local_path).where(
                VoiceCloneProfile.id.not_in(profile_by_id)
            )
        )
        if str(path or '').strip()
    }
    source_sample_identities = {
        _local_path_identity(path)
        for path in session.scalars(select(VoiceCloneSample.audio_local_path))
        if str(path or '').strip()
    }
    preserved_target_identities = (
        preserved_output_identities | other_preview_identities
    )
    targets = {
        target.resource_key: target
        for target in map(
            _canonicalize_deletion_target,
            (
                *deletion_targets,
                *_profile_deletion_targets(
                    profiles,
                    preserved_local_identities=(
                        preserved_target_identities
                        | source_sample_identities
                    ),
                ),
            ),
        )
        if (
            target.kind != 'local'
            or _local_path_identity(target.local_path)
            not in preserved_target_identities
        )
    }
    _enqueue_deletion_targets(session, targets)

    for record in records_to_delete:
        session.delete(record)
    for profile in profiles:
        session.delete(profile)


def _enqueue_deletion_targets(
    session: Session, targets: dict[str, VoiceCloneDeletionTarget],
) -> None:
    existing_keys = set(session.scalars(
        select(VoiceCloneDeletionTask.resource_key).where(
            VoiceCloneDeletionTask.resource_key.in_(targets)
        )
    )) if targets else set()
    for resource_key, target in targets.items():
        if resource_key in existing_keys:
            continue
        session.add(VoiceCloneDeletionTask(
            project_id=target.project_id,
            kind=target.kind,
            resource_key=target.resource_key,
            provider=target.provider,
            target_model=target.target_model,
            voice_id=target.voice_id,
            oss_object_key=target.oss_object_key,
            local_path=target.local_path,
        ))


def drain_voice_clone_deletion_tasks(
    session: Session,
    limit: int = 20,
    now: Optional[datetime] = None,
    *,
    project_id: Optional[str] = None,
) -> VoiceCloneDeletionTaskDrainResult:
    task_limit = max(0, int(limit))
    if task_limit == 0:
        return VoiceCloneDeletionTaskDrainResult()
    reference_now = _normalize_utc_datetime(now or datetime.utcnow())
    tasks = list(session.scalars(
        select(VoiceCloneDeletionTask)
        .where(VoiceCloneDeletionTask.project_id == project_id if project_id else True)
        .where(or_(
            VoiceCloneDeletionTask.next_attempt_at.is_(None),
            VoiceCloneDeletionTask.next_attempt_at <= reference_now,
        ))
        .order_by(
            VoiceCloneDeletionTask.next_attempt_at.asc().nullsfirst(),
            VoiceCloneDeletionTask.created_at,
            VoiceCloneDeletionTask.id,
        )
        .limit(task_limit)
    ))
    completed_count = 0
    failed_count = 0
    for task in tasks:
        try:
            if _voice_clone_deletion_task_is_referenced(session, task):
                session.delete(task)
                completed_count += 1
                continue
            if task.kind == 'profile_release':
                from app.services.voice_cloning import _begin_voice_result_write, _release_voice_clone_after_download

                session.commit()
                _begin_voice_result_write(session)
                profile = session.get(VoiceCloneProfile, task.resource_key.removeprefix('profile_release:'))
                if profile is not None:
                    if profile.project_id != task.project_id:
                        raise RuntimeError('profile release project conflict')
                    if not _release_voice_clone_after_download(session, profile):
                        raise RuntimeError('voice generation still in progress')
                session.commit()
            else:
                _run_voice_clone_deletion_task(task)
        except Exception as error:
            task.attempt_count = int(task.attempt_count or 0) + 1
            delay_minutes = (
                1440
                if task.attempt_count >= 11
                else 2 ** task.attempt_count
            )
            task.next_attempt_at = reference_now + timedelta(
                minutes=delay_minutes
            )
            task.last_error = (
                f'{type(error).__name__}: asset deletion failed'
            )
            failed_count += 1
            continue
        session.delete(task)
        completed_count += 1
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    return VoiceCloneDeletionTaskDrainResult(
        attempted_count=len(tasks),
        completed_count=completed_count,
        failed_count=failed_count,
    )


def _voice_clone_deletion_task_is_referenced(
    session: Session,
    task: VoiceCloneDeletionTask,
) -> bool:
    kind = str(task.kind or '').strip().lower()
    if kind in {'local', 'oss'}:
        protected_paths, protected_keys = _project_media_references(session)
        if kind == 'local' and _local_path_identity(task.local_path) in protected_paths:
            return True
        if kind == 'oss' and task.oss_object_key in protected_keys:
            return True
    if kind == 'local':
        try:
            _guarded_voice_clone_local_path(task.local_path)
        except RuntimeError:
            # Unsafe task paths must reach the deletion guard and remain
            # visible for retry/inspection instead of being silently canceled
            # through a resolved alias match.
            return False
        local_identity = _local_path_identity(task.local_path)
        if not local_identity:
            return False
        referenced_paths = (
            session.scalars(select(VoiceprintSample.audio_local_path)),
            session.scalars(select(VoiceprintSample.clone_audio_local_path)),
            session.scalars(select(VoiceCloneProfile.source_audio_local_path)),
            session.scalars(select(VoiceCloneSample.audio_local_path)),
            session.scalars(select(VoiceCloneProfile.preview_audio_local_path)),
            session.scalars(select(
                VoiceCloneSynthesisRecord.output_audio_local_path
            )),
        )
        return any(
            _local_path_identity(path) == local_identity
            for paths in referenced_paths
            for path in paths
            if str(path or '').strip()
        )
    if kind == 'oss':
        object_key = str(task.oss_object_key or '').strip()
        if not object_key:
            return False
        public_urls = (
            session.scalars(select(VoiceprintSample.audio_public_url)),
            session.scalars(select(VoiceprintSample.clone_audio_public_url)),
            session.scalars(select(VoiceCloneSample.audio_public_url)),
            session.scalars(select(
                VoiceCloneSynthesisRecord.output_audio_public_url
            )),
        )
        return any(
            owned_oss_object_key(public_url) == object_key
            for urls in public_urls
            for public_url in urls
        )
    if kind == 'provider':
        raw_provider = str(task.provider or '').strip()
        target_model = str(task.target_model or '').strip()
        voice_id = str(task.voice_id or '').strip()
        if not raw_provider or not target_model or not voice_id:
            return False
        provider = _normalize_provider(raw_provider)
        providers = session.scalars(
            select(VoiceCloneProfile.provider).where(
                func.trim(VoiceCloneProfile.target_model) == target_model,
                func.trim(VoiceCloneProfile.voice_id) == voice_id,
            )
        )
        return any(_normalize_provider(value) == provider for value in providers)
    return False


def _run_voice_clone_deletion_task(task: VoiceCloneDeletionTask) -> None:
    kind = str(task.kind or '').strip().lower()
    if kind == 'provider':
        provider = str(task.provider or '').strip().lower()
        target_model = str(task.target_model or '').strip()
        voice_id = str(task.voice_id or '').strip()
        if not provider or not target_model or not voice_id:
            raise RuntimeError('invalid provider deletion task')
        _delete_provider_voice(
            provider,
            voice_id,
            target_model=target_model,
        )
        return
    if kind == 'oss':
        object_key = str(task.oss_object_key or '').strip()
        if not object_key or not settings.oss_is_configured:
            raise RuntimeError('invalid OSS deletion task')
        delete_oss_object(object_key)
        return
    if kind == 'local':
        path_value = str(task.local_path or '').strip()
        if not path_value:
            raise RuntimeError('invalid local deletion task')
        target = _guarded_voice_clone_local_path(path_value)
        if not target.exists():
            return
        if not target.is_file():
            raise RuntimeError('local deletion target is not a file')
        target.unlink()
        return
    raise RuntimeError('unknown deletion task kind')


def _voice_clone_managed_roots() -> tuple[Path, ...]:
    return (
        settings.upload_dir_path,
        settings.audio_dir_path,
        settings.render_dir_path,
        settings.media_library_dir_path,
        settings.lower_third_library_dir_path,
        settings.voiceprint_library_dir_path,
        settings.voice_clone_library_dir_path,
    )


def _guarded_voice_clone_local_path(path_value: object) -> Path:
    target = Path(_canonical_local_path(path_value))
    roots = _voice_clone_managed_roots()
    for root in sorted(
        roots,
        key=lambda value: len(Path(_canonical_local_path(value)).parts),
        reverse=True,
    ):
        lexical_root = Path(_canonical_local_path(root))
        if not (
            target == lexical_root
            or target.is_relative_to(lexical_root)
        ):
            continue
        current = lexical_root
        for component in target.relative_to(lexical_root).parts:
            current /= component
            if current.is_symlink():
                raise RuntimeError('local deletion path contains a symlink')
        resolved_target = target.resolve(strict=False)
        resolved_root = lexical_root.resolve(strict=False)
        if (
            resolved_target == resolved_root
            or resolved_target.is_relative_to(resolved_root)
        ):
            return target
    raise RuntimeError('unmanaged local deletion task')


def _normalize_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def apply_voice_clone_cleanup_plan(
    session: Session,
    plan: VoiceCloneCleanupPlan,
    backup_path: Optional[Path],
    plan_path: Path,
    *,
    archive_dir: Optional[Path] = None,
    no_backup: bool = False,
) -> VoiceCloneCleanupResult:
    if plan.reset_all and not no_backup:
        raise RuntimeError('全量重置必须明确选择不保留备份 no_backup')
    if no_backup and (not plan.reset_all or backup_path is not None or archive_dir is not None):
        raise RuntimeError('不保留备份仅允许用于全量重置，且不能同时指定备份或归档')
    if backup_path is None and not no_backup:
        raise RuntimeError('必须指定备份路径，或为全量重置明确选择不保留备份')
    if (
        plan.exact_duplicate_sample_count + plan.name_cap_sample_count
        and archive_dir is None
    ):
        raise RuntimeError('执行清理前必须指定媒体归档目录')

    _validate_saved_plan(plan, Path(plan_path))
    session.rollback()
    if backup_path is not None:
        _backup_sqlite_database(session, Path(backup_path))
    relinked_profile_count = 0
    detached_profile_count = 0
    archived_file_count = 0
    try:
        session.execute(text('BEGIN IMMEDIATE'))
        current_plan = build_voice_clone_cleanup_plan(session, reset_all=plan.reset_all)
        if current_plan.foreign_key_baseline != plan.foreign_key_baseline:
            raise RuntimeError('清理计划外键基线已变化，已中止执行')
        if current_plan.plan_fingerprint != plan.plan_fingerprint:
            raise RuntimeError('清理计划已过期或内容变化，请重新预览')
        if plan.reset_all:
            session.execute(text('PRAGMA secure_delete=ON'))
            if session.scalar(text('PRAGMA secure_delete')) != 1:
                raise RuntimeError('SQLite secure_delete 未生效，已取消全量重置')
            baseline = set(session.execute(text('PRAGMA foreign_key_check')).all())
            _enqueue_deletion_targets(session, {
                target.resource_key: target for target in plan.deletion_targets
            })
            session.flush()  # Persist the asset queue before removing any references.
            for model in (
                VoiceCloneIdentityReview, VoiceCloneSynthesisRecord, VoiceCloneProfile,
                VoiceCloneSample, VoiceprintMatch, VoiceprintSample, VoiceCloneIdentity,
            ):
                session.execute(delete(model))
            session.flush()
            if set(session.execute(text('PRAGMA foreign_key_check')).all()) - baseline:
                raise RuntimeError('全量重置引入了失效引用，已取消清理')
            session.commit()
            return VoiceCloneCleanupResult(0, 0, 0, len(plan.invalid_profiles), 0, 0, 0, None)
        _validate_plan(session, plan)
        archived_file_count = _archive_scheduled_files(
            session,
            plan,
            archive_dir,
        ) if archive_dir else 0

        for sample in session.scalars(select(VoiceCloneSample)):
            sample.name_key = normalize_voice_clone_name(sample.display_name)

        for update_entry in plan.readiness_updates:
            sample = session.get(VoiceCloneSample, update_entry.sample_id)
            if sample is None:
                raise RuntimeError('清理计划已过期，请重新预览')
            sample.clone_readiness_status = update_entry.status
            sample.clone_readiness_message = update_entry.message
            sample.duration_ms = update_entry.duration_ms
            sample.content_sha256 = update_entry.content_sha256
            sample.file_size_bytes = update_entry.file_size_bytes

        for group in plan.duplicate_groups:
            canonical = session.get(VoiceCloneSample, group.canonical_sample_id)
            if not canonical:
                raise RuntimeError('清理计划已过期，请重新预览')
            duplicate_ids = list(group.duplicate_sample_ids)
            relinked_profile_count += int(session.scalar(
                select(func.count(VoiceCloneProfile.id)).where(
                    VoiceCloneProfile.source_clone_sample_id.in_(duplicate_ids)
                )
            ) or 0)
            session.execute(
                update(VoiceCloneProfile)
                .where(VoiceCloneProfile.source_clone_sample_id.in_(duplicate_ids))
                .values(
                    source_clone_sample_id=canonical.id,
                    source_audio_local_path=canonical.audio_local_path,
                )
            )
            identity = hash_voice_clone_audio(Path(canonical.audio_local_path))
            canonical.name_key = group.name_key
            canonical.content_sha256 = identity.content_sha256
            canonical.file_size_bytes = identity.file_size_bytes
            duplicates = [
                session.get(VoiceCloneSample, sample_id)
                for sample_id in duplicate_ids
            ]
            canonical.last_used_at = max(
                (
                    sample.last_used_at
                    for sample in [canonical, *duplicates]
                    if sample is not None and sample.last_used_at is not None
                ),
                default=canonical.last_used_at,
            )
            for sample in duplicates:
                if sample is not None:
                    session.delete(sample)

        for group in plan.name_cap_groups:
            removed_ids = list(group.removed_sample_ids)
            detached_profile_count += int(session.scalar(
                select(func.count(VoiceCloneProfile.id)).where(
                    VoiceCloneProfile.source_clone_sample_id.in_(removed_ids)
                )
            ) or 0)

        hard_delete_voice_clone_profiles(
            session,
            [entry.profile_id for entry in plan.invalid_profiles],
            deletion_targets=plan.deletion_targets,
        )
        session.flush()
        for entry in plan.invalid_samples:
            sample = session.get(VoiceCloneSample, entry.sample_id)
            if sample is None:
                continue
            if session.scalar(
                select(func.count(VoiceCloneProfile.id)).where(
                    VoiceCloneProfile.source_clone_sample_id == sample.id
                )
            ):
                continue
            session.delete(sample)
        for group in plan.name_cap_groups:
            for sample_id in group.removed_sample_ids:
                sample = session.get(VoiceCloneSample, sample_id)
                if sample is not None:
                    session.delete(sample)

        session.flush()
        scheduled_ids = [
            sample_id
            for group in plan.duplicate_groups
            for sample_id in group.duplicate_sample_ids
        ] + [
            sample_id
            for group in plan.name_cap_groups
            for sample_id in group.removed_sample_ids
        ]
        if scheduled_ids and session.scalar(
            select(func.count(VoiceCloneProfile.id)).where(
                VoiceCloneProfile.source_clone_sample_id.in_(scheduled_ids)
            )
        ):
            raise RuntimeError('仍有音色引用待删除样本，已取消清理')
        _assert_voice_clone_integrity(session)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return VoiceCloneCleanupResult(
        merged_sample_count=plan.exact_duplicate_sample_count,
        trimmed_sample_count=plan.name_cap_sample_count,
        relinked_profile_count=relinked_profile_count,
        detached_profile_count=detached_profile_count,
        removed_file_count=0,
        removed_file_bytes=0,
        archived_file_count=archived_file_count,
        archive_dir=str(archive_dir) if archive_dir else None,
        file_errors=(),
    )


def _validate_saved_plan(plan: VoiceCloneCleanupPlan, plan_path: Path) -> None:
    if not plan_path.is_file():
        raise RuntimeError('必须提供已保存的清理计划')
    saved_plan = load_voice_clone_cleanup_plan(plan_path)
    if (
        plan.plan_fingerprint != _plan_fingerprint(plan)
        or saved_plan != plan
    ):
        raise RuntimeError('保存的清理计划已被修改，已中止执行')


def _backup_sqlite_database(session: Session, backup_path: Path) -> None:
    bind = session.get_bind()
    if bind.dialect.name != 'sqlite':
        raise RuntimeError('声音复刻清理仅支持 SQLite 数据库')
    database = str(bind.url.database or '')
    if not database or database == ':memory:':
        raise RuntimeError('无法备份内存 SQLite 数据库')
    source_path = Path(database).resolve()
    target_path = backup_path.resolve()
    if not source_path.is_file():
        raise RuntimeError('SQLite 数据库文件不存在')
    if target_path == source_path or target_path.exists():
        raise RuntimeError('备份路径必须是新的独立文件')
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        sqlite3.connect(f'file:{source_path}?mode=ro', uri=True) as source,
        sqlite3.connect(target_path) as target,
    ):
        source.backup(target)


def _assert_voice_clone_integrity(session: Session) -> None:
    dangling_profile = session.execute(text(
        'SELECT profile.id '
        'FROM voice_clone_profiles AS profile '
        'LEFT JOIN voice_clone_samples AS sample '
        'ON sample.id = profile.source_clone_sample_id '
        'WHERE profile.source_clone_sample_id IS NOT NULL '
        'AND sample.id IS NULL LIMIT 1'
    )).first()
    dangling_record = session.execute(text(
        'SELECT record.id '
        'FROM voice_clone_synthesis_records AS record '
        'LEFT JOIN voice_clone_profiles AS profile '
        'ON profile.id = record.voice_clone_profile_id '
        'WHERE record.voice_clone_profile_id IS NOT NULL '
        'AND profile.id IS NULL LIMIT 1'
    )).first()
    foreign_key_violations = session.execute(
        text('PRAGMA foreign_key_check')
    ).all()
    if dangling_profile or dangling_record or foreign_key_violations:
        raise RuntimeError('声音复刻引用完整性检查失败，已取消清理')


def _validate_plan(session: Session, plan: VoiceCloneCleanupPlan) -> None:
    for group in plan.duplicate_groups:
        member_ids = (group.canonical_sample_id, *group.duplicate_sample_ids)
        for sample_id in member_ids:
            sample = session.get(VoiceCloneSample, sample_id)
            if not sample:
                raise RuntimeError('清理计划已过期，请重新预览')
            path = Path(_canonical_local_path(sample.audio_local_path))
            if (
                normalize_voice_clone_name(sample.display_name) != group.name_key
                or not path.is_file()
                or hash_voice_clone_audio(path).content_sha256 != group.content_sha256
            ):
                raise RuntimeError('清理计划内容已变化，请重新预览')

    for group in plan.name_cap_groups:
        expected_hashes = dict(group.sample_hashes)
        member_ids = (*group.retained_sample_ids, *group.removed_sample_ids)
        for sample_id in member_ids:
            sample = session.get(VoiceCloneSample, sample_id)
            if not sample:
                raise RuntimeError('清理计划已过期，请重新预览')
            if normalize_voice_clone_name(sample.display_name) != group.name_key:
                raise RuntimeError('清理计划内容已变化，请重新预览')
            path = Path(_canonical_local_path(sample.audio_local_path))
            expected_hash = expected_hashes.get(sample_id, '')
            if expected_hash:
                if (
                    not path.is_file()
                    or hash_voice_clone_audio(path).content_sha256 != expected_hash
                ):
                    raise RuntimeError('清理计划内容已变化，请重新预览')
            elif path.is_file():
                raise RuntimeError('清理计划内容已变化，请重新预览')


def _archive_scheduled_files(
    session: Session,
    plan: VoiceCloneCleanupPlan,
    archive_dir: Path,
) -> int:
    archive_path = Path(archive_dir)
    archive_path.mkdir(parents=True, exist_ok=False)
    reasons: dict[str, str] = {}
    for group in plan.duplicate_groups:
        reasons.update(
            (sample_id, 'exact_duplicate')
            for sample_id in group.duplicate_sample_ids
        )
    for group in plan.name_cap_groups:
        reasons.update(
            (sample_id, 'name_cap')
            for sample_id in group.removed_sample_ids
        )

    archived = 0
    entries: list[dict[str, object]] = []
    for sample_id, reason in reasons.items():
        sample = session.get(VoiceCloneSample, sample_id)
        if not sample:
            raise RuntimeError('清理计划已过期，请重新预览')
        source_path = Path(_canonical_local_path(sample.audio_local_path))
        entry: dict[str, object] = {
            'sample_id': sample.id,
            'display_name': sample.display_name,
            'name_key': normalize_voice_clone_name(sample.display_name),
            'project_id': sample.project_id,
            'reason': reason,
            'source_path': str(source_path),
            'source_exists': False,
        }
        try:
            source_path = _guarded_voice_clone_local_path(source_path)
        except RuntimeError:
            entry['archive_error'] = 'unsafe_local_path'
            entries.append(entry)
            continue
        entry['source_exists'] = source_path.is_file()
        if entry['source_exists']:
            archive_name = f'{sample.id}-{source_path.name}'
            target_path = archive_path / archive_name
            shutil.copy2(source_path, target_path)
            entry['archive_path'] = str(target_path)
            entry['content_sha256'] = hash_voice_clone_audio(target_path).content_sha256
            entry['file_size_bytes'] = target_path.stat().st_size
            archived += 1
        entries.append(entry)
    manifest = {
        'created_at': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        'scheduled_sample_count': len(reasons),
        'archived_file_count': archived,
        'entries': entries,
    }
    (archive_path / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return archived


def _retention_rank(
    sample: VoiceCloneSample,
    profile_states: list[str],
) -> tuple[int, int, float, float, str]:
    readiness_rank = {
        'ready': 2,
        'warning': 1,
        'rejected': 0,
    }.get(str(sample.clone_readiness_status or '').lower(), 1)
    return (
        int(any(status in {'ready', 'creating'} for status in profile_states)),
        readiness_rank,
        _timestamp(sample.last_used_at),
        _timestamp(sample.created_at),
        sample.id,
    )


def _estimate_releasable_bytes(
    canonical_path: Path,
    duplicate_paths: list[Path],
) -> int:
    try:
        canonical_stat = canonical_path.stat()
        canonical_identity = (canonical_stat.st_dev, canonical_stat.st_ino)
    except OSError:
        canonical_identity = None
    seen_identities = {canonical_identity} if canonical_identity else set()
    total = 0
    for path in duplicate_paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        identity = (stat.st_dev, stat.st_ino)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        total += stat.st_size
    return total


def _estimate_paths_bytes(paths: list[Path]) -> int:
    total = 0
    seen_identities: set[tuple[int, int]] = set()
    for path in paths:
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        identity = (stat.st_dev, stat.st_ino)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        total += stat.st_size
    return total


def _timestamp(value: Optional[datetime]) -> float:
    return value.timestamp() if value else 0.0
