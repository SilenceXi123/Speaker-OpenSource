from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.voice_clone_identity import VoiceCloneIdentity
from app.models.voice_clone_identity_review import VoiceCloneIdentityReview
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_sample import VoiceCloneSample
from app.services.voice_clone_identity import normalize_voice_clone_name, current_voice_clone_sample_clause
from app.services import voice_retention


def list_pending_identity_reviews(session: Session) -> list[dict]:
    reviews = session.scalars(
        select(VoiceCloneIdentityReview)
        .where(VoiceCloneIdentityReview.status == 'pending', VoiceCloneIdentityReview.created_at > voice_retention.voice_retention_cutoff())
        .order_by(VoiceCloneIdentityReview.created_at.asc())
    ).all()
    rows = []
    # ponytail: pending review queues stay small; batch joins can replace this if list latency becomes measurable.
    for review in reviews:
        sample = session.get(VoiceCloneSample, review.sample_id) if review.sample_id else None
        candidate = session.get(VoiceCloneIdentity, review.candidate_identity_id)
        if (sample is None or candidate is None or voice_retention.voice_is_expired(sample.created_at)
                or voice_retention.voice_is_expired(candidate.created_at)):
            continue
        candidate_sample = session.scalar(
            select(VoiceCloneSample)
            .where(
                VoiceCloneSample.identity_id == candidate.id,
                VoiceCloneSample.id != sample.id,
                current_voice_clone_sample_clause(),
            )
            .order_by(VoiceCloneSample.created_at.asc())
        )
        rows.append({
            'id': review.id,
            'sample_id': review.sample_id,
            'submitted_name': review.submitted_name,
            'original_filename': sample.original_filename,
            'candidate_identity_id': candidate.id,
            'candidate_display_name': candidate.display_name,
            'candidate_aliases': list(candidate.aliases or []),
            'match_score': review.match_score,
            'embedding_model_id': sample.embedding_model_id or candidate.embedding_model_id,
            'created_at': review.created_at.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z'),
            'expires_at': voice_retention.voice_expires_at(min(review.created_at, sample.created_at, candidate.created_at)).replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z'),
            'sample_audio_url': f'/api/v1/voice-cloning/samples/{sample.id}/audio',
            'candidate_sample_audio_url': (
                f'/api/v1/voice-cloning/samples/{candidate_sample.id}/audio'
                if candidate_sample else None
            ),
        })
    return rows


def resolve_identity_review(
    session: Session,
    review_id: str,
    action: str,
    username: str,
    reason: str | None,
) -> VoiceCloneIdentityReview:
    normalized_action = str(action or '').strip()
    normalized_reason = str(reason or '').strip()
    if normalized_action not in {'confirm', 'release'}:
        raise ValueError('审核动作必须是 confirm 或 release')
    if normalized_action == 'release' and not normalized_reason:
        raise ValueError('判定不同人时必须填写原因')

    try:
        review = session.scalar(
            select(VoiceCloneIdentityReview)
            .where(VoiceCloneIdentityReview.id == review_id)
            .with_for_update()
        )
        if review is None or voice_retention.voice_is_expired(review.created_at):
            raise LookupError('声音身份审核不存在')
        candidate = session.get(VoiceCloneIdentity, review.candidate_identity_id)
        sample = session.scalar(select(VoiceCloneSample).where(VoiceCloneSample.id == review.sample_id, current_voice_clone_sample_clause()))
        if sample is None or candidate is None or voice_retention.voice_is_expired(candidate.created_at):
            raise LookupError('声音身份审核关联数据已过期或不存在')
        if review.status != 'pending':
            return review

        if normalized_action == 'release':
            _release_false_match(session, review)

        review.status = 'confirmed' if normalized_action == 'confirm' else 'released'
        review.resolved_by = str(username or '').strip()
        review.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        review.resolution_reason = normalized_reason or None
        session.commit()
        session.refresh(review)
        return review
    except Exception:
        session.rollback()
        raise


def _release_false_match(session: Session, review: VoiceCloneIdentityReview) -> None:
    sample = session.get(VoiceCloneSample, review.sample_id) if review.sample_id else None
    candidate = session.get(VoiceCloneIdentity, review.candidate_identity_id)
    if sample is None or candidate is None:
        raise RuntimeError('审核关联的声音身份或样本不存在')
    if sample.identity_id != candidate.id:
        raise RuntimeError('审核样本已不再属于候选声音身份')
    if not sample.embedding:
        raise RuntimeError('审核样本缺少声纹 embedding，无法释放')

    alias = normalize_voice_clone_name(review.submitted_name)
    identity = VoiceCloneIdentity(
        display_name=review.submitted_name,
        aliases=[alias] if alias else [],
        reference_embedding=list(sample.embedding),
        embedding_model_id=sample.embedding_model_id,
        created_at=min(sample.created_at, candidate.created_at),
    )
    session.add(identity)
    session.flush()
    sample.identity_id = identity.id

    for profile in session.scalars(
        select(VoiceCloneProfile).where(VoiceCloneProfile.source_clone_sample_id == sample.id,
            VoiceCloneProfile.created_at > voice_retention.voice_retention_cutoff())
    ):
        profile.identity_id = identity.id

    alias_still_used = any(
        normalize_voice_clone_name(other.name_key or other.display_name) == alias
        for other in session.scalars(
            select(VoiceCloneSample).where(
                VoiceCloneSample.identity_id == candidate.id,
                VoiceCloneSample.id != sample.id,
                current_voice_clone_sample_clause(),
            )
        )
    )
    if alias and not alias_still_used:
        candidate.aliases = [
            existing
            for existing in list(candidate.aliases or [])
            if normalize_voice_clone_name(existing) != alias
        ]
