from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from typing import Any, Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.lexicon_term import LexiconTerm
from app.models.lexicon_term_alias import LexiconTermAlias
from app.models.lexicon_term_override import LexiconTermOverride
from app.services.lexicon_normalize import normalize_term


BUNDLE_VERSION = "runtime_lexicon_bundle.v1"
SOURCE_PRIORITY = {
    "user_forced": 100,
    "project": 80,
    "temporary": 70,
    "domain": 50,
    "global": 40,
    "builtin": 20,
}
PROTECTION_PRIORITY = {"hard": 30, "strong": 20, "soft": 10}


def build_runtime_lexicon_bundle(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    project_aliases: Iterable[str] | None = None,
    domain_id: str = 'broadcast',
    job_id: str | None = None,
    temporary_terms: Iterable[str | dict[str, Any]] | None = None,
    canonical_query_text: str | None = None,
) -> dict[str, Any]:
    aliases = tuple(
        dict.fromkeys(
            str(value or '').strip()
            for value in (project_aliases or [])
            if str(value or '').strip() and str(value or '').strip() != project_id
        )
    )
    project_scope_ids = (project_id, *aliases)
    disabled_terms = _load_disabled_terms(
        session,
        tenant_id=tenant_id,
        project_scope_ids=project_scope_ids,
    )
    disabled_term_ids = {item["term_id"] for item in disabled_terms if item["term_id"]}
    disabled_normalized_terms = {item["normalized_term"] for item in disabled_terms if not item["term_id"]}

    selected_terms: dict[str, dict[str, Any]] = {}
    conflict_report: list[dict[str, Any]] = []
    normalized_domain_id = str(domain_id or 'broadcast').strip() or 'broadcast'
    for candidate in _load_db_terms(
        session,
        tenant_id=tenant_id,
        project_scope_ids=project_scope_ids,
        domain_id=normalized_domain_id,
    ):
        primary_scope_boost = 1 if aliases and candidate.scope_type == 'project' and candidate.scope_id == project_id else 0
        _select_term(
            selected_terms,
            conflict_report,
            _term_snapshot(candidate, priority_boost=primary_scope_boost),
            disabled_term_ids,
            disabled_normalized_terms,
        )

    for candidate in _temporary_term_snapshots(temporary_terms or []):
        _select_term(selected_terms, conflict_report, candidate, disabled_term_ids, disabled_normalized_terms)

    aliases_by_term_id = _load_active_aliases(
        session,
        tenant_id=tenant_id,
        term_ids={
            str(item.get('term_id') or '')
            for item in selected_terms.values()
            if str(item.get('term_id') or '')
        },
    )
    for term in selected_terms.values():
        term_id = str(term.get('term_id') or '')
        if term_id and aliases_by_term_id.get(term_id):
            term['aliases'] = aliases_by_term_id[term_id]

    learned_corrections = sorted(
        (
            {
                'source_text': source,
                'replacement_text': term['display_text'],
                'term_id': term['term_id'],
                'scope_type': term['scope_type'],
                'scope_id': term['scope_id'],
                'confidence': float(details.get('consistency') or 0.0),
                'evidence_count': int(details.get('evidence_count') or 0),
                'project_count': int(details.get('project_count') or 0),
            }
            for term in selected_terms.values()
            for source, details in term.get('learned_sources', {}).items()
            if source in {
                normalize_term(alias) for alias in term.get('aliases') or []
            }
        ),
        key=lambda item: (
            item['source_text'],
            -SOURCE_PRIORITY.get(item['scope_type'], 0),
            item['replacement_text'],
            item['term_id'] or '',
        ),
    )
    canonical_corrections: list[dict[str, Any]] = []
    if normalized_domain_id == 'classical_poetry' and str(canonical_query_text or '').strip():
        from app.services.canonical_passages import retrieve_canonical_corrections

        canonical_corrections = [
            item.as_dict()
            for item in retrieve_canonical_corrections(
                session,
                tenant_id=tenant_id,
                domain_id=normalized_domain_id,
                query_text=str(canonical_query_text),
            )
        ]

    terms = sorted(
        selected_terms.values(),
        key=lambda item: (
            item["normalized_term"],
            item["scope_type"],
            item["protection_level"],
            item["term_id"] or "",
        ),
    )
    disabled_terms = sorted(disabled_terms, key=lambda item: (item["normalized_term"], item["override_id"]))
    conflict_report = sorted(conflict_report, key=lambda item: (item["normalized_term"], item["discarded"]["term_id"] or ""))

    source_counts = Counter(term["scope_type"] for term in terms)
    protection_counts = Counter(term["protection_level"] for term in terms)
    hash_payload = {
        "bundle_version": BUNDLE_VERSION,
        "terms": terms,
        "disabled_terms": disabled_terms,
        "conflict_report": conflict_report,
        "learned_corrections": learned_corrections,
        "canonical_corrections": canonical_corrections,
    }

    return {
        "bundle_version": BUNDLE_VERSION,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "domain_id": normalized_domain_id,
        "project_aliases": list(aliases),
        "job_id": job_id,
        "generated_at": datetime.utcnow().isoformat(timespec="microseconds") + "Z",
        "term_set_hash": _stable_hash_json(hash_payload),
        "source_counts": dict(sorted(source_counts.items())),
        "protection_counts": dict(sorted(protection_counts.items())),
        "terms": terms,
        "disabled_terms": disabled_terms,
        "conflict_report": conflict_report,
        "learned_corrections": learned_corrections,
        "canonical_corrections": canonical_corrections,
    }


def _load_db_terms(
    session: Session,
    *,
    tenant_id: str,
    project_scope_ids: tuple[str, ...],
    domain_id: str,
) -> list[LexiconTerm]:
    statement = (
        select(LexiconTerm)
        .where(
            LexiconTerm.tenant_id == tenant_id,
            LexiconTerm.status == "active",
            or_(
                LexiconTerm.scope_type.in_(("global", "builtin")),
                (LexiconTerm.scope_type == "domain") & (LexiconTerm.scope_id == domain_id),
                (LexiconTerm.scope_type == "project") & (LexiconTerm.scope_id.in_(project_scope_ids)),
            ),
        )
        .order_by(LexiconTerm.created_at.asc(), LexiconTerm.id.asc())
    )
    return list(session.scalars(statement))


def _load_disabled_terms(
    session: Session,
    *,
    tenant_id: str,
    project_scope_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    statement = (
        select(LexiconTermOverride)
        .where(
            LexiconTermOverride.tenant_id == tenant_id,
            LexiconTermOverride.action == "disable",
            LexiconTermOverride.scope_type == "project",
            LexiconTermOverride.scope_id.in_(project_scope_ids),
        )
        .order_by(LexiconTermOverride.created_at.asc(), LexiconTermOverride.id.asc())
    )
    return [
        {
            "override_id": override.id,
            "term_id": override.term_id,
            "normalized_term": override.normalized_term,
            "scope_type": override.scope_type,
            "scope_id": override.scope_id,
            "action": override.action,
            "reason": override.reason,
            "created_by": override.created_by,
        }
        for override in session.scalars(statement)
    ]


def _load_active_aliases(
    session: Session,
    *,
    tenant_id: str,
    term_ids: set[str],
) -> dict[str, list[str]]:
    if not term_ids:
        return {}
    statement = (
        select(LexiconTermAlias)
        .where(
            LexiconTermAlias.tenant_id == tenant_id,
            LexiconTermAlias.term_id.in_(term_ids),
            LexiconTermAlias.status == 'active',
        )
        .order_by(
            LexiconTermAlias.term_id.asc(),
            LexiconTermAlias.normalized_alias.asc(),
            LexiconTermAlias.created_at.asc(),
            LexiconTermAlias.id.asc(),
        )
    )
    result: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for alias in session.scalars(statement):
        normalized = normalize_term(alias.alias_text)
        if not normalized or normalized in seen.setdefault(alias.term_id, set()):
            continue
        seen[alias.term_id].add(normalized)
        result.setdefault(alias.term_id, []).append(alias.alias_text)
    return result


def _term_snapshot(term: LexiconTerm, *, priority_boost: int = 0) -> dict[str, Any]:
    source_type = term.scope_type if term.scope_type in SOURCE_PRIORITY else term.source_type
    learned_sources = {
        str(source): dict(details)
        for source, details in dict(
            (term.metadata_json or {}).get('learned_sources') or {}
        ).items()
        if str(source or '').strip() and isinstance(details, dict)
    }
    return {
        "term_id": term.id,
        "normalized_term": term.normalized_term,
        "display_text": term.display_text,
        "term_type": term.term_type,
        "scope_type": term.scope_type,
        "scope_id": term.scope_id,
        "source_type": source_type,
        "protection_level": term.protection_level,
        "priority": SOURCE_PRIORITY.get(source_type or "", 0) + priority_boost,
        "aliases": [],
        "learned_sources": learned_sources,
    }


def _temporary_term_snapshots(temporary_terms: Iterable[str | dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots = []
    for item in temporary_terms:
        if isinstance(item, str):
            display_text = item
            term_type = "term"
            protection_level = "strong"
            aliases: list[str] = []
        else:
            display_text = str(item.get("display_text") or item.get("normalized_term") or "")
            term_type = str(item.get("term_type") or "term")
            protection_level = str(item.get("protection_level") or "strong")
            aliases = [
                str(alias or '').strip()
                for alias in (item.get('aliases') or [])
                if str(alias or '').strip()
            ]
        normalized = normalize_term(
            (item.get('normalized_term') or display_text)
            if isinstance(item, dict)
            else display_text
        )
        if not normalized:
            continue
        term_id = _temporary_term_id(
            normalized_term=normalized,
            display_text=display_text,
            term_type=term_type,
            protection_level=protection_level,
        )
        snapshots.append(
            {
                "term_id": term_id,
                "normalized_term": normalized,
                "display_text": display_text,
                "term_type": term_type,
                "scope_type": "temporary",
                "scope_id": None,
                "source_type": "temporary",
                "protection_level": protection_level,
                "priority": SOURCE_PRIORITY["temporary"],
                "aliases": sorted(
                    {
                        alias
                        for alias in aliases
                        if normalize_term(alias) and normalize_term(alias) != normalized
                    },
                    key=lambda value: (normalize_term(value), value),
                ),
            }
        )
    return sorted(
        snapshots,
        key=lambda item: (
            item["normalized_term"],
            -PROTECTION_PRIORITY.get(item["protection_level"], 0),
            item["display_text"],
            item["term_type"],
            item["term_id"],
        ),
    )


def _select_term(
    selected_terms: dict[str, dict[str, Any]],
    conflict_report: list[dict[str, Any]],
    candidate: dict[str, Any],
    disabled_term_ids: set[str],
    disabled_normalized_terms: set[str],
) -> None:
    normalized = candidate["normalized_term"]
    if candidate["term_id"] in disabled_term_ids or normalized in disabled_normalized_terms:
        return

    existing = selected_terms.get(normalized)
    if existing is None:
        selected_terms[normalized] = candidate
        return

    if _term_sort_key(candidate) > _term_sort_key(existing):
        selected_terms[normalized] = candidate
        kept, discarded = candidate, existing
    else:
        kept, discarded = existing, candidate
    kept['aliases'] = sorted(
        {
            str(alias or '').strip()
            for alias in [*(kept.get('aliases') or []), *(discarded.get('aliases') or [])]
            if str(alias or '').strip()
        },
        key=lambda value: (normalize_term(value), value),
    )
    conflict_report.append(
        {
            "normalized_term": normalized,
            "kept": _conflict_term(kept),
            "discarded": _conflict_term(discarded),
            "reason": "higher_source_priority",
        }
    )


def _term_sort_key(term: dict[str, Any]) -> tuple[int, str]:
    return (
        int(term.get("priority") or 0),
        PROTECTION_PRIORITY.get(str(term.get("protection_level") or ""), 0),
        str(term.get("display_text") or ""),
        str(term.get("term_type") or ""),
        str(term.get("term_id") or ""),
    )


def _conflict_term(term: dict[str, Any]) -> dict[str, Any]:
    return {
        "term_id": term["term_id"],
        "scope_type": term["scope_type"],
        "source_type": term["source_type"],
        "protection_level": term["protection_level"],
        "priority": term["priority"],
    }


def _stable_hash_json(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _temporary_term_id(
    *,
    normalized_term: str,
    display_text: str,
    term_type: str,
    protection_level: str,
) -> str:
    payload = {
        "display_text": display_text,
        "normalized_term": normalized_term,
        "protection_level": protection_level,
        "source_type": "temporary",
        "term_type": term_type,
    }
    return f"temporary:{_stable_hash_json(payload)[:16]}"
