from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import MetaData, Text, create_engine, inspect, text

from app.db.schema_bootstrap import (
    PROVIDER_COST_FACT_TABLES,
    provider_cost_schema_status,
)
from app.db.session import Base
import scripts.migrate_v2_provider_cost_facts as provider_cost_migration
from scripts.migrate_v2_provider_cost_facts import (
    inspect_provider_cost_fact_migration,
)


EXPECTED_TABLES = frozenset({'provider_calls', 'cost_events', 'price_catalog'})
BACKEND_DIR = Path(__file__).resolve().parents[1]


def _build_legacy_engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text(
            'CREATE TABLE legacy_sentinel ('
            'id INTEGER PRIMARY KEY, value TEXT NOT NULL UNIQUE'
            ')'
        ))
        connection.execute(text(
            'CREATE INDEX ix_legacy_sentinel_value ON legacy_sentinel(value)'
        ))
        connection.execute(text(
            'CREATE TABLE usage_ledgers ('
            'id TEXT PRIMARY KEY, job_id TEXT, provider TEXT, amount INTEGER'
            ')'
        ))
        connection.execute(text(
            "INSERT INTO legacy_sentinel (id, value) VALUES (1, 'preserve-me')"
        ))
        connection.execute(text(
            "INSERT INTO usage_ledgers (id, job_id, provider, amount) "
            "VALUES ('usage-1', 'job-1', 'legacy-provider', 73)"
        ))
    return engine


def _build_provider_schema_with_column_override(
    tmp_path: Path,
    column_name: str,
    *,
    column_type=None,
    nullable: bool | None = None,
):
    engine = _build_legacy_engine(tmp_path)
    metadata = MetaData()
    copied_tables = {
        name: Base.metadata.tables[name].to_metadata(metadata)
        for name in EXPECTED_TABLES
    }
    column = copied_tables['provider_calls'].c[column_name]
    if column_type is not None:
        column.type = column_type
    if nullable is not None:
        column.nullable = nullable
    metadata.create_all(engine)
    return engine


def _legacy_snapshot(engine) -> tuple[list[tuple], list[tuple], list[tuple]]:
    with engine.connect() as connection:
        schema = connection.execute(text(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE tbl_name IN ('legacy_sentinel', 'usage_ledgers') "
            'ORDER BY type, name'
        )).tuples().all()
        sentinel_rows = connection.execute(text(
            'SELECT id, value FROM legacy_sentinel ORDER BY id'
        )).tuples().all()
        usage_rows = connection.execute(text(
            'SELECT id, job_id, provider, amount FROM usage_ledgers ORDER BY id'
        )).tuples().all()
    return schema, sentinel_rows, usage_rows


def _journal_mode(engine) -> str:
    with engine.connect() as connection:
        return connection.exec_driver_sql('PRAGMA journal_mode').scalar_one()


def _run_migration_cli(database_path: Path, *args: str) -> dict:
    env = os.environ.copy()
    env.update({
        'PYTHONPATH': str(BACKEND_DIR),
        'PYTHONPYCACHEPREFIX': str(database_path.parent / 'pycache'),
        'PYTHONDONTWRITEBYTECODE': '1',
        'SQLITE_PATH': str(database_path),
    })
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'scripts.migrate_v2_provider_cost_facts',
            *args,
        ],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_provider_cost_inspection_engine_configures_connection_without_wal(
    tmp_path: Path,
) -> None:
    runtime_engine = create_engine(f"sqlite:///{tmp_path / 'inspection.db'}")

    inspection_engine = provider_cost_migration._create_inspection_engine(
        runtime_engine,
    )
    try:
        with inspection_engine.connect() as connection:
            assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one() == 1
            assert connection.exec_driver_sql('PRAGMA busy_timeout').scalar_one() == 5000
            assert connection.exec_driver_sql('PRAGMA journal_mode').scalar_one() == 'delete'
    finally:
        inspection_engine.dispose()


def test_provider_cost_migration_dry_run_apply_and_repeat_preserve_legacy_data(
    tmp_path: Path,
) -> None:
    engine = _build_legacy_engine(tmp_path)
    legacy_before = _legacy_snapshot(engine)

    preview = inspect_provider_cost_fact_migration(engine)

    assert preview == {
        'mode': 'dry-run',
        'schema_mutated': False,
        'planned_tables': sorted(EXPECTED_TABLES),
        'created_tables': [],
        'missing_tables': sorted(EXPECTED_TABLES),
        'schema_ready': False,
        'error_code': 'migration_required',
        'usage_ledgers_backfilled': False,
        'created_indexes': [],
        'invalid_tables': {},
    }
    assert _legacy_snapshot(engine) == legacy_before
    assert set(inspect(engine).get_table_names()) == {
        'legacy_sentinel',
        'usage_ledgers',
    }

    applied = inspect_provider_cost_fact_migration(engine, apply=True)

    assert applied['mode'] == 'apply'
    assert applied['schema_mutated'] is True
    assert set(applied['planned_tables']) == EXPECTED_TABLES
    assert set(applied['created_tables']) == EXPECTED_TABLES
    assert applied['missing_tables'] == []
    assert applied['schema_ready'] is True
    assert applied['error_code'] is None
    assert applied['usage_ledgers_backfilled'] is False
    assert _legacy_snapshot(engine) == legacy_before

    repeated = inspect_provider_cost_fact_migration(engine, apply=True)

    assert repeated['schema_mutated'] is False
    assert repeated['created_tables'] == []
    assert repeated['missing_tables'] == []
    assert repeated['schema_ready'] is True
    assert repeated['error_code'] is None
    assert repeated['usage_ledgers_backfilled'] is False
    assert _legacy_snapshot(engine) == legacy_before


def test_provider_cost_migration_cli_dry_run_apply_and_repeat(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / 'legacy.db'
    engine = _build_legacy_engine(tmp_path)
    legacy_before = _legacy_snapshot(engine)
    assert _journal_mode(engine) == 'delete'
    engine.dispose()

    preview = _run_migration_cli(database_path)

    assert preview['mode'] == 'dry-run'
    assert preview['schema_mutated'] is False
    assert preview['created_tables'] == []
    assert set(preview['missing_tables']) == EXPECTED_TABLES
    assert set(inspect(engine).get_table_names()) == {
        'legacy_sentinel',
        'usage_ledgers',
    }
    assert _journal_mode(engine) == 'delete'
    assert _legacy_snapshot(engine) == legacy_before
    engine.dispose()

    applied = _run_migration_cli(database_path, '--apply')

    assert applied['mode'] == 'apply'
    assert applied['schema_mutated'] is True
    assert set(applied['created_tables']) == EXPECTED_TABLES
    assert applied['missing_tables'] == []
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES | {
        'legacy_sentinel',
        'usage_ledgers',
    }
    assert _journal_mode(engine) == 'wal'
    assert _legacy_snapshot(engine) == legacy_before
    engine.dispose()

    repeated = _run_migration_cli(database_path, '--apply')

    assert repeated['mode'] == 'apply'
    assert repeated['schema_mutated'] is False
    assert repeated['created_tables'] == []
    assert repeated['missing_tables'] == []
    assert _journal_mode(engine) == 'wal'
    assert _legacy_snapshot(engine) == legacy_before


def test_provider_cost_status_rejects_malformed_same_name_tables(
    tmp_path: Path,
) -> None:
    engine = _build_legacy_engine(tmp_path)
    with engine.begin() as connection:
        for table_name in sorted(EXPECTED_TABLES):
            connection.execute(text(f'CREATE TABLE {table_name} (id TEXT)'))
    legacy_before = _legacy_snapshot(engine)
    with engine.connect() as connection:
        malformed_schema_before = connection.execute(text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name IN "
            "('provider_calls', 'cost_events', 'price_catalog') "
            'ORDER BY name'
        )).tuples().all()

    status = provider_cost_schema_status(engine)

    assert status['ready'] is False
    assert status['error_code'] == 'migration_required'
    assert status['missing_tables'] == []
    assert set(status['invalid_tables']) == EXPECTED_TABLES
    provider_issues = status['invalid_tables']['provider_calls']
    assert 'project_id' in provider_issues['missing_columns']
    assert provider_issues['primary_key'] == {
        'expected': ['id'],
        'actual': [],
    }
    assert provider_issues['missing_unique_constraints'] == [{
        'name': 'uq_provider_calls_idempotency_attempt',
        'columns': ['idempotency_key', 'attempt'],
    }]
    assert status['invalid_tables']['cost_events']['missing_check_constraints'] == [{
        'name': 'ck_cost_events_measurement_status',
        'sqltext': "measurement_status in ('measured', 'estimated', 'unmetered')",
    }]

    applied = inspect_provider_cost_fact_migration(engine, apply=True)

    assert applied['schema_mutated'] is False
    assert applied['created_tables'] == []
    assert applied['created_indexes'] == []
    assert applied['schema_ready'] is False
    assert applied['error_code'] == 'migration_required'
    assert set(applied['invalid_tables']) == EXPECTED_TABLES
    assert _legacy_snapshot(engine) == legacy_before
    with engine.connect() as connection:
        malformed_schema_after = connection.execute(text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name IN "
            "('provider_calls', 'cost_events', 'price_catalog') "
            'ORDER BY name'
        )).tuples().all()
    assert malformed_schema_after == malformed_schema_before


def test_provider_cost_apply_repairs_only_missing_declared_indexes(
    tmp_path: Path,
) -> None:
    engine = _build_legacy_engine(tmp_path)
    inspect_provider_cost_fact_migration(engine, apply=True)
    missing_index_names = {
        'ix_provider_calls_project_id',
        'ix_cost_events_job_id',
    }
    with engine.begin() as connection:
        for index_name in sorted(missing_index_names):
            connection.execute(text(f'DROP INDEX {index_name}'))

    interrupted = provider_cost_schema_status(engine)

    assert interrupted['ready'] is False
    assert interrupted['error_code'] == 'migration_required'
    assert {
        index['name']
        for issues in interrupted['invalid_tables'].values()
        for index in issues['missing_indexes']
    } == missing_index_names

    recovered = inspect_provider_cost_fact_migration(engine, apply=True)

    assert recovered['schema_mutated'] is True
    assert set(recovered['created_indexes']) == missing_index_names
    assert recovered['invalid_tables'] == {}
    assert recovered['schema_ready'] is True
    assert recovered['error_code'] is None

    repeated = inspect_provider_cost_fact_migration(engine, apply=True)

    assert repeated['schema_mutated'] is False
    assert repeated['created_indexes'] == []
    assert repeated['schema_ready'] is True


def test_provider_cost_apply_refuses_same_name_wrong_index(
    tmp_path: Path,
) -> None:
    engine = _build_legacy_engine(tmp_path)
    inspect_provider_cost_fact_migration(engine, apply=True)
    with engine.begin() as connection:
        connection.execute(text('DROP INDEX ix_provider_calls_project_id'))
        connection.execute(text(
            'CREATE INDEX ix_provider_calls_project_id '
            'ON provider_calls(provider)'
        ))

    status = provider_cost_schema_status(engine)

    invalid_indexes = status['invalid_tables']['provider_calls']['invalid_indexes']
    assert invalid_indexes == [{
        'name': 'ix_provider_calls_project_id',
        'expected_columns': ['project_id'],
        'actual_columns': ['provider'],
        'expected_unique': False,
        'actual_unique': False,
    }]

    applied = inspect_provider_cost_fact_migration(engine, apply=True)

    assert applied['schema_mutated'] is False
    assert applied['created_indexes'] == []
    assert applied['schema_ready'] is False
    assert applied['error_code'] == 'migration_required'
    actual_indexes = {
        index['name']: index['column_names']
        for index in inspect(engine).get_indexes('provider_calls')
    }
    assert actual_indexes['ix_provider_calls_project_id'] == ['provider']


def test_provider_cost_status_allows_extra_columns_and_indexes(
    tmp_path: Path,
) -> None:
    engine = _build_legacy_engine(tmp_path)
    inspect_provider_cost_fact_migration(engine, apply=True)
    with engine.begin() as connection:
        connection.execute(text(
            'ALTER TABLE provider_calls ADD COLUMN future_note TEXT'
        ))
        connection.execute(text(
            'CREATE INDEX ix_provider_calls_future_note '
            'ON provider_calls(future_note)'
        ))

    assert provider_cost_schema_status(engine) == {
        'ready': True,
        'error_code': None,
        'missing_tables': [],
        'invalid_tables': {},
    }


@pytest.mark.parametrize(
    ('column_name', 'column_type', 'nullable', 'expected_invalid_column'),
    [
        (
            'attempt',
            Text(),
            None,
            {
                'name': 'attempt',
                'expected': {
                    'type': 'INTEGER',
                    'nullable': False,
                },
                'actual': {
                    'type': 'TEXT',
                    'nullable': False,
                },
                'mismatches': ['type'],
            },
        ),
        (
            'idempotency_key',
            None,
            True,
            {
                'name': 'idempotency_key',
                'expected': {
                    'type': 'VARCHAR(255)',
                    'nullable': False,
                },
                'actual': {
                    'type': 'VARCHAR(255)',
                    'nullable': True,
                },
                'mismatches': ['nullable'],
            },
        ),
    ],
    ids=['wrong-type', 'wrong-nullable'],
)
def test_provider_cost_status_rejects_invalid_required_column_contract(
    tmp_path: Path,
    column_name: str,
    column_type,
    nullable: bool | None,
    expected_invalid_column: dict,
) -> None:
    engine = _build_provider_schema_with_column_override(
        tmp_path,
        column_name,
        column_type=column_type,
        nullable=nullable,
    )

    status = provider_cost_schema_status(engine)

    assert status['ready'] is False
    assert status['error_code'] == 'migration_required'
    assert status['missing_tables'] == []
    assert set(status['invalid_tables']) == {'provider_calls'}
    assert status['invalid_tables']['provider_calls']['invalid_columns'] == [
        expected_invalid_column
    ]

    applied = inspect_provider_cost_fact_migration(engine, apply=True)

    assert applied['schema_mutated'] is False
    assert applied['created_indexes'] == []
    assert applied['schema_ready'] is False
    assert applied['error_code'] == 'migration_required'


def test_provider_cost_migration_creates_declared_constraints_and_indexes(
    tmp_path: Path,
) -> None:
    engine = _build_legacy_engine(tmp_path)

    inspect_provider_cost_fact_migration(engine, apply=True)
    inspector = inspect(engine)

    expected_unique_columns = {
        'provider_calls': ('idempotency_key', 'attempt'),
        'cost_events': ('provider_call_id', 'meter', 'cost_basis'),
        'price_catalog': (
            'provider',
            'model',
            'operation',
            'meter',
            'effective_from',
        ),
    }
    for table_name, expected_columns in expected_unique_columns.items():
        unique_columns = {
            tuple(constraint['column_names'])
            for constraint in inspector.get_unique_constraints(table_name)
        }
        assert expected_columns in unique_columns

    context_indexes = {
        ('project_id',),
        ('job_id',),
        ('run_id',),
        ('window_id',),
    }
    provider_indexes = {
        tuple(index['column_names'])
        for index in inspector.get_indexes('provider_calls')
    }
    cost_indexes = {
        tuple(index['column_names'])
        for index in inspector.get_indexes('cost_events')
    }
    assert context_indexes <= provider_indexes
    assert context_indexes | {('occurred_at',)} <= cost_indexes

    checks = inspector.get_check_constraints('cost_events')
    measurement_check = next(
        constraint
        for constraint in checks
        if constraint['name'] == 'ck_cost_events_measurement_status'
    )
    normalized_sql = ' '.join(measurement_check['sqltext'].split())
    assert normalized_sql == (
        "measurement_status IN ('measured', 'estimated', 'unmetered')"
    )


def test_provider_cost_table_set_is_fixed_for_this_migration(tmp_path: Path) -> None:
    assert PROVIDER_COST_FACT_TABLES == EXPECTED_TABLES

    engine = _build_legacy_engine(tmp_path)
    inspect_provider_cost_fact_migration(engine, apply=True)

    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES | {
        'legacy_sentinel',
        'usage_ledgers',
    }


def test_provider_cost_schema_status_is_read_only_before_and_after_migration(
    tmp_path: Path,
) -> None:
    engine = _build_legacy_engine(tmp_path)
    schema_before = _legacy_snapshot(engine)
    tables_before = set(inspect(engine).get_table_names())

    missing = provider_cost_schema_status(engine)

    assert missing == {
        'ready': False,
        'error_code': 'migration_required',
        'missing_tables': sorted(EXPECTED_TABLES),
        'invalid_tables': {},
    }
    assert set(inspect(engine).get_table_names()) == tables_before
    assert _legacy_snapshot(engine) == schema_before

    inspect_provider_cost_fact_migration(engine, apply=True)
    tables_after_migration = set(inspect(engine).get_table_names())
    legacy_after_migration = _legacy_snapshot(engine)

    ready = provider_cost_schema_status(engine)

    assert ready == {
        'ready': True,
        'error_code': None,
        'missing_tables': [],
        'invalid_tables': {},
    }
    assert set(inspect(engine).get_table_names()) == tables_after_migration
    assert _legacy_snapshot(engine) == legacy_after_migration


def test_legacy_main_starts_without_provider_cost_migration(tmp_path: Path) -> None:
    database_path = tmp_path / 'speaker.db'
    env = os.environ.copy()
    env.update({
        'PYTHONPATH': str(BACKEND_DIR),
        'PYTHONPYCACHEPREFIX': str(tmp_path / 'pycache'),
        'PYTHONDONTWRITEBYTECODE': '1',
        'SQLITE_PATH': str(database_path),
    })

    subprocess.run(
        [sys.executable, '-c', 'import main'],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    engine = create_engine(f'sqlite:///{database_path}')
    assert set(inspect(engine).get_table_names()).isdisjoint(EXPECTED_TABLES)
