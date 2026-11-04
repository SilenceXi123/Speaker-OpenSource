from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

import jieba
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.knowledge_correction_evidence import KnowledgeCorrectionEvidence
from app.models.lexicon_term import LexiconTerm
from app.models.lexicon_term_alias import LexiconTermAlias
from app.models.project import Project
from app.services.lexicon_normalize import normalize_term, stable_hash_json


DEFAULT_KNOWLEDGE_DOMAIN = 'broadcast'
_PARTICLES = {'的', '地', '得'}
_SENTENCE_MARKERS = (
    '我们', '你们', '他们', '她们', '大家', '这个', '那个', '这些', '那些',
    '这里', '那里', '如何', '什么', '怎么', '为什么', '就是', '可以', '需要',
)
_PROMOTION_RULES = {
    'project': {'min_jobs': 2, 'min_projects': 1, 'consistency': 1.0},
    'domain': {'min_jobs': 6, 'min_projects': 3, 'consistency': 0.98},
    'global': {'min_jobs': 10, 'min_projects': 5, 'consistency': 0.98},
}


@dataclass(frozen=True, slots=True)
class LearnedCorrection:
    segment_id: str
    source_text: str
    corrected_text: str
    raw_context: str
    corrected_context: str
    promotable: bool


@dataclass(frozen=True, slots=True)
class KnowledgeLearningResult:
    evidence_added: int = 0
    evidence_duplicated: int = 0
    corrections_ignored: int = 0
    promoted_scopes: tuple[str, ...] = ()
    disabled_scopes: tuple[str, ...] = ()
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'evidence_added': self.evidence_added,
            'evidence_duplicated': self.evidence_duplicated,
            'corrections_ignored': self.corrections_ignored,
            'promoted_scopes': list(self.promoted_scopes),
            'disabled_scopes': list(self.disabled_scopes),
            'skipped_reason': self.skipped_reason,
        }


def learn_from_user_edit_patch(
    session: Session,
    *,
    job: Job,
    edit_patch: dict[str, Any],
) -> KnowledgeLearningResult:
    project_id = str(job.project_id or '').strip()
    if not project_id:
        return KnowledgeLearningResult(skipped_reason='job_has_no_project')
    project = session.get(Project, project_id)
    if project is None:
        return KnowledgeLearningResult(skipped_reason='project_not_found')

    corrections = extract_learned_corrections(edit_patch)
    if not corrections:
        return KnowledgeLearningResult(skipped_reason='no_safe_text_corrections')
    tenant_id = str(project.tenant_id or 'default')
    domain_id = str(project.knowledge_domain or DEFAULT_KNOWLEDGE_DOMAIN)
    patch_source = str(edit_patch.get('source') or 'unknown')
    added = 0
    duplicated = 0
    ignored = 0
    affected_sources: set[str] = set()
    for correction in corrections:
        normalized_source = normalize_term(correction.source_text)
        normalized_corrected = normalize_term(correction.corrected_text)
        if not normalized_source or not normalized_corrected or normalized_source == normalized_corrected:
            ignored += 1
            continue
        fingerprint = stable_hash_json({
            'job_id': job.id,
            'segment_id': correction.segment_id,
            'source': normalized_source,
            'corrected': normalized_corrected,
            'patch_source': patch_source,
        })
        existing = session.scalar(
            select(KnowledgeCorrectionEvidence).where(
                KnowledgeCorrectionEvidence.tenant_id == tenant_id,
                KnowledgeCorrectionEvidence.fingerprint == fingerprint,
            )
        )
        if existing is not None:
            duplicated += 1
            continue

        inverse_rows = list(session.scalars(
            select(KnowledgeCorrectionEvidence).where(
                KnowledgeCorrectionEvidence.tenant_id == tenant_id,
                KnowledgeCorrectionEvidence.project_id == project_id,
                KnowledgeCorrectionEvidence.normalized_source == normalized_corrected,
                KnowledgeCorrectionEvidence.normalized_corrected == normalized_source,
                KnowledgeCorrectionEvidence.evidence_status == 'active',
            )
        ))
        evidence_status = 'reversal' if inverse_rows else 'active'
        for inverse in inverse_rows:
            inverse.evidence_status = 'reversed'
            affected_sources.add(inverse.normalized_source)

        session.add(KnowledgeCorrectionEvidence(
            tenant_id=tenant_id,
            project_id=project_id,
            domain_id=domain_id,
            job_id=str(job.id),
            segment_id=correction.segment_id,
            patch_source=patch_source,
            source_text=correction.source_text,
            corrected_text=correction.corrected_text,
            normalized_source=normalized_source,
            normalized_corrected=normalized_corrected,
            evidence_status=evidence_status,
            promotable=correction.promotable,
            confidence=1.0,
            fingerprint=fingerprint,
            raw_context=correction.raw_context,
            corrected_context=correction.corrected_context,
            metadata_json={'schema_version': 1},
        ))
        added += 1
        affected_sources.add(normalized_source)

    session.flush()
    promoted: set[str] = set()
    disabled: set[str] = set()
    for source in sorted(affected_sources):
        scope_changes = _reevaluate_source(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            domain_id=domain_id,
            normalized_source=source,
        )
        promoted.update(scope_changes['promoted'])
        disabled.update(scope_changes['disabled'])
    session.flush()
    return KnowledgeLearningResult(
        evidence_added=added,
        evidence_duplicated=duplicated,
        corrections_ignored=ignored,
        promoted_scopes=tuple(sorted(promoted)),
        disabled_scopes=tuple(sorted(disabled)),
    )


def extract_learned_corrections(
    edit_patch: dict[str, Any],
) -> tuple[LearnedCorrection, ...]:
    results: list[LearnedCorrection] = []
    for change in edit_patch.get('changes') or []:
        if not isinstance(change, dict) or change.get('field') != 'text':
            continue
        before = str(change.get('before') or '').strip()
        after = str(change.get('after') or '').strip()
        if not before or not after or before == after:
            continue
        before_tokens = tuple(jieba.lcut(normalize_term(before)))
        after_tokens = tuple(jieba.lcut(normalize_term(after)))
        matcher = SequenceMatcher(a=before_tokens, b=after_tokens, autojunk=False)
        for operation, left_start, left_end, right_start, right_end in matcher.get_opcodes():
            if operation != 'replace':
                continue
            source_text = ''.join(before_tokens[left_start:left_end])
            corrected_text = ''.join(after_tokens[right_start:right_end])
            if not _safe_correction_pair(source_text, corrected_text):
                continue
            results.append(LearnedCorrection(
                segment_id=str(change.get('segment_id') or ''),
                source_text=source_text,
                corrected_text=corrected_text,
                raw_context=before,
                corrected_context=after,
                promotable=_promotable_pair(source_text, corrected_text),
            ))
    deduplicated = {
        (item.segment_id, normalize_term(item.source_text), normalize_term(item.corrected_text)): item
        for item in results
    }
    return tuple(deduplicated[key] for key in sorted(deduplicated))


def _safe_correction_pair(source: str, corrected: str) -> bool:
    if not source or not corrected or source == corrected:
        return False
    if len(source) > 16 or len(corrected) > 16:
        return False
    return all(
        char.isalnum() or '\u3400' <= char <= '\u9fff'
        for char in source + corrected
    )


def _promotable_pair(source: str, corrected: str) -> bool:
    if source in _PARTICLES or corrected in _PARTICLES:
        return False
    if len(source) < 2 or len(corrected) < 2:
        return False
    return not any(marker in corrected for marker in _SENTENCE_MARKERS)


def _reevaluate_source(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    domain_id: str,
    normalized_source: str,
) -> dict[str, set[str]]:
    changes = {'promoted': set(), 'disabled': set()}
    scope_specs = (
        ('project', project_id),
        ('domain', domain_id),
        ('global', None),
    )
    for scope_type, scope_id in scope_specs:
        rows = _scope_evidence(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            domain_id=domain_id,
            scope_type=scope_type,
            normalized_source=normalized_source,
        )
        decision = _promotion_decision(rows, scope_type)
        if decision is None:
            if _disable_learned_alias(
                session,
                tenant_id=tenant_id,
                scope_type=scope_type,
                scope_id=scope_id,
                normalized_source=normalized_source,
            ):
                changes['disabled'].add(scope_type)
            continue
        corrected, job_count, project_count, consistency = decision
        _activate_learned_term(
            session,
            tenant_id=tenant_id,
            scope_type=scope_type,
            scope_id=scope_id,
            normalized_source=normalized_source,
            normalized_corrected=corrected,
            evidence_count=job_count,
            project_count=project_count,
            consistency=consistency,
        )
        changes['promoted'].add(scope_type)
    return changes


def _scope_evidence(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    domain_id: str,
    scope_type: str,
    normalized_source: str,
) -> list[KnowledgeCorrectionEvidence]:
    statement = select(KnowledgeCorrectionEvidence).where(
        KnowledgeCorrectionEvidence.tenant_id == tenant_id,
        KnowledgeCorrectionEvidence.normalized_source == normalized_source,
        KnowledgeCorrectionEvidence.evidence_status == 'active',
        KnowledgeCorrectionEvidence.promotable.is_(True),
    )
    if scope_type == 'project':
        statement = statement.where(KnowledgeCorrectionEvidence.project_id == project_id)
    elif scope_type == 'domain':
        statement = statement.where(KnowledgeCorrectionEvidence.domain_id == domain_id)
    return list(session.scalars(statement))


def _promotion_decision(
    rows: Iterable[KnowledgeCorrectionEvidence],
    scope_type: str,
) -> tuple[str, int, int, float] | None:
    distinct = {
        (row.job_id, row.project_id, row.normalized_corrected)
        for row in rows
    }
    if not distinct:
        return None
    job_targets: dict[str, str] = {}
    job_projects: dict[str, str] = {}
    for job_id, project_id, target in sorted(distinct):
        previous = job_targets.get(job_id)
        if previous is not None and previous != target:
            job_targets[job_id] = '__conflict__'
        else:
            job_targets[job_id] = target
            job_projects[job_id] = project_id
    counts = Counter(target for target in job_targets.values() if target != '__conflict__')
    if not counts:
        return None
    corrected, winner_jobs = min(counts.items(), key=lambda item: (-item[1], item[0]))
    total_jobs = len(job_targets)
    consistency = winner_jobs / total_jobs
    winner_projects = len({
        job_projects[job_id]
        for job_id, target in job_targets.items()
        if target == corrected
    })
    rule = _PROMOTION_RULES[scope_type]
    if (
        winner_jobs < rule['min_jobs']
        or winner_projects < rule['min_projects']
        or consistency + 1e-12 < rule['consistency']
    ):
        return None
    return corrected, winner_jobs, winner_projects, round(consistency, 6)


def _activate_learned_term(
    session: Session,
    *,
    tenant_id: str,
    scope_type: str,
    scope_id: str | None,
    normalized_source: str,
    normalized_corrected: str,
    evidence_count: int,
    project_count: int,
    consistency: float,
) -> LexiconTerm:
    term = session.scalar(select(LexiconTerm).where(
        LexiconTerm.tenant_id == tenant_id,
        LexiconTerm.scope_type == scope_type,
        LexiconTerm.scope_id == scope_id,
        LexiconTerm.normalized_term == normalized_corrected,
    ).order_by(LexiconTerm.created_at.asc(), LexiconTerm.id.asc()))
    if term is None:
        term = LexiconTerm(
            tenant_id=tenant_id,
            scope_type=scope_type,
            scope_id=scope_id,
            normalized_term=normalized_corrected,
            display_text=normalized_corrected,
            term_type='learned_correction',
            status='active',
            protection_level='strong' if scope_type == 'project' else 'soft',
            review_status='auto_verified',
            source_type='auto_user_edit',
            source_ref=f'correction:{normalized_source}',
            created_by='knowledge_learning',
            reviewed_by='knowledge_learning',
            confidence=consistency,
        )
        session.add(term)
        session.flush()
    term.status = 'active'
    term.confidence = consistency
    term.occurrence_count = max(int(term.occurrence_count or 0), evidence_count)
    metadata = dict(term.metadata_json or {})
    learned_sources = dict(metadata.get('learned_sources') or {})
    learned_sources[normalized_source] = {
        'evidence_count': evidence_count,
        'project_count': project_count,
        'consistency': consistency,
        'scope_type': scope_type,
    }
    metadata['knowledge_schema_version'] = 1
    metadata['learned_sources'] = learned_sources
    term.metadata_json = metadata

    _disable_competing_learned_aliases(
        session,
        tenant_id=tenant_id,
        scope_type=scope_type,
        scope_id=scope_id,
        normalized_source=normalized_source,
        winning_term_id=term.id,
    )

    alias = session.scalar(select(LexiconTermAlias).where(
        LexiconTermAlias.tenant_id == tenant_id,
        LexiconTermAlias.term_id == term.id,
        LexiconTermAlias.normalized_alias == normalized_source,
    ).order_by(LexiconTermAlias.created_at.asc(), LexiconTermAlias.id.asc()))
    if alias is None and normalized_source != normalized_corrected:
        session.add(LexiconTermAlias(
            tenant_id=tenant_id,
            term_id=term.id,
            alias_text=normalized_source,
            normalized_alias=normalized_source,
            status='active',
        ))
    elif alias is not None:
        alias.status = 'active'
    session.flush()
    return term


def _disable_competing_learned_aliases(
    session: Session,
    *,
    tenant_id: str,
    scope_type: str,
    scope_id: str | None,
    normalized_source: str,
    winning_term_id: str,
) -> None:
    competing_terms = list(session.scalars(select(LexiconTerm).where(
        LexiconTerm.tenant_id == tenant_id,
        LexiconTerm.scope_type == scope_type,
        LexiconTerm.scope_id == scope_id,
        LexiconTerm.id != winning_term_id,
    )))
    for term in competing_terms:
        learned_sources = dict((term.metadata_json or {}).get('learned_sources') or {})
        if normalized_source not in learned_sources:
            continue
        aliases = list(session.scalars(select(LexiconTermAlias).where(
            LexiconTermAlias.tenant_id == tenant_id,
            LexiconTermAlias.term_id == term.id,
        )))
        for alias in aliases:
            if alias.normalized_alias == normalized_source:
                alias.status = 'disabled'
        if (
            term.source_type == 'auto_user_edit'
            and not any(alias.status == 'active' for alias in aliases)
        ):
            term.status = 'disabled'


def _disable_learned_alias(
    session: Session,
    *,
    tenant_id: str,
    scope_type: str,
    scope_id: str | None,
    normalized_source: str,
) -> bool:
    terms = list(session.scalars(select(LexiconTerm).where(
        LexiconTerm.tenant_id == tenant_id,
        LexiconTerm.scope_type == scope_type,
        LexiconTerm.scope_id == scope_id,
    )))
    changed = False
    for term in terms:
        learned_sources = dict((term.metadata_json or {}).get('learned_sources') or {})
        if normalized_source not in learned_sources:
            continue
        aliases = list(session.scalars(select(LexiconTermAlias).where(
            LexiconTermAlias.tenant_id == tenant_id,
            LexiconTermAlias.term_id == term.id,
            LexiconTermAlias.normalized_alias == normalized_source,
        )))
        for alias in aliases:
            if alias.status == 'active':
                alias.status = 'disabled'
                changed = True
        active_alias_count = sum(
            1 for alias in session.scalars(select(LexiconTermAlias).where(
                LexiconTermAlias.tenant_id == tenant_id,
                LexiconTermAlias.term_id == term.id,
            ))
            if alias.status == 'active'
        )
        if term.source_type == 'auto_user_edit' and not active_alias_count:
            term.status = 'disabled'
    return changed
