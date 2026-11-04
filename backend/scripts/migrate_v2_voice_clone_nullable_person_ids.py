from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Any

from app.db.session import engine


PROFILE_COLUMNS = (
    'id',
    'project_id',
    'person_id',
    'source_sample_id',
    'source_clone_sample_id',
    'source_audio_local_path',
    'program_key',
    'program_name',
    'provider',
    'provider_model_family',
    'target_model',
    'voice_id',
    'voice_name',
    'status',
    'provider_message',
    'preview_text',
    'preview_audio_local_path',
    'usage_program_keys',
    'created_at',
    'updated_at',
    'last_used_at',
)

RECORD_COLUMNS = (
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


PROFILE_TABLE_SQL = '''
CREATE TABLE voice_clone_profiles_v2_nullable (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36),
    person_id VARCHAR(36),
    source_sample_id VARCHAR(36),
    source_clone_sample_id VARCHAR(36),
    source_audio_local_path TEXT,
    program_key VARCHAR(255),
    program_name VARCHAR(255),
    provider VARCHAR(32) NOT NULL,
    provider_model_family VARCHAR(64) NOT NULL,
    target_model VARCHAR(128) NOT NULL,
    voice_id VARCHAR(255),
    voice_name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL,
    provider_message TEXT,
    preview_text TEXT,
    preview_audio_local_path TEXT,
    usage_program_keys JSON,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    last_used_at DATETIME,
    FOREIGN KEY(person_id) REFERENCES people(id),
    FOREIGN KEY(source_sample_id) REFERENCES voiceprint_samples(id),
    FOREIGN KEY(source_clone_sample_id) REFERENCES voice_clone_samples(id)
)
'''

RECORD_TABLE_SQL = '''
CREATE TABLE voice_clone_synthesis_records_v2_nullable (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    project_id VARCHAR(36),
    voice_clone_profile_id VARCHAR(36) NOT NULL,
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
    FOREIGN KEY(voice_clone_profile_id) REFERENCES voice_clone_profiles_v2_nullable(id),
    FOREIGN KEY(person_id) REFERENCES people(id)
)
'''

INDEX_STATEMENTS = (
    'CREATE INDEX ix_voice_clone_profiles_project_id ON voice_clone_profiles(project_id)',
    'CREATE INDEX ix_voice_clone_profiles_person_id ON voice_clone_profiles(person_id)',
    'CREATE INDEX ix_voice_clone_profiles_source_sample_id ON voice_clone_profiles(source_sample_id)',
    'CREATE INDEX ix_voice_clone_profiles_source_clone_sample_id ON voice_clone_profiles(source_clone_sample_id)',
    'CREATE INDEX ix_voice_clone_profiles_program_key ON voice_clone_profiles(program_key)',
    'CREATE INDEX ix_voice_clone_profiles_program_name ON voice_clone_profiles(program_name)',
    'CREATE INDEX ix_voice_clone_profiles_voice_id ON voice_clone_profiles(voice_id)',
    'CREATE INDEX ix_voice_clone_synthesis_records_project_id ON voice_clone_synthesis_records(project_id)',
    'CREATE INDEX ix_voice_clone_synthesis_records_voice_clone_profile_id ON voice_clone_synthesis_records(voice_clone_profile_id)',
    'CREATE INDEX ix_voice_clone_synthesis_records_person_id ON voice_clone_synthesis_records(person_id)',
    'CREATE INDEX ix_voice_clone_synthesis_records_program_key ON voice_clone_synthesis_records(program_key)',
)


def inspect_voice_clone_nullable_person_migration(
    database_path: str | Path,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    path = Path(database_path).expanduser().resolve()
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
            for row in connection.execute('PRAGMA foreign_key_check')
            if row['table'] in {'voice_clone_profiles', 'voice_clone_synthesis_records'}
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
        'voiceprint_samples',
        'voice_clone_samples',
        'voice_clone_profiles',
        'voice_clone_synthesis_records',
    }
    missing_tables = sorted(required_tables - table_names)
    if missing_tables:
        raise RuntimeError(f"missing required tables: {', '.join(missing_tables)}")
    for table_name, required_columns in (
        ('voice_clone_profiles', PROFILE_COLUMNS),
        ('voice_clone_synthesis_records', RECORD_COLUMNS),
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
    profile_info = {
        row['name']: dict(row)
        for row in connection.execute('PRAGMA table_info(voice_clone_profiles)')
    }
    record_info = {
        row['name']: dict(row)
        for row in connection.execute('PRAGMA table_info(voice_clone_synthesis_records)')
    }
    counts = {
        'profile_count': _scalar(connection, 'SELECT COUNT(*) FROM voice_clone_profiles'),
        'record_count': _scalar(connection, 'SELECT COUNT(*) FROM voice_clone_synthesis_records'),
        'profile_invalid_person_count': _scalar(connection, '''
            SELECT COUNT(*)
            FROM voice_clone_profiles AS profile
            WHERE profile.person_id IS NOT NULL
              AND (
                  TRIM(profile.person_id) = ''
                  OR NOT EXISTS (
                      SELECT 1 FROM people AS person WHERE person.id = profile.person_id
                  )
              )
        '''),
        'record_invalid_person_count': _scalar(connection, '''
            SELECT COUNT(*)
            FROM voice_clone_synthesis_records AS record
            WHERE record.person_id IS NOT NULL
              AND (
                  TRIM(record.person_id) = ''
                  OR NOT EXISTS (
                      SELECT 1 FROM people AS person WHERE person.id = record.person_id
                  )
              )
        '''),
        'profile_invalid_source_sample_count': _scalar(connection, '''
            SELECT COUNT(*)
            FROM voice_clone_profiles AS profile
            WHERE TRIM(COALESCE(profile.source_sample_id, '')) <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM voiceprint_samples AS sample
                  WHERE sample.id = profile.source_sample_id
              )
        '''),
        'profile_invalid_clone_sample_count': _scalar(connection, '''
            SELECT COUNT(*)
            FROM voice_clone_profiles AS profile
            WHERE TRIM(COALESCE(profile.source_clone_sample_id, '')) <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM voice_clone_samples AS sample
                  WHERE sample.id = profile.source_clone_sample_id
              )
        '''),
    }
    profile_person_nullable = not bool(profile_info['person_id']['notnull'])
    record_person_nullable = not bool(record_info['person_id']['notnull'])
    migration_required = (
        not profile_person_nullable
        or not record_person_nullable
        or any(
            counts[key]
            for key in (
                'profile_invalid_person_count',
                'record_invalid_person_count',
                'profile_invalid_source_sample_count',
                'profile_invalid_clone_sample_count',
            )
        )
    )
    return {
        **counts,
        'profile_person_nullable': profile_person_nullable,
        'record_person_nullable': record_person_nullable,
        'migration_required': migration_required,
    }


def _apply_migration(connection: sqlite3.Connection) -> None:
    connection.commit()
    connection.execute('PRAGMA foreign_keys=OFF')
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('DROP TABLE IF EXISTS voice_clone_synthesis_records_v2_nullable')
        connection.execute('DROP TABLE IF EXISTS voice_clone_profiles_v2_nullable')
        connection.execute(PROFILE_TABLE_SQL)
        profile_columns = ', '.join(PROFILE_COLUMNS)
        connection.execute(f'''
            INSERT INTO voice_clone_profiles_v2_nullable ({profile_columns})
            SELECT
                id,
                project_id,
                CASE
                    WHEN TRIM(COALESCE(person_id, '')) = '' THEN NULL
                    WHEN NOT EXISTS (SELECT 1 FROM people WHERE people.id = voice_clone_profiles.person_id) THEN NULL
                    ELSE person_id
                END,
                CASE
                    WHEN TRIM(COALESCE(source_sample_id, '')) = '' THEN NULL
                    WHEN NOT EXISTS (
                        SELECT 1 FROM voiceprint_samples
                        WHERE voiceprint_samples.id = voice_clone_profiles.source_sample_id
                    ) THEN NULL
                    ELSE source_sample_id
                END,
                CASE
                    WHEN TRIM(COALESCE(source_clone_sample_id, '')) = '' THEN NULL
                    WHEN NOT EXISTS (
                        SELECT 1 FROM voice_clone_samples
                        WHERE voice_clone_samples.id = voice_clone_profiles.source_clone_sample_id
                    ) THEN NULL
                    ELSE source_clone_sample_id
                END,
                source_audio_local_path,
                program_key,
                program_name,
                provider,
                provider_model_family,
                target_model,
                voice_id,
                voice_name,
                status,
                provider_message,
                preview_text,
                preview_audio_local_path,
                usage_program_keys,
                created_at,
                updated_at,
                last_used_at
            FROM voice_clone_profiles
        ''')
        connection.execute(RECORD_TABLE_SQL)
        record_columns = ', '.join(RECORD_COLUMNS)
        connection.execute(f'''
            INSERT INTO voice_clone_synthesis_records_v2_nullable ({record_columns})
            SELECT
                id,
                project_id,
                voice_clone_profile_id,
                CASE
                    WHEN TRIM(COALESCE(person_id, '')) = '' THEN NULL
                    WHEN NOT EXISTS (
                        SELECT 1 FROM people
                        WHERE people.id = voice_clone_synthesis_records.person_id
                    ) THEN NULL
                    ELSE person_id
                END,
                program_key,
                program_name,
                target_model,
                input_text,
                output_audio_local_path,
                output_audio_public_url,
                status,
                provider_message,
                created_at
            FROM voice_clone_synthesis_records
        ''')
        _assert_copy_count(connection, 'voice_clone_profiles', 'voice_clone_profiles_v2_nullable')
        _assert_copy_count(
            connection,
            'voice_clone_synthesis_records',
            'voice_clone_synthesis_records_v2_nullable',
        )
        connection.execute('DROP TABLE voice_clone_synthesis_records')
        connection.execute('DROP TABLE voice_clone_profiles')
        connection.execute(
            'ALTER TABLE voice_clone_profiles_v2_nullable RENAME TO voice_clone_profiles'
        )
        connection.execute(
            'ALTER TABLE voice_clone_synthesis_records_v2_nullable '
            'RENAME TO voice_clone_synthesis_records'
        )
        for statement in INDEX_STATEMENTS:
            connection.execute(statement)
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
        description='Make legacy voice-clone person IDs nullable for Speaker 2.0.',
    )
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--database', type=Path, default=None)
    arguments = parser.parse_args()
    report = inspect_voice_clone_nullable_person_migration(
        arguments.database or _default_database_path(),
        apply=arguments.apply,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
