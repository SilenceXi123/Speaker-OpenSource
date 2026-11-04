from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.voiceprint_sample import VoiceprintSample
from app.services.voice_retention import voice_retention_cutoff, voice_is_expired
from app.services.local_files import remove_local_file


MAX_VOICEPRINT_SAMPLES_PER_PERSON = 5
AUTO_CONFIRM_SIMILARITY_THRESHOLD = 0.88


@dataclass(frozen=True)
class VoiceprintRetentionResult:
    removed_sample_ids: list[str] = field(default_factory=list)
    removed_paths: list[Path] = field(default_factory=list)

    @property
    def removed_count(self) -> int:
        return len(self.removed_sample_ids)


def auto_confirm_consistent_samples(
    session: Session,
    sample: VoiceprintSample,
) -> int:
    """Passively confirm matching evidence without trusting a name alone.

    A new automatic candidate is confirmed when it matches either an already
    confirmed sample or an automatic candidate collected from a different job.
    The second case confirms both independent samples so users do not need to
    maintain the voiceprint library manually.
    """

    if (sample.created_at and voice_is_expired(sample.created_at)) or sample.embedding_status != 'ready' or not sample.embedding:
        return 0

    references = list(
        session.scalars(
            select(VoiceprintSample).where(
                VoiceprintSample.person_id == sample.person_id,
                VoiceprintSample.created_at > voice_retention_cutoff(),
                VoiceprintSample.id != sample.id,
                VoiceprintSample.embedding_status == 'ready',
            )
        )
    )
    corroborating: list[tuple[VoiceprintSample, float]] = []
    for reference in references:
        if not reference.embedding:
            continue
        similarity = _cosine_similarity(sample.embedding, reference.embedding)
        if similarity < AUTO_CONFIRM_SIMILARITY_THRESHOLD:
            continue
        if reference.review_status == 'confirmed':
            corroborating.append((reference, similarity))
            continue
        if (
            sample.source_job_id
            and reference.source_job_id
            and sample.source_job_id != reference.source_job_id
        ):
            corroborating.append((reference, similarity))

    if not corroborating:
        return 0

    promoted_count = 0
    best_similarity = max(similarity for _, similarity in corroborating)
    for item in [sample, *(reference for reference, _ in corroborating)]:
        if item.review_status == 'confirmed':
            continue
        item.review_status = 'confirmed'
        if item.source_type == 'auto_candidate':
            item.source_type = 'auto_confirmed'
        item.confidence_score = max(float(item.confidence_score or 0), best_similarity)
        item.provider_message = _append_message(
            item.provider_message,
            f'跨任务声纹一致，已自动确认（相似度 {best_similarity:.3f}）',
        )
        promoted_count += 1
    session.flush()
    return promoted_count


def enforce_voiceprint_sample_limit(
    session: Session,
    person_id: str,
    *,
    maximum: int = MAX_VOICEPRINT_SAMPLES_PER_PERSON,
) -> VoiceprintRetentionResult:
    samples = list(
        session.scalars(
            select(VoiceprintSample).where(VoiceprintSample.person_id == person_id, VoiceprintSample.created_at > voice_retention_cutoff())
        )
    )
    overflow = max(0, len(samples) - max(1, int(maximum)))
    if overflow <= 0:
        return VoiceprintRetentionResult()

    ranked = sorted(samples, key=_retention_rank, reverse=True)
    removed = ranked[-overflow:]
    removed_paths = _unique_sample_paths(removed)
    for sample in removed:
        _detach_sample_references(session, sample.id)
        session.delete(sample)
    session.flush()
    return VoiceprintRetentionResult(
        removed_sample_ids=[sample.id for sample in removed],
        removed_paths=removed_paths,
    )


def remove_pruned_voiceprint_files(session: Session, result: VoiceprintRetentionResult) -> None:
    for path in result.removed_paths:
        path_value = str(path)
        still_referenced = session.scalar(
            select(VoiceprintSample.id).where(
                or_(
                    VoiceprintSample.audio_local_path == path_value,
                    VoiceprintSample.clone_audio_local_path == path_value,
                )
            ).limit(1)
        )
        if not still_referenced:
            remove_local_file(path)


def _retention_rank(sample: VoiceprintSample) -> tuple:
    review_rank = 2 if sample.review_status == 'confirmed' else 1
    source_rank = {
        'manual_upload': 3,
        'auto_confirmed': 2,
        'auto_candidate': 1,
    }.get(str(sample.source_type or ''), 0)
    embedding_rank = 1 if sample.embedding_status == 'ready' and sample.embedding else 0
    quality = float(sample.quality_score or 0)
    confidence = float(sample.confidence_score or 0)
    duration_ms = int(sample.duration_ms or 0)
    duration_rank = 1 if 2_000 <= duration_ms <= 15_000 else 0
    created_at = sample.created_at or datetime.min
    return (
        review_rank,
        source_rank,
        embedding_rank,
        quality,
        confidence,
        duration_rank,
        created_at,
        sample.id,
    )


def _detach_sample_references(session: Session, sample_id: str) -> None:
    # Voice-clone profiles retain their copied source path, so clearing the
    # optional source row reference keeps them usable while preserving FK
    # integrity when an older voiceprint sample is pruned.
    from app.models.voice_clone_profile import VoiceCloneProfile

    for profile in session.scalars(
        select(VoiceCloneProfile).where(VoiceCloneProfile.source_sample_id == sample_id)
    ):
        profile.source_sample_id = None
    for duplicate in session.scalars(
        select(VoiceprintSample).where(VoiceprintSample.duplicate_of_sample_id == sample_id)
    ):
        duplicate.duplicate_of_sample_id = None


def _unique_sample_paths(samples: list[VoiceprintSample]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for sample in samples:
        for value in (sample.audio_local_path, sample.clone_audio_local_path):
            normalized = str(value or '').strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            paths.append(Path(normalized))
    return paths


def _append_message(existing: str | None, message: str) -> str:
    normalized = str(existing or '').strip()
    if not normalized:
        return message
    if message in normalized:
        return normalized
    return f'{normalized}；{message}'


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
