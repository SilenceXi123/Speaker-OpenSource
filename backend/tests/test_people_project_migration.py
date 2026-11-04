from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from scripts.migrate_v2_people_project_ids import inspect_people_project_migration


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
            'id TEXT PRIMARY KEY, name TEXT, program_key TEXT, program_name TEXT'
            ')'
        ))
        connection.execute(text(
            "INSERT INTO projects (id, name, name_key, legacy_program_key) VALUES "
            "('project-a', '项目 A', '项目 a', '旧项目 a'), "
            "('project-b', '项目 B', '项目 b', '冲突项目'), "
            "('project-c', '项目 C', '项目 c', '冲突项目')"
        ))
        connection.execute(text(
            "INSERT INTO people (id, name, program_key, program_name) VALUES "
            "('person-a', '人物 A', '旧项目 a', '项目 A'), "
            "('person-global', '全局人物', NULL, NULL), "
            "('person-missing', '未知人物', '未知项目', '未知项目'), "
            "('person-ambiguous', '歧义人物', '冲突项目', '冲突项目')"
        ))
    return engine


def test_people_project_migration_dry_run_is_read_only_and_apply_is_idempotent() -> None:
    engine = _build_legacy_engine()
    before_columns = {column['name'] for column in inspect(engine).get_columns('people')}

    preview = inspect_people_project_migration(engine, apply=False)

    assert preview['schema_mutated'] is False
    assert preview['mappable_people_count'] == 1
    assert preview['global_people_count'] == 1
    assert preview['unresolved_people_count'] == 1
    assert preview['ambiguous_people_count'] == 1
    assert {column['name'] for column in inspect(engine).get_columns('people')} == before_columns

    applied = inspect_people_project_migration(engine, apply=True)

    assert applied['schema_mutated'] is True
    assert applied['mapped_people_count'] == 1
    with engine.connect() as connection:
        mapped_project_id = connection.execute(text(
            "SELECT project_id FROM people WHERE id = 'person-a'"
        )).scalar_one()
        global_project_id = connection.execute(text(
            "SELECT project_id FROM people WHERE id = 'person-global'"
        )).scalar_one()
    assert mapped_project_id == 'project-a'
    assert global_project_id is None

    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE people SET project_id = 'missing-project' WHERE id = 'person-missing'"
        ))

    repeated = inspect_people_project_migration(engine, apply=True)

    assert repeated['schema_mutated'] is False
    assert repeated['mapped_people_count'] == 0
    assert repeated['already_mapped_count'] == 1
    assert repeated['stale_project_people_count'] == 1
