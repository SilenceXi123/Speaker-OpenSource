from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.voice_retention import voice_retention_cutoff
from app.models.person import Person
from app.models.voiceprint_sample import VoiceprintSample
from app.services.voiceprint_global import promote_consistent_person_to_global
from app.services.voiceprint_policy import (
    auto_confirm_consistent_samples,
    enforce_voiceprint_sample_limit,
    remove_pruned_voiceprint_files,
)


@dataclass(frozen=True)
class VoiceprintMaintenanceReport:
    auto_confirmed_count: int = 0
    global_promoted_count: int = 0
    merged_person_count: int = 0
    pruned_sample_count: int = 0


def reconcile_voiceprint_library(session: Session) -> VoiceprintMaintenanceReport:
    auto_confirmed_count = 0
    pruned_sample_count = 0

    person_ids = list(session.scalars(select(Person.id)))
    for person_id in person_ids:
        candidates = list(
            session.scalars(
                select(VoiceprintSample).where(
                    VoiceprintSample.person_id == person_id,
                    VoiceprintSample.created_at > voice_retention_cutoff(),
                    VoiceprintSample.review_status != 'confirmed',
                )
            )
        )
        for sample in candidates:
            auto_confirmed_count += auto_confirm_consistent_samples(session, sample)
        retention = enforce_voiceprint_sample_limit(session, person_id)
        session.commit()
        remove_pruned_voiceprint_files(session, retention)
        pruned_sample_count += retention.removed_count

    global_promoted_count = 0
    merged_person_count = 0
    for person_id in list(session.scalars(select(Person.id))):
        person = session.get(Person, person_id)
        if not person or not person.project_id:
            continue
        promotion = promote_consistent_person_to_global(session, person.id)
        if not promotion.promoted:
            continue
        global_promoted_count += 1
        merged_person_count += len(promotion.merged_person_ids)
        pruned_sample_count += promotion.pruned_sample_count

    return VoiceprintMaintenanceReport(
        auto_confirmed_count=auto_confirmed_count,
        global_promoted_count=global_promoted_count,
        merged_person_count=merged_person_count,
        pruned_sample_count=pruned_sample_count,
    )
