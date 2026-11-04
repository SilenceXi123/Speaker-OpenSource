"""Bounded voice-data expiry; asset deletion is committed through the durable queue."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, exists, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.models.voice_clone_identity import VoiceCloneIdentity as Identity
from app.models.voice_clone_identity_review import VoiceCloneIdentityReview as Review
from app.models.voice_clone_profile import VoiceCloneProfile as Profile
from app.models.voice_clone_sample import VoiceCloneSample as Sample
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord as Record
from app.models.voiceprint_match import VoiceprintMatch as Match
from app.models.voiceprint_sample import VoiceprintSample as Voiceprint
from app.services.oss_storage import owned_oss_object_key
from app.services.voice_clone_cleanup import (
    VoiceCloneDeletionTarget, _canonical_local_path, _enqueue_deletion_targets,
    _profile_deletion_targets,
)
from app.services.voice_retention import normalize_utc, utc_now, voice_retention_cutoff


VOICE_IN_FLIGHT_GRACE = timedelta(minutes=15)
MAX_BATCH_SIZE = 100


@dataclass(frozen=True)
class VoiceRetentionCleanupReport:
    deleted_by_table: dict[str, int] = field(default_factory=dict)
    detached_record_count: int = 0

    @property
    def deleted_count(self) -> int:
        return sum(self.deleted_by_table.values())


def _queue_assets(session, rows, local_fields=(), public_fields=(), *, profiles=False):
    targets = {
        target.resource_key: target
        for target in (_profile_deletion_targets(rows) if profiles else ())
    }
    for row in rows:
        project_id = getattr(row, 'project_id', None)
        for column in local_fields:
            path = _canonical_local_path(getattr(row, column))
            if path:
                key = f'local:{path}'
                targets[key] = VoiceCloneDeletionTarget(project_id, 'local', key, local_path=path)
        for column in public_fields:
            object_key = owned_oss_object_key(getattr(row, column))
            if object_key:
                key = f'oss:{object_key}'
                targets[key] = VoiceCloneDeletionTarget(project_id, 'oss', key, oss_object_key=object_key)
    _enqueue_deletion_targets(session, targets)
    session.flush()


def expire_voice_data(
    session: Session, *, now: datetime | None = None, batch_size: int = MAX_BATCH_SIZE,
) -> VoiceRetentionCleanupReport:
    """Own a short SQLite transaction, without loading embeddings or calling providers.

    Each table and the record-detachment phase are capped independently. Parents
    remain until the next tick if their dependent rows exhaust the current batch.
    """
    reference_now = normalize_utc(now or utc_now())
    cutoff = voice_retention_cutoff(reference_now)
    grace_cutoff = reference_now - VOICE_IN_FLIGHT_GRACE
    limit = max(1, min(MAX_BATCH_SIZE, int(batch_size)))
    expired_identities = select(Identity.id).where(Identity.created_at <= cutoff)
    sample_expired = or_(Sample.created_at <= cutoff, Sample.identity_id.in_(expired_identities))
    expired_samples = select(Sample.id).where(sample_expired)
    expired_voiceprints = select(Voiceprint.id).where(Voiceprint.created_at <= cutoff)
    profile_expired = or_(
        Profile.created_at <= cutoff, Profile.identity_id.in_(expired_identities),
        Profile.source_clone_sample_id.in_(expired_samples),
        Profile.source_sample_id.in_(expired_voiceprints),
    )
    active_profile = or_(
        and_(Profile.status == 'creating', Profile.created_at > grace_cutoff),
        exists(select(1).where(
            Record.voice_clone_profile_id == Profile.id,
            Record.status == 'processing', Record.created_at > grace_cutoff,
        ).correlate(Profile)),
    )
    deleted_counts = {}

    def rows(model, condition, *columns):
        return list(session.execute(select(model.id, *columns).where(condition)
                    .order_by(model.created_at, model.id).limit(limit)))

    def remove(model, selected):
        if selected:
            session.execute(delete(model).where(model.id.in_([row.id for row in selected]))
                            .execution_options(synchronize_session=False))
        deleted_counts[model.__tablename__] = len(selected)

    try:
        # Serialize the dependency check and queue writes against concurrent API
        # commits. Network/file deletion only runs after this transaction commits.
        session.execute(text('BEGIN IMMEDIATE'))
        expired_records = rows(Record, Record.created_at <= cutoff,
                               Record.project_id, Record.output_audio_local_path,
                               Record.output_audio_public_url)
        _queue_assets(session, expired_records, ('output_audio_local_path',), ('output_audio_public_url',))
        for row in expired_records:
            if row.output_audio_local_path:
                session.execute(update(Profile).where(
                    Profile.preview_audio_local_path == row.output_audio_local_path,
                ).values(preview_audio_local_path=None, preview_text=None)
                  .execution_options(synchronize_session=False))
        remove(Record, expired_records)

        # Detach only a bounded number of records before deleting their profile.
        # Newer generated output keeps its own 72-hour lifetime and snapshots.
        detached = list(session.execute(select(
            Record.id, Profile.voice_name, Profile.provider, Profile.target_model,
        ).join(Profile, Record.voice_clone_profile_id == Profile.id)
          .where(profile_expired, ~active_profile)
          .order_by(Record.created_at, Record.id).limit(limit)))
        for row in detached:
            session.execute(update(Record).where(Record.id == row.id).values(
                voice_clone_profile_id=None,
                voice_name_snapshot=func.coalesce(Record.voice_name_snapshot, row.voice_name),
                provider_snapshot=func.coalesce(Record.provider_snapshot, row.provider),
                target_model_snapshot=func.coalesce(Record.target_model_snapshot, row.target_model),
            ).execution_options(synchronize_session=False))
            session.execute(update(Record).where(Record.id == row.id, Record.status == 'processing').values(
                status='failed', provider_message='声音已到期，请重新创建声音后生成',
            ).execution_options(synchronize_session=False))

        profiles = rows(Profile, and_(profile_expired, ~active_profile, ~exists(
            select(1).where(Record.voice_clone_profile_id == Profile.id).correlate(Profile),
        )), Profile.project_id, Profile.provider, Profile.target_model, Profile.voice_id,
                        Profile.preview_audio_local_path, Profile.source_audio_local_path)
        _queue_assets(session, profiles, ('source_audio_local_path',), profiles=True)
        remove(Profile, profiles)

        reviews = rows(Review, or_(
            Review.created_at <= cutoff, Review.candidate_identity_id.in_(expired_identities),
            Review.sample_id.in_(expired_samples),
        ))
        remove(Review, reviews)

        samples = rows(Sample, and_(sample_expired,
            ~exists(select(1).where(Profile.source_clone_sample_id == Sample.id).correlate(Sample)),
            ~exists(select(1).where(Review.sample_id == Sample.id).correlate(Sample)),
        ), Sample.project_id, Sample.audio_local_path, Sample.audio_public_url)
        _queue_assets(session, samples, ('audio_local_path',), ('audio_public_url',))
        remove(Sample, samples)

        voiceprints = rows(Voiceprint, and_(Voiceprint.created_at <= cutoff,
            ~exists(select(1).where(Profile.source_sample_id == Voiceprint.id).correlate(Voiceprint)),
        ), Voiceprint.audio_local_path, Voiceprint.clone_audio_local_path,
                           Voiceprint.audio_public_url, Voiceprint.clone_audio_public_url)
        _queue_assets(session, voiceprints, ('audio_local_path', 'clone_audio_local_path'),
                      ('audio_public_url', 'clone_audio_public_url'))
        if voiceprints:
            session.execute(update(Voiceprint).where(Voiceprint.duplicate_of_sample_id.in_(
                [row.id for row in voiceprints],
            )).values(duplicate_of_sample_id=None).execution_options(synchronize_session=False))
        remove(Voiceprint, voiceprints)
        remove(Match, rows(Match, Match.created_at <= cutoff))

        identities = rows(Identity, and_(Identity.created_at <= cutoff,
            ~exists(select(1).where(Profile.identity_id == Identity.id).correlate(Identity)),
            ~exists(select(1).where(Sample.identity_id == Identity.id).correlate(Identity)),
            ~exists(select(1).where(Review.candidate_identity_id == Identity.id).correlate(Identity)),
        ))
        remove(Identity, identities)
        session.commit()
        session.expire_all()
    except Exception:
        session.rollback()
        raise
    return VoiceRetentionCleanupReport(deleted_counts, len(detached))
