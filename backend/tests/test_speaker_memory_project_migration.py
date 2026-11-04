from __future__ import annotations

import json

from sqlalchemy import create_engine, inspect, text

from scripts.migrate_v2_speaker_memories_project_ids import (
    inspect_speaker_memory_project_migration,
)


def _create_tables(engine, *, include_project_id: bool) -> None:
    project_id_column = ', project_id TEXT' if include_project_id else ''
    with engine.begin() as connection:
        connection.execute(text(
            'CREATE TABLE projects ('
            'id TEXT PRIMARY KEY, name TEXT, name_key TEXT, legacy_program_key TEXT'
            ')'
        ))
        connection.execute(text(
            'CREATE TABLE speaker_memories ('
            'id TEXT PRIMARY KEY, program_key TEXT UNIQUE, program_name TEXT, '
            'speaker_names JSON, lower_third_assets JSON, legacy_payload JSON, '
            f'created_at TEXT, updated_at TEXT{project_id_column}'
            ')'
        ))


def _insert_projects(engine, projects: list[dict[str, str]]) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                'INSERT INTO projects (id, name, name_key, legacy_program_key) '
                'VALUES (:id, :name, :name_key, :legacy_program_key)'
            ),
            projects,
        )


def _insert_memories(engine, memories: list[dict[str, object]], *, include_project_id: bool) -> None:
    columns = [
        'id',
        'program_key',
        'program_name',
        'speaker_names',
        'lower_third_assets',
        'legacy_payload',
        'created_at',
        'updated_at',
    ]
    if include_project_id:
        columns.append('project_id')
    placeholders = ', '.join(f':{column}' for column in columns)
    with engine.begin() as connection:
        connection.execute(
            text(
                f"INSERT INTO speaker_memories ({', '.join(columns)}) "
                f'VALUES ({placeholders})'
            ),
            memories,
        )


def _memory_row(
    memory_id: str,
    program_key: str,
    program_name: str,
    *,
    project_id: str | None = None,
) -> dict[str, object]:
    return {
        'id': memory_id,
        'program_key': program_key,
        'program_name': program_name,
        'speaker_names': json.dumps([f'{memory_id}-speaker'], sort_keys=True),
        'lower_third_assets': json.dumps(
            [{'speaker_name': f'{memory_id}-speaker', 'object_key': f'{memory_id}.png'}],
            sort_keys=True,
        ),
        'legacy_payload': json.dumps({'memory_id': memory_id, 'keep': True}, sort_keys=True),
        'created_at': '2026-07-01T01:02:03',
        'updated_at': '2026-07-02T04:05:06',
        'project_id': project_id,
    }


def _legacy_snapshot(engine) -> list[dict[str, object]]:
    with engine.connect() as connection:
        rows = connection.execute(text(
            'SELECT id, program_key, program_name, speaker_names, lower_third_assets, '
            'legacy_payload, created_at, updated_at FROM speaker_memories ORDER BY id'
        )).mappings().all()
    return [dict(row) for row in rows]


def _build_legacy_engine():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    _create_tables(engine, include_project_id=False)
    _insert_projects(engine, [
        {
            'id': 'id-project',
            'name': 'ID Display',
            'name_key': 'id-name-key',
            'legacy_program_key': 'id-legacy',
        },
        {
            'id': 'project-name',
            'name': 'Display Name Alias',
            'name_key': 'name-key',
            'legacy_program_key': 'name-legacy',
        },
        {
            'id': 'project-key',
            'name': 'Key Display',
            'name_key': 'Only Name Key',
            'legacy_program_key': 'key-legacy',
        },
        {
            'id': 'project-legacy',
            'name': 'Legacy Display',
            'name_key': 'legacy-name-key',
            'legacy_program_key': 'Historic Program',
        },
        {
            'id': 'project-ambiguous-a',
            'name': 'Ambiguous A',
            'name_key': 'ambiguous-a',
            'legacy_program_key': 'Shared Program',
        },
        {
            'id': 'project-ambiguous-b',
            'name': 'Ambiguous B',
            'name_key': 'ambiguous-b',
            'legacy_program_key': 'Shared Program',
        },
        {
            'id': 'project-duplicate',
            'name': 'Duplicate Display',
            'name_key': 'duplicate-name-key',
            'legacy_program_key': 'duplicate-legacy',
        },
    ])
    _insert_memories(engine, [
        _memory_row('map-id', ' ID-PROJECT ', ''),
        _memory_row('map-name', 'Display Name Alias', ''),
        _memory_row('map-name-key', 'only name key', ''),
        _memory_row('map-legacy', 'Historic Program', ''),
        _memory_row('global', '', ''),
        _memory_row('unresolved', 'Missing Program', 'Missing Program'),
        _memory_row('ambiguous', 'Shared Program', 'Shared Program'),
        _memory_row('duplicate-a', 'duplicate-name-key', ''),
        _memory_row('duplicate-b', 'duplicate-legacy', ''),
    ], include_project_id=False)
    return engine


def _build_existing_binding_engine():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    _create_tables(engine, include_project_id=True)
    _insert_projects(engine, [
        {
            'id': 'project-a',
            'name': 'Alpha',
            'name_key': 'alpha-key',
            'legacy_program_key': 'alpha-legacy',
        },
        {
            'id': 'project-b',
            'name': 'Beta',
            'name_key': 'beta-key',
            'legacy_program_key': 'beta-legacy',
        },
        {
            'id': 'project-c',
            'name': 'Gamma',
            'name_key': 'gamma-key',
            'legacy_program_key': 'gamma-legacy',
        },
        {
            'id': 'project-d',
            'name': 'Delta',
            'name_key': 'delta-key',
            'legacy_program_key': 'delta-legacy',
        },
    ])
    _insert_memories(engine, [
        _memory_row('already-a', 'alpha-legacy', 'Alpha', project_id='project-a'),
        _memory_row('conflict-c', 'beta-legacy', 'Beta', project_id='project-c'),
        _memory_row('stale', 'alpha-key', 'Alpha', project_id='deleted-project'),
        _memory_row('noncanonical-b', 'beta-key', 'Beta', project_id=' PROJECT-B '),
        _memory_row('bound-d', 'delta-key', 'Delta', project_id='project-d'),
        _memory_row('candidate-d', 'delta-legacy', '', project_id=None),
    ], include_project_id=True)
    return engine


def test_speaker_memory_project_migration_dry_run_apply_and_repeat_are_safe() -> None:
    engine = _build_legacy_engine()
    before_columns = {column['name'] for column in inspect(engine).get_columns('speaker_memories')}
    before_indexes = inspect(engine).get_indexes('speaker_memories')
    before_legacy_rows = _legacy_snapshot(engine)

    preview = inspect_speaker_memory_project_migration(engine, apply=False)

    assert preview['mode'] == 'dry-run'
    assert preview['schema_mutated'] is False
    assert preview['classification_counts'] == {
        'already_mapped': 0,
        'global': 1,
        'unresolved': 1,
        'ambiguous': 1,
        'stale': 0,
        'noncanonical': 0,
        'existing_binding_conflict': 0,
    }
    assert preview['candidate_memory_count'] == 6
    assert preview['mappable_memory_count'] == 4
    assert preview['duplicate_target_project_ids'] == ['project-duplicate']
    assert preview['duplicate_candidate_memory_ids'] == ['duplicate-a', 'duplicate-b']
    assert {column['name'] for column in inspect(engine).get_columns('speaker_memories')} == before_columns
    assert inspect(engine).get_indexes('speaker_memories') == before_indexes
    assert _legacy_snapshot(engine) == before_legacy_rows

    applied = inspect_speaker_memory_project_migration(engine, apply=True)

    assert applied['schema_mutated'] is True
    assert applied['project_id_column_added'] is True
    assert applied['project_id_index_added'] is True
    assert applied['mapped_memory_count'] == 4
    columns = {column['name']: column for column in inspect(engine).get_columns('speaker_memories')}
    assert str(columns['project_id']['type']).upper() == 'TEXT'
    assert columns['project_id']['nullable'] is True
    indexes = {index_info['name']: index_info for index_info in inspect(engine).get_indexes('speaker_memories')}
    assert indexes['ix_speaker_memories_project_id']['column_names'] == ['project_id']

    with engine.connect() as connection:
        project_ids = dict(connection.execute(text(
            'SELECT id, project_id FROM speaker_memories ORDER BY id'
        )).all())
    assert project_ids['map-id'] == 'id-project'
    assert project_ids['map-name'] == 'project-name'
    assert project_ids['map-name-key'] == 'project-key'
    assert project_ids['map-legacy'] == 'project-legacy'
    assert project_ids['global'] is None
    assert project_ids['unresolved'] is None
    assert project_ids['ambiguous'] is None
    assert project_ids['duplicate-a'] is None
    assert project_ids['duplicate-b'] is None
    assert _legacy_snapshot(engine) == before_legacy_rows

    repeated = inspect_speaker_memory_project_migration(engine, apply=True)

    assert repeated['schema_mutated'] is False
    assert repeated['mapped_memory_count'] == 0
    assert repeated['already_mapped_count'] == 4
    assert repeated['duplicate_target_project_ids'] == ['project-duplicate']
    assert _legacy_snapshot(engine) == before_legacy_rows


def test_speaker_memory_project_migration_classifies_existing_bindings_and_duplicates() -> None:
    engine = _build_existing_binding_engine()
    before_legacy_rows = _legacy_snapshot(engine)
    assert 'ix_speaker_memories_project_id' not in {
        index_info['name'] for index_info in inspect(engine).get_indexes('speaker_memories')
    }

    preview = inspect_speaker_memory_project_migration(engine, apply=False)

    assert preview['schema_mutated'] is False
    assert preview['classification_counts'] == {
        'already_mapped': 2,
        'global': 0,
        'unresolved': 0,
        'ambiguous': 0,
        'stale': 1,
        'noncanonical': 1,
        'existing_binding_conflict': 1,
    }
    assert preview['classified_memory_ids']['stale'] == ['stale']
    assert preview['classified_memory_ids']['noncanonical'] == ['noncanonical-b']
    assert preview['noncanonical_memories'] == [{
        'memory_id': 'noncanonical-b',
        'project_id': ' PROJECT-B ',
        'normalized_project_id': 'PROJECT-B',
        'canonical_project_ids': ['project-b'],
    }]
    assert preview['existing_binding_conflicts'] == [{
        'memory_id': 'conflict-c',
        'project_id': 'project-c',
        'candidate_project_ids': ['project-b'],
    }]
    assert preview['duplicate_target_project_ids'] == ['project-d']
    assert preview['duplicate_target_projects'] == [{
        'project_id': 'project-d',
        'memory_ids': ['bound-d', 'candidate-d'],
        'already_bound_memory_ids': ['bound-d'],
        'candidate_memory_ids': ['candidate-d'],
    }]
    assert preview['mappable_memory_count'] == 0

    applied = inspect_speaker_memory_project_migration(engine, apply=True)

    assert applied['schema_mutated'] is True
    assert applied['project_id_column_added'] is False
    assert applied['project_id_index_added'] is True
    assert applied['mapped_memory_count'] == 0
    with engine.connect() as connection:
        project_ids = dict(connection.execute(text(
            'SELECT id, project_id FROM speaker_memories ORDER BY id'
        )).all())
    assert project_ids == {
        'already-a': 'project-a',
        'bound-d': 'project-d',
        'candidate-d': None,
        'conflict-c': 'project-c',
        'noncanonical-b': ' PROJECT-B ',
        'stale': 'deleted-project',
    }
    assert _legacy_snapshot(engine) == before_legacy_rows
