from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import Engine, inspect, text

from app.db.session import engine
from app.services.speaker_memory import build_program_key


CLASSIFICATION_NAMES = (
    'already_mapped',
    'global',
    'unresolved',
    'ambiguous',
    'stale',
    'noncanonical',
    'existing_binding_conflict',
)


def _normalized_aliases(*values: Any) -> set[str]:
    aliases: set[str] = set()
    for value in values:
        alias = build_program_key(value)
        if alias:
            aliases.add(alias)
    return aliases


def inspect_speaker_memory_project_migration(
    target_engine: Engine,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    inspector = inspect(target_engine)
    table_names = set(inspector.get_table_names())
    if 'projects' not in table_names:
        raise RuntimeError('projects table is missing; run the M1 project migration first')
    if 'speaker_memories' not in table_names:
        raise RuntimeError('speaker_memories table is missing')

    project_columns = {column['name'] for column in inspector.get_columns('projects')}
    required_project_columns = {'id', 'name', 'name_key', 'legacy_program_key'}
    missing_project_columns = required_project_columns - project_columns
    if missing_project_columns:
        missing = ', '.join(sorted(missing_project_columns))
        raise RuntimeError(f'projects table is missing required columns: {missing}')

    memory_columns = {column['name'] for column in inspector.get_columns('speaker_memories')}
    required_memory_columns = {'id', 'program_key', 'program_name'}
    missing_memory_columns = required_memory_columns - memory_columns
    if missing_memory_columns:
        missing = ', '.join(sorted(missing_memory_columns))
        raise RuntimeError(f'speaker_memories table is missing required columns: {missing}')

    had_project_id_column = 'project_id' in memory_columns
    memory_index_names = {
        index_info['name']
        for index_info in inspector.get_indexes('speaker_memories')
        if index_info.get('name')
    }
    had_project_id_index = 'ix_speaker_memories_project_id' in memory_index_names

    if apply:
        with target_engine.begin() as connection:
            if not had_project_id_column:
                connection.execute(text('ALTER TABLE speaker_memories ADD COLUMN project_id TEXT'))
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_speaker_memories_project_id '
                'ON speaker_memories(project_id)'
            ))
        memory_columns.add('project_id')

    selected_memory_columns = ['id', 'program_key', 'program_name']
    if 'project_id' in memory_columns:
        selected_memory_columns.append('project_id')

    with target_engine.connect() as connection:
        project_rows = connection.execute(text(
            'SELECT id, name, name_key, legacy_program_key FROM projects ORDER BY id'
        )).mappings().all()
        memory_rows = connection.execute(text(
            f"SELECT {', '.join(selected_memory_columns)} "
            'FROM speaker_memories ORDER BY id'
        )).mappings().all()

    project_aliases: dict[str, set[str]] = {}
    known_project_ids: set[str] = set()
    for project in project_rows:
        project_id = str(project['id'])
        known_project_ids.add(project_id)
        for alias in _normalized_aliases(
            project.get('id'),
            project.get('name'),
            project.get('name_key'),
            project.get('legacy_program_key'),
        ):
            project_aliases.setdefault(alias, set()).add(project_id)

    classified_memory_ids = {name: [] for name in CLASSIFICATION_NAMES}
    ambiguous_memories: list[dict[str, Any]] = []
    stale_memories: list[dict[str, Any]] = []
    noncanonical_memories: list[dict[str, Any]] = []
    existing_binding_conflicts: list[dict[str, Any]] = []
    candidate_mappings: list[dict[str, str]] = []
    target_references: dict[str, list[dict[str, str]]] = {}

    for memory in memory_rows:
        memory_id = str(memory['id'])
        aliases = _normalized_aliases(memory.get('program_key'), memory.get('program_name'))
        matched_project_ids = {
            project_id
            for alias in aliases
            for project_id in project_aliases.get(alias, set())
        }
        raw_existing_project_id = memory.get('project_id')
        stored_project_id = (
            str(raw_existing_project_id)
            if raw_existing_project_id is not None
            else ''
        )
        existing_project_id = stored_project_id.strip()

        if existing_project_id:
            if stored_project_id in known_project_ids:
                target_references.setdefault(existing_project_id, []).append({
                    'memory_id': memory_id,
                    'source': 'already_bound',
                })
                if matched_project_ids and matched_project_ids != {existing_project_id}:
                    classified_memory_ids['existing_binding_conflict'].append(memory_id)
                    existing_binding_conflicts.append({
                        'memory_id': memory_id,
                        'project_id': existing_project_id,
                        'candidate_project_ids': sorted(matched_project_ids),
                    })
                else:
                    classified_memory_ids['already_mapped'].append(memory_id)
                continue

            normalized_existing_id = build_program_key(existing_project_id)
            canonical_project_ids = sorted(project_aliases.get(normalized_existing_id, set()))
            if canonical_project_ids:
                classified_memory_ids['noncanonical'].append(memory_id)
                noncanonical_memories.append({
                    'memory_id': memory_id,
                    'project_id': stored_project_id,
                    'normalized_project_id': existing_project_id,
                    'canonical_project_ids': canonical_project_ids,
                })
            else:
                classified_memory_ids['stale'].append(memory_id)
                stale_memories.append({
                    'memory_id': memory_id,
                    'project_id': stored_project_id,
                })
            continue

        if not aliases:
            classified_memory_ids['global'].append(memory_id)
        elif not matched_project_ids:
            classified_memory_ids['unresolved'].append(memory_id)
        elif len(matched_project_ids) > 1:
            classified_memory_ids['ambiguous'].append(memory_id)
            ambiguous_memories.append({
                'memory_id': memory_id,
                'candidate_project_ids': sorted(matched_project_ids),
            })
        else:
            target_project_id = next(iter(matched_project_ids))
            mapping = {'memory_id': memory_id, 'project_id': target_project_id}
            candidate_mappings.append(mapping)
            target_references.setdefault(target_project_id, []).append({
                'memory_id': memory_id,
                'source': 'candidate',
            })

    duplicate_target_projects: list[dict[str, Any]] = []
    for project_id, references in sorted(target_references.items()):
        memory_ids = sorted({reference['memory_id'] for reference in references})
        if len(memory_ids) < 2:
            continue
        duplicate_target_projects.append({
            'project_id': project_id,
            'memory_ids': memory_ids,
            'already_bound_memory_ids': sorted(
                reference['memory_id']
                for reference in references
                if reference['source'] == 'already_bound'
            ),
            'candidate_memory_ids': sorted(
                reference['memory_id']
                for reference in references
                if reference['source'] == 'candidate'
            ),
        })

    duplicate_target_project_ids = [
        duplicate['project_id'] for duplicate in duplicate_target_projects
    ]
    duplicate_target_id_set = set(duplicate_target_project_ids)
    safe_mappings = [
        mapping
        for mapping in candidate_mappings
        if mapping['project_id'] not in duplicate_target_id_set
    ]
    duplicate_candidate_memory_ids = sorted(
        mapping['memory_id']
        for mapping in candidate_mappings
        if mapping['project_id'] in duplicate_target_id_set
    )

    mapped_memory_count = 0
    if apply and safe_mappings:
        with target_engine.begin() as connection:
            for mapping in safe_mappings:
                result = connection.execute(
                    text(
                        'UPDATE speaker_memories SET project_id = :project_id '
                        'WHERE id = :memory_id '
                        "AND TRIM(COALESCE(project_id, '')) = ''"
                    ),
                    mapping,
                )
                mapped_memory_count += int(result.rowcount or 0)

    classification_counts = {
        name: len(memory_ids)
        for name, memory_ids in classified_memory_ids.items()
    }
    return {
        'mode': 'apply' if apply else 'dry-run',
        'schema_mutated': bool(
            apply and (not had_project_id_column or not had_project_id_index)
        ),
        'project_id_column_added': bool(apply and not had_project_id_column),
        'project_id_index_added': bool(apply and not had_project_id_index),
        'project_count': len(project_rows),
        'speaker_memory_count': len(memory_rows),
        'classification_counts': classification_counts,
        'classified_memory_ids': classified_memory_ids,
        'already_mapped_count': classification_counts['already_mapped'],
        'global_memory_count': classification_counts['global'],
        'unresolved_memory_count': classification_counts['unresolved'],
        'ambiguous_memory_count': classification_counts['ambiguous'],
        'stale_memory_count': classification_counts['stale'],
        'noncanonical_memory_count': classification_counts['noncanonical'],
        'existing_binding_conflict_count': classification_counts['existing_binding_conflict'],
        'candidate_memory_count': len(candidate_mappings),
        'mappable_memory_count': len(safe_mappings),
        'mapped_memory_count': mapped_memory_count,
        'candidate_mappings': candidate_mappings,
        'planned_mappings': safe_mappings,
        'duplicate_candidate_memory_ids': duplicate_candidate_memory_ids,
        'duplicate_target_project_ids': duplicate_target_project_ids,
        'duplicate_target_projects': duplicate_target_projects,
        'ambiguous_memories': ambiguous_memories,
        'stale_memories': stale_memories,
        'noncanonical_memories': noncanonical_memories,
        'existing_binding_conflicts': existing_binding_conflicts,
        'legacy_fields_preserved': True,
    }


# Keep the plural spelling available alongside the model-oriented singular name.
inspect_speaker_memories_project_migration = inspect_speaker_memory_project_migration


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Preview or apply Speaker 2.0 speaker_memories.project_id migration.'
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Add speaker_memories.project_id and backfill safe, unambiguous rows.',
    )
    args = parser.parse_args()
    report = inspect_speaker_memory_project_migration(engine, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
