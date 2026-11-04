from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
import unicodedata
from typing import Literal

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.voice_clone_identity import VoiceCloneIdentity
from app.models.voice_clone_sample import VoiceCloneSample
from app.services.voiceprint_provider import extract_voiceprint_embedding
from app.services import voice_retention


_WHITESPACE_PATTERN = re.compile(r'\s+')


def current_voice_clone_sample_clause(sample=VoiceCloneSample, *, cutoff=None):
    cutoff = cutoff or voice_retention.voice_retention_cutoff()
    return and_(
        sample.created_at > cutoff,
        or_(sample.identity_id.is_(None), sample.identity_id == '', exists(
            select(1).select_from(VoiceCloneIdentity).where(
                VoiceCloneIdentity.id == sample.identity_id,
                VoiceCloneIdentity.created_at > cutoff,
            ).correlate(sample)
        )),
    )


@dataclass(frozen=True)
class VoiceCloneAudioIdentity:
    content_sha256: str
    file_size_bytes: int


@dataclass(frozen=True)
class VoiceCloneIdentityResolution:
    identity: VoiceCloneIdentity
    match_type: Literal['exact_audio', 'speaker_embedding', 'new_identity']
    match_score: float | None = None
    exact_sample: VoiceCloneSample | None = None
    embedding: list[float] | None = None
    embedding_status: str = 'unavailable'
    embedding_provider_name: str | None = None
    embedding_model_id: str | None = None
    embedding_message: str | None = None


def hash_voice_clone_audio(path: Path) -> VoiceCloneAudioIdentity:
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
            size += len(chunk)
    return VoiceCloneAudioIdentity(
        content_sha256=digest.hexdigest(),
        file_size_bytes=size,
    )


def normalize_voice_clone_name(value: str) -> str:
    normalized = unicodedata.normalize('NFKC', str(value or ''))
    return _WHITESPACE_PATTERN.sub(' ', normalized).strip().casefold()


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


def resolve_voice_clone_identity(
    session: Session,
    *,
    display_name: str,
    content_sha256: str,
    audio_path: Path,
    threshold: float | None = None,
) -> VoiceCloneIdentityResolution:
    exact_sample = session.scalar(
        select(VoiceCloneSample)
        .where(VoiceCloneSample.content_sha256 == content_sha256, current_voice_clone_sample_clause())
        .order_by(VoiceCloneSample.created_at.asc())
    )
    if exact_sample is not None:
        identity = session.get(VoiceCloneIdentity, exact_sample.identity_id) if exact_sample.identity_id else None
        if identity is None:
            identity = _create_identity(
                session,
                display_name=exact_sample.display_name,
                embedding=exact_sample.embedding,
                embedding_model_id=exact_sample.embedding_model_id,
                created_at=exact_sample.created_at,
            )
            exact_sample.identity_id = identity.id
        _append_alias(identity, display_name)
        return VoiceCloneIdentityResolution(
            identity=identity,
            match_type='exact_audio',
            exact_sample=exact_sample,
            embedding=exact_sample.embedding,
            embedding_status=exact_sample.embedding_status or 'unavailable',
            embedding_provider_name=exact_sample.embedding_provider_name,
            embedding_model_id=exact_sample.embedding_model_id,
            embedding_message=exact_sample.embedding_message,
        )

    try:
        embedding_result = extract_voiceprint_embedding(audio_path)
    except Exception as error:
        raise RuntimeError(f'声纹检查失败：{error}') from error

    best_identity = None
    best_score = -math.inf
    for identity in session.scalars(
        select(VoiceCloneIdentity).where(
            VoiceCloneIdentity.embedding_model_id == embedding_result.provider_model_id,
            VoiceCloneIdentity.created_at > voice_retention.voice_retention_cutoff(),
        )
    ):
        score = cosine_similarity(identity.reference_embedding or [], embedding_result.embedding)
        if score > best_score:
            best_identity = identity
            best_score = score

    if best_identity is not None and best_score >= (
        settings.voice_clone_identity_match_threshold if threshold is None else threshold
    ):
        _append_alias(best_identity, display_name)
        return _embedding_resolution(
            best_identity,
            match_type='speaker_embedding',
            match_score=best_score,
            embedding=embedding_result.embedding,
            provider_name=embedding_result.provider_name,
            model_id=embedding_result.provider_model_id,
            message=embedding_result.provider_message,
        )

    identity = _create_identity(
        session,
        display_name=display_name,
        embedding=embedding_result.embedding,
        embedding_model_id=embedding_result.provider_model_id,
    )
    return _embedding_resolution(
        identity,
        match_type='new_identity',
        embedding=embedding_result.embedding,
        provider_name=embedding_result.provider_name,
        model_id=embedding_result.provider_model_id,
        message=embedding_result.provider_message,
    )


def _create_identity(
    session: Session,
    *,
    display_name: str,
    embedding: list[float] | None,
    embedding_model_id: str | None,
    created_at=None,
) -> VoiceCloneIdentity:
    identity = VoiceCloneIdentity(
        display_name=display_name,
        aliases=[normalize_voice_clone_name(display_name)] if normalize_voice_clone_name(display_name) else [],
        reference_embedding=embedding,
        embedding_model_id=embedding_model_id,
        created_at=created_at or voice_retention.utc_now(),
    )
    session.add(identity)
    session.flush()
    return identity


def _append_alias(identity: VoiceCloneIdentity, display_name: str) -> None:
    alias = normalize_voice_clone_name(display_name)
    aliases = list(identity.aliases or [])
    if alias and alias not in {normalize_voice_clone_name(item) for item in aliases}:
        identity.aliases = [*aliases, alias]


def _embedding_resolution(
    identity: VoiceCloneIdentity,
    *,
    match_type: Literal['speaker_embedding', 'new_identity'],
    embedding: list[float],
    provider_name: str,
    model_id: str,
    message: str,
    match_score: float | None = None,
) -> VoiceCloneIdentityResolution:
    return VoiceCloneIdentityResolution(
        identity=identity,
        match_type=match_type,
        match_score=match_score,
        embedding=embedding,
        embedding_status='ready',
        embedding_provider_name=provider_name,
        embedding_model_id=model_id,
        embedding_message=message,
    )
