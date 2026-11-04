from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Any

from app.db.session import engine


LEGACY_RECORD_COLUMNS = (
    'id',
    'project_id',
    'voice_clone_profile_id',
    'person_id',
    'program_key',
    'program_name',
    'target_model',
    'input_text',
    'output_audio_local_path',
    'output_audio_public_url',
    'status',
    'provider_message',
    'created_at',
)

PROFILE_COLUMNS = (
    'id',
    'voice_name',
    'provider',
    'target_model',
)

SNAPSHOT_COLUMNS = (
    'voice_name_snapshot',
    'provider_snapshot',
    'target_model_snapshot',
)

RECORD_TABLE_SQL = '''
CREATE TABLE voice_clone_synthesis_records_v2_reliability (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36),
    voice_clone_profile_id VARCHAR(36),
    voice_name_snapshot VARCHAR(255),
    provider_snapshot VARCHAR(32),
    target_model_snapshot VARCHAR(128),
    person_id VARCHAR(36),
    program_key VARCHAR(255),
    program_name VARCHAR(255),
    target_model VARCHAR(128) NOT NULL,
    input_text TEXT NOT NULL,
    output_audio_local_path TEXT,
    output_audio_public_url TEXT,
    status VARCHAR(32) NOT NULL,
    provider_message TEXT,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(voice_clone_profile_id)
        REFERENCES voice_clone_profiles(id) ON DELETE SET NULL,
    FOREIGN KEY(person_id) REFERENCES people(id)
)
'''

INDEX_STATEMENTS = (
    'CREATE INDEX ix_voice_clone_synthesis_records_project_id '
    'ON voice_clone_synthesis_records(project_id)',
    'CREATE INDEX ix_voice_clone_synthesis_records_voice_clone_profile_id '
    'ON voice_clone_synthesis_records(voice_clone_profile_id)',
    'CREATE INDEX ix_voice_clone_synthesis_records_person_id '
    'ON voice_clone_synthesis_records(person_id)',
    'CREATE INDEX ix_voice_clone_synthesis_records_program_key '
    'ON voice_clone_synthesis_records(program_key)',
)

INDEX_COLUMNS = {
    'ix_voice_clone_synthesis_records_project_id': ('project_id',),
    'ix_voice_clone_synthesis_records_voice_clone_profile_id': (
        'voice_clone_profile_id',
    ),
    'ix_voice_clone_synthesis_records_person_id': ('person_id',),
    'ix_voice_clone_synthesis_records_program_key': ('program_key',),
}

DELETION_TASK_COLUMNS = frozenset({
    'id',
    'project_id',
    'kind',
    'resource_key',
    'provider',
    'target_model',
    'voice_id',
    'oss_object_key',
    'local_path',
    'attempt_count',
    'next_attempt_at',
    'last_error',
    'created_at',
    'updated_at',
})

DELETION_TASK_TABLE_SQL = '''
CREATE TABLE IF NOT EXISTS voice_clone_deletion_tasks (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36),
    kind VARCHAR(32) NOT NULL,
    resource_key VARCHAR(1024) NOT NULL UNIQUE,
    provider VARCHAR(32),
    target_model VARCHAR(128),
    voice_id VARCHAR(255),
    oss_object_key TEXT,
    local_path TEXT,
    attempt_count INTEGER NOT NULL,
    next_attempt_at DATETIME,
    last_error TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
'''


def inspect_voice_clone_reliability_migration(
    database_path: str | Path,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f'SQLite database file does not exist: {path}')
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA busy_timeout=5000')
    try:
        _require_schema(connection)
        before = _migration_state(connection)
        schema_mutated = False
        if apply and before['migration_required']:
            _apply_migration(connection)
            schema_mutated = True
        after = _migration_state(connection)
        foreign_key_violations = [
            dict(row)
            for row in connection.execute(
                'PRAGMA foreign_key_check(voice_clone_synthesis_records)'
            )
        ]
        return {
            'mode': 'apply' if apply else 'preview',
            'database_path': str(path),
            'schema_mutated': schema_mutated,
            'schema_ready': not after['migration_required'],
            'before': before,
            'after': after,
            'foreign_key_violation_count': len(foreign_key_violations),
            'foreign_key_violations': foreign_key_violations,
        }
    finally:
        connection.close()


def _require_schema(connection: sqlite3.Connection) -> None:
    table_names = {
        row['name']
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    required_tables = {
        'people',
        'voice_clone_profiles',
        'voice_clone_synthesis_records',
    }
    missing_tables = sorted(required_tables - table_names)
    if missing_tables:
        raise RuntimeError(f"missing required tables: {', '.join(missing_tables)}")
    for table_name, required_columns in (
        ('voice_clone_profiles', PROFILE_COLUMNS),
        ('voice_clone_synthesis_records', LEGACY_RECORD_COLUMNS),
    ):
        existing_columns = {
            row['name'] for row in connection.execute(f'PRAGMA table_info({table_name})')
        }
        missing_columns = sorted(set(required_columns) - existing_columns)
        if missing_columns:
            raise RuntimeError(
                f"{table_name} missing required columns: {', '.join(missing_columns)}"
            )


def _migration_state(connection: sqlite3.Connection) -> dict[str, Any]:
    record_info = {
        row['name']: dict(row)
        for row in connection.execute('PRAGMA table_info(voice_clone_synthesis_records)')
    }
    snapshot_columns_present = all(
        column_name in record_info for column_name in SNAPSHOT_COLUMNS
    )
    profile_id_nullable = not bool(record_info['voice_clone_profile_id']['notnull'])
    actual_indexes = {
        index_name: tuple(
            row['name']
            for row in connection.execute(f'PRAGMA index_info({index_name})')
        )
        for index_name in INDEX_COLUMNS
    }
    indexes_ready = actual_indexes == INDEX_COLUMNS
    profile_foreign_keys = [
        row
        for row in connection.execute(
            'PRAGMA foreign_key_list(voice_clone_synthesis_records)'
        )
        if row['from'] == 'voice_clone_profile_id'
    ]
    profile_fk_ready = (
        len(profile_foreign_keys) == 1
        and profile_foreign_keys[0]['table'] == 'voice_clone_profiles'
        and profile_foreign_keys[0]['to'] == 'id'
        and profile_foreign_keys[0]['on_delete'].upper() == 'SET NULL'
    )
    deletion_task_columns = {
        row['name']
        for row in connection.execute(
            'PRAGMA table_info(voice_clone_deletion_tasks)'
        )
    }
    deletion_task_unique_columns = {
        tuple(
            item['name']
            for item in connection.execute(
                f"PRAGMA index_info({row['name']})"
            )
        )
        for row in connection.execute(
            'PRAGMA index_list(voice_clone_deletion_tasks)'
        )
        if row['unique']
    }
    deletion_task_table_ready = (
        deletion_task_columns == DELETION_TASK_COLUMNS
        and ('resource_key',) in deletion_task_unique_columns
        and not list(
            connection.execute(
                'PRAGMA foreign_key_list(voice_clone_deletion_tasks)'
            )
        )
    )
    return {
        'record_count': _scalar(
            connection,
            'SELECT COUNT(*) FROM voice_clone_synthesis_records',
        ),
        'profile_id_nullable': profile_id_nullable,
        'snapshot_columns_present': snapshot_columns_present,
        'indexes_ready': indexes_ready,
        'profile_fk_ready': profile_fk_ready,
        'deletion_task_table_ready': deletion_task_table_ready,
        'migration_required': (
            not profile_id_nullable
            or not snapshot_columns_present
            or not indexes_ready
            or not profile_fk_ready
            or not deletion_task_table_ready
        ),
    }


def _apply_migration(connection: sqlite3.Connection) -> None:
    connection.commit()
    connection.execute('PRAGMA foreign_keys=OFF')
    try:
        connection.execute('BEGIN IMMEDIATE')
        before = _migration_state(connection)
        connection.execute(DELETION_TASK_TABLE_SQL)
        record_schema_ready = (
            before['profile_id_nullable']
            and before['snapshot_columns_present']
            and before['indexes_ready']
            and before['profile_fk_ready']
        )
        if record_schema_ready:
            connection.commit()
            return
        connection.execute('DROP TABLE IF EXISTS voice_clone_synthesis_records_v2_reliability')
        connection.execute(RECORD_TABLE_SQL)
        record_info = {
            row['name']
            for row in connection.execute(
                'PRAGMA table_info(voice_clone_synthesis_records)'
            )
        }
        snapshot_values = []
        for snapshot_column, profile_column in (
            ('voice_name_snapshot', 'voice_name'),
            ('provider_snapshot', 'provider'),
            ('target_model_snapshot', 'target_model'),
        ):
            profile_value = f'profile.{profile_column}'
            snapshot_values.append(
                f'COALESCE(record.{snapshot_column}, {profile_value})'
                if snapshot_column in record_info
                else profile_value
            )
        snapshot_select = ',\n                '.join(snapshot_values)
        connection.execute(f'''
            INSERT INTO voice_clone_synthesis_records_v2_reliability (
                id,
                project_id,
                voice_clone_profile_id,
                voice_name_snapshot,
                provider_snapshot,
                target_model_snapshot,
                person_id,
                program_key,
                program_name,
                target_model,
                input_text,
                output_audio_local_path,
                output_audio_public_url,
                status,
                provider_message,
                created_at
            )
            SELECT
                record.id,
                record.project_id,
                CASE
                    WHEN profile.id IS NULL THEN NULL
                    ELSE record.voice_clone_profile_id
                END,
                {snapshot_select},
                record.person_id,
                record.program_key,
                record.program_name,
                record.target_model,
                record.input_text,
                record.output_audio_local_path,
                record.output_audio_public_url,
                record.status,
                record.provider_message,
                record.created_at
            FROM voice_clone_synthesis_records AS record
            LEFT JOIN voice_clone_profiles AS profile
                ON profile.id = record.voice_clone_profile_id
        ''')
        _assert_copy_count(
            connection,
            'voice_clone_synthesis_records',
            'voice_clone_synthesis_records_v2_reliability',
        )
        connection.execute('DROP TABLE voice_clone_synthesis_records')
        connection.execute(
            'ALTER TABLE voice_clone_synthesis_records_v2_reliability '
            'RENAME TO voice_clone_synthesis_records'
        )
        for statement in INDEX_STATEMENTS:
            connection.execute(statement)
        foreign_key_violations = list(
            connection.execute(
                'PRAGMA foreign_key_check(voice_clone_synthesis_records)'
            )
        )
        if foreign_key_violations:
            raise RuntimeError(
                'voice_clone_synthesis_records foreign key check failed: '
                f'{len(foreign_key_violations)} violation(s)'
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute('PRAGMA foreign_keys=ON')


def _assert_copy_count(
    connection: sqlite3.Connection,
    source_table: str,
    target_table: str,
) -> None:
    source_count = _scalar(connection, f'SELECT COUNT(*) FROM {source_table}')
    target_count = _scalar(connection, f'SELECT COUNT(*) FROM {target_table}')
    if source_count != target_count:
        raise RuntimeError(
            f'{source_table} copy count mismatch: source={source_count} target={target_count}'
        )


def _scalar(connection: sqlite3.Connection, statement: str) -> int:
    return int(connection.execute(statement).fetchone()[0])


def _default_database_path() -> Path:
    database = engine.url.database
    if not database:
        raise RuntimeError('SQLite database path is unavailable')
    return Path(database)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Preserve voice clone history snapshots for Speaker 2.0.',
    )
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--database', type=Path, default=None)
    arguments = parser.parse_args()
    report = inspect_voice_clone_reliability_migration(
        arguments.database or _default_database_path(),
        apply=arguments.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
