from __future__ import annotations

import sqlite3

import pytest

from scripts.migrate_v2_voice_clone_identities import (
    inspect_voice_clone_identity_migration,
)


def build_legacy_voice_clone_database(database_path):
    connection = sqlite3.connect(database_path)
    connection.executescript('''
        CREATE TABLE voice_clone_samples (
            id VARCHAR(36) PRIMARY KEY,
            display_name VARCHAR(255) NOT NULL,
            name_key VARCHAR(255),
            original_filename VARCHAR(255) NOT NULL,
            audio_local_path TEXT NOT NULL,
            clone_readiness_status VARCHAR(32) NOT NULL,
            created_at DATETIME NOT NULL
        );
        CREATE TABLE voice_clone_profiles (
            id VARCHAR(36) PRIMARY KEY,
            voice_name VARCHAR(255) NOT NULL
        );
        CREATE TRIGGER trg_voice_clone_samples_name_limit
        BEFORE INSERT ON voice_clone_samples
        WHEN NEW.name_key IS NOT NULL AND (
            SELECT COUNT(*) FROM voice_clone_samples WHERE name_key = NEW.name_key
        ) >= 3
        BEGIN SELECT RAISE(ABORT, 'voice_clone_sample_name_limit'); END;
    ''')
    connection.commit()
    connection.close()
    return database_path


def table_names(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def column_names(connection, table_name):
    return {row[1] for row in connection.execute(f'PRAGMA table_info({table_name})')}


def trigger_names(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
    }


def sample_review_delete_policy(connection):
    return next(
        row[6]
        for row in connection.execute(
            'PRAGMA foreign_key_list(voice_clone_identity_reviews)'
        )
        if row[3] == 'sample_id'
    )


def test_identity_migration_preview_is_read_only(tmp_path):
    database = build_legacy_voice_clone_database(tmp_path / 'legacy.db')
    before = database.read_bytes()

    report = inspect_voice_clone_identity_migration(database)

    assert report['missing_tables'] == [
        'voice_clone_identities',
        'voice_clone_identity_reviews',
    ]
    assert database.read_bytes() == before


def test_identity_migration_adds_tables_columns_and_indexes(tmp_path):
    database = build_legacy_voice_clone_database(tmp_path / 'legacy.db')

    report = inspect_voice_clone_identity_migration(database, apply=True)

    assert report['applied'] is True
    with sqlite3.connect(database) as connection:
        assert 'voice_clone_identities' in table_names(connection)
        assert 'voice_clone_identity_reviews' in table_names(connection)
        assert 'identity_id' in column_names(connection, 'voice_clone_samples')
        assert 'embedding' in column_names(connection, 'voice_clone_samples')
        assert 'identity_id' in column_names(connection, 'voice_clone_profiles')
        assert 'trg_voice_clone_samples_name_limit' not in trigger_names(connection)
        assert 'trg_voice_clone_samples_identity_limit' in trigger_names(connection)
        assert sample_review_delete_policy(connection) == 'SET NULL'


def test_identity_migration_upgrades_existing_review_sample_delete_policy(tmp_path):
    database = build_legacy_voice_clone_database(tmp_path / 'legacy-reviews.db')
    with sqlite3.connect(database) as connection:
        connection.executescript('''
            CREATE TABLE voice_clone_identities (
                id VARCHAR(36) PRIMARY KEY,
                display_name VARCHAR(255) NOT NULL,
                aliases JSON NOT NULL,
                reference_embedding JSON,
                embedding_model_id VARCHAR(255),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE voice_clone_identity_reviews (
                id VARCHAR(36) PRIMARY KEY,
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
                FOREIGN KEY(sample_id) REFERENCES voice_clone_samples(id)
            );
        ''')

    report = inspect_voice_clone_identity_migration(database, apply=True)

    assert report['applied'] is True
    with sqlite3.connect(database) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        assert sample_review_delete_policy(connection) == 'SET NULL'
        connection.execute(
            "INSERT INTO voice_clone_identities VALUES "
            "('identity-1', '主持人', '[]', NULL, NULL, '2026-08-31', '2026-08-31')"
        )
        connection.execute(
            "INSERT INTO voice_clone_samples (id, display_name, original_filename, "
            "audio_local_path, clone_readiness_status, created_at) VALUES "
            "('sample-1', '主持人', 'sample.wav', '/tmp/sample.wav', 'ready', '2026-08-31')"
        )
        connection.execute(
            "INSERT INTO voice_clone_identity_reviews VALUES "
            "('review-1', 'identity-1', 'sample-1', '主持人', 0.95, 'confirmed', "
            "'admin', 'same speaker', '2026-08-31', '2026-08-31')"
        )
        connection.execute("DELETE FROM voice_clone_samples WHERE id = 'sample-1'")
        assert connection.execute(
            "SELECT sample_id FROM voice_clone_identity_reviews WHERE id = 'review-1'"
        ).fetchone() == (None,)


def test_identity_limit_trigger_rejects_a_fourth_insert_or_update(tmp_path):
    database = build_legacy_voice_clone_database(tmp_path / 'legacy.db')
    inspect_voice_clone_identity_migration(database, apply=True)

    with sqlite3.connect(database) as connection:
        for sample_id in ('sample-1', 'sample-2', 'sample-3', 'sample-4'):
            connection.execute(
                '''
                INSERT INTO voice_clone_samples (
                    id, display_name, original_filename, audio_local_path,
                    clone_readiness_status, created_at
                ) VALUES (?, 'Name', 'sample.wav', '/tmp/sample.wav', 'unknown', datetime('now'))
                ''',
                (sample_id,),
            )
        connection.execute(
            "UPDATE voice_clone_samples SET identity_id = 'identity-1' "
            "WHERE id IN ('sample-1', 'sample-2', 'sample-3')"
        )
        with pytest.raises(sqlite3.IntegrityError, match='voice_clone_sample_identity_limit'):
            connection.execute(
                "UPDATE voice_clone_samples SET identity_id = 'identity-1' "
                "WHERE id = 'sample-4'"
            )


@pytest.mark.parametrize('startup_schema', [False, True])
def test_expired_samples_do_not_occupy_identity_slots(tmp_path, monkeypatch, startup_schema):
    database = build_legacy_voice_clone_database(tmp_path / 'expired.db')
    inspect_voice_clone_identity_migration(database, apply=True)
    if startup_schema:
        from sqlalchemy import create_engine
        from app.db import session as db

        engine = create_engine(f'sqlite:///{database}')
        db.Base.metadata.create_all(engine)
        monkeypatch.setattr(db, 'engine', engine)
        db.ensure_schema()
        engine.dispose()
    with sqlite3.connect(database) as connection:
        for index in range(3):
            connection.execute(
                "INSERT INTO voice_clone_samples (id, display_name, original_filename, audio_local_path, "
                "clone_readiness_status, created_at, identity_id) VALUES (?, 'Name', 'sample.wav', "
                "'/tmp/sample.wav', 'ready', datetime('now', '-72 hours'), 'identity-1')", (f'old-{index}',),
            )
        connection.execute(
            "INSERT INTO voice_clone_samples (id, display_name, original_filename, audio_local_path, "
            "clone_readiness_status, created_at) VALUES ('new', 'Name', 'sample.wav', "
            "'/tmp/sample.wav', 'ready', datetime('now'))"
        )
        connection.execute("UPDATE voice_clone_samples SET identity_id = 'identity-1' WHERE id = 'new'")
        assert connection.execute("SELECT count(*) FROM voice_clone_samples").fetchone() == (4,)
