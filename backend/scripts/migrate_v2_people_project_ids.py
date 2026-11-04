from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import Engine, inspect, text

from app.db.session import engine
from app.services.speaker_memory import build_program_key


def inspect_people_project_migration(target_engine: Engine, *, apply: bool = False) -> dict[str, Any]:
    inspector = inspect(target_engine)
    table_names = set(inspector.get_table_names())
    if 'projects' not in table_names:
        raise RuntimeError('projects 表不存在，请先执行 M1 项目实体迁移')
    if 'people' not in table_names:
        raise RuntimeError('people 表不存在，不能执行人物项目迁移')

    people_columns = {column['name'] for column in inspector.get_columns('people')}
    had_project_id_column = 'project_id' in people_columns
    people_index_names = {index['name'] for index in inspector.get_indexes('people')}
    had_project_id_index = 'ix_people_project_id' in people_index_names
    if apply:
        with target_engine.begin() as connection:
            if not had_project_id_column:
                connection.execute(text('ALTER TABLE people ADD COLUMN project_id TEXT'))
            connection.execute(text('CREATE INDEX IF NOT EXISTS ix_people_project_id ON people(project_id)'))
        people_columns.add('project_id')

    project_aliases: dict[str, set[str]] = {}
    with target_engine.connect() as connection:
        project_rows = connection.execute(text(
            'SELECT id, name, name_key, legacy_program_key FROM projects'
        )).mappings().all()
        selected_columns = ['id', 'program_key', 'program_name']
        if 'project_id' in people_columns:
            selected_columns.append('project_id')
        people_rows = connection.execute(text(
            f"SELECT {', '.join(selected_columns)} FROM people"
        )).mappings().all()

    for project in project_rows:
        for value in (
            project.get('id'),
            project.get('name'),
            project.get('name_key'),
            project.get('legacy_program_key'),
        ):
            alias = build_program_key(value)
            if alias:
                project_aliases.setdefault(alias, set()).add(str(project['id']))
    known_project_ids = {str(project['id']) for project in project_rows}

    mappings: list[tuple[str, str]] = []
    unresolved_people: list[str] = []
    ambiguous_people: list[dict[str, Any]] = []
    global_people_count = 0
    already_mapped_count = 0
    stale_project_people: list[dict[str, str]] = []

    for person in people_rows:
        existing_project_id = str(person.get('project_id') or '').strip()
        if existing_project_id:
            if existing_project_id in known_project_ids:
                already_mapped_count += 1
            else:
                stale_project_people.append({
                    'person_id': str(person['id']),
                    'project_id': existing_project_id,
                })
            continue
        aliases = {
            build_program_key(value)
            for value in (person.get('program_key'), person.get('program_name'))
            if build_program_key(value)
        }
        if not aliases:
            global_people_count += 1
            continue
        matched_project_ids = {
            project_id
            for alias in aliases
            for project_id in project_aliases.get(alias, set())
        }
        if len(matched_project_ids) == 1:
            mappings.append((str(person['id']), next(iter(matched_project_ids))))
        elif len(matched_project_ids) > 1:
            ambiguous_people.append({
                'person_id': str(person['id']),
                'candidate_project_ids': sorted(matched_project_ids),
            })
        else:
            unresolved_people.append(str(person['id']))

    mapped_count = 0
    if apply and mappings:
        with target_engine.begin() as connection:
            for person_id, project_id in mappings:
                result = connection.execute(
                    text(
                        'UPDATE people SET project_id = :project_id '
                        "WHERE id = :person_id AND TRIM(COALESCE(project_id, '')) = ''"
                    ),
                    {'project_id': project_id, 'person_id': person_id},
                )
                mapped_count += int(result.rowcount or 0)

    return {
        'mode': 'apply' if apply else 'dry-run',
        'schema_mutated': bool(apply and (not had_project_id_column or not had_project_id_index)),
        'project_count': len(project_rows),
        'people_count': len(people_rows),
        'already_mapped_count': already_mapped_count,
        'mappable_people_count': len(mappings),
        'mapped_people_count': mapped_count,
        'global_people_count': global_people_count,
        'unresolved_people_count': len(unresolved_people),
        'ambiguous_people_count': len(ambiguous_people),
        'stale_project_people_count': len(stale_project_people),
        'unresolved_person_ids': unresolved_people,
        'ambiguous_people': ambiguous_people,
        'stale_project_people': stale_project_people,
        'legacy_fields_preserved': True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='Preview or apply Speaker 2.0 people.project_id migration.')
    parser.add_argument('--apply', action='store_true', help='Add people.project_id and backfill unambiguous rows.')
    args = parser.parse_args()
    report = inspect_people_project_migration(engine, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
