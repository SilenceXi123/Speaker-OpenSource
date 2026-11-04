from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Any


IDENTITY_TABLE_SQL = '''
CREATE TABLE IF NOT EXISTS voice_clone_identities (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    display_name VARCHAR(255) NOT NULL,
    aliases JSON NOT NULL,
    reference_embedding JSON,
    embedding_model_id VARCHAR(255),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
'''

IDENTITY_REVIEW_TABLE_SQL = '''
CREATE TABLE IF NOT EXISTS voice_clone_identity_reviews (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    candidate_identity_id VARCHAR(36) NOT NULL,
    sample_id VARCHAR(36),
    submitted_name VARCHAR(255) NOT NULL,
    match_score FLOAT NOT NULL,
    status VARCHAR(32) NOT NULL,
    resolved_by VARCHAR(255),
    resolution_reason TEXT,
    created_at DATETIME NOT NULL,
    resolved_at DATETIME,
    FOREIGN KEY(candidate_identity_id) REFERENCES voice_clone_identities(id),
    FOREIGN KEY(sample_id) REFERENCES voice_clone_samples(id) ON DELETE SET NULL
)
'''

IDENTITY_REVIEW_REPLACEMENT_TABLE_SQL = IDENTITY_REVIEW_TABLE_SQL.replace(
    'voice_clone_identity_reviews',
    'voice_clone_identity_reviews_replacement',
    1,
).replace('IF NOT EXISTS ', '')

SAMPLE_COLUMNS = {
    'identity_id': 'VARCHAR(36)',
    'embedding': 'JSON',
    'embedding_status': 'VARCHAR(32)',
    'embedding_provider_name': 'VARCHAR(255)',
    'embedding_model_id': 'VARCHAR(255)',
    'embedding_message': 'TEXT',
}
PROFILE_COLUMNS = {'identity_id': 'VARCHAR(36)'}
INDEX_STATEMENTS = (
    'CREATE INDEX IF NOT EXISTS ix_voice_clone_identities_display_name ON voice_clone_identities(display_name)',
    'CREATE INDEX IF NOT EXISTS ix_voice_clone_identities_embedding_model_id ON voice_clone_identities(embedding_model_id)',
    'CREATE INDEX IF NOT EXISTS ix_voice_clone_identity_reviews_candidate_identity_id ON voice_clone_identity_reviews(candidate_identity_id)',
    'CREATE INDEX IF NOT EXISTS ix_voice_clone_identity_reviews_sample_id ON voice_clone_identity_reviews(sample_id)',
    'CREATE INDEX IF NOT EXISTS ix_voice_clone_identity_reviews_status ON voice_clone_identity_reviews(status)',
    'CREATE INDEX IF NOT EXISTS ix_voice_clone_samples_identity_id ON voice_clone_samples(identity_id)',
    'CREATE INDEX IF NOT EXISTS ix_voice_clone_profiles_identity_id ON voice_clone_profiles(identity_id)',
)
INSERT_TRIGGER_SQL = '''
CREATE TRIGGER trg_voice_clone_samples_identity_limit
BEFORE INSERT ON voice_clone_samples
WHEN NEW.identity_id IS NOT NULL AND (
    SELECT COUNT(*) FROM voice_clone_samples
    WHERE identity_id = NEW.identity_id
    AND julianday(created_at) > julianday('now', '-72 hours')
) >= 3
BEGIN SELECT RAISE(ABORT, 'voice_clone_sample_identity_limit'); END
'''
UPDATE_TRIGGER_SQL = '''
CREATE TRIGGER trg_voice_clone_samples_identity_limit_update
BEFORE UPDATE OF identity_id ON voice_clone_samples
WHEN NEW.identity_id IS NOT NULL AND (
    SELECT COUNT(*) FROM voice_clone_samples
    WHERE identity_id = NEW.identity_id AND id <> NEW.id
    AND julianday(created_at) > julianday('now', '-72 hours')
) >= 3
BEGIN SELECT RAISE(ABORT, 'voice_clone_sample_identity_limit'); END
'''


def inspect_voice_clone_identity_migration(
    database_path: Path,
    apply: bool = False,
) -> dict[str, object]:
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f'SQLite database file does not exist: {path}')
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA busy_timeout=5000')
    try:
        before = _migration_state(connection)
        applied = False
        if apply and before['migration_required']:
            _apply_migration(connection)
            applied = True
        after = _migration_state(connection)
        return {
            'mode': 'apply' if apply else 'preview',
            'database_path': str(path),
            'missing_tables': before['missing_tables'],
            'applied': applied,
            'schema_ready': not after['migration_required'],
            'before': before,
            'after': after,
        }
    finally:
        connection.close()


def _migration_state(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing_tables = sorted(
        {'voice_clone_identities', 'voice_clone_identity_reviews'} - tables
    )
    missing_columns = {
        table_name: sorted(
            set(required_columns) - _column_names(connection, table_name)
        )
        for table_name, required_columns in (
            ('voice_clone_samples', SAMPLE_COLUMNS),
            ('voice_clone_profiles', PROFILE_COLUMNS),
        )
    }
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    required_indexes = {
        'ix_voice_clone_identities_embedding_model_id',
        'ix_voice_clone_samples_identity_id',
        'ix_voice_clone_profiles_identity_id',
    }
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
    }
    trigger_ready = (
        'trg_voice_clone_samples_name_limit' not in triggers
        and 'trg_voice_clone_samples_identity_limit' in triggers
        and 'trg_voice_clone_samples_identity_limit_update' in triggers
        and all('julianday(created_at)' in (row[0] or '') for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name IN ("
            "'trg_voice_clone_samples_identity_limit', 'trg_voice_clone_samples_identity_limit_update')"
        ))
    )
    review_sample_delete_policy = _review_sample_delete_policy(connection)
    return {
        'missing_tables': missing_tables,
        'missing_columns': missing_columns,
        'missing_indexes': sorted(required_indexes - indexes),
        'trigger_ready': trigger_ready,
        'review_sample_delete_policy': review_sample_delete_policy,
        'migration_required': bool(
            missing_tables
            or any(missing_columns.values())
            or required_indexes - indexes
            or not trigger_ready
            or (
                'voice_clone_identity_reviews' in tables
                and review_sample_delete_policy != 'SET NULL'
            )
        ),
    }


def _column_names(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row[1]
        for row in connection.execute(f'PRAGMA table_info({table_name})')
    }


def _review_sample_delete_policy(connection: sqlite3.Connection) -> str | None:
    return next((
        str(row[6]).upper()
        for row in connection.execute(
            'PRAGMA foreign_key_list(voice_clone_identity_reviews)'
        )
        if row[3] == 'sample_id'
    ), None)


def _rebuild_identity_review_table(connection: sqlite3.Connection) -> None:
    connection.execute('DROP TABLE IF EXISTS voice_clone_identity_reviews_replacement')
    connection.execute(IDENTITY_REVIEW_REPLACEMENT_TABLE_SQL)
    columns = (
        'id, candidate_identity_id, sample_id, submitted_name, match_score, '
        'status, resolved_by, resolution_reason, created_at, resolved_at'
    )
    connection.execute(
        f'INSERT INTO voice_clone_identity_reviews_replacement ({columns}) '
        f'SELECT {columns} FROM voice_clone_identity_reviews'
    )
    connection.execute('DROP TABLE voice_clone_identity_reviews')
    connection.execute(
        'ALTER TABLE voice_clone_identity_reviews_replacement '
        'RENAME TO voice_clone_identity_reviews'
    )


def _apply_migration(connection: sqlite3.Connection) -> None:
    connection.execute('BEGIN IMMEDIATE')
    try:
        connection.execute(IDENTITY_TABLE_SQL)
        connection.execute(IDENTITY_REVIEW_TABLE_SQL)
        if _review_sample_delete_policy(connection) != 'SET NULL':
            _rebuild_identity_review_table(connection)
        for table_name, required_columns in (
            ('voice_clone_samples', SAMPLE_COLUMNS),
            ('voice_clone_profiles', PROFILE_COLUMNS),
        ):
            existing_columns = _column_names(connection, table_name)
            for name, column_type in required_columns.items():
                if name not in existing_columns:
                    connection.execute(
                        f'ALTER TABLE {table_name} ADD COLUMN {name} {column_type}'
                    )
        for statement in INDEX_STATEMENTS:
            connection.execute(statement)
        connection.execute('DROP TRIGGER IF EXISTS trg_voice_clone_samples_name_limit')
        connection.execute('DROP TRIGGER IF EXISTS trg_voice_clone_samples_identity_limit')
        connection.execute('DROP TRIGGER IF EXISTS trg_voice_clone_samples_identity_limit_update')
        connection.execute(INSERT_TRIGGER_SQL)
        connection.execute(UPDATE_TRIGGER_SQL)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('database_path', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(
        inspect_voice_clone_identity_migration(args.database_path, apply=args.apply),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == '__main__':
    main()
