from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from scripts.migrate_v2_voice_clone_project_ids import inspect_voice_clone_project_migration


def _build_legacy_engine():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    with engine.begin() as connection:
        connection.execute(text(
            'CREATE TABLE projects ('
            'id TEXT PRIMARY KEY, name TEXT, name_key TEXT, legacy_program_key TEXT'
            ')'
        ))
        connection.execute(text(
            'CREATE TABLE people ('
            'id TEXT PRIMARY KEY, project_id TEXT, program_key TEXT, program_name TEXT'
            ')'
        ))
        connection.execute(text(
            'CREATE TABLE voiceprint_samples (id TEXT PRIMARY KEY, person_id TEXT)'
        ))
        connection.execute(text(
            'CREATE TABLE voice_clone_samples ('
            'id TEXT PRIMARY KEY, program_key TEXT, program_name TEXT, legacy_payload TEXT'
            ')'
        ))
        connection.execute(text(
            'CREATE TABLE voice_clone_profiles ('
            'id TEXT PRIMARY KEY, program_key TEXT, program_name TEXT, '
            'source_clone_sample_id TEXT, source_sample_id TEXT, person_id TEXT, legacy_payload TEXT'
            ')'
        ))
        connection.execute(text(
            'CREATE TABLE voice_clone_synthesis_records ('
            'id TEXT PRIMARY KEY, program_key TEXT, program_name TEXT, '
            'voice_clone_profile_id TEXT, legacy_payload TEXT'
            ')'
        ))
        connection.execute(text(
            "INSERT INTO projects (id, name, name_key, legacy_program_key) VALUES "
            "('project-a', 'Alpha', 'alpha', 'alpha-old'), "
            "('project-b', 'Beta', 'beta', 'shared'), "
            "('project-c', 'Gamma', 'gamma', 'shared')"
        ))
        connection.execute(text(
            "INSERT INTO people (id, project_id, program_key, program_name) VALUES "
            "('person-b', 'project-b', 'beta', 'Beta')"
        ))
        connection.execute(text(
            "INSERT INTO voiceprint_samples (id, person_id) VALUES ('voiceprint-b', 'person-b')"
        ))
        connection.execute(text(
            "INSERT INTO voice_clone_samples "
            "(id, program_key, program_name, legacy_payload) VALUES "
            "('sample-alias', 'alpha-old', 'Alpha', 'keep-sample-alias'), "
            "('sample-unresolved', 'missing', 'Missing', 'keep-sample-unresolved'), "
            "('sample-empty', NULL, NULL, 'keep-sample-empty'), "
            "('sample-ambiguous', 'shared', 'Shared', 'keep-sample-ambiguous'), "
            "('sample-stale', 'alpha', 'Alpha', 'keep-sample-stale'), "
            "('sample-mapped', 'alpha', 'Alpha', 'keep-sample-mapped')"
        ))
        connection.execute(text('ALTER TABLE voice_clone_samples ADD COLUMN project_id TEXT'))
        connection.execute(text(
            "UPDATE voice_clone_samples SET project_id = 'missing-project' WHERE id = 'sample-stale'"
        ))
        connection.execute(text(
            "UPDATE voice_clone_samples SET project_id = 'project-a' WHERE id = 'sample-mapped'"
        ))
        connection.execute(text(
            "INSERT INTO voice_clone_profiles "
            "(id, program_key, program_name, source_clone_sample_id, source_sample_id, person_id, legacy_payload) VALUES "
            "('profile-parent', 'alpha', 'Alpha', 'sample-alias', NULL, NULL, 'keep-profile-parent'), "
            "('profile-conflict', 'beta', 'Beta', 'sample-alias', NULL, NULL, 'keep-profile-conflict'), "
            "('profile-orphan', 'alpha', 'Alpha', 'missing-sample', NULL, NULL, 'keep-profile-orphan'), "
            "('profile-alias', 'alpha-old', 'Alpha', NULL, NULL, NULL, 'keep-profile-alias'), "
            "('profile-person', 'beta', 'Beta', NULL, NULL, 'person-b', 'keep-profile-person'), "
            "('profile-voiceprint', NULL, NULL, NULL, 'voiceprint-b', NULL, 'keep-profile-voiceprint'), "
            "('profile-unresolved-parent', 'alpha', 'Alpha', 'sample-unresolved', NULL, NULL, 'keep-profile-unresolved'), "
            "('profile-ambiguous', 'shared', 'Shared', NULL, NULL, NULL, 'keep-profile-ambiguous')"
        ))
        connection.execute(text(
            "INSERT INTO voice_clone_synthesis_records "
            "(id, program_key, program_name, voice_clone_profile_id, legacy_payload) VALUES "
            "('record-parent', 'alpha', 'Alpha', 'profile-parent', 'keep-record-parent'), "
            "('record-conflict', 'beta', 'Beta', 'profile-parent', 'keep-record-conflict'), "
            "('record-orphan', 'alpha', 'Alpha', 'missing-profile', 'keep-record-orphan'), "
            "('record-from-alias', NULL, NULL, 'profile-alias', 'keep-record-alias')"
        ))
    return engine


def _legacy_snapshot(engine, table_name: str) -> list[tuple]:
    with engine.connect() as connection:
        return list(connection.execute(text(
            f'SELECT id, program_key, program_name, legacy_payload FROM {table_name} ORDER BY id'
        )).all())


def test_voice_clone_project_migration_dry_run_apply_and_repeat_are_safe() -> None:
    engine = _build_legacy_engine()
    tables = (
        'voice_clone_samples',
        'voice_clone_profiles',
        'voice_clone_synthesis_records',
    )
    before_columns = {
        table: {column['name'] for column in inspect(engine).get_columns(table)}
        for table in tables
    }
    before_legacy = {table: _legacy_snapshot(engine, table) for table in tables}

    preview = inspect_voice_clone_project_migration(engine, apply=False)

    assert preview['mode'] == 'dry-run'
    assert preview['schema_mutated'] is False
    assert preview['classification_counts'] == {
        'already_mapped': 1,
        'parent_mappable': 5,
        'alias_mappable': 2,
        'unresolved': 3,
        'ambiguous': 2,
        'stale': 1,
        'orphan_parent': 2,
        'parent_conflict': 2,
        'existing_binding_conflict': 0,
    }
    assert preview['mappable_count'] == 7
    assert preview['samples']['classified_ids']['alias_mappable'] == ['sample-alias']
    assert set(preview['profiles']['classified_ids']['parent_mappable']) == {
        'profile-parent',
        'profile-person',
        'profile-voiceprint',
    }
    assert set(preview['records']['classified_ids']['parent_mappable']) == {
        'record-from-alias',
        'record-parent',
    }
    assert preview['profiles']['classified_ids']['parent_conflict'] == ['profile-conflict']
    assert preview['records']['classified_ids']['parent_conflict'] == ['record-conflict']
    assert {
        table: {column['name'] for column in inspect(engine).get_columns(table)}
        for table in tables
    } == before_columns
    assert {table: _legacy_snapshot(engine, table) for table in tables} == before_legacy

    applied = inspect_voice_clone_project_migration(engine, apply=True)

    assert applied['schema_mutated'] is True
    assert set(applied['project_id_columns_added']) == {
        'voice_clone_profiles',
        'voice_clone_synthesis_records',
    }
    assert applied['mapped_count'] == 7
    for table in tables:
        columns = {column['name']: column for column in inspect(engine).get_columns(table)}
        assert columns['project_id']['nullable'] is True
        indexes = {item['name']: item for item in inspect(engine).get_indexes(table)}
        assert indexes[f'ix_{table}_project_id']['column_names'] == ['project_id']
    with engine.connect() as connection:
        sample_ids = dict(connection.execute(text(
            'SELECT id, project_id FROM voice_clone_samples ORDER BY id'
        )).all())
        profile_ids = dict(connection.execute(text(
            'SELECT id, project_id FROM voice_clone_profiles ORDER BY id'
        )).all())
        record_ids = dict(connection.execute(text(
            'SELECT id, project_id FROM voice_clone_synthesis_records ORDER BY id'
        )).all())
    assert sample_ids['sample-alias'] == 'project-a'
    assert sample_ids['sample-empty'] is None
    assert sample_ids['sample-stale'] == 'missing-project'
    assert profile_ids['profile-parent'] == 'project-a'
    assert profile_ids['profile-person'] == 'project-b'
    assert profile_ids['profile-conflict'] is None
    assert record_ids['record-parent'] == 'project-a'
    assert record_ids['record-conflict'] is None
    assert {table: _legacy_snapshot(engine, table) for table in tables} == before_legacy

    repeated = inspect_voice_clone_project_migration(engine, apply=True)

    assert repeated['schema_mutated'] is False
    assert repeated['mapped_count'] == 0
    assert repeated['already_mapped_count'] == 8
    assert {table: _legacy_snapshot(engine, table) for table in tables} == before_legacy


def test_voice_clone_project_migration_reports_existing_parent_binding_conflicts() -> None:
    engine = _build_legacy_engine()
    inspect_voice_clone_project_migration(engine, apply=True)
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE voice_clone_profiles SET project_id = 'project-b' "
            "WHERE id = 'profile-parent'"
        ))
        connection.execute(text(
            "UPDATE voice_clone_synthesis_records SET project_id = 'project-b' "
            "WHERE id = 'record-from-alias'"
        ))

    report = inspect_voice_clone_project_migration(engine, apply=False)

    assert report['existing_binding_conflict_count'] == 3
    assert report['profiles']['classified_ids']['existing_binding_conflict'] == [
        'profile-parent'
    ]
    assert set(report['records']['classified_ids']['existing_binding_conflict']) == {
        'record-from-alias',
        'record-parent',
    }
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT project_id FROM voice_clone_profiles WHERE id = 'profile-parent'"
        )).scalar_one() == 'project-b'
        assert connection.execute(text(
            "SELECT project_id FROM voice_clone_synthesis_records "
            "WHERE id = 'record-from-alias'"
        )).scalar_one() == 'project-b'
