from __future__ import annotations

from dataclasses import dataclass, field
import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.voice_retention import voice_retention_cutoff
from app.models.person import Person
from app.models.voice_clone_profile import VoiceCloneProfile
from app.models.voice_clone_synthesis_record import VoiceCloneSynthesisRecord
from app.models.voiceprint_match import VoiceprintMatch
from app.models.voiceprint_sample import VoiceprintSample
from app.services.speaker_memory import normalize_program_name
from app.services.voiceprint_policy import (
    enforce_voiceprint_sample_limit,
    remove_pruned_voiceprint_files,
)


GLOBAL_PROMOTION_SIMILARITY_THRESHOLD = 0.90


@dataclass(frozen=True)
class GlobalVoiceprintPromotionResult:
    promoted: bool = False
    global_person_id: str | None = None
    merged_person_ids: list[str] = field(default_factory=list)
    pruned_sample_count: int = 0
    reason: str | None = None


def promote_consistent_person_to_global(
    session: Session,
    person_id: str,
) -> GlobalVoiceprintPromotionResult:
    trigger = session.get(Person, person_id)
    if not trigger:
        return GlobalVoiceprintPromotionResult(reason='人物不存在')
    if _is_global_person(trigger):
        return GlobalVoiceprintPromotionResult(
            promoted=False,
            global_person_id=trigger.id,
            reason='已经是通用声纹',
        )

    normalized_name = normalize_program_name(trigger.name)
    if not normalized_name:
        return GlobalVoiceprintPromotionResult(reason='人物姓名为空')
    people = [
        person
        for person in session.scalars(select(Person))
        if normalize_program_name(person.name) == normalized_name
    ]
    confirmed_by_person = {
        person.id: _confirmed_samples(session, person.id)
        for person in people
    }
    trigger_samples = confirmed_by_person.get(trigger.id, [])
    if not trigger_samples:
        return GlobalVoiceprintPromotionResult(reason='当前人物还没有可信声纹')

    compatible = [trigger]
    for person in people:
        if person.id == trigger.id:
            continue
        samples = confirmed_by_person.get(person.id, [])
        if samples and _max_cross_similarity(trigger_samples, samples) >= GLOBAL_PROMOTION_SIMILARITY_THRESHOLD:
            compatible.append(person)

    project_ids = {person.project_id for person in compatible if person.project_id}
    if len(project_ids) < 2:
        return GlobalVoiceprintPromotionResult(reason='尚未在两个独立项目中得到一致声纹')

    global_people = [person for person in people if _is_global_person(person)]
    if len(global_people) > 1:
        return GlobalVoiceprintPromotionResult(reason='存在多个同名通用人物，需先人工整理')
    global_person = global_people[0] if global_people else None
    if global_person and global_person not in compatible:
        global_samples = confirmed_by_person.get(global_person.id, [])
        if global_samples and _max_cross_similarity(trigger_samples, global_samples) < GLOBAL_PROMOTION_SIMILARITY_THRESHOLD:
            return GlobalVoiceprintPromotionResult(reason='现有同名通用声纹与当前声纹不一致')
        compatible.append(global_person)

    if not global_person:
        global_person = Person(
            name=normalized_name,
            project_id=None,
            program_key=None,
            program_name=None,
            aliases=None,
            notes='系统根据跨项目一致声纹自动晋升为通用人物',
        )
        session.add(global_person)
        session.flush()

    merged_people = [person for person in compatible if person.id != global_person.id]
    merged_ids = {person.id for person in merged_people}
    global_person.aliases = _merge_aliases(global_person, merged_people)
    for sample in session.scalars(
        select(VoiceprintSample).where(VoiceprintSample.person_id.in_(merged_ids))
    ):
        sample.person_id = global_person.id
    for match in session.scalars(
        select(VoiceprintMatch).where(VoiceprintMatch.person_id.in_(merged_ids))
    ):
        match.person_id = global_person.id
        match.top_candidates = _rewrite_top_candidates(match.top_candidates, merged_ids, global_person)
    for profile in session.scalars(
        select(VoiceCloneProfile).where(VoiceCloneProfile.person_id.in_(merged_ids))
    ):
        profile.person_id = global_person.id
    for record in session.scalars(
        select(VoiceCloneSynthesisRecord).where(VoiceCloneSynthesisRecord.person_id.in_(merged_ids))
    ):
        record.person_id = global_person.id
    for person in merged_people:
        session.delete(person)
    session.flush()

    retention = enforce_voiceprint_sample_limit(session, global_person.id)
    session.commit()
    remove_pruned_voiceprint_files(session, retention)
    return GlobalVoiceprintPromotionResult(
        promoted=True,
        global_person_id=global_person.id,
        merged_person_ids=sorted(merged_ids),
        pruned_sample_count=retention.removed_count,
    )


def _confirmed_samples(session: Session, person_id: str) -> list[VoiceprintSample]:
    return list(
        session.scalars(
            select(VoiceprintSample).where(
                VoiceprintSample.person_id == person_id,
                VoiceprintSample.created_at > voice_retention_cutoff(),
                VoiceprintSample.review_status == 'confirmed',
                VoiceprintSample.embedding_status == 'ready',
            )
        )
    )


def _is_global_person(person: Person) -> bool:
    return (
        not person.project_id
        and not str(person.program_key or '').strip()
        and not str(person.program_name or '').strip()
    )


def _merge_aliases(global_person: Person, merged_people: list[Person]) -> list[str] | None:
    aliases = {
        normalize_program_name(value)
        for person in [global_person, *merged_people]
        for value in [person.name, *(person.aliases or [])]
        if normalize_program_name(value)
    }
    aliases.discard(normalize_program_name(global_person.name))
    return sorted(aliases) or None


def _rewrite_top_candidates(
    candidates: list[dict[str, object]] | None,
    merged_ids: set[str],
    global_person: Person,
) -> list[dict[str, object]] | None:
    if not candidates:
        return candidates
    by_person: dict[str, dict[str, object]] = {}
    for item in candidates:
        next_item = dict(item)
        person_id = str(next_item.get('person_id') or '')
        if person_id in merged_ids:
            person_id = global_person.id
            next_item['person_id'] = global_person.id
            next_item['person_name'] = global_person.name
        current = by_person.get(person_id)
        if current and float(current.get('score') or 0) >= float(next_item.get('score') or 0):
            continue
        by_person[person_id] = next_item
    return sorted(by_person.values(), key=lambda item: float(item.get('score') or 0), reverse=True)


def _max_cross_similarity(
    left_samples: list[VoiceprintSample],
    right_samples: list[VoiceprintSample],
) -> float:
    return max(
        (
            _cosine_similarity(left.embedding or [], right.embedding or [])
            for left in left_samples
            for right in right_samples
        ),
        default=0.0,
    )


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
