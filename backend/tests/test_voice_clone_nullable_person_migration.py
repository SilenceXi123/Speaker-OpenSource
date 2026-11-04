from __future__ import annotations

import sqlite3

from scripts.migrate_v2_voice_clone_nullable_person_ids import (
    inspect_voice_clone_nullable_person_migration,
)


def test_nullable_person_migration_preserves_rows_and_is_idempotent(tmp_path) -> None:
    database_path = tmp_path / 'legacy-voice-clone.db'
    connection = sqlite3.connect(database_path)
    connection.executescript('''
        PRAGMA foreign_keys=OFF;
        CREATE TABLE people (id TEXT PRIMARY KEY);
        CREATE TABLE voiceprint_samples (id TEXT PRIMARY KEY);
        CREATE TABLE voice_clone_samples (id TEXT PRIMARY KEY);
        CREATE TABLE voice_clone_profiles (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            person_id VARCHAR(36) NOT NULL,
            source_sample_id VARCHAR(36),
            source_audio_local_path TEXT,
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
            source_clone_sample_id TEXT,
            program_key TEXT,
            program_name TEXT,
            project_id TEXT,
            FOREIGN KEY(person_id) REFERENCES people(id),
            FOREIGN KEY(source_sample_id) REFERENCES voiceprint_samples(id)
        );
        CREATE TABLE voice_clone_synthesis_records (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            voice_clone_profile_id VARCHAR(36) NOT NULL,
            person_id VARCHAR(36) NOT NULL,
            program_key VARCHAR(255),
            program_name VARCHAR(255),
            target_model VARCHAR(128) NOT NULL,
            input_text TEXT NOT NULL,
            output_audio_local_path TEXT,
            output_audio_public_url TEXT,
            status VARCHAR(32) NOT NULL,
            provider_message TEXT,
            created_at DATETIME NOT NULL,
            project_id TEXT,
            FOREIGN KEY(voice_clone_profile_id) REFERENCES voice_clone_profiles(id),
            FOREIGN KEY(person_id) REFERENCES people(id)
        );
        INSERT INTO people VALUES ('person-1');
        INSERT INTO voiceprint_samples VALUES ('voiceprint-1');
        INSERT INTO voice_clone_samples VALUES ('clone-1');
        INSERT INTO voice_clone_profiles VALUES (
            'profile-blank', '', 'missing-voiceprint', '/tmp/blank.wav',
            'qwen', 'qwen', 'qwen-model', 'voice-blank', 'Blank', 'ready',
            NULL, NULL, NULL, '[]', '2026-01-01', '2026-01-01', NULL,
            'clone-1', 'program', 'Program', 'project-1'
        );
        INSERT INTO voice_clone_profiles VALUES (
            'profile-person', 'person-1', 'voiceprint-1', '/tmp/person.wav',
            'qwen', 'qwen', 'qwen-model', 'voice-person', 'Person', 'ready',
            NULL, NULL, NULL, '[]', '2026-01-01', '2026-01-01', NULL,
            'missing-clone', 'program', 'Program', 'project-1'
        );
        INSERT INTO voice_clone_synthesis_records VALUES (
            'record-blank', 'profile-blank', '', 'program', 'Program',
            'qwen-model', 'text', NULL, NULL, 'ready', NULL, '2026-01-01', 'project-1'
        );
    ''')
    connection.commit()
    connection.close()

    preview = inspect_voice_clone_nullable_person_migration(database_path)
    assert preview['before']['profile_person_nullable'] is False
    assert preview['before']['record_person_nullable'] is False
    assert preview['before']['profile_invalid_person_count'] == 1
    assert preview['before']['record_invalid_person_count'] == 1

    applied = inspect_voice_clone_nullable_person_migration(database_path, apply=True)
    assert applied['schema_mutated'] is True
    assert applied['schema_ready'] is True
    assert applied['after']['profile_count'] == 2
    assert applied['after']['record_count'] == 1
    assert applied['foreign_key_violation_count'] == 0

    connection = sqlite3.connect(database_path)
    profile_rows = connection.execute(
        'SELECT id, person_id, source_sample_id, source_clone_sample_id '
        'FROM voice_clone_profiles ORDER BY id'
    ).fetchall()
    record_row = connection.execute(
        'SELECT person_id FROM voice_clone_synthesis_records WHERE id = ?',
        ('record-blank',),
    ).fetchone()
    connection.close()
    assert profile_rows == [
        ('profile-blank', None, None, 'clone-1'),
        ('profile-person', 'person-1', 'voiceprint-1', None),
    ]
    assert record_row == (None,)

    repeated = inspect_voice_clone_nullable_person_migration(database_path, apply=True)
    assert repeated['schema_mutated'] is False
    assert repeated['schema_ready'] is True
