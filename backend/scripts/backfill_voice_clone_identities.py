from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable
import uuid

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.db.session import configure_sqlite_engine  # noqa: E402
from app.models.voice_clone_identity import VoiceCloneIdentity  # noqa: E402
from app.models.voice_clone_profile import VoiceCloneProfile  # noqa: E402
from app.models.voice_clone_sample import VoiceCloneSample  # noqa: E402
from app.services.voice_clone_identity import (  # noqa: E402
    cosine_similarity,
    hash_voice_clone_audio,
    normalize_voice_clone_name,
)
from app.services.voiceprint_provider import (  # noqa: E402
    VoiceprintEmbeddingResult,
    extract_voiceprint_embedding,
)


BACKFILL_IDENTITY_NAMESPACE = uuid.UUID('e8ace6b7-175d-4bd9-9648-9ece4f075e8b')


@dataclass(frozen=True)
class VoiceCloneIdentityBackfillPlan:
    database_path: str
    embedding_model: str
    threshold: float
    unreadable_files: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    exact_hash_groups: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    acoustic_candidates: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    three_sample_conflicts: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    proposed_identities: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    model_mismatches: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    dangling_references: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    identity_updates: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    sample_updates: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    profile_updates: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    sample_fingerprints: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    database_baseline: dict[str, Any] = field(default_factory=dict)
    plan_version: int = 1
    confirmation_token: str = ''

    def as_dict(self) -> dict[str, Any]:
        return {
            'database_path': self.database_path,
            'embedding_model': self.embedding_model,
            'threshold': self.threshold,
            'unreadable_files': list(self.unreadable_files),
            'exact_groups': len(self.exact_hash_groups),
            'exact_hash_groups': list(self.exact_hash_groups),
            'speaker_match_candidates': len(self.acoustic_candidates),
            'acoustic_candidates': list(self.acoustic_candidates),
            'three_sample_conflicts': list(self.three_sample_conflicts),
            'proposed_identities': list(self.proposed_identities),
            'model_mismatches': list(self.model_mismatches),
            'dangling_references': list(self.dangling_references),
            'identity_updates': list(self.identity_updates),
            'sample_updates': list(self.sample_updates),
            'profile_updates': list(self.profile_updates),
            'sample_fingerprints': list(self.sample_fingerprints),
            'database_baseline': self.database_baseline,
            'plan_version': self.plan_version,
            'confirmation_token': self.confirmation_token,
        }


EmbeddingExtractor = Callable[[Path], VoiceprintEmbeddingResult]


def build_voice_clone_identity_backfill_plan(
    session: Session,
    extract_embedding: EmbeddingExtractor,
    *,
    threshold: float | None = None,
) -> VoiceCloneIdentityBackfillPlan:
    match_threshold = (
        settings.voice_clone_identity_match_threshold
        if threshold is None
        else threshold
    )
    samples = list(session.scalars(
        select(VoiceCloneSample).order_by(
            VoiceCloneSample.created_at,
            VoiceCloneSample.id,
        )
    ))
    identities = {
        identity.id: identity
        for identity in session.scalars(
            select(VoiceCloneIdentity).order_by(VoiceCloneIdentity.id)
        )
    }
    profiles = list(session.scalars(
        select(VoiceCloneProfile).order_by(VoiceCloneProfile.id)
    ))
    database_path = _database_path(session)
    baseline = _database_baseline(samples, identities.values(), profiles)

    unreadable_files: list[dict[str, Any]] = []
    fingerprints: list[dict[str, Any]] = []
    samples_by_hash: dict[str, list[VoiceCloneSample]] = {}
    blocked_hashes: set[str] = set()
    for sample in samples:
        path = Path(sample.audio_local_path).expanduser()
        try:
            audio_identity = hash_voice_clone_audio(path)
        except (OSError, ValueError) as error:
            if sample.content_sha256:
                blocked_hashes.add(sample.content_sha256)
            unreadable_files.append({
                'sample_id': sample.id,
                'path': str(path),
                'reason': str(error),
                'group_content_sha256': sample.content_sha256,
            })
            continue
        fingerprints.append({
            'sample_id': sample.id,
            'path': str(path.resolve()),
            'content_sha256': audio_identity.content_sha256,
            'file_size_bytes': audio_identity.file_size_bytes,
        })
        samples_by_hash.setdefault(audio_identity.content_sha256, []).append(sample)

    legacy_ids = {sample.id for sample in samples if sample.identity_id is None}
    exact_hash_groups = [
        {
            'content_sha256': content_sha256,
            'sample_ids': sorted(sample.id for sample in group),
        }
        for content_sha256, group in samples_by_hash.items()
        if len(group) > 1 and any(sample.id in legacy_ids for sample in group)
    ]
    exact_hash_groups.sort(key=lambda entry: entry['content_sha256'])

    dangling_references: list[dict[str, Any]] = []
    for sample in samples:
        if sample.identity_id and sample.identity_id not in identities:
            dangling_references.append({
                'kind': 'sample_identity',
                'row_id': sample.id,
                'target_id': sample.identity_id,
            })

    sample_ids = {sample.id for sample in samples}
    profiles_by_sample: dict[str, list[VoiceCloneProfile]] = {}
    invalid_profile_ids: set[str] = set()
    blocked_profile_sample_ids: set[str] = set()
    for profile in profiles:
        source_id = profile.source_clone_sample_id
        if source_id:
            profiles_by_sample.setdefault(source_id, []).append(profile)
        if profile.identity_id and profile.identity_id not in identities:
            dangling_references.append({
                'kind': 'profile_identity',
                'row_id': profile.id,
                'target_id': profile.identity_id,
            })
            invalid_profile_ids.add(profile.id)
            if source_id in sample_ids:
                blocked_profile_sample_ids.add(source_id)
        elif source_id and source_id not in sample_ids:
            dangling_references.append({
                'kind': 'profile_sample',
                'row_id': profile.id,
                'target_id': source_id,
            })
            invalid_profile_ids.add(profile.id)

    assigned_samples = {
        sample.id: sample.identity_id
        for sample in samples
        if sample.identity_id in identities
    }
    planned_counts = {
        identity_id: sum(value == identity_id for value in assigned_samples.values())
        for identity_id in identities
    }
    candidate_samples = {
        identity_id: sorted(
            sample_id
            for sample_id, assigned_identity_id in assigned_samples.items()
            if assigned_identity_id == identity_id
        )
        for identity_id in identities
    }
    candidates = {
        identity.id: {
            'identity_id': identity.id,
            'embedding': identity.reference_embedding,
            'model_id': identity.embedding_model_id,
        }
        for identity in identities.values()
        if identity.reference_embedding and identity.embedding_model_id
    }
    proposals: dict[str, dict[str, Any]] = {}
    identity_updates: dict[str, dict[str, Any]] = {}
    sample_updates: list[dict[str, Any]] = []
    acoustic_candidates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    model_mismatches: list[dict[str, Any]] = []
    run_model: str | None = None

    ordered_groups = sorted(
        samples_by_hash.items(),
        key=lambda item: min(
            (sample.created_at, sample.id) for sample in item[1]
        ),
    )
    for content_sha256, group in ordered_groups:
        targets = [sample for sample in group if sample.id in legacy_ids]
        if not targets:
            continue
        if content_sha256 in blocked_hashes:
            continue
        target_ids = sorted(sample.id for sample in targets)
        group_ids = {sample.id for sample in group}
        if group_ids & blocked_profile_sample_ids:
            continue
        dangling_ids = sorted({
            sample.identity_id
            for sample in group
            if sample.identity_id and sample.identity_id not in identities
        })
        exact_identity_ids = sorted({
            sample.identity_id
            for sample in group
            if sample.identity_id in identities
        })
        if dangling_ids or len(exact_identity_ids) > 1:
            dangling_references.append({
                'kind': 'exact_hash_group',
                'sample_ids': target_ids,
                'target_ids': dangling_ids or exact_identity_ids,
                'reason': (
                    'dangling_identity'
                    if dangling_ids
                    else 'conflicting_identities'
                ),
            })
            continue

        exact_identity_id = exact_identity_ids[0] if exact_identity_ids else None
        proposed_identity_id = _proposed_identity_id(
            content_sha256,
            target_ids,
        )
        cap_identity_id = exact_identity_id or proposed_identity_id
        current_count = planned_counts.get(cap_identity_id, 0)
        if current_count + len(targets) > 3:
            conflicts.append({
                'identity_id': cap_identity_id,
                'sample_ids': target_ids,
                'existing_sample_count': current_count,
                'proposed_sample_count': len(targets),
            })
            continue

        canonical = min(group, key=lambda sample: (sample.created_at, sample.id))
        try:
            embedding_result = extract_embedding(Path(canonical.audio_local_path))
            if not embedding_result.embedding:
                raise ValueError('empty embedding')
        except Exception as error:
            unreadable_files.append({
                'sample_id': canonical.id,
                'path': canonical.audio_local_path,
                'reason': f'embedding_error: {error}',
                'group_sample_ids': target_ids,
            })
            continue

        if run_model is None:
            run_model = embedding_result.provider_model_id
        if embedding_result.provider_model_id != run_model:
            model_mismatches.append({
                'sample_ids': target_ids,
                'expected_model': run_model,
                'actual_model': embedding_result.provider_model_id,
            })
            continue
        known_models = {
            sample.embedding_model_id
            for sample in group
            if sample.embedding_model_id
        }
        if exact_identity_id is not None:
            exact_identity_model = identities[exact_identity_id].embedding_model_id
            if exact_identity_model:
                known_models.add(exact_identity_model)
        if known_models and known_models != {run_model}:
            model_mismatches.append({
                'sample_ids': target_ids,
                'expected_model': sorted(known_models)[0],
                'actual_model': run_model,
            })
            continue

        matched_identity_id = exact_identity_id
        if matched_identity_id is None:
            comparable = []
            for candidate in candidates.values():
                if candidate['model_id'] != run_model:
                    continue
                score = cosine_similarity(
                    candidate['embedding'] or [],
                    embedding_result.embedding,
                )
                if score >= match_threshold:
                    comparable.append((score, candidate['identity_id']))
            if comparable:
                score, matched_identity_id = sorted(
                    comparable,
                    key=lambda entry: (-entry[0], entry[1]),
                )[0]
                acoustic_candidates.append({
                    'sample_ids': target_ids,
                    'candidate_identity_id': matched_identity_id,
                    'candidate_sample_ids': candidate_samples.get(
                        matched_identity_id,
                        [],
                    ),
                    'match_score': score,
                    'embedding_model': run_model,
                })
                current_count = planned_counts.get(matched_identity_id, 0)
                if current_count + len(targets) > 3:
                    conflicts.append({
                        'identity_id': matched_identity_id,
                        'sample_ids': target_ids,
                        'existing_sample_count': current_count,
                        'proposed_sample_count': len(targets),
                    })
                    continue

        identity_id = matched_identity_id or proposed_identity_id
        profile_conflicts = [
            profile
            for sample_id in group_ids
            for profile in profiles_by_sample.get(sample_id, [])
            if profile.id not in invalid_profile_ids
            and profile.identity_id
            and profile.identity_id != identity_id
        ]
        if profile_conflicts:
            for profile in profile_conflicts:
                dangling_references.append({
                    'kind': 'profile_identity_conflict',
                    'row_id': profile.id,
                    'target_id': profile.identity_id,
                    'sample_identity_id': identity_id,
                })
            continue
        aliases = _aliases(sample.display_name for sample in targets)
        if identity_id in identities:
            identity = identities[identity_id]
            merged_aliases = _aliases([*(identity.aliases or []), *aliases])
            reference_embedding = (
                identity.reference_embedding or embedding_result.embedding
            )
            embedding_model_id = identity.embedding_model_id or run_model
            if (
                merged_aliases != sorted(identity.aliases or [])
                or identity.reference_embedding is None
                or identity.embedding_model_id is None
            ):
                identity_updates[identity_id] = {
                    'identity_id': identity_id,
                    'aliases': merged_aliases,
                    'reference_embedding': reference_embedding,
                    'embedding_model_id': embedding_model_id,
                }
            candidates[identity_id] = {
                'identity_id': identity_id,
                'embedding': reference_embedding,
                'model_id': embedding_model_id,
            }
        elif identity_id in proposals:
            proposal = proposals[identity_id]
            proposal['aliases'] = _aliases([*proposal['aliases'], *aliases])
            proposal['sample_ids'] = sorted([
                *proposal['sample_ids'],
                *target_ids,
            ])
        else:
            proposals[identity_id] = {
                'identity_id': identity_id,
                'display_name': min(
                    targets,
                    key=lambda sample: (sample.created_at, sample.id),
                ).display_name,
                'aliases': aliases,
                'reference_embedding': embedding_result.embedding,
                'embedding_model_id': run_model,
                'sample_ids': target_ids,
            }
            candidates[identity_id] = {
                'identity_id': identity_id,
                'embedding': embedding_result.embedding,
                'model_id': run_model,
            }

        planned_counts[identity_id] = planned_counts.get(identity_id, 0) + len(targets)
        candidate_samples.setdefault(identity_id, []).extend(target_ids)
        candidate_samples[identity_id].sort()
        for sample in targets:
            assigned_samples[sample.id] = identity_id
            sample_updates.append({
                'sample_id': sample.id,
                'identity_id': identity_id,
                'embedding': embedding_result.embedding,
                'embedding_status': 'ready',
                'embedding_provider_name': embedding_result.provider_name,
                'embedding_model_id': run_model,
                'embedding_message': embedding_result.provider_message,
            })

    profile_updates: list[dict[str, Any]] = []
    for profile in profiles:
        if profile.id in invalid_profile_ids:
            continue
        source_id = profile.source_clone_sample_id
        assigned_identity_id = assigned_samples.get(source_id or '')
        if assigned_identity_id is None:
            continue
        if profile.identity_id and profile.identity_id != assigned_identity_id:
            dangling_references.append({
                'kind': 'profile_identity_conflict',
                'row_id': profile.id,
                'target_id': profile.identity_id,
                'sample_identity_id': assigned_identity_id,
            })
            continue
        if profile.identity_id is None:
            profile_updates.append({
                'profile_id': profile.id,
                'identity_id': assigned_identity_id,
            })

    plan = VoiceCloneIdentityBackfillPlan(
        database_path=database_path,
        embedding_model=run_model or settings.voiceprint_model_id,
        threshold=match_threshold,
        unreadable_files=tuple(sorted(
            unreadable_files,
            key=lambda entry: (entry['sample_id'], entry['path']),
        )),
        exact_hash_groups=tuple(exact_hash_groups),
        acoustic_candidates=tuple(acoustic_candidates),
        three_sample_conflicts=tuple(conflicts),
        proposed_identities=tuple(
            proposals[key] for key in sorted(proposals)
        ),
        model_mismatches=tuple(model_mismatches),
        dangling_references=tuple(dangling_references),
        identity_updates=tuple(
            identity_updates[key] for key in sorted(identity_updates)
        ),
        sample_updates=tuple(sorted(
            sample_updates,
            key=lambda entry: entry['sample_id'],
        )),
        profile_updates=tuple(profile_updates),
        sample_fingerprints=tuple(sorted(
            fingerprints,
            key=lambda entry: entry['sample_id'],
        )),
        database_baseline=baseline,
    )
    return replace(plan, confirmation_token=_confirmation_token(plan))


def run_backfill(
    database: Path,
    output: Path,
    extract_embedding: EmbeddingExtractor = extract_voiceprint_embedding,
    *,
    apply: bool = False,
    confirmation_file: Path | None = None,
    threshold: float | None = None,
) -> dict[str, Any]:
    database_path = Path(database).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f'SQLite database file does not exist: {database_path}')
    _reject_output_collision(output_path, [database_path])
    if apply and (confirmation_file is None or not Path(confirmation_file).is_file()):
        raise RuntimeError('--apply 必须提供确认文件')

    engine = create_engine(
        f'sqlite:///{database_path}',
        connect_args={'check_same_thread': False},
    )
    configure_sqlite_engine(engine, enable_wal=False)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    staged_report_path: Path | None = None
    data_applied = False
    try:
        try:
            with factory() as session:
                plan = build_voice_clone_identity_backfill_plan(
                    session,
                    extract_embedding,
                    threshold=threshold,
                )
                _reject_output_collision(
                    output_path,
                    [
                        database_path,
                        *(
                            Path(entry['audio_local_path'])
                            for entry in plan.database_baseline['samples']
                        ),
                    ],
                )
                report = plan.as_dict()
                staged_report_path = _stage_report(output_path, report)
                if apply:
                    if _confirmation_file_token(Path(confirmation_file)) != plan.confirmation_token:
                        raise RuntimeError('确认文件与当前计划不匹配；计划已过期或文件已变化')
                    _apply_plan(session, plan)
                    data_applied = True
        finally:
            engine.dispose()

        try:
            _finalize_staged_report(staged_report_path, output_path)
        except Exception as error:
            if data_applied:
                raise RuntimeError(
                    '数据库回填已经应用，但报告最终写入失败；禁止重试 --apply，请人工恢复报告'
                ) from error
            raise
        return report
    finally:
        if staged_report_path is not None:
            staged_report_path.unlink(missing_ok=True)


def _stage_report(output_path: Path, report: dict[str, Any]) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode='w',
        encoding='utf-8',
        dir=output_path.parent,
        prefix=f'.{output_path.name}.',
        suffix='.tmp',
        delete=False,
    ) as file_obj:
        json.dump(
            report,
            file_obj,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        file_obj.flush()
        os.fsync(file_obj.fileno())
        return Path(file_obj.name)


def _finalize_staged_report(staged_path: Path, output_path: Path) -> None:
    os.replace(staged_path, output_path)


def _apply_plan(session: Session, plan: VoiceCloneIdentityBackfillPlan) -> None:
    session.rollback()
    try:
        session.execute(text('BEGIN IMMEDIATE'))
        current_samples = list(session.scalars(
            select(VoiceCloneSample).order_by(
                VoiceCloneSample.created_at,
                VoiceCloneSample.id,
            )
        ))
        current_identities = list(session.scalars(
            select(VoiceCloneIdentity).order_by(VoiceCloneIdentity.id)
        ))
        current_profiles = list(session.scalars(
            select(VoiceCloneProfile).order_by(VoiceCloneProfile.id)
        ))
        if _database_baseline(
            current_samples,
            current_identities,
            current_profiles,
        ) != plan.database_baseline:
            raise RuntimeError('数据库已变化，回填计划已过期')
        _validate_file_fingerprints(plan.sample_fingerprints)

        for entry in plan.proposed_identities:
            session.add(VoiceCloneIdentity(
                id=entry['identity_id'],
                display_name=entry['display_name'],
                aliases=entry['aliases'],
                reference_embedding=entry['reference_embedding'],
                embedding_model_id=entry['embedding_model_id'],
            ))
        for entry in plan.identity_updates:
            identity = session.get(VoiceCloneIdentity, entry['identity_id'])
            if identity is None:
                raise RuntimeError('声音身份已变化，回填计划已过期')
            identity.aliases = entry['aliases']
            identity.reference_embedding = entry['reference_embedding']
            identity.embedding_model_id = entry['embedding_model_id']
        for entry in plan.sample_updates:
            sample = session.get(VoiceCloneSample, entry['sample_id'])
            if sample is None or sample.identity_id is not None:
                raise RuntimeError('样本已变化，回填计划已过期')
            for name in (
                'identity_id',
                'embedding',
                'embedding_status',
                'embedding_provider_name',
                'embedding_model_id',
                'embedding_message',
            ):
                setattr(sample, name, entry[name])
        for entry in plan.profile_updates:
            profile = session.get(VoiceCloneProfile, entry['profile_id'])
            if profile is None or profile.identity_id is not None:
                raise RuntimeError('音色已变化，回填计划已过期')
            profile.identity_id = entry['identity_id']

        session.flush()
        if len(current_samples) != session.scalar(
            select(func.count()).select_from(VoiceCloneSample)
        ) or len(current_profiles) != session.scalar(
            select(func.count()).select_from(VoiceCloneProfile)
        ):
            raise RuntimeError('回填不得删除样本或音色')
        session.commit()
    except Exception:
        session.rollback()
        raise


def _validate_file_fingerprints(
    fingerprints: tuple[dict[str, Any], ...],
) -> None:
    for entry in fingerprints:
        try:
            current = hash_voice_clone_audio(Path(entry['path']))
        except OSError as error:
            raise RuntimeError('文件已变化，回填计划已过期') from error
        if (
            current.content_sha256 != entry['content_sha256']
            or current.file_size_bytes != entry['file_size_bytes']
        ):
            raise RuntimeError('文件已变化，回填计划已过期')


def _database_path(session: Session) -> str:
    bind = session.get_bind()
    if bind.dialect.name != 'sqlite':
        raise RuntimeError('声音身份回填仅支持 SQLite 数据库')
    database = str(bind.url.database or '')
    if not database or database == ':memory:':
        raise RuntimeError('声音身份回填需要独立 SQLite 数据库文件')
    return str(Path(database).resolve())


def _database_baseline(
    samples: list[VoiceCloneSample],
    identities,
    profiles: list[VoiceCloneProfile],
) -> dict[str, Any]:
    return {
        'samples': [
            {
                'id': sample.id,
                'display_name': sample.display_name,
                'audio_local_path': sample.audio_local_path,
                'content_sha256': sample.content_sha256,
                'identity_id': sample.identity_id,
                'embedding': sample.embedding,
                'embedding_status': sample.embedding_status,
                'embedding_provider_name': sample.embedding_provider_name,
                'embedding_model_id': sample.embedding_model_id,
                'embedding_message': sample.embedding_message,
                'created_at': sample.created_at.isoformat(),
            }
            for sample in samples
        ],
        'identities': [
            {
                'id': identity.id,
                'aliases': identity.aliases,
                'reference_embedding': identity.reference_embedding,
                'embedding_model_id': identity.embedding_model_id,
            }
            for identity in sorted(identities, key=lambda item: item.id)
        ],
        'profiles': [
            {
                'id': profile.id,
                'source_clone_sample_id': profile.source_clone_sample_id,
                'identity_id': profile.identity_id,
            }
            for profile in profiles
        ],
    }


def _aliases(values) -> list[str]:
    return sorted({
        normalized
        for value in values
        if (normalized := normalize_voice_clone_name(value))
    })


def _reject_output_collision(
    output: Path,
    protected_paths: list[Path],
) -> None:
    for protected in protected_paths:
        protected_path = Path(protected).expanduser()
        if output.resolve(strict=False) == protected_path.resolve(strict=False):
            raise RuntimeError('报告输出路径与数据库或样本音频冲突')
        try:
            if (
                output.exists()
                and protected_path.exists()
                and output.samefile(protected_path)
            ):
                raise RuntimeError('报告输出路径与数据库或样本音频冲突')
        except OSError:
            continue


def _proposed_identity_id(
    content_sha256: str,
    sample_ids: list[str],
) -> str:
    return str(uuid.uuid5(
        BACKFILL_IDENTITY_NAMESPACE,
        f'{content_sha256}:{"|".join(sample_ids)}',
    ))


def _confirmation_token(plan: VoiceCloneIdentityBackfillPlan) -> str:
    payload = plan.as_dict()
    payload.pop('confirmation_token')
    return hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')).hexdigest()


def _confirmation_file_token(path: Path) -> str:
    raw = path.read_text(encoding='utf-8').strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(payload, dict) or not isinstance(
        payload.get('confirmation_token'),
        str,
    ):
        raise RuntimeError('确认文件缺少 confirmation_token')
    return payload['confirmation_token']


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='预览或执行历史声音身份回填',
    )
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirmation-file', type=Path)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    report = run_backfill(
        args.database,
        args.output,
        apply=args.apply,
        confirmation_file=args.confirmation_file,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
