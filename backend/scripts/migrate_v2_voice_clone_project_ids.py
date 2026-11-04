from __future__ import annotations

import argparse
import json
from typing import Any, Callable, Optional

from sqlalchemy import Engine, inspect, text

from app.db.session import engine
from app.services.speaker_memory import build_program_key


CLASSIFICATION_NAMES = (
    'already_mapped',
    'parent_mappable',
    'alias_mappable',
    'unresolved',
    'ambiguous',
    'stale',
    'orphan_parent',
    'parent_conflict',
    'existing_binding_conflict',
)

TABLE_SPECS = (
    ('samples', 'voice_clone_samples'),
    ('profiles', 'voice_clone_profiles'),
    ('records', 'voice_clone_synthesis_records'),
)


def _normalized_aliases(*values: Any) -> set[str]:
    aliases: set[str] = set()
    for value in values:
        alias = build_program_key(value)
        if alias:
            aliases.add(alias)
    return aliases


def inspect_voice_clone_project_migration(
    target_engine: Engine,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    inspector = inspect(target_engine)
    table_names = set(inspector.get_table_names())
    required_tables = {'projects', *(table_name for _, table_name in TABLE_SPECS)}
    missing_tables = required_tables - table_names
    if missing_tables:
        raise RuntimeError(f"missing required tables: {', '.join(sorted(missing_tables))}")

    project_columns = {column['name'] for column in inspector.get_columns('projects')}
    required_project_columns = {'id', 'name', 'name_key', 'legacy_program_key'}
    missing_project_columns = required_project_columns - project_columns
    if missing_project_columns:
        raise RuntimeError(
            f"projects table is missing required columns: {', '.join(sorted(missing_project_columns))}"
        )

    required_columns = {
        'voice_clone_samples': {'id', 'program_key', 'program_name'},
        'voice_clone_profiles': {
            'id',
            'program_key',
            'program_name',
            'source_clone_sample_id',
            'source_sample_id',
            'person_id',
        },
        'voice_clone_synthesis_records': {
            'id',
            'program_key',
            'program_name',
            'voice_clone_profile_id',
        },
    }
    table_columns: dict[str, set[str]] = {}
    schema_before: dict[str, dict[str, bool]] = {}
    for _, table_name in TABLE_SPECS:
        columns = {column['name'] for column in inspector.get_columns(table_name)}
        missing_columns = required_columns[table_name] - columns
        if missing_columns:
            raise RuntimeError(
                f"{table_name} table is missing required columns: {', '.join(sorted(missing_columns))}"
            )
        index_name = f'ix_{table_name}_project_id'
        index_names = {
            index_info['name']
            for index_info in inspector.get_indexes(table_name)
            if index_info.get('name')
        }
        table_columns[table_name] = columns
        schema_before[table_name] = {
            'had_project_id_column': 'project_id' in columns,
            'had_project_id_index': index_name in index_names,
        }

    if apply:
        with target_engine.begin() as connection:
            for _, table_name in TABLE_SPECS:
                if not schema_before[table_name]['had_project_id_column']:
                    connection.execute(text(f'ALTER TABLE {table_name} ADD COLUMN project_id TEXT'))
                connection.execute(text(
                    f'CREATE INDEX IF NOT EXISTS ix_{table_name}_project_id '
                    f'ON {table_name}(project_id)'
                ))
                table_columns[table_name].add('project_id')

    with target_engine.connect() as connection:
        project_rows = [dict(row) for row in connection.execute(text(
            'SELECT id, name, name_key, legacy_program_key FROM projects ORDER BY id'
        )).mappings().all()]
        sample_rows = _select_rows(
            connection,
            'voice_clone_samples',
            ('id', 'program_key', 'program_name', 'project_id'),
            table_columns['voice_clone_samples'],
        )
        profile_rows = _select_rows(
            connection,
            'voice_clone_profiles',
            (
                'id',
                'program_key',
                'program_name',
                'project_id',
                'source_clone_sample_id',
                'source_sample_id',
                'person_id',
            ),
            table_columns['voice_clone_profiles'],
        )
        record_rows = _select_rows(
            connection,
            'voice_clone_synthesis_records',
            (
                'id',
                'program_key',
                'program_name',
                'project_id',
                'voice_clone_profile_id',
            ),
            table_columns['voice_clone_synthesis_records'],
        )
        people_rows = _select_optional_rows(
            connection,
            table_names,
            inspector,
            'people',
            ('id', 'project_id', 'program_key', 'program_name'),
        )
        voiceprint_rows = _select_optional_rows(
            connection,
            table_names,
            inspector,
            'voiceprint_samples',
            ('id', 'person_id'),
        )

    known_project_ids = {str(project['id']) for project in project_rows}
    project_aliases: dict[str, set[str]] = {}
    for project in project_rows:
        project_id = str(project['id'])
        for alias in _normalized_aliases(
            project.get('id'),
            project.get('name'),
            project.get('name_key'),
            project.get('legacy_program_key'),
        ):
            project_aliases.setdefault(alias, set()).add(project_id)

    def alias_project_ids(row: dict[str, Any]) -> set[str]:
        aliases = _normalized_aliases(row.get('program_key'), row.get('program_name'))
        return {
            project_id
            for alias in aliases
            for project_id in project_aliases.get(alias, set())
        }

    table_reports = {
        key: _empty_table_report(table_name)
        for key, table_name in TABLE_SPECS
    }
    sample_effective_ids: dict[str, str] = {}
    profile_effective_ids: dict[str, str] = {}
    people_by_id = {str(row['id']): row for row in people_rows}
    voiceprints_by_id = {str(row['id']): row for row in voiceprint_rows}
    samples_by_id = {str(row['id']): row for row in sample_rows}
    profiles_by_id = {str(row['id']): row for row in profile_rows}

    def person_project_ids(person: dict[str, Any]) -> set[str]:
        existing_project_id = str(person.get('project_id') or '').strip()
        if existing_project_id:
            return {existing_project_id} if existing_project_id in known_project_ids else set()
        return alias_project_ids(person)

    for sample in sample_rows:
        entity_id = str(sample['id'])
        report = table_reports['samples']
        existing_result = _classify_existing_project_id(
            report,
            entity_id,
            sample.get('project_id'),
            known_project_ids,
        )
        if existing_result:
            if existing_result == 'already_mapped':
                sample_effective_ids[entity_id] = str(sample.get('project_id')).strip()
            continue
        candidates = alias_project_ids(sample)
        mapping = _classify_alias_candidate(report, entity_id, candidates)
        if mapping:
            sample_effective_ids[entity_id] = mapping['project_id']

    for profile in profile_rows:
        entity_id = str(profile['id'])
        report = table_reports['profiles']
        parent_state = _profile_parent_state(
            profile,
            samples_by_id=samples_by_id,
            sample_effective_ids=sample_effective_ids,
            people_by_id=people_by_id,
            voiceprints_by_id=voiceprints_by_id,
            person_project_ids=person_project_ids,
        )
        existing_project_id = str(profile.get('project_id') or '').strip()
        if existing_project_id:
            if existing_project_id not in known_project_ids:
                _classify_existing_project_id(
                    report,
                    entity_id,
                    existing_project_id,
                    known_project_ids,
                )
                continue
            if _classify_existing_parent_conflict(
                report,
                entity_id,
                existing_project_id,
                parent_state,
            ):
                continue
            report['classified_ids']['already_mapped'].append(entity_id)
            profile_effective_ids[entity_id] = existing_project_id
            continue

        alias_candidates = alias_project_ids(profile)
        mapping = _classify_with_parent(
            report,
            entity_id,
            parent_state,
            alias_candidates,
        )
        if mapping:
            profile_effective_ids[entity_id] = mapping['project_id']

    for record in record_rows:
        entity_id = str(record['id'])
        report = table_reports['records']
        profile_id = str(record.get('voice_clone_profile_id') or '').strip()
        if not profile_id or profile_id not in profiles_by_id:
            parent_state = {
                'has_parent': bool(profile_id),
                'project_ids': set(),
                'orphan_parent_ids': [profile_id or '<empty>'],
                'unresolved_parent_ids': [],
            }
        else:
            parent_project_id = profile_effective_ids.get(profile_id)
            parent_state = {
                'has_parent': True,
                'project_ids': {parent_project_id} if parent_project_id else set(),
                'orphan_parent_ids': [],
                'unresolved_parent_ids': [] if parent_project_id else [profile_id],
            }
        existing_project_id = str(record.get('project_id') or '').strip()
        if existing_project_id:
            if existing_project_id not in known_project_ids:
                _classify_existing_project_id(
                    report,
                    entity_id,
                    existing_project_id,
                    known_project_ids,
                )
                continue
            if _classify_existing_parent_conflict(
                report,
                entity_id,
                existing_project_id,
                parent_state,
            ):
                continue
            report['classified_ids']['already_mapped'].append(entity_id)
            continue
        _classify_with_parent(
            report,
            entity_id,
            parent_state,
            alias_project_ids(record),
        )

    mapped_counts: dict[str, int] = {key: 0 for key, _ in TABLE_SPECS}
    if apply:
        with target_engine.begin() as connection:
            for key, table_name in TABLE_SPECS:
                for mapping in table_reports[key]['planned_mappings']:
                    result = connection.execute(
                        text(
                            f'UPDATE {table_name} SET project_id = :project_id '
                            'WHERE id = :entity_id '
                            "AND TRIM(COALESCE(project_id, '')) = ''"
                        ),
                        mapping,
                    )
                    mapped_counts[key] += int(result.rowcount or 0)

    aggregate_ids = {name: [] for name in CLASSIFICATION_NAMES}
    aggregate_counts = {name: 0 for name in CLASSIFICATION_NAMES}
    for key, table_name in TABLE_SPECS:
        report = table_reports[key]
        report['classification_counts'] = {
            name: len(report['classified_ids'][name])
            for name in CLASSIFICATION_NAMES
        }
        report['row_count'] = {
            'samples': len(sample_rows),
            'profiles': len(profile_rows),
            'records': len(record_rows),
        }[key]
        report['mappable_count'] = len(report['planned_mappings'])
        report['mapped_count'] = mapped_counts[key]
        for name in CLASSIFICATION_NAMES:
            aggregate_counts[name] += report['classification_counts'][name]
            aggregate_ids[name].extend(
                {'table': table_name, 'id': entity_id}
                for entity_id in report['classified_ids'][name]
            )

    columns_added = [
        table_name
        for _, table_name in TABLE_SPECS
        if apply and not schema_before[table_name]['had_project_id_column']
    ]
    indexes_added = [
        f'ix_{table_name}_project_id'
        for _, table_name in TABLE_SPECS
        if apply and not schema_before[table_name]['had_project_id_index']
    ]
    total_mappable = sum(report['mappable_count'] for report in table_reports.values())
    total_mapped = sum(mapped_counts.values())
    reports_by_table = {
        table_name: table_reports[key]
        for key, table_name in TABLE_SPECS
    }
    return {
        'mode': 'apply' if apply else 'dry-run',
        'schema_mutated': bool(columns_added or indexes_added),
        'project_id_columns_added': columns_added,
        'project_id_indexes_added': indexes_added,
        'project_count': len(project_rows),
        'sample_count': len(sample_rows),
        'profile_count': len(profile_rows),
        'record_count': len(record_rows),
        'classification_counts': aggregate_counts,
        'classified_entities': aggregate_ids,
        'table_reports': table_reports,
        'tables': reports_by_table,
        'samples': table_reports['samples'],
        'profiles': table_reports['profiles'],
        'records': table_reports['records'],
        'voice_clone_samples': table_reports['samples'],
        'voice_clone_profiles': table_reports['profiles'],
        'voice_clone_synthesis_records': table_reports['records'],
        'mappable_count': total_mappable,
        'mapped_count': total_mapped,
        'already_mapped_count': aggregate_counts['already_mapped'],
        'parent_mappable_count': aggregate_counts['parent_mappable'],
        'alias_mappable_count': aggregate_counts['alias_mappable'],
        'unresolved_count': aggregate_counts['unresolved'],
        'ambiguous_count': aggregate_counts['ambiguous'],
        'stale_count': aggregate_counts['stale'],
        'orphan_parent_count': aggregate_counts['orphan_parent'],
        'parent_conflict_count': aggregate_counts['parent_conflict'],
        'existing_binding_conflict_count': aggregate_counts['existing_binding_conflict'],
        'legacy_fields_preserved': True,
        'external_files_accessed': False,
        'cloud_services_accessed': False,
    }


def _empty_table_report(table_name: str) -> dict[str, Any]:
    return {
        'table': table_name,
        'classified_ids': {name: [] for name in CLASSIFICATION_NAMES},
        'classification_counts': {name: 0 for name in CLASSIFICATION_NAMES},
        'planned_mappings': [],
        'ambiguous_entities': [],
        'stale_entities': [],
        'orphan_parents': [],
        'parent_conflicts': [],
        'existing_binding_conflicts': [],
    }


def _select_rows(connection, table_name: str, columns: tuple[str, ...], available: set[str]):
    selected = [column for column in columns if column in available]
    return [dict(row) for row in connection.execute(text(
        f"SELECT {', '.join(selected)} FROM {table_name} ORDER BY id"
    )).mappings().all()]


def _select_optional_rows(
    connection,
    table_names: set[str],
    inspector,
    table_name: str,
    columns: tuple[str, ...],
) -> list[dict[str, Any]]:
    if table_name not in table_names:
        return []
    available = {column['name'] for column in inspector.get_columns(table_name)}
    return _select_rows(connection, table_name, columns, available)


def _classify_existing_project_id(
    report: dict[str, Any],
    entity_id: str,
    raw_project_id: Any,
    known_project_ids: set[str],
) -> Optional[str]:
    project_id = str(raw_project_id or '').strip()
    if not project_id:
        return None
    if project_id in known_project_ids:
        report['classified_ids']['already_mapped'].append(entity_id)
        return 'already_mapped'
    report['classified_ids']['stale'].append(entity_id)
    report['stale_entities'].append({'id': entity_id, 'project_id': project_id})
    return 'stale'


def _classify_alias_candidate(
    report: dict[str, Any],
    entity_id: str,
    candidate_ids: set[str],
) -> Optional[dict[str, str]]:
    if len(candidate_ids) == 1:
        mapping = {
            'entity_id': entity_id,
            'project_id': next(iter(candidate_ids)),
            'source': 'alias',
        }
        report['classified_ids']['alias_mappable'].append(entity_id)
        report['planned_mappings'].append(mapping)
        return mapping
    if len(candidate_ids) > 1:
        report['classified_ids']['ambiguous'].append(entity_id)
        report['ambiguous_entities'].append({
            'id': entity_id,
            'candidate_project_ids': sorted(candidate_ids),
        })
        return None
    report['classified_ids']['unresolved'].append(entity_id)
    return None


def _profile_parent_state(
    profile: dict[str, Any],
    *,
    samples_by_id: dict[str, dict[str, Any]],
    sample_effective_ids: dict[str, str],
    people_by_id: dict[str, dict[str, Any]],
    voiceprints_by_id: dict[str, dict[str, Any]],
    person_project_ids: Callable[[dict[str, Any]], set[str]],
) -> dict[str, Any]:
    has_parent = False
    project_ids: set[str] = set()
    orphan_parent_ids: list[str] = []
    unresolved_parent_ids: list[str] = []

    clone_sample_id = str(profile.get('source_clone_sample_id') or '').strip()
    if clone_sample_id:
        has_parent = True
        if clone_sample_id not in samples_by_id:
            orphan_parent_ids.append(f'voice_clone_sample:{clone_sample_id}')
        elif clone_sample_id in sample_effective_ids:
            project_ids.add(sample_effective_ids[clone_sample_id])
        else:
            unresolved_parent_ids.append(f'voice_clone_sample:{clone_sample_id}')

    person_ids: set[str] = set()
    person_id = str(profile.get('person_id') or '').strip()
    if person_id:
        has_parent = True
        person_ids.add(person_id)
    source_sample_id = str(profile.get('source_sample_id') or '').strip()
    if source_sample_id:
        has_parent = True
        source_sample = voiceprints_by_id.get(source_sample_id)
        if not source_sample:
            orphan_parent_ids.append(f'voiceprint_sample:{source_sample_id}')
        else:
            source_person_id = str(source_sample.get('person_id') or '').strip()
            if source_person_id:
                person_ids.add(source_person_id)
            else:
                orphan_parent_ids.append(f'voiceprint_sample_person:{source_sample_id}')

    for parent_person_id in sorted(person_ids):
        person = people_by_id.get(parent_person_id)
        if not person:
            orphan_parent_ids.append(f'person:{parent_person_id}')
            continue
        candidates = person_project_ids(person)
        if len(candidates) == 1:
            project_ids.update(candidates)
        else:
            unresolved_parent_ids.append(f'person:{parent_person_id}')

    return {
        'has_parent': has_parent,
        'project_ids': project_ids,
        'orphan_parent_ids': orphan_parent_ids,
        'unresolved_parent_ids': unresolved_parent_ids,
    }


def _classify_with_parent(
    report: dict[str, Any],
    entity_id: str,
    parent_state: dict[str, Any],
    alias_candidate_ids: set[str],
) -> Optional[dict[str, str]]:
    orphan_parent_ids = parent_state['orphan_parent_ids']
    if orphan_parent_ids:
        report['classified_ids']['orphan_parent'].append(entity_id)
        report['orphan_parents'].append({
            'id': entity_id,
            'orphan_parent_ids': sorted(orphan_parent_ids),
        })
        return None

    parent_project_ids = set(parent_state['project_ids'])
    unresolved_parent_ids = parent_state['unresolved_parent_ids']
    if parent_project_ids:
        conflicts = (
            len(parent_project_ids) != 1
            or bool(unresolved_parent_ids)
            or bool(alias_candidate_ids and alias_candidate_ids != parent_project_ids)
        )
        if conflicts:
            report['classified_ids']['parent_conflict'].append(entity_id)
            report['parent_conflicts'].append({
                'id': entity_id,
                'parent_project_ids': sorted(parent_project_ids),
                'alias_project_ids': sorted(alias_candidate_ids),
                'unresolved_parent_ids': sorted(unresolved_parent_ids),
            })
            return None
        mapping = {
            'entity_id': entity_id,
            'project_id': next(iter(parent_project_ids)),
            'source': 'parent',
        }
        report['classified_ids']['parent_mappable'].append(entity_id)
        report['planned_mappings'].append(mapping)
        return mapping

    if parent_state['has_parent']:
        if len(alias_candidate_ids) > 1:
            report['classified_ids']['ambiguous'].append(entity_id)
            report['ambiguous_entities'].append({
                'id': entity_id,
                'candidate_project_ids': sorted(alias_candidate_ids),
                'unresolved_parent_ids': sorted(unresolved_parent_ids),
            })
        else:
            report['classified_ids']['unresolved'].append(entity_id)
        return None
    return _classify_alias_candidate(report, entity_id, alias_candidate_ids)


def _classify_existing_parent_conflict(
    report: dict[str, Any],
    entity_id: str,
    existing_project_id: str,
    parent_state: dict[str, Any],
) -> bool:
    parent_project_ids = set(parent_state['project_ids'])
    unresolved_parent_ids = list(parent_state['unresolved_parent_ids'])
    orphan_parent_ids = list(parent_state['orphan_parent_ids'])
    has_conflict = bool(parent_state['has_parent']) and (
        parent_project_ids != {existing_project_id}
        or bool(unresolved_parent_ids)
        or bool(orphan_parent_ids)
    )
    if not has_conflict:
        return False
    report['classified_ids']['existing_binding_conflict'].append(entity_id)
    report['existing_binding_conflicts'].append({
        'id': entity_id,
        'existing_project_id': existing_project_id,
        'parent_project_ids': sorted(parent_project_ids),
        'unresolved_parent_ids': sorted(unresolved_parent_ids),
        'orphan_parent_ids': sorted(orphan_parent_ids),
    })
    return True


# Keep discoverable singular/plural aliases for callers and tests.
inspect_voice_cloning_project_migration = inspect_voice_clone_project_migration
inspect_voice_clone_project_ids_migration = inspect_voice_clone_project_migration
inspect_voice_cloning_project_ids_migration = inspect_voice_clone_project_migration


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Preview or apply Speaker 2.0 voice clone project_id migration.'
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Add project_id columns/indexes and backfill only safe empty rows.',
    )
    args = parser.parse_args()
    report = inspect_voice_clone_project_migration(engine, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
