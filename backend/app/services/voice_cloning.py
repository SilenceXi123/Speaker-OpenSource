from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import mimetypes
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional
from uuid import uuid4

import httpx
from sqlalchemy import and_, exists, func, or_, select, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from app.core.config import settings
from app.services.qwen_audio_next_voice_clone import NEXT_MODEL, next_endpoint, synthesize_qwen_audio_next_voice
from app.models.person import Person
from app.models.project import Project
from app.models.voice_clone_identity_review import VoiceCloneIdentityReview
from app.models.voice_clone_identity import VoiceCloneIdentity
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_sample import VoiceCloneSample
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
from app.models.voiceprint_sample import VoiceprintSample
from app.schemas.voice_cloning import (
    VoiceClonePreviewRequest,
    VoiceCloneProfileCreateRequest,
    VoiceCloneProviderOption,
    VoiceCloneModelOption,
    VoiceCloneProviderStatusResponse,
    VoiceCloneProfileResponse,
    VoiceCloneSampleResponse,
    VoiceCloneSampleSaveResponse,
    VoiceCloneSynthesisRecordResponse,
)
from app.schemas.provider_cost import ProviderCallContext
from app.services.local_files import copy_local_file_to_library, remove_local_file
from app.services.media import (
    get_media_duration_seconds,
    normalize_voice_clone_generated_audio,
    prepare_voice_clone_reference_audio,
)
from app.services.cosyvoice_voice_clone import (
    CosyVoiceCloneError,
    create_cosyvoice_voice,
    delete_cosyvoice_voice,
    supports_cosyvoice_instruction,
    query_cosyvoice_voice,
    weighted_instruction_length,
    COSYVOICE_INSTRUCTION_LIMIT,
    synthesize_cosyvoice_voice,
)
from app.services.qwen_voice_clone import (
    QwenVoiceCloneError,
    create_qwen_voice,
    delete_qwen_voice,
    synthesize_qwen_voice,
)
from app.services.qwen_audio_voice_clone import (
    QwenAudioVoiceCloneError,
    QWEN_AUDIO_MODELS,
    QWEN_AUDIO_INSTRUCTION_LIMIT,
    create_qwen_audio_voice,
    query_qwen_audio_voice,
    delete_qwen_audio_voice,
    synthesize_qwen_audio_voice,
)
from app.services.projects import get_project
from app.services.oss_storage import delete_oss_object, owned_oss_object_key
from app.services.speaker_memory import build_program_key, normalize_program_name
from app.services.voiceprints import build_sample_response
from app.services.voice_sample_analysis import VoiceCloneReadinessResult, ensure_sample_public_url
from app.services.voice_clone_identity import (
    current_voice_clone_sample_clause,
    hash_voice_clone_audio,
    normalize_voice_clone_name,
    resolve_voice_clone_identity,
)
from app.services import voice_retention


STRICT_TEXT_INSTRUCTION = '只朗读输入文本，不续写，不添加样本里的其他话。'
SUPPORTED_AUDIO_SUFFIXES = {'.wav', '.mp3', '.m4a', '.mp4', '.aac', '.ogg', '.flac'}
MAX_VOICE_CLONE_SAMPLES_PER_IDENTITY = 3
RETIRED_QWEN_MODEL = 'qwen3-tts-vc-2026-01-22'
RETIRED_QWEN_MESSAGE = 'Qwen3-TTS 声音复刻模型已下线，请使用原参考样本创建 CosyVoice 3.5 Plus 或 Qwen-Audio 音色；旧音色不能直接切换模型'


class VoiceAssetExpiredError(RuntimeError):
    def __init__(self):
        super().__init__('声音数据已超过 72 小时或已被清理，请重新上传')


def current_voice_clone_profile_clause(profile=VoiceCloneProfile, *, cutoff=None):
    cutoff = cutoff or voice_retention.voice_retention_cutoff()
    return and_(
        profile.created_at > cutoff,
        or_(profile.identity_id.is_(None), profile.identity_id == '', exists(
            select(1).where(VoiceCloneIdentity.id == profile.identity_id,
                           VoiceCloneIdentity.created_at > cutoff).correlate(profile))),
        or_(profile.source_clone_sample_id.is_(None), exists(
            select(1).where(VoiceCloneSample.id == profile.source_clone_sample_id,
                           current_voice_clone_sample_clause(cutoff=cutoff)).correlate(profile))),
        or_(profile.source_sample_id.is_(None), exists(
            select(1).where(VoiceprintSample.id == profile.source_sample_id,
                           VoiceprintSample.created_at > cutoff).correlate(profile))),
    )


def voice_clone_asset_expires_at(asset, session: Session | None = None) -> datetime:
    session = session or object_session(asset)
    created = [asset.created_at]
    if session is not None:
        def loaded(model, ident):
            return session.identity_map.get(session.identity_key(model, ident)) or session.get(model, ident)
        identity_id = getattr(asset, 'identity_id', None)
        if identity_id and (identity := loaded(VoiceCloneIdentity, identity_id)) is not None:
            created.append(identity.created_at)
        if isinstance(asset, VoiceCloneProfile):
            source = (loaded(VoiceCloneSample, asset.source_clone_sample_id) if asset.source_clone_sample_id
                      else loaded(VoiceprintSample, asset.source_sample_id) if asset.source_sample_id else None)
            if source is not None:
                created.append(source.created_at)
                if isinstance(source, VoiceCloneSample) and source.identity_id:
                    identity = loaded(VoiceCloneIdentity, source.identity_id)
                    if identity is not None:
                        created.append(identity.created_at)
    return voice_retention.voice_expires_at(min(map(voice_retention.normalize_utc, created))).replace(tzinfo=timezone.utc)


def _voice_clone_asset_expired(asset, session: Session | None = None) -> bool:
    return voice_retention.normalize_utc(voice_clone_asset_expires_at(asset, session)) <= voice_retention.utc_now()


def _begin_voice_result_write(session: Session) -> None:
    # Provider I/O must not keep a stale read snapshot or a database write lock.
    session.rollback()
    if session.get_bind().dialect.name == 'sqlite':
        session.execute(sql_text('BEGIN IMMEDIATE'))


def _enqueue_late_voice_assets(session: Session, project_id, *, provider=None, target_model=None,
                               voice_id=None, local_path=None, public_url=None) -> None:
    from app.services.voice_clone_cleanup import (
        VoiceCloneDeletionTarget, _canonical_local_path, _enqueue_deletion_targets,
    )
    targets = {}
    if voice_id:
        key = f'provider:{provider}:{target_model}:{voice_id}'
        targets[key] = VoiceCloneDeletionTarget(project_id, 'provider', key,
            provider=provider, target_model=target_model, voice_id=voice_id)
    path = _canonical_local_path(str(local_path)) if local_path else None
    if path:
        key = f'local:{path}'
        targets[key] = VoiceCloneDeletionTarget(project_id, 'local', key, local_path=path)
    oss_key = owned_oss_object_key(public_url) if public_url else None
    if oss_key:
        key = f'oss:{oss_key}'
        targets[key] = VoiceCloneDeletionTarget(project_id, 'oss', key, oss_object_key=oss_key)
    _enqueue_deletion_targets(session, targets)


def _profile_after_provider(session: Session, profile_id: str, project_id, provider: str,
                            target_model: str, voice_id=None) -> VoiceCloneProfile:
    _begin_voice_result_write(session)
    profile = get_voice_clone_profile(session, profile_id)
    if profile is None:
        _enqueue_late_voice_assets(session, project_id, provider=provider,
            target_model=target_model, voice_id=voice_id)
        session.commit()
        raise VoiceAssetExpiredError()
    return profile


def _processing_record_after_provider(session: Session, record_id: str, project_id, *,
                                      local_path=None, public_url=None) -> VoiceCloneSynthesisRecord:
    _begin_voice_result_write(session)
    record = get_voice_clone_synthesis_record(session, record_id)
    if (record is None or record.status != 'processing'
            or voice_retention.normalize_utc(record.created_at) <= voice_retention.utc_now() - timedelta(minutes=15)):
        _enqueue_late_voice_assets(session, project_id, local_path=local_path, public_url=public_url)
        if record is not None and record.status == 'processing':
            record.status = 'failed'
            record.provider_message = '生成超过 15 分钟，迟到结果已进入清理队列'
            record.voice_clone_profile_id = None
        session.commit()
        raise VoiceAssetExpiredError()
    return record


@dataclass(frozen=True)
class VoiceCloningProviderStatus:
    enabled: bool
    provider: str
    region: str
    target_model: str
    available: bool
    message: str


@dataclass(frozen=True)
class VoiceCloneSampleSaveResult:
    sample: VoiceCloneSample
    reused_existing: bool
    identity_id: str
    identity_display_name: str
    identity_aliases: list[str]
    match_type: str
    match_score: float | None
    sample_slots_remaining: int


@dataclass(frozen=True)
class VoiceCloneProfileCreateResult:
    profile: VoiceCloneProfile
    reused_existing: bool


class VoiceCloneProjectConflictError(RuntimeError):
    pass


class VoiceCloneSampleLimitError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        identity_id: str,
        existing_samples: list[dict[str, str]],
    ) -> None:
        super().__init__(message)
        self.identity_id = identity_id
        self.existing_samples = existing_samples

    def as_detail(self) -> dict[str, object]:
        return {
            'message': str(self),
            'identity_id': self.identity_id,
            'existing_samples': self.existing_samples,
        }


class VoiceCloneSampleReviewConflictError(RuntimeError):
    code = 'voice_clone_sample_pending_identity_review'

    def __init__(self, review_ids: list[str]) -> None:
        super().__init__('该样本有待处理的声音身份审核，暂不能删除')
        self.review_ids = review_ids

    def as_detail(self) -> dict[str, object]:
        return {
            'code': self.code,
            'message': str(self),
            'review_ids': self.review_ids,
        }


class VoiceCloneIdentityUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceCloneProfileAvailability:
    selectable: bool
    reason: str | None = None


class VoiceCloneUnavailableError(RuntimeError):
    code = 'voice_clone_profile_unavailable'


def _positive_audio_fact(value: object) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _nonempty_local_file_size(path_value: object) -> int | None:
    normalized_path = str(path_value or '').strip()
    if not normalized_path:
        return None
    try:
        path = Path(normalized_path)
        if not path.is_file():
            return None
        size = path.stat().st_size
    except OSError:
        return None
    return size if size > 0 else None


def _source_sample_has_trustworthy_local_facts(
    source: VoiceCloneSample | VoiceprintSample,
) -> bool:
    if isinstance(source, VoiceCloneSample):
        actual_size = _nonempty_local_file_size(source.audio_local_path)
        fingerprint = str(source.content_sha256 or '').strip().lower()
        return bool(
            actual_size
            and len(fingerprint) == 64
            and all(character in '0123456789abcdef' for character in fingerprint)
            and _positive_audio_fact(source.file_size_bytes)
            and int(source.file_size_bytes or 0) == actual_size
            and _positive_audio_fact(source.duration_ms)
            and _positive_audio_fact(source.sample_rate_hz)
            and _positive_audio_fact(source.channel_count)
        )

    clone_path = str(source.clone_audio_local_path or '').strip()
    local_path = clone_path or str(source.audio_local_path or '').strip()
    sample_rate_hz = (
        source.clone_sample_rate_hz
        if clone_path
        else source.sample_rate_hz
    )
    channel_count = (
        source.clone_channel_count
        if clone_path
        else source.channel_count
    )
    # Legacy V1 rows predate clone-sample hash/size columns. Their persisted
    # probe metadata plus a current nonempty local file is the compatible fact.
    return bool(
        _nonempty_local_file_size(local_path)
        and _positive_audio_fact(source.duration_ms)
        and _positive_audio_fact(sample_rate_hz)
        and _positive_audio_fact(channel_count)
    )


def _source_sample_is_available(
    source: VoiceCloneSample | VoiceprintSample,
) -> bool:
    return (
        not _voice_clone_asset_expired(source)
        and
        str(source.clone_readiness_status or '').lower() == 'ready'
        and _source_sample_has_trustworthy_local_facts(source)
    )


def get_voice_clone_profile_availability(
    session: Session,
    profile: VoiceCloneProfile,
) -> VoiceCloneProfileAvailability:
    if _voice_clone_asset_expired(profile, session):
        return VoiceCloneProfileAvailability(False, 'voice_asset_expired')
    if profile.status != 'ready' or not str(profile.voice_id or '').strip():
        return VoiceCloneProfileAvailability(False, 'profile_not_ready')
    if _normalize_provider(profile.provider) == 'qwen' and profile.target_model == RETIRED_QWEN_MODEL:
        # Retirement must not classify retained source assets as invalid cleanup targets.
        return VoiceCloneProfileAvailability(False, 'model_retired')
    provider = _normalize_provider(profile.provider)
    if not _get_model_option(provider, str(profile.target_model or '').strip()):
        return VoiceCloneProfileAvailability(False, 'unsupported_provider_model')
    source = (
        session.get(VoiceCloneSample, profile.source_clone_sample_id)
        if profile.source_clone_sample_id
        else session.get(VoiceprintSample, profile.source_sample_id)
        if profile.source_sample_id
        else None
    )
    if (
        not source
        or not _source_sample_is_available(source)
    ):
        return VoiceCloneProfileAvailability(False, 'source_sample_unavailable')
    return VoiceCloneProfileAvailability(True)


def _voice_clone_sample_name_key(sample: VoiceCloneSample) -> str:
    return (sample.name_key or normalize_voice_clone_name(sample.display_name)).strip()


def get_active_voice_clone_project(session: Session, project_id: str) -> Project:
    normalized_project_id = str(project_id or '').strip()
    project = get_project(session, normalized_project_id) if normalized_project_id else None
    if not project:
        raise LookupError('项目不存在')
    if project.status != 'active':
        raise VoiceCloneProjectConflictError('项目已归档')
    return project


def resolve_active_voice_clone_project_by_alias(
    session: Session,
    program_name: Optional[str],
) -> Optional[Project]:
    candidate_ids = _resolve_project_alias_ids(session, program_name)
    if len(candidate_ids) != 1:
        return None
    project = get_project(session, next(iter(candidate_ids)))
    if not project or project.status != 'active':
        return None
    return project


def get_voice_cloning_provider_status() -> VoiceCloneProviderStatusResponse:
    provider = 'qwen_audio_next'
    target_model = NEXT_MODEL
    models = [item for item in _build_model_options()
              if item.model in {NEXT_MODEL, 'cosyvoice-v3.5-flash'}]
    selected = next((item for item in models if item.provider == provider and item.model == target_model), None)
    return VoiceCloneProviderStatusResponse(
        enabled=bool(settings.voice_cloning_enabled),
        provider=provider,
        region=settings.voice_cloning_region,
        target_model=target_model,
        available=bool(selected and selected.available),
        message=(selected.unavailable_reason if selected else '当前配置的声音复刻模型未接通') or '声音复刻 provider 已就绪',
        providers=_build_provider_options(),
        models=models,
    )


def ensure_voice_cloning_enabled() -> None:
    if not settings.voice_cloning_enabled:
        raise RuntimeError('声音复刻功能未开启')


def list_clone_samples(
    session: Session,
    person_id: str,
    project_id: Optional[str] = None,
) -> list[VoiceprintSample]:
    if project_id:
        project = get_active_voice_clone_project(session, project_id)
        person = session.get(Person, person_id)
        if not person or not _person_belongs_to_project(session, person, project.id):
            return []
    return list(
        session.scalars(
            select(VoiceprintSample).where(VoiceprintSample.person_id == person_id, VoiceprintSample.created_at > voice_retention.voice_retention_cutoff()).order_by(VoiceprintSample.created_at.desc())
        )
    )


def get_voice_clone_person_for_project(
    session: Session,
    project_id: str,
    person_id: str,
) -> Optional[Person]:
    project = get_active_voice_clone_project(session, project_id)
    person = session.get(Person, person_id)
    if not person or not _person_belongs_to_project(session, person, project.id):
        return None
    return person


def get_voiceprint_sample_for_voice_clone_project(
    session: Session,
    project_id: str,
    sample_id: str,
) -> Optional[VoiceprintSample]:
    project = get_active_voice_clone_project(session, project_id)
    sample = session.get(VoiceprintSample, sample_id)
    person = session.get(Person, sample.person_id) if sample else None
    if not sample or _voice_clone_asset_expired(sample, session) or not person or not _person_belongs_to_project(session, person, project.id):
        return None
    return sample


def list_voice_clone_samples(session: Session, program_name: Optional[str] = None) -> list[VoiceCloneSample]:
    statement = select(VoiceCloneSample).where(current_voice_clone_sample_clause()).order_by(VoiceCloneSample.created_at.desc())
    normalized_program = normalize_program_name(program_name)
    if normalized_program:
        project = resolve_active_voice_clone_project_by_alias(session, normalized_program)
        if project:
            return _merge_voice_clone_entities(
                list_voice_clone_samples_for_project(session, project.id),
                list(session.scalars(
                    select(VoiceCloneSample)
                    .where(
                        VoiceCloneSample.project_id.is_(None),
                        VoiceCloneSample.program_key.is_(None),
                        current_voice_clone_sample_clause(),
                    )
                    .order_by(VoiceCloneSample.created_at.desc())
                )),
            )
        program_key = build_program_key(normalized_program)
        statement = statement.where(
            or_(
                VoiceCloneSample.program_key == program_key,
                VoiceCloneSample.program_name == normalized_program,
            )
        )
    return list(session.scalars(statement))


def get_voice_clone_sample(session: Session, sample_id: str) -> Optional[VoiceCloneSample]:
    return session.scalar(select(VoiceCloneSample).where(VoiceCloneSample.id == sample_id, current_voice_clone_sample_clause()))


def list_voice_clone_samples_for_project(
    session: Session,
    project_id: str,
    *,
    claim_legacy: bool = False,
) -> list[VoiceCloneSample]:
    project = get_active_voice_clone_project(session, project_id)
    candidates = list(session.scalars(
        select(VoiceCloneSample)
        .where(or_(
            VoiceCloneSample.project_id == project.id,
            VoiceCloneSample.project_id.is_(None),
        ))
        .where(current_voice_clone_sample_clause())
        .order_by(VoiceCloneSample.created_at.desc())
    ))
    return _filter_and_claim_project_entities(
        session,
        project,
        candidates,
        _voice_clone_sample_project_ids,
        claim_legacy=claim_legacy,
    )


def get_voice_clone_sample_for_project(
    session: Session,
    project_id: str,
    sample_id: str,
    *,
    claim_legacy: bool = False,
) -> Optional[VoiceCloneSample]:
    project = get_active_voice_clone_project(session, project_id)
    sample = get_voice_clone_sample(session, sample_id)
    if not sample:
        return None
    return _claim_project_entity(
        session,
        project,
        sample,
        _voice_clone_sample_project_ids,
        claim_legacy=claim_legacy,
    )


def save_voice_clone_sample(
    session: Session,
    display_name: str,
    audio_local_path: Path,
    original_filename: str,
    program_name: Optional[str] = None,
    project_id: Optional[str] = None,
) -> VoiceCloneSample:
    return find_or_create_voice_clone_sample(
        session=session,
        display_name=display_name,
        audio_local_path=audio_local_path,
        original_filename=original_filename,
        program_name=program_name,
        project_id=project_id,
    ).sample


def _voice_clone_sample_limit_error(
    session: Session,
    identity_id: str,
) -> VoiceCloneSampleLimitError:
    existing_samples = list(session.scalars(
        select(VoiceCloneSample)
        .where(VoiceCloneSample.identity_id == identity_id, current_voice_clone_sample_clause())
        .order_by(VoiceCloneSample.created_at.asc())
        .limit(MAX_VOICE_CLONE_SAMPLES_PER_IDENTITY)
    ))
    return VoiceCloneSampleLimitError(
        f'声音身份最多保留 {MAX_VOICE_CLONE_SAMPLES_PER_IDENTITY} 条声音复刻样本，'
        '请直接复用已有样本',
        identity_id=identity_id,
        existing_samples=[
            {
                'id': sample.id,
                'display_name': sample.display_name,
                'original_filename': sample.original_filename,
            }
            for sample in existing_samples
        ],
    )


def find_or_create_voice_clone_sample(
    session: Session,
    display_name: str,
    audio_local_path: Path,
    original_filename: str,
    program_name: Optional[str] = None,
    project_id: Optional[str] = None,
    persist_to_library: bool = False,
) -> VoiceCloneSampleSaveResult:
    ensure_voice_cloning_enabled()
    normalized_name = str(display_name or '').strip()
    if not normalized_name:
        raise RuntimeError('请先填写音色名称')
    normalized_program = normalize_program_name(program_name)
    project = (
        get_active_voice_clone_project(session, project_id)
        if str(project_id or '').strip()
        else resolve_active_voice_clone_project_by_alias(session, normalized_program)
    )
    snapshot_program_key, snapshot_program_name = _project_snapshots(project, normalized_program)
    audio_identity = hash_voice_clone_audio(audio_local_path)
    name_key = normalize_voice_clone_name(normalized_name)
    persisted_audio_path: Path | None = None
    new_oss_object_key: str | None = None
    resolved_identity_id: str | None = None
    try:
        try:
            resolution = resolve_voice_clone_identity(
                session,
                display_name=normalized_name,
                content_sha256=audio_identity.content_sha256,
                audio_path=audio_local_path,
            )
        except RuntimeError as error:
            raise VoiceCloneIdentityUnavailableError(str(error)) from error
        resolved_identity_id = resolution.identity.id
        session.flush()
        identity_sample_count = session.scalar(
            select(func.count())
            .select_from(VoiceCloneSample)
            .where(VoiceCloneSample.identity_id == resolution.identity.id, current_voice_clone_sample_clause())
        ) or 0
        if resolution.exact_sample is not None:
            session.commit()
            session.refresh(resolution.exact_sample)
            return VoiceCloneSampleSaveResult(
                sample=resolution.exact_sample,
                reused_existing=True,
                identity_id=resolution.identity.id,
                identity_display_name=resolution.identity.display_name,
                identity_aliases=list(resolution.identity.aliases or []),
                match_type=resolution.match_type,
                match_score=resolution.match_score,
                sample_slots_remaining=max(
                    0,
                    MAX_VOICE_CLONE_SAMPLES_PER_IDENTITY - identity_sample_count,
                ),
            )
        if identity_sample_count >= MAX_VOICE_CLONE_SAMPLES_PER_IDENTITY:
            raise _voice_clone_sample_limit_error(
                session,
                resolved_identity_id,
            )

        if persist_to_library:
            persisted_audio_path = copy_local_file_to_library(
                source_path=audio_local_path,
                target_dir=settings.voice_clone_library_dir_path,
                original_name=f'{Path(original_filename).stem}-clone.wav',
            )
        stored_audio_path = persisted_audio_path or audio_local_path
        duration_ms = int(get_media_duration_seconds(stored_audio_path) * 1000) or None
        sample_rate_hz = max(16000, int(settings.voice_cloning_sample_target_rate_hz or 24000))
        channel_count = 1 if int(settings.voice_cloning_sample_channel_count or 1) <= 1 else 2
        if not duration_ms or not 3000 <= duration_ms <= 60000:
            raise RuntimeError('声音复刻样本需为 3–60 秒，建议使用 10–20 秒清晰录音')
        if audio_identity.file_size_bytes > 10 * 1024 * 1024:
            raise RuntimeError('声音复刻样本不能超过 10 MB')
        clone_readiness = VoiceCloneReadinessResult(
            status='ready',
            message='样本满足声音复刻基础要求，创建时会检查所选模型的要求',
        )
        audio_public_url = None
        public_url_error = None
        try:
            audio_public_url = ensure_sample_public_url(stored_audio_path)
            object_key = owned_oss_object_key(audio_public_url)
            if object_key and not _sample_public_url_is_referenced(session, audio_public_url):
                new_oss_object_key = object_key
        except Exception as error:
            public_url_error = str(error)

        if public_url_error and clone_readiness.message:
            clone_message = f'{clone_readiness.message}；样本公网地址未准备好：{public_url_error}'
        elif public_url_error:
            clone_message = f'样本公网地址未准备好：{public_url_error}'
        else:
            clone_message = clone_readiness.message

        if voice_retention.voice_is_expired(resolution.identity.created_at):
            raise VoiceAssetExpiredError()

        sample = VoiceCloneSample(
            display_name=normalized_name,
            name_key=name_key,
            project_id=project.id if project else None,
            program_key=snapshot_program_key,
            program_name=snapshot_program_name,
            original_filename=Path(original_filename).name,
            audio_local_path=str(stored_audio_path),
            audio_public_url=audio_public_url,
            content_sha256=audio_identity.content_sha256,
            file_size_bytes=audio_identity.file_size_bytes,
            duration_ms=duration_ms,
            sample_rate_hz=sample_rate_hz,
            channel_count=channel_count,
            clone_readiness_status=clone_readiness.status,
            clone_readiness_message=clone_message,
            identity_id=resolution.identity.id,
            embedding=resolution.embedding,
            embedding_status=resolution.embedding_status,
            embedding_provider_name=resolution.embedding_provider_name,
            embedding_model_id=resolution.embedding_model_id,
            embedding_message=resolution.embedding_message,
        )
        session.add(sample)
        session.flush()
        if resolution.match_type == 'speaker_embedding':
            session.add(VoiceCloneIdentityReview(
                candidate_identity_id=resolution.identity.id,
                sample_id=sample.id,
                submitted_name=normalized_name,
                match_score=resolution.match_score,
            ))
        session.commit()
        persisted_audio_path = None
        new_oss_object_key = None
    except VoiceAssetExpiredError:
        session.rollback()
        _enqueue_late_voice_assets(session, project.id if project else None,
            local_path=persisted_audio_path,
            public_url=audio_public_url if new_oss_object_key else None)
        session.commit()
        raise
    except IntegrityError as error:
        session.rollback()
        remove_local_file(persisted_audio_path)
        _delete_oss_object_best_effort(new_oss_object_key)
        if 'voice_clone_sample_identity_limit' in str(error) and resolved_identity_id:
            raise _voice_clone_sample_limit_error(
                session,
                resolved_identity_id,
            ) from error
        raise
    except Exception:
        session.rollback()
        remove_local_file(persisted_audio_path)
        _delete_oss_object_best_effort(new_oss_object_key)
        raise
    session.refresh(sample)
    return VoiceCloneSampleSaveResult(
        sample=sample,
        reused_existing=False,
        identity_id=resolution.identity.id,
        identity_display_name=resolution.identity.display_name,
        identity_aliases=list(resolution.identity.aliases or []),
        match_type=resolution.match_type,
        match_score=resolution.match_score,
        sample_slots_remaining=max(
            0,
            MAX_VOICE_CLONE_SAMPLES_PER_IDENTITY - identity_sample_count - 1,
        ),
    )


def _sample_public_url_is_referenced(session: Session, public_url: str) -> bool:
    return any((
        session.scalar(select(VoiceCloneSample.id).where(
            VoiceCloneSample.audio_public_url == public_url
        ).limit(1)),
        session.scalar(select(VoiceprintSample.id).where(or_(
            VoiceprintSample.audio_public_url == public_url,
            VoiceprintSample.clone_audio_public_url == public_url,
        )).limit(1)),
        session.scalar(select(VoiceCloneSynthesisRecord.id).where(
            VoiceCloneSynthesisRecord.output_audio_public_url == public_url
        ).limit(1)),
    ))


def _delete_oss_object_best_effort(object_key: str | None) -> None:
    if not object_key:
        return
    try:
        delete_oss_object(object_key)
    except Exception:
        pass


def prepare_voice_clone_sample_audio(source_path: Path) -> Path:
    return prepare_voice_clone_reference_audio(source_path)


def delete_voice_clone_sample(
    session: Session,
    sample: VoiceCloneSample,
    project_id: Optional[str] = None,
) -> None:
    if project_id:
        project = get_active_voice_clone_project(session, project_id)
        if str(sample.project_id or '').strip() != project.id:
            raise VoiceCloneProjectConflictError('共享样本只能由其来源项目删除')
        reference_count = len(list(session.scalars(
            select(VoiceCloneProfile).where(
                VoiceCloneProfile.source_clone_sample_id == sample.id,
                VoiceCloneProfile.status != 'deleted',
            )
        )))
        if reference_count:
            raise VoiceCloneProjectConflictError(
                f'该共享样本正被 {reference_count} 个音色使用，不能直接删除'
            )
    pending_review_ids = list(session.scalars(
        select(VoiceCloneIdentityReview.id)
        .where(
            VoiceCloneIdentityReview.sample_id == sample.id,
            VoiceCloneIdentityReview.status == 'pending',
        )
        .order_by(VoiceCloneIdentityReview.created_at, VoiceCloneIdentityReview.id)
    ))
    if pending_review_ids:
        raise VoiceCloneSampleReviewConflictError(pending_review_ids)
    sample_path = Path(sample.audio_local_path)
    session.delete(sample)
    session.commit()
    remove_local_file(sample_path)


def list_voice_clone_profiles(
    session: Session,
    person_id: Optional[str] = None,
    program_name: Optional[str] = None,
) -> list[VoiceCloneProfile]:
    statement = select(VoiceCloneProfile).where(current_voice_clone_profile_clause()).order_by(VoiceCloneProfile.created_at.desc())
    if person_id is not None:
        statement = statement.where(VoiceCloneProfile.person_id == person_id)
    normalized_program = normalize_program_name(program_name)
    if normalized_program:
        project = resolve_active_voice_clone_project_by_alias(session, normalized_program)
        if project:
            unassigned_statement = select(VoiceCloneProfile).where(
                current_voice_clone_profile_clause(),
                VoiceCloneProfile.project_id.is_(None),
                VoiceCloneProfile.program_key.is_(None),
            )
            if person_id is not None:
                unassigned_statement = unassigned_statement.where(
                    VoiceCloneProfile.person_id == person_id
                )
            return _merge_voice_clone_entities(
                list_voice_clone_profiles_for_project(
                    session,
                    project.id,
                    person_id=person_id,
                ),
                list(session.scalars(
                    unassigned_statement.order_by(VoiceCloneProfile.created_at.desc())
                )),
            )
        program_key = build_program_key(normalized_program)
        statement = statement.where(
            or_(
                VoiceCloneProfile.program_key == program_key,
                VoiceCloneProfile.program_name == normalized_program,
            )
        )
    return list(session.scalars(statement))


def get_voice_clone_profile(session: Session, profile_id: str) -> Optional[VoiceCloneProfile]:
    return session.scalar(select(VoiceCloneProfile).where(VoiceCloneProfile.id == profile_id, current_voice_clone_profile_clause()))


def list_voice_clone_profiles_for_project(
    session: Session,
    project_id: str,
    person_id: Optional[str] = None,
    *,
    claim_legacy: bool = False,
) -> list[VoiceCloneProfile]:
    project = get_active_voice_clone_project(session, project_id)
    statement = select(VoiceCloneProfile).where(or_(
        VoiceCloneProfile.project_id == project.id,
        VoiceCloneProfile.project_id.is_(None),
    )).where(current_voice_clone_profile_clause())
    if person_id is not None:
        statement = statement.where(VoiceCloneProfile.person_id == person_id)
    candidates = list(session.scalars(statement.order_by(VoiceCloneProfile.created_at.desc())))
    return _filter_and_claim_project_entities(
        session,
        project,
        candidates,
        _voice_clone_profile_project_ids,
        claim_legacy=claim_legacy,
    )


def get_voice_clone_profile_for_project(
    session: Session,
    project_id: str,
    profile_id: str,
    *,
    claim_legacy: bool = False,
) -> Optional[VoiceCloneProfile]:
    project = get_active_voice_clone_project(session, project_id)
    profile = get_voice_clone_profile(session, profile_id)
    if not profile:
        return None
    return _claim_project_entity(
        session,
        project,
        profile,
        _voice_clone_profile_project_ids,
        claim_legacy=claim_legacy,
    )


def get_voice_clone_synthesis_record(session: Session, record_id: str) -> Optional[VoiceCloneSynthesisRecord]:
    return session.scalar(select(VoiceCloneSynthesisRecord).where(VoiceCloneSynthesisRecord.id == record_id,
        VoiceCloneSynthesisRecord.created_at > voice_retention.voice_retention_cutoff()))


def get_voice_clone_synthesis_record_for_project(
    session: Session,
    project_id: str,
    record_id: str,
    *,
    claim_legacy: bool = False,
) -> Optional[VoiceCloneSynthesisRecord]:
    project = get_active_voice_clone_project(session, project_id)
    record = get_voice_clone_synthesis_record(session, record_id)
    if not record:
        return None
    return _claim_project_entity(
        session,
        project,
        record,
        _voice_clone_record_project_ids,
        claim_legacy=claim_legacy,
    )


def list_voice_clone_synthesis_records(
    session: Session,
    profile_id: Optional[str] = None,
    limit: int = 8,
    project_id: Optional[str] = None,
) -> list[VoiceCloneSynthesisRecord]:
    if project_id:
        project = get_active_voice_clone_project(session, project_id)
        if profile_id is None:
            return list(session.scalars(select(VoiceCloneSynthesisRecord).where(
                VoiceCloneSynthesisRecord.project_id == project.id,
                VoiceCloneSynthesisRecord.status != 'downloaded',
                VoiceCloneSynthesisRecord.created_at > voice_retention.voice_retention_cutoff(),
            ).order_by(VoiceCloneSynthesisRecord.created_at.desc(), VoiceCloneSynthesisRecord.id.desc()).limit(limit)))
        candidates = list(session.scalars(
            select(VoiceCloneSynthesisRecord)
            .where(
                VoiceCloneSynthesisRecord.status != 'downloaded',
                VoiceCloneSynthesisRecord.created_at > voice_retention.voice_retention_cutoff(),
                VoiceCloneSynthesisRecord.voice_clone_profile_id == profile_id if profile_id else True,
                or_(
                    VoiceCloneSynthesisRecord.project_id == project.id,
                    VoiceCloneSynthesisRecord.project_id.is_(None),
                ),
            )
            .order_by(VoiceCloneSynthesisRecord.created_at.desc())
        ))
        records = _filter_and_claim_project_entities(
            session,
            project,
            candidates,
            _voice_clone_record_project_ids,
            claim_legacy=False,
        )
        return records[:limit]
    return list(
        session.scalars(
            select(VoiceCloneSynthesisRecord)
            .where(VoiceCloneSynthesisRecord.voice_clone_profile_id == profile_id,
                   VoiceCloneSynthesisRecord.status != 'downloaded',
                VoiceCloneSynthesisRecord.created_at > voice_retention.voice_retention_cutoff())
            .order_by(VoiceCloneSynthesisRecord.created_at.desc())
            .limit(limit)
        )
    )


def delete_voice_clone_synthesis_record(session: Session, record: VoiceCloneSynthesisRecord) -> None:
    audio_path = Path(record.output_audio_local_path) if record.output_audio_local_path else None
    profile = get_voice_clone_profile(session, record.voice_clone_profile_id)
    if profile and profile.preview_audio_local_path == record.output_audio_local_path:
        profile.preview_audio_local_path = None
    session.delete(record)
    session.commit()
    remove_local_file(audio_path)


def create_voice_clone_profile(
    session: Session,
    payload: VoiceCloneProfileCreateRequest,
    project_id: Optional[str] = None,
) -> VoiceCloneProfile:
    return find_or_create_voice_clone_profile(
        session=session,
        payload=payload,
        project_id=project_id,
    ).profile


def find_or_create_voice_clone_profile(
    session: Session,
    payload: VoiceCloneProfileCreateRequest,
    project_id: Optional[str] = None,
) -> VoiceCloneProfileCreateResult:
    locked_project_id = str(project_id or '').strip()
    payload_project_id = str(payload.project_id or '').strip()
    if locked_project_id and payload_project_id and locked_project_id != payload_project_id:
        raise VoiceCloneProjectConflictError('请求中的 project_id 与 URL 项目不一致')
    requested_project_id = locked_project_id or payload_project_id
    ensure_voice_cloning_enabled()
    provider = _normalize_provider(payload.provider or _default_provider())
    target_model = (payload.target_model or _default_target_model(provider)).strip()
    model_option = _require_model_available(provider, target_model)
    project = get_active_voice_clone_project(session, requested_project_id) if requested_project_id else None

    clone_sample: VoiceCloneSample | None = None
    source_sample: VoiceprintSample | None = None
    person: Person | None = None
    person_id: str | None = None
    normalized_payload_program = normalize_program_name(payload.program_name)
    profile_program_name: str | None = normalized_payload_program or None

    if payload.clone_sample_id:
        clone_sample = session.get(VoiceCloneSample, payload.clone_sample_id)
        if not clone_sample:
            raise LookupError('声音复刻样本不存在')
        if get_voice_clone_sample(session, clone_sample.id) is None:
            raise VoiceAssetExpiredError()
        source_project_ids = _voice_clone_sample_project_ids(session, clone_sample)
        source_project = _single_active_project_for_ids(
            session,
            source_project_ids,
        )
        if not project and source_project:
            project = source_project
            _claim_project_entity(
                session,
                project,
                clone_sample,
                _voice_clone_sample_project_ids,
                claim_legacy=True,
            )
        elif not project and source_project_ids:
            raise VoiceCloneProjectConflictError('声音复刻样本项目归属无效或不唯一')
        if not _source_sample_is_available(clone_sample):
            raise RuntimeError('当前样本已失效，请重新上传')
        sample_public_url = (clone_sample.audio_public_url or '').strip()
        source_audio_local_path = clone_sample.audio_local_path
        source_clone_sample_id = clone_sample.id
        profile_program_name = profile_program_name or normalize_program_name(clone_sample.program_name) or None
    else:
        if not payload.person_id or not payload.sample_id:
            raise RuntimeError('请先上传声音复刻样本')
        person = session.get(Person, payload.person_id)
        if not person:
            raise LookupError('人物不存在')
        source_sample = session.get(VoiceprintSample, payload.sample_id)
        if not source_sample or source_sample.person_id != person.id:
            raise LookupError('样本不存在')
        if _voice_clone_asset_expired(source_sample, session):
            raise VoiceAssetExpiredError()
        source_project_ids = _person_project_ids(session, person)
        source_project = _single_active_project_for_ids(
            session,
            source_project_ids,
        )
        if project and not _person_belongs_to_project(session, person, project.id):
            raise VoiceCloneProjectConflictError('人物或声纹样本不属于当前项目')
        if not project and source_project:
            project = source_project
        elif not project and source_project_ids:
            raise VoiceCloneProjectConflictError('人物或声纹样本项目归属无效或不唯一')
        if not _source_sample_is_available(source_sample):
            raise RuntimeError('当前样本已失效，请重新上传')
        sample_public_url = _resolve_clone_sample_public_url(source_sample) or ''
        source_audio_local_path = _resolve_clone_sample_local_path(source_sample)
        source_clone_sample_id = None
        person_id = person.id
        profile_program_name = profile_program_name or normalize_program_name(person.program_name) or None

    if not project:
        project = resolve_active_voice_clone_project_by_alias(session, profile_program_name)
    if project and person and not _person_belongs_to_project(session, person, project.id):
        raise VoiceCloneProjectConflictError('人物或声纹样本不属于当前项目')

    _validate_model_sample(clone_sample or source_sample, model_option)
    profile_program_key, profile_program_name = _project_snapshots(project, profile_program_name)
    profile_statement = select(VoiceCloneProfile).where(
        current_voice_clone_profile_clause(),
        VoiceCloneProfile.source_sample_id == (
            source_sample.id if source_sample else None
        ),
        VoiceCloneProfile.source_clone_sample_id == source_clone_sample_id,
        VoiceCloneProfile.identity_id == (
            clone_sample.identity_id if clone_sample else None
        ),
        VoiceCloneProfile.provider == provider,
        VoiceCloneProfile.target_model == target_model,
        VoiceCloneProfile.status.in_({'ready', 'creating'}),
    )
    if source_sample:
        scoped_project_id = project.id if project else None
        profile_statement = profile_statement.where(
            VoiceCloneProfile.project_id == scoped_project_id
            if scoped_project_id is not None
            else VoiceCloneProfile.project_id.is_(None)
        )
    profile_candidates = list(session.scalars(
        profile_statement.order_by(VoiceCloneProfile.created_at.desc())
    ))
    existing_profile = next(iter(profile_candidates), None)
    if existing_profile:
        if clone_sample:
            clone_sample.last_used_at = datetime.utcnow()
            session.commit()
        return VoiceCloneProfileCreateResult(
            profile=existing_profile,
            reused_existing=True,
        )

    if not sample_public_url:
        raise RuntimeError('当前样本缺少公网可访问地址，请先补齐 OSS 配置后重新上传样本')

    profile = VoiceCloneProfile(
        project_id=project.id if project else None,
        person_id=person_id,
        source_sample_id=source_sample.id if source_sample else None,
        source_clone_sample_id=source_clone_sample_id,
        identity_id=clone_sample.identity_id if clone_sample else None,
        source_audio_local_path=source_audio_local_path,
        program_key=profile_program_key,
        program_name=profile_program_name,
        provider=provider,
        provider_model_family=provider,
        target_model=target_model,
        voice_name=payload.voice_name.strip(),
        status='creating',
        preview_text=settings.voice_cloning_preview_text,
        usage_program_keys=[],
    )
    if clone_sample:
        clone_sample.last_used_at = datetime.utcnow()
    session.add(profile)
    session.commit()
    session.refresh(profile)

    created_profile_id, created_project_id = profile.id, profile.project_id
    provider_voice_id = None
    try:
        response = _create_provider_voice(
            provider=provider,
            sample_public_url=sample_public_url,
            target_model=target_model,
            voice_name=payload.voice_name,
            language_hints=payload.language_hints,
            provider_context=_voice_provider_context(profile.project_id),
            observation_key=f'voice:create:{profile.id}',
        )
    except httpx.HTTPError:
        # The provider may have accepted a request whose response was lost.
        result_status = 'creating'
        result_message = '云端创建结果未确认，请勿重复创建；请稍后刷新或联系管理员核对音色'
    except (CosyVoiceCloneError, QwenVoiceCloneError, QwenAudioVoiceCloneError) as error:
        result_status = 'failed'
        result_message = str(error)
    else:
        output = response.get('output') or {}
        provider_voice_id = _extract_provider_voice_id(provider, output)
        result_status = ('creating' if provider in {'cosyvoice', 'qwen_audio'} else 'ready') if provider_voice_id else 'failed'
        result_message = _build_provider_create_message(provider, output, bool(provider_voice_id))
    profile = _profile_after_provider(session, created_profile_id, created_project_id,
                                     provider, target_model, provider_voice_id)
    profile.voice_id = provider_voice_id
    profile.status = result_status
    profile.provider_message = result_message
    session.commit()
    session.refresh(profile)
    if profile.status == 'creating' and profile.voice_id:
        profile = refresh_voice_clone_profile(session, profile)
    return VoiceCloneProfileCreateResult(
        profile=profile,
        reused_existing=False,
    )


def refresh_voice_clone_profile(session: Session, profile: VoiceCloneProfile) -> VoiceCloneProfile:
    profile_id, project_id = profile.id, profile.project_id
    provider, target_model, voice_id = profile.provider, profile.target_model, profile.voice_id
    if get_voice_clone_profile(session, profile_id) is None:
        raise VoiceAssetExpiredError()
    if (profile.provider not in {'cosyvoice', 'qwen_audio'} or profile.status != 'creating' or not profile.voice_id
            or not settings.voice_cloning_enabled):
        return profile
    model = _get_model_option(profile.provider, profile.target_model)
    if not model or not model.available:
        return profile
    try:
        response = (query_qwen_audio_voice(voice_id, target_model=target_model)
                    if provider == 'qwen_audio' else query_cosyvoice_voice(voice_id))
        output = response.get('output') or {}
    except (CosyVoiceCloneError, QwenAudioVoiceCloneError, httpx.HTTPError) as error:
        new_status = 'creating'
        message = f'云端状态暂未确认，请稍后重试：{error}'
    else:
        cloud_status = str(output.get('status') or '').upper()
        if cloud_status == 'OK':
            new_status = 'ready'
            message = '音色已创建，云端确认可用'
        elif cloud_status in {'UNDEPLOYED', 'FAILED', 'DELETED'}:
            new_status = 'failed'
            message = str(output.get('message') or '云端音色创建失败，请重新创建')
        else:
            new_status = 'creating'
            message = '音色正在云端创建，请稍候'
    profile = _profile_after_provider(session, profile_id, project_id, provider, target_model, voice_id)
    profile.status = new_status
    profile.provider_message = message
    session.commit()
    session.refresh(profile)
    return profile


def synthesize_voice_clone_preview(
    session: Session,
    profile_id: str,
    payload: VoiceClonePreviewRequest,
    project_id: Optional[str] = None,
) -> VoiceCloneSynthesisRecord:
    locked_project_id = str(project_id or '').strip()
    payload_project_id = str(payload.project_id or '').strip()
    if locked_project_id and payload_project_id and locked_project_id != payload_project_id:
        raise VoiceCloneProjectConflictError('请求中的 project_id 与 URL 项目不一致')
    requested_project_id = locked_project_id or payload_project_id

    project = (
        get_active_voice_clone_project(session, requested_project_id)
        if requested_project_id
        else None
    )
    profile = get_voice_clone_profile(session, profile_id)
    if not profile:
        if (stored_profile := session.get(VoiceCloneProfile, profile_id)) is not None:
            if _voice_clone_asset_expired(stored_profile, session):
                raise VoiceAssetExpiredError()
            raise VoiceCloneUnavailableError('该音色已失效并被清理，请重新选择或上传')
        raise LookupError('音色不存在')

    ensure_voice_cloning_enabled()
    provider = _normalize_provider(profile.provider)
    availability = get_voice_clone_profile_availability(session, profile)
    if availability.reason == 'model_retired':
        raise RuntimeError(RETIRED_QWEN_MESSAGE)
    if not availability.selectable:
        raise VoiceCloneUnavailableError('该音色已失效并被清理，请重新选择或上传')
    _require_model_available(provider, profile.target_model)

    text = payload.text.strip()
    if not text:
        raise RuntimeError('请先输入试听文案')

    instruction, instruction_message = _resolve_synthesis_instruction(
        provider=provider,
        target_model=profile.target_model,
        instruction=payload.instruction,
    )
    provider_messages = [instruction_message] if instruction_message else []

    record = VoiceCloneSynthesisRecord(
        project_id=project.id if project else profile.project_id,
        voice_clone_profile_id=profile.id,
        voice_name_snapshot=profile.voice_name,
        provider_snapshot=profile.provider,
        target_model_snapshot=profile.target_model,
        person_id=str(profile.person_id or '').strip() or None,
        program_key=project.legacy_program_key if project else profile.program_key,
        program_name=project.name if project else profile.program_name,
        target_model=profile.target_model,
        input_text=text,
        status='processing',
    )
    session.add(record)
    session.commit()
    session.refresh(record)

    record_id, record_project_id = record.id, record.project_id
    target_model, voice_id = profile.target_model, profile.voice_id
    reference_url = None
    if provider == 'qwen_audio_next':
        source = (session.get(VoiceCloneSample, profile.source_clone_sample_id) if profile.source_clone_sample_id
                  else session.get(VoiceprintSample, profile.source_sample_id) if profile.source_sample_id else None)
        reference_url = (source.audio_public_url if isinstance(source, VoiceCloneSample)
                         else _resolve_clone_sample_public_url(source) if source else None)
    try:
        response = _synthesize_provider_voice(
            provider=provider,
            target_model=target_model,
            voice_id=voice_id,
            text=text,
            instruction=instruction,
            language_hints=payload.language_hints if hasattr(payload, 'language_hints') else None,
            provider_context=_voice_provider_context(record_project_id),
            observation_key=f'voice:synthesize:{record_id}',
            attempt=1,
            **({'sample_public_url': reference_url} if provider == 'qwen_audio_next' else {}),
        )
    except (CosyVoiceCloneError, QwenVoiceCloneError, httpx.HTTPError, RuntimeError) as error:
        if not instruction or provider in {'qwen_audio', 'qwen_audio_next'}:
            record = _processing_record_after_provider(session, record_id, record_project_id)
            record.status = 'failed'
            record.provider_message = str(error)
            session.commit()
            session.refresh(record)
            return record
        _processing_record_after_provider(session, record_id, record_project_id)
        session.rollback()
        try:
            response = _synthesize_provider_voice(
                provider=provider,
                target_model=target_model,
                voice_id=voice_id,
                text=text,
                instruction=None,
                language_hints=payload.language_hints if hasattr(payload, 'language_hints') else None,
                provider_context=_voice_provider_context(record_project_id),
                observation_key=f'voice:synthesize:{record_id}',
                attempt=2,
            )
            provider_messages.append(f'语气提示调用失败，已自动改用普通方式生成：{error}')
        except (CosyVoiceCloneError, QwenVoiceCloneError, httpx.HTTPError, RuntimeError) as fallback_error:
            record = _processing_record_after_provider(session, record_id, record_project_id)
            record.status = 'failed'
            record.provider_message = f'语气提示调用失败，普通生成也失败：{fallback_error}'
            session.commit()
            session.refresh(record)
            return record

    audio_url = None
    try:
        audio_url = _extract_synthesized_audio_url(response)
        local_path = _download_preview_audio(audio_url, record_id)
    except (httpx.HTTPError, RuntimeError, OSError) as error:
        partial_path = settings.audio_dir_path / f'voice-clone-preview-{record_id}.wav'
        partial_path = partial_path if partial_path.is_file() else None
        record = _processing_record_after_provider(session, record_id, record_project_id,
                                                   local_path=partial_path, public_url=audio_url)
        _enqueue_late_voice_assets(session, record_project_id, local_path=partial_path, public_url=audio_url)
        record.status = 'failed'
        record.provider_message = str(error)
        session.commit()
        session.refresh(record)
        return record

    record = _processing_record_after_provider(session, record_id, record_project_id,
                                               local_path=local_path, public_url=audio_url)
    profile = get_voice_clone_profile(session, profile_id)
    if profile is not None:
        profile.preview_text = text
        profile.preview_audio_local_path = str(local_path)
        profile.last_used_at = datetime.utcnow()
    else:
        record.voice_clone_profile_id = None
    record.status = 'ready'
    record.provider_message = '试听音频已生成' + (f'；{"；".join(provider_messages)}' if provider_messages else '')
    record.output_audio_local_path = str(local_path)
    record.output_audio_public_url = audio_url
    session.commit()
    session.refresh(record)
    return record


def complete_voice_clone_download(session: Session, project_id: str, record_id: str) -> None:
    from app.services.voice_clone_cleanup import VoiceCloneDeletionTarget, _enqueue_deletion_targets

    _begin_voice_result_write(session)
    record = get_voice_clone_synthesis_record_for_project(session, project_id, record_id)
    if record is None:
        raise LookupError('当前项目中不存在该生成记录')
    if record.status == 'downloaded':
        session.commit()
        return
    if record.status != 'ready':
        raise RuntimeError('音频尚未生成成功，不能清理音色')
    profile = session.get(VoiceCloneProfile, record.voice_clone_profile_id) if record.voice_clone_profile_id else None
    if profile is not None:
        if profile.project_id != project_id:
            raise VoiceCloneProjectConflictError('只能清理当前项目创建的音色')
        if not _release_voice_clone_after_download(session, profile):
            key = f'profile_release:{profile.id}'
            _enqueue_deletion_targets(session, {key: VoiceCloneDeletionTarget(project_id, 'profile_release', key)})
    else:
        from app.services.voice_retention_cleanup import _queue_assets
        _queue_assets(session, [record], ('output_audio_local_path',), ('output_audio_public_url',))
        record.status, record.output_audio_local_path, record.output_audio_public_url = 'downloaded', None, None
        record.input_text = ''
    session.commit()


def _release_voice_clone_after_download(session: Session, profile: VoiceCloneProfile) -> bool:
    from app.services.voice_clone_cleanup import hard_delete_voice_clone_profiles
    from app.services.voice_retention_cleanup import _queue_assets

    # The result-write transaction serializes cleanup against other generations.
    session.flush()
    profiles = list(session.scalars(select(VoiceCloneProfile).where(
        VoiceCloneProfile.source_clone_sample_id == profile.source_clone_sample_id,
        VoiceCloneProfile.project_id == profile.project_id,
    ))) if profile.source_clone_sample_id else [profile]
    profile_ids = [item.id for item in profiles]
    if session.scalar(select(VoiceCloneSynthesisRecord.id).where(
        VoiceCloneSynthesisRecord.voice_clone_profile_id.in_(profile_ids),
        VoiceCloneSynthesisRecord.status == 'processing',
        VoiceCloneSynthesisRecord.created_at > datetime.utcnow() - timedelta(minutes=15),
    ).limit(1)):
        return False
    if any(item.status == 'creating' and item.created_at > datetime.utcnow() - timedelta(minutes=15) for item in profiles):
        return False
    records = list(session.scalars(select(VoiceCloneSynthesisRecord).where(
        VoiceCloneSynthesisRecord.voice_clone_profile_id.in_(profile_ids),
        or_(VoiceCloneSynthesisRecord.project_id == profile.project_id,
            VoiceCloneSynthesisRecord.project_id.is_(None)),
    )))
    _queue_assets(session, records, ('output_audio_local_path',), ('output_audio_public_url',))
    for record in records:
        # Keep a small receipt for idempotent download acknowledgement; no audio remains.
        record.status = 'downloaded'
        record.output_audio_local_path = None
        record.output_audio_public_url = None
        record.input_text = ''
    sample_id, identity_id = profile.source_clone_sample_id, profile.identity_id
    hard_delete_voice_clone_profiles(session, profile_ids)
    session.flush()
    sample = session.get(VoiceCloneSample, sample_id) if sample_id else None
    if sample is not None and not session.scalar(select(VoiceCloneProfile.id).where(
        VoiceCloneProfile.source_clone_sample_id == sample.id,
    ).limit(1)):
        _queue_assets(session, [sample], ('audio_local_path',), ('audio_public_url',))
        for review in session.scalars(select(VoiceCloneIdentityReview).where(
            VoiceCloneIdentityReview.sample_id == sample.id,
        )):
            session.delete(review)
        session.delete(sample)
    session.flush()
    if identity_id and not session.scalar(select(VoiceCloneSample.id).where(
        VoiceCloneSample.identity_id == identity_id,
    ).limit(1)) and not session.scalar(select(VoiceCloneProfile.id).where(
        VoiceCloneProfile.identity_id == identity_id,
    ).limit(1)) and not session.scalar(select(VoiceCloneIdentityReview.id).where(
        VoiceCloneIdentityReview.candidate_identity_id == identity_id,
    ).limit(1)):
        identity = session.get(VoiceCloneIdentity, identity_id)
        if identity is not None:
            session.delete(identity)
    return True


def delete_voice_clone_profile(
    session: Session,
    profile_id: str,
    project_id: Optional[str] = None,
) -> VoiceCloneProfile:
    project = get_active_voice_clone_project(session, project_id) if project_id else None
    profile = get_voice_clone_profile(session, profile_id)
    if not profile:
        raise LookupError('音色不存在')
    profile_project_id = str(profile.project_id or '').strip()
    requested_project_id = project.id if project else ''
    if profile_project_id != requested_project_id:
        raise VoiceCloneProjectConflictError('共享音色只能由其来源项目删除')
    from app.services.voice_clone_cleanup import hard_delete_voice_clone_profiles

    try:
        hard_delete_voice_clone_profiles(session, [profile.id])
        session.commit()
    except Exception:
        session.rollback()
        raise
    return profile


def build_voice_clone_profile_response(
    profile: VoiceCloneProfile,
    source_sample: Optional[VoiceprintSample] = None,
    source_clone_sample: Optional[VoiceCloneSample] = None,
) -> VoiceCloneProfileResponse:
    return VoiceCloneProfileResponse(
        expires_at=voice_clone_asset_expires_at(profile),
        id=profile.id,
        identity_id=profile.identity_id,
        project_id=profile.project_id,
        person_id=profile.person_id,
        source_sample_id=profile.source_sample_id,
        source_clone_sample_id=profile.source_clone_sample_id,
        source_audio_local_path=profile.source_audio_local_path,
        provider=profile.provider,
        provider_model_family=profile.provider_model_family,
        target_model=profile.target_model,
        voice_id=profile.voice_id,
        voice_name=profile.voice_name,
        status=profile.status,
        provider_message=profile.provider_message,
        program_key=profile.program_key,
        program_name=profile.program_name,
        usage_program_keys=profile.usage_program_keys or [],
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        last_used_at=profile.last_used_at,
        source_sample=build_sample_response(source_sample) if source_sample else None,
        source_clone_sample=build_voice_clone_sample_response(source_clone_sample) if source_clone_sample else None,
    )


def build_voice_clone_sample_response(sample: VoiceCloneSample) -> VoiceCloneSampleResponse:
    return VoiceCloneSampleResponse(
        expires_at=voice_clone_asset_expires_at(sample),
        id=sample.id,
        display_name=sample.display_name,
        project_id=sample.project_id,
        program_key=sample.program_key,
        program_name=sample.program_name,
        original_filename=sample.original_filename,
        content_sha256=sample.content_sha256,
        file_size_bytes=sample.file_size_bytes,
        duration_ms=sample.duration_ms,
        sample_rate_hz=sample.sample_rate_hz,
        channel_count=sample.channel_count,
        clone_readiness_status=sample.clone_readiness_status,
        clone_readiness_message=sample.clone_readiness_message,
        audio_public_url=sample.audio_public_url,
        created_at=sample.created_at,
        last_used_at=sample.last_used_at,
    )


def build_voice_clone_sample_save_response(
    result: VoiceCloneSampleSaveResult,
) -> VoiceCloneSampleSaveResponse:
    sample_response = build_voice_clone_sample_response(result.sample)
    return VoiceCloneSampleSaveResponse(
        **sample_response.model_dump(),
        reused_existing=result.reused_existing,
        identity_id=result.identity_id,
        identity_display_name=result.identity_display_name,
        identity_aliases=result.identity_aliases,
        match_type=result.match_type,
        match_score=result.match_score,
        sample_slots_remaining=result.sample_slots_remaining,
        message=(
            '检测到相同样本，已直接复用'
            if result.reused_existing
            else '样本已保存'
        ),
    )


def build_voice_clone_synthesis_record_response(
    record: VoiceCloneSynthesisRecord,
) -> VoiceCloneSynthesisRecordResponse:
    return VoiceCloneSynthesisRecordResponse(
        expires_at=voice_clone_asset_expires_at(record),
        id=record.id,
        project_id=record.project_id,
        voice_clone_profile_id=record.voice_clone_profile_id,
        voice_name_snapshot=record.voice_name_snapshot,
        provider_snapshot=record.provider_snapshot,
        target_model_snapshot=record.target_model_snapshot,
        person_id=record.person_id,
        program_key=record.program_key,
        program_name=record.program_name,
        target_model=record.target_model,
        input_text=record.input_text,
        output_audio_local_path=record.output_audio_local_path,
        output_audio_public_url=record.output_audio_public_url,
        status=record.status,
        provider_message=record.provider_message,
        created_at=record.created_at,
    )


def _filter_and_claim_project_entities(
    session: Session,
    project: Project,
    candidates: list,
    resolver,
    *,
    claim_legacy: bool,
) -> list:
    matched = []
    claimed = []
    for entity in candidates:
        if resolver(session, entity) != {project.id}:
            continue
        matched.append(entity)
        if claim_legacy and not str(entity.project_id or '').strip():
            entity.project_id = project.id
            claimed.append(entity)
    if claimed:
        session.commit()
        for entity in claimed:
            session.refresh(entity)
    return matched


def _merge_voice_clone_entities(*groups: list) -> list:
    by_id = {
        str(entity.id): entity
        for group in groups
        for entity in group
    }
    return sorted(
        by_id.values(),
        key=lambda entity: (entity.created_at or datetime.min, str(entity.id)),
        reverse=True,
    )


def _claim_project_entity(
    session: Session,
    project: Project,
    entity,
    resolver,
    *,
    claim_legacy: bool,
):
    if resolver(session, entity) != {project.id}:
        return None
    if claim_legacy and not str(entity.project_id or '').strip():
        entity.project_id = project.id
        session.commit()
        session.refresh(entity)
    return entity


def _normalized_aliases(*values: Optional[str]) -> set[str]:
    return {
        alias
        for value in values
        if (alias := build_program_key(value))
    }


def _resolve_project_alias_ids(session: Session, *values: Optional[str]) -> set[str]:
    aliases = _normalized_aliases(*values)
    if not aliases:
        return set()
    matched_project_ids: set[str] = set()
    for project in session.scalars(select(Project)):
        project_aliases = _normalized_aliases(
            project.id,
            project.name,
            project.name_key,
            project.legacy_program_key,
        )
        if aliases.intersection(project_aliases):
            matched_project_ids.add(project.id)
    return matched_project_ids


def _voice_clone_sample_project_ids(
    session: Session,
    sample: VoiceCloneSample,
) -> set[str]:
    existing_project_id = str(sample.project_id or '').strip()
    if existing_project_id:
        return {existing_project_id}
    return _resolve_project_alias_ids(session, sample.program_key, sample.program_name)


def _person_project_ids(session: Session, person: Person) -> set[str]:
    existing_project_id = str(person.project_id or '').strip()
    if existing_project_id:
        return {existing_project_id}
    return _resolve_project_alias_ids(session, person.program_key, person.program_name)


def _person_belongs_to_project(session: Session, person: Person, project_id: str) -> bool:
    candidate_ids = _person_project_ids(session, person)
    if candidate_ids:
        return candidate_ids == {project_id}
    if _normalized_aliases(person.program_key, person.program_name):
        return False
    return bool(get_project(session, project_id))


def _voice_clone_profile_project_ids(
    session: Session,
    profile: VoiceCloneProfile,
) -> set[str]:
    parent_project_ids: set[str] = set()
    if profile.source_clone_sample_id:
        clone_sample = session.get(VoiceCloneSample, profile.source_clone_sample_id)
        if clone_sample:
            parent_project_ids.update(_voice_clone_sample_project_ids(session, clone_sample))

    person = session.get(Person, profile.person_id) if profile.person_id else None
    if not person and profile.source_sample_id:
        source_sample = session.get(VoiceprintSample, profile.source_sample_id)
        if source_sample:
            person = session.get(Person, source_sample.person_id)
    if person:
        parent_project_ids.update(_person_project_ids(session, person))

    existing_project_id = str(profile.project_id or '').strip()
    if existing_project_id:
        return {existing_project_id}

    alias_project_ids = _resolve_project_alias_ids(
        session,
        profile.program_key,
        profile.program_name,
    )
    if parent_project_ids:
        if alias_project_ids and alias_project_ids != parent_project_ids:
            return parent_project_ids | alias_project_ids
        return parent_project_ids
    return alias_project_ids


def _voice_clone_record_project_ids(
    session: Session,
    record: VoiceCloneSynthesisRecord,
) -> set[str]:
    profile = (
        session.get(VoiceCloneProfile, record.voice_clone_profile_id)
        if record.voice_clone_profile_id
        else None
    )
    parent_project_ids = (
        _voice_clone_profile_project_ids(session, profile)
        if profile
        else set()
    )
    existing_project_id = str(record.project_id or '').strip()
    if existing_project_id:
        return {existing_project_id}
    alias_project_ids = _resolve_project_alias_ids(
        session,
        record.program_key,
        record.program_name,
    )
    if parent_project_ids:
        if alias_project_ids and alias_project_ids != parent_project_ids:
            return parent_project_ids | alias_project_ids
        return parent_project_ids
    return alias_project_ids


def _single_active_project_for_ids(
    session: Session,
    project_ids: set[str],
) -> Optional[Project]:
    if len(project_ids) != 1:
        return None
    project = get_project(session, next(iter(project_ids)))
    if not project or project.status != 'active':
        return None
    return project


def _project_snapshots(
    project: Optional[Project],
    fallback_program_name: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    if project:
        return (
            str(project.legacy_program_key or project.name_key or build_program_key(project.name)).strip() or None,
            normalize_program_name(project.name) or None,
        )
    normalized_program = normalize_program_name(fallback_program_name)
    return build_program_key(normalized_program) or None, normalized_program or None


def _extract_synthesized_audio_url(response: dict) -> str:
    output = response.get('output') or {}
    audio = output.get('audio') or {}
    audio_url = str(audio.get('url') or '').strip()
    if not audio_url:
        raise RuntimeError('语音合成接口没有返回试听音频地址')
    return audio_url


def _normalize_provider(value: str) -> str:
    normalized = str(value or '').strip().lower()
    if normalized in {'qwen3', 'qwen3-tts', 'qwen-tts'}:
        return 'qwen'
    return normalized or 'qwen'


MODEL_LABELS = {
    'cosyvoice-v3.5-plus': 'CosyVoice 3.5 Plus',
    'cosyvoice-v3.5-flash': 'CosyVoice 3.5 Flash',
    'cosyvoice-v3-plus': 'CosyVoice 3 Plus',
    'cosyvoice-v3-flash': 'CosyVoice 3 Flash',
    'cosyvoice-v2': 'CosyVoice 2',
    'cosyvoice-v1': 'CosyVoice 1',
    'qwen3-tts-vc-2026-01-22': 'Qwen3-TTS',
    'qwen-audio-3.0-tts-plus': 'Qwen-Audio 3.0 Plus',
    'qwen-audio-3.0-tts-flash': 'Qwen-Audio 3.0 Flash',
    'qwen-audio-3.1-tts-flash': 'Qwen-Audio 3.1 Flash',
    NEXT_MODEL: 'Qwen-Audio 3.1 TTS Next',
}


def _build_model_options() -> list[VoiceCloneModelOption]:
    models = []
    # Keep legacy contracts for installed clients and cleanup outside the public catalog.
    for provider, model in [
        ('qwen_audio_next', NEXT_MODEL),
        ('cosyvoice', 'cosyvoice-v3.5-flash'),
        ('cosyvoice', 'cosyvoice-v3.5-plus'),
        *(('qwen_audio', model) for model in QWEN_AUDIO_MODELS),
    ]:
        if model not in MODEL_LABELS or (provider == 'cosyvoice') != model.startswith('cosyvoice-'):
            continue
        supports_instruction = provider in {'qwen_audio', 'qwen_audio_next'} or (provider == 'cosyvoice' and supports_cosyvoice_instruction(model))
        reason = None
        if not settings.voice_cloning_enabled:
            reason = '声音复刻功能未开启'
        elif not settings.dashscope_api_key.strip():
            reason = '缺少 DASHSCOPE_API_KEY'
        elif provider in {'qwen_audio', 'qwen_audio_next'} and settings.voice_cloning_region != 'cn-beijing':
            reason = 'Qwen-Audio 非实时语音合成目前仅支持北京地域'
        elif settings.voice_cloning_region == 'intl' and provider == 'cosyvoice' and model != 'cosyvoice-v3-plus':
            reason = '当前地域未接通该声音复刻模型'
        if provider == 'qwen_audio_next' and reason is None:
            try:
                next_endpoint()
            except RuntimeError as error:
                reason = str(error)
        max_instruction = (QWEN_AUDIO_INSTRUCTION_LIMIT if provider in {'qwen_audio', 'qwen_audio_next'}
                           else COSYVOICE_INSTRUCTION_LIMIT if supports_instruction else 0)
        strict_weight = weighted_instruction_length(STRICT_TEXT_INSTRUCTION) if provider == 'cosyvoice' else len(STRICT_TEXT_INSTRUCTION)
        models.append(VoiceCloneModelOption(
            id=f'{provider}:{model}', provider=provider, model=model, label=MODEL_LABELS[model],
            recommended=provider == 'qwen_audio_next', available=reason is None, unavailable_reason=reason,
            capabilities={'instruction': supports_instruction},
            constraints={
                'sample_min_seconds': 3 if provider == 'qwen' else 5,
                'sample_max_seconds': 30 if provider == 'qwen_audio_next' else 60,
                'sample_min_rate_hz': 24000 if provider == 'qwen' else 16000,
                'instruction_max_weight': max(0, max_instruction - strict_weight),
                'instruction_provider_max_weight': max_instruction,
                'instruction_han_weight': 2 if provider == 'cosyvoice' else 1,
            },
        ))
    return models


def _get_model_option(provider: str, target_model: str) -> VoiceCloneModelOption | None:
    return next((item for item in _build_model_options()
                 if item.provider == provider and item.model == target_model), None)


def _require_model_available(provider: str, target_model: str) -> VoiceCloneModelOption:
    ensure_voice_cloning_enabled()
    if target_model == RETIRED_QWEN_MODEL:
        raise RuntimeError(RETIRED_QWEN_MESSAGE)
    option = _get_model_option(provider, target_model)
    if not option:
        raise RuntimeError('当前声音复刻模型未接通，请选择模型列表中的可用模型')
    if not option.available:
        raise RuntimeError(option.unavailable_reason)
    return option


def _validate_model_sample(source: VoiceCloneSample | VoiceprintSample, option: VoiceCloneModelOption) -> None:
    limits = option.constraints
    duration = int(source.duration_ms or 0) / 1000
    clone_path = isinstance(source, VoiceprintSample) and bool(source.clone_audio_local_path)
    rate = source.clone_sample_rate_hz if clone_path else source.sample_rate_hz
    channels = source.clone_channel_count if clone_path else source.channel_count
    path = source.clone_audio_local_path if clone_path else source.audio_local_path
    if not limits.sample_min_seconds <= duration <= limits.sample_max_seconds:
        raise RuntimeError(f'当前模型需要 {limits.sample_min_seconds}–{limits.sample_max_seconds} 秒的样本，请调整录音')
    if int(rate or 0) < limits.sample_min_rate_hz or (option.provider == 'qwen' and channels != 1):
        raise RuntimeError(f'当前模型需要至少 {limits.sample_min_rate_hz // 1000} kHz 的样本，Qwen 需单声道')
    if (_nonempty_local_file_size(path) or 0) > limits.sample_max_bytes:
        raise RuntimeError('样本文件不能超过 10 MB')


def _default_provider() -> str:
    provider = _normalize_provider(settings.voice_cloning_provider)
    return provider if provider in {'cosyvoice', 'qwen_audio'} else 'qwen_audio_next'

def _default_target_model(provider: str) -> str:
    if provider == 'qwen_audio_next':
        return NEXT_MODEL
    if provider == 'cosyvoice':
        return 'cosyvoice-v3.5-plus'
    if provider == 'qwen_audio':
        return 'qwen-audio-3.0-tts-plus'
    return 'qwen3-tts-vc-2026-01-22'


def _build_provider_options() -> list[VoiceCloneProviderOption]:
    return [VoiceCloneProviderOption(
        provider='qwen_audio_next', label=MODEL_LABELS[NEXT_MODEL], target_model=NEXT_MODEL,
        sample_requirements=['上传 5–30 秒清晰单人参考音频，最大 10 MB，建议 10–20 秒', 'WAV、MP3；建议 24 kHz 单声道，无背景音乐或其他人声'],
        notes=['使用参考音频直接生成，支持语气描述，无需创建云端持久音色', '可生成多个版本试听，下载选定版本后清理本次参考音频和全部生成版本'],
    ), VoiceCloneProviderOption(
        provider='cosyvoice', label=MODEL_LABELS['cosyvoice-v3.5-flash'], target_model='cosyvoice-v3.5-flash',
        sample_requirements=['上传 5–60 秒清晰单人参考音频，最大 10 MB，建议 10–20 秒', '建议无背景音乐或其他人声'],
        notes=['支持声音复刻和语气指令；与 Next 使用独立模型版本，可试听比较', '下载选定版本并保存成功后，清理本次两种模型的临时音色、参考音频和全部生成版本'],
    )]


def _resolve_synthesis_instruction(
    *,
    provider: str,
    target_model: str,
    instruction: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    option = _get_model_option(provider, target_model)
    if not option or not option.capabilities.instruction:
        if str(instruction or '').strip():
            raise RuntimeError('当前模型不支持语气提示，请清空提示后重试')
        return None, None
    normalized_instruction = _compose_strict_synthesis_instruction(instruction)
    limits = option.constraints
    measure = weighted_instruction_length if limits.instruction_han_weight == 2 else len
    if (measure(str(instruction or '').strip()) > limits.instruction_max_weight
            or measure(normalized_instruction) > limits.instruction_provider_max_weight):
        raise RuntimeError(f'语气提示超过 {limits.instruction_max_weight} 加权字符预算，请缩短后重试')
    return normalized_instruction, None


def _compose_strict_synthesis_instruction(instruction: Optional[str]) -> str:
    normalized = str(instruction or '').strip()
    if not normalized:
        return STRICT_TEXT_INSTRUCTION
    if '只朗读输入文本' in normalized or '不添加样本里的其他话' in normalized:
        return normalized
    return f'{STRICT_TEXT_INSTRUCTION}{normalized}'


def _resolve_clone_sample_public_url(sample: VoiceprintSample) -> Optional[str]:
    return (sample.clone_audio_public_url or sample.audio_public_url or '').strip() or None


def _resolve_clone_sample_local_path(sample: VoiceprintSample) -> Optional[str]:
    return (sample.clone_audio_local_path or sample.audio_local_path or '').strip() or None


def _create_provider_voice(
    *,
    provider: str,
    sample_public_url: str,
    target_model: str,
    voice_name: str,
    language_hints: Optional[list[str]],
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
) -> dict:
    if provider == 'qwen_audio_next':
        return {'output': {'voice_id': f'reference-{uuid4()}'}}
    if provider == 'qwen_audio':
        return create_qwen_audio_voice(
            sample_public_url=sample_public_url, target_model=target_model, voice_name=voice_name,
            language_hints=language_hints, provider_context=provider_context, observation_key=observation_key,
        )
    if provider == 'cosyvoice':
        return create_cosyvoice_voice(
            sample_public_url=sample_public_url,
            target_model=target_model,
            voice_name=voice_name,
            language_hints=language_hints,
            provider_context=provider_context,
            observation_key=observation_key,
        )
    if provider == 'qwen':
        return create_qwen_voice(
            sample_public_url=sample_public_url,
            target_model=target_model,
            voice_name=voice_name,
            language_hints=language_hints,
            provider_context=provider_context,
            observation_key=observation_key,
        )
    raise RuntimeError(f'不支持的声音复刻 provider：{provider}')


def _synthesize_provider_voice(
    *,
    provider: str,
    target_model: str,
    voice_id: str,
    text: str,
    instruction: Optional[str] = None,
    language_hints: Optional[list[str]] = None,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
    attempt: int = 1,
    sample_public_url: Optional[str] = None,
) -> dict:
    if provider == 'qwen_audio_next':
        return synthesize_qwen_audio_next_voice(sample_public_url=sample_public_url or '', text=text,
            instruction=instruction, provider_context=provider_context, observation_key=observation_key, attempt=attempt)
    if provider == 'qwen_audio':
        return synthesize_qwen_audio_voice(
            target_model=target_model, voice_id=voice_id, text=text, instruction=instruction,
            language_hints=language_hints, provider_context=provider_context,
            observation_key=observation_key, attempt=attempt,
        )
    if provider == 'cosyvoice':
        return synthesize_cosyvoice_voice(
            target_model=target_model,
            voice_id=voice_id,
            text=text,
            instruction=instruction,
            provider_context=provider_context,
            observation_key=observation_key,
            attempt=attempt,
        )
    if provider == 'qwen':
        return synthesize_qwen_voice(
            target_model=target_model,
            voice=voice_id,
            text=text,
            instruction=instruction,
            language_hints=language_hints,
            provider_context=provider_context,
            observation_key=observation_key,
            attempt=attempt,
        )
    raise RuntimeError(f'不支持的声音复刻 provider：{provider}')


def _delete_provider_voice(
    provider: str,
    voice_id: str,
    *,
    target_model: str,
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
) -> None:
    if provider == 'qwen_audio_next':
        return  # Next has no enrolled provider voice to delete.
    if provider == 'qwen_audio':
        try:
            delete_qwen_audio_voice(voice_id, target_model=target_model,
                                    provider_context=provider_context, observation_key=observation_key)
        except QwenAudioVoiceCloneError as error:
            if error.status_code not in {400, 416} or error.code != 'BadRequest.ResourceNotExist':
                raise
        return
    if provider == 'cosyvoice':
        try:
            delete_cosyvoice_voice(
                voice_id,
                target_model=target_model,
                provider_context=provider_context,
                observation_key=observation_key,
            )
        except CosyVoiceCloneError as error:
            # Current docs use 400; the older CosyVoice API documented 416.
            if error.status_code not in {400, 416} or error.code != 'BadRequest.ResourceNotExist':
                raise
        return
    if provider == 'qwen':
        try:
            delete_qwen_voice(
                voice_id,
                target_model=target_model,
                provider_context=provider_context,
                observation_key=observation_key,
            )
        except QwenVoiceCloneError as error:
            # Never infer absence from free-form messages or another account's list.
            if error.status_code != 400 or error.code != 'BadRequest.VoiceNotFound':
                raise
        return
    raise RuntimeError(f'不支持的声音复刻 provider：{provider}')


def _voice_provider_context(project_id: Optional[str]) -> Optional[ProviderCallContext]:
    normalized_project_id = str(project_id or '').strip()
    if not normalized_project_id:
        return None
    return ProviderCallContext(project_id=normalized_project_id, pipeline_scope='project_asset')


def _extract_provider_voice_id(provider: str, output: dict) -> Optional[str]:
    if provider == 'qwen':
        return str(output.get('voice') or '').strip() or None
    return str(output.get('voice_id') or '').strip() or None


def _build_provider_create_message(provider: str, output: dict, ready: bool) -> str:
    if not ready:
        return '音色创建未返回 voice_id'
    if provider == 'qwen_audio_next':
        return '参考音频已就绪，生成时直接使用，无云端持久音色'
    if provider == 'qwen' and bool(output.get('fallback_mode')):
        reason = str(output.get('fallback_reason') or '样本质量或文本匹配不理想').strip()
        return f'音色创建完成，但 Qwen 返回降级模式：{reason}'
    return '音色创建完成'


def _download_preview_audio(audio_url: str, record_id: str) -> Path:
    response = httpx.get(audio_url, timeout=120, follow_redirects=True)
    response.raise_for_status()
    suffix = _guess_audio_suffix(audio_url, response.headers.get('content-type'), response.content)
    source_path = settings.audio_dir_path / f'voice-clone-preview-{record_id}-source{suffix}'
    output_path = settings.audio_dir_path / f'voice-clone-preview-{record_id}.wav'
    try:
        source_path.write_bytes(response.content)
        return normalize_voice_clone_generated_audio(source_path, output_path)
    finally:
        remove_local_file(source_path)


def _guess_audio_suffix(audio_url: str, content_type: Optional[str], content: bytes | None = None) -> str:
    content_suffix = _guess_audio_suffix_from_bytes(content or b'')
    if content_suffix:
        return content_suffix
    if content_type:
        suffix = mimetypes.guess_extension(content_type.split(';', 1)[0].strip())
        if suffix in SUPPORTED_AUDIO_SUFFIXES:
            return suffix
    path_suffix = Path(urlparse(audio_url).path).suffix.lower()
    if path_suffix in SUPPORTED_AUDIO_SUFFIXES:
        return path_suffix
    return '.mp3'


def _guess_audio_suffix_from_bytes(content: bytes) -> Optional[str]:
    if len(content) >= 12 and content[:4] == b'RIFF' and content[8:12] == b'WAVE':
        return '.wav'
    if content[:3] == b'ID3':
        return '.mp3'
    if len(content) >= 2 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0:
        return '.mp3'
    if len(content) >= 12 and content[4:8] == b'ftyp':
        return '.m4a'
    if content[:4] == b'OggS':
        return '.ogg'
    if content[:4] == b'fLaC':
        return '.flac'
    return None
