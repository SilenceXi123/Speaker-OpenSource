from __future__ import annotations

import sqlite3

import pytest

from app.db.schema_bootstrap import V2_EXPLICIT_MIGRATION_TABLES
from scripts.migrate_v2_voice_clone_reliability import (
    inspect_voice_clone_reliability_migration,
)
from scripts.migrate_v2_voice_clone_nullable_person_ids import (
    inspect_voice_clone_nullable_person_migration,
)


SNAPSHOT_COLUMN_TYPES = {
    'voice_name_snapshot': 'VARCHAR(255)',
    'provider_snapshot': 'VARCHAR(32)',
    'target_model_snapshot': 'VARCHAR(128)',
}

SNAPSHOT_VALUES = {
    'voice_name_snapshot': '保留的历史音色',
    'provider_snapshot': 'historical-provider',
    'target_model_snapshot': 'historical-model',
}

DELETION_TASK_COLUMNS = {
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
}


def _create_reliability_database(
    database_path,
    *,
    snapshot_columns=(),
    profile_exists=True,
    profile_id_not_null=False,
    profile_fk_on_delete=None,
) -> None:
    snapshot_definitions = ''.join(
        f'            {column_name} {SNAPSHOT_COLUMN_TYPES[column_name]},\n'
        for column_name in snapshot_columns
    )
    on_delete = (
        f' ON DELETE {profile_fk_on_delete}'
        if profile_fk_on_delete
        else ''
    )
    connection = sqlite3.connect(database_path)
    connection.executescript(f'''
        PRAGMA foreign_keys=OFF;
        CREATE TABLE people (id TEXT PRIMARY KEY);
        CREATE TABLE voice_clone_profiles (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            voice_name VARCHAR(255) NOT NULL,
            provider VARCHAR(32) NOT NULL,
            target_model VARCHAR(128) NOT NULL
        );
        CREATE TABLE voice_clone_synthesis_records (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            project_id VARCHAR(36),
            voice_clone_profile_id VARCHAR(36) {'NOT NULL' if profile_id_not_null else ''},
{snapshot_definitions}            person_id VARCHAR(36),
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
                REFERENCES voice_clone_profiles(id){on_delete},
            FOREIGN KEY(person_id) REFERENCES people(id)
        );
        CREATE INDEX ix_voice_clone_synthesis_records_project_id
            ON voice_clone_synthesis_records(project_id);
        CREATE INDEX ix_voice_clone_synthesis_records_voice_clone_profile_id
            ON voice_clone_synthesis_records(voice_clone_profile_id);
        CREATE INDEX ix_voice_clone_synthesis_records_person_id
            ON voice_clone_synthesis_records(person_id);
        CREATE INDEX ix_voice_clone_synthesis_records_program_key
            ON voice_clone_synthesis_records(program_key);
    ''')
    if profile_exists:
        connection.execute(
            'INSERT INTO voice_clone_profiles VALUES (?, ?, ?, ?)',
            ('profile-1', '当前音色', 'current-provider', 'current-model'),
        )
    record_columns = [
        'id',
        'project_id',
        'voice_clone_profile_id',
        *snapshot_columns,
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
    ]
    record_values = [
        'record-1',
        'project-1',
        'profile-1',
        *(SNAPSHOT_VALUES[column_name] for column_name in snapshot_columns),
        None,
        'program-1',
        '节目一',
        'record-model',
        '历史试听',
        '/tmp/history.wav',
        'https://example.com/history.wav',
        'ready',
        None,
        '2026-07-01',
    ]
    placeholders = ', '.join('?' for _ in record_columns)
    connection.execute(
        f'''
        INSERT INTO voice_clone_synthesis_records ({', '.join(record_columns)})
        VALUES ({placeholders})
        ''',
        record_values,
    )
    connection.commit()
    connection.close()


def test_reliability_preview_does_not_create_missing_database(tmp_path) -> None:
    database_path = tmp_path / 'missing.db'

    with pytest.raises(FileNotFoundError):
        inspect_voice_clone_reliability_migration(database_path)

    assert database_path.exists() is False


def test_reliability_migration_detaches_dangling_profile_and_keeps_snapshots(
    tmp_path,
) -> None:
    database_path = tmp_path / 'dangling-profile.db'
    _create_reliability_database(
        database_path,
        snapshot_columns=tuple(SNAPSHOT_COLUMN_TYPES),
        profile_exists=False,
        profile_id_not_null=True,
    )

    applied = inspect_voice_clone_reliability_migration(database_path, apply=True)

    connection = sqlite3.connect(database_path)
    row = connection.execute(
        '''
        SELECT
            voice_clone_profile_id,
            voice_name_snapshot,
            provider_snapshot,
            target_model_snapshot
        FROM voice_clone_synthesis_records
        '''
    ).fetchone()
    connection.close()
    assert applied['schema_ready'] is True
    assert applied['after']['record_count'] == 1
    assert applied['foreign_key_violation_count'] == 0
    assert row == (
        None,
        '保留的历史音色',
        'historical-provider',
        'historical-model',
    )


@pytest.mark.parametrize(
    ('snapshot_columns', 'expected_snapshots'),
    [
        (
            ('voice_name_snapshot',),
            ('保留的历史音色', 'current-provider', 'current-model'),
        ),
        (
            ('provider_snapshot',),
            ('当前音色', 'historical-provider', 'current-model'),
        ),
        (
            ('target_model_snapshot',),
            ('当前音色', 'current-provider', 'historical-model'),
        ),
        (
            ('voice_name_snapshot', 'provider_snapshot'),
            ('保留的历史音色', 'historical-provider', 'current-model'),
        ),
        (
            ('voice_name_snapshot', 'target_model_snapshot'),
            ('保留的历史音色', 'current-provider', 'historical-model'),
        ),
        (
            ('provider_snapshot', 'target_model_snapshot'),
            ('当前音色', 'historical-provider', 'historical-model'),
        ),
    ],
)
def test_reliability_migration_preserves_each_partial_snapshot_subset(
    tmp_path,
    snapshot_columns,
    expected_snapshots,
) -> None:
    database_path = tmp_path / 'partial-snapshots.db'
    _create_reliability_database(
        database_path,
        snapshot_columns=snapshot_columns,
    )

    applied = inspect_voice_clone_reliability_migration(database_path, apply=True)

    connection = sqlite3.connect(database_path)
    snapshots = connection.execute(
        '''
        SELECT
            voice_name_snapshot,
            provider_snapshot,
            target_model_snapshot
        FROM voice_clone_synthesis_records
        '''
    ).fetchone()
    connection.close()
    assert applied['schema_ready'] is True
    assert snapshots == expected_snapshots


def test_reliability_migration_repairs_missing_and_misdirected_indexes(
    tmp_path,
) -> None:
    database_path = tmp_path / 'damaged-indexes.db'
    _create_reliability_database(
        database_path,
        snapshot_columns=tuple(SNAPSHOT_COLUMN_TYPES),
        profile_fk_on_delete='SET NULL',
    )
    connection = sqlite3.connect(database_path)
    connection.execute('DROP INDEX ix_voice_clone_synthesis_records_project_id')
    connection.execute('DROP INDEX ix_voice_clone_synthesis_records_program_key')
    connection.execute(
        'CREATE INDEX ix_voice_clone_synthesis_records_program_key '
        'ON voice_clone_synthesis_records(program_name)'
    )
    connection.commit()
    connection.close()

    preview = inspect_voice_clone_reliability_migration(database_path)
    assert preview['schema_ready'] is False
    assert preview['before']['indexes_ready'] is False

    applied = inspect_voice_clone_reliability_migration(database_path, apply=True)

    connection = sqlite3.connect(database_path)
    actual_indexes = {
        row[1]: tuple(
            index_row[2]
            for index_row in connection.execute(f'PRAGMA index_info({row[1]})')
        )
        for row in connection.execute(
            'PRAGMA index_list(voice_clone_synthesis_records)'
        )
        if row[1].startswith('ix_voice_clone_synthesis_records_')
    }
    connection.close()
    assert applied['schema_mutated'] is True
    assert applied['schema_ready'] is True
    assert applied['after']['indexes_ready'] is True
    assert actual_indexes == {
        'ix_voice_clone_synthesis_records_project_id': ('project_id',),
        'ix_voice_clone_synthesis_records_voice_clone_profile_id': (
            'voice_clone_profile_id',
        ),
        'ix_voice_clone_synthesis_records_person_id': ('person_id',),
        'ix_voice_clone_synthesis_records_program_key': ('program_key',),
    }


def test_reliability_migration_repairs_profile_fk_and_sets_null_on_delete(
    tmp_path,
) -> None:
    database_path = tmp_path / 'wrong-profile-fk.db'
    _create_reliability_database(
        database_path,
        snapshot_columns=tuple(SNAPSHOT_COLUMN_TYPES),
    )

    preview = inspect_voice_clone_reliability_migration(database_path)
    assert preview['schema_ready'] is False
    assert preview['before']['profile_fk_ready'] is False

    applied = inspect_voice_clone_reliability_migration(database_path, apply=True)

    connection = sqlite3.connect(database_path)
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute("DELETE FROM voice_clone_profiles WHERE id = 'profile-1'")
    row = connection.execute(
        '''
        SELECT
            voice_clone_profile_id,
            voice_name_snapshot,
            provider_snapshot,
            target_model_snapshot
        FROM voice_clone_synthesis_records
        WHERE id = 'record-1'
        '''
    ).fetchone()
    connection.commit()
    connection.close()
    assert applied['schema_mutated'] is True
    assert applied['schema_ready'] is True
    assert applied['after']['profile_fk_ready'] is True
    assert row == (
        None,
        '保留的历史音色',
        'historical-provider',
        'historical-model',
    )


def test_reliability_migration_preserves_history_and_is_idempotent(tmp_path) -> None:
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
            source_clone_sample_id VARCHAR(36),
            program_key VARCHAR(255),
            program_name VARCHAR(255),
            project_id VARCHAR(36),
            FOREIGN KEY(person_id) REFERENCES people(id),
            FOREIGN KEY(source_sample_id) REFERENCES voiceprint_samples(id),
            FOREIGN KEY(source_clone_sample_id) REFERENCES voice_clone_samples(id)
        );
        CREATE TABLE voice_clone_synthesis_records (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            project_id VARCHAR(36),
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
            FOREIGN KEY(voice_clone_profile_id) REFERENCES voice_clone_profiles(id),
            FOREIGN KEY(person_id) REFERENCES people(id)
        );
        CREATE INDEX ix_voice_clone_synthesis_records_project_id
            ON voice_clone_synthesis_records(project_id);
        CREATE INDEX ix_voice_clone_synthesis_records_voice_clone_profile_id
            ON voice_clone_synthesis_records(voice_clone_profile_id);
        CREATE INDEX ix_voice_clone_synthesis_records_person_id
            ON voice_clone_synthesis_records(person_id);
        CREATE INDEX ix_voice_clone_synthesis_records_program_key
            ON voice_clone_synthesis_records(program_key);
        INSERT INTO people VALUES ('person-1');
        INSERT INTO voiceprint_samples VALUES ('voiceprint-1');
        INSERT INTO voice_clone_samples VALUES ('clone-1');
        INSERT INTO voice_clone_profiles VALUES (
            'profile-1', 'person-1', 'voiceprint-1', '/tmp/source.wav',
            'qwen', 'qwen', 'qwen3-tts-vc-2026-01-22', 'voice-1',
            '历史音色', 'ready', NULL, NULL, NULL, '[]',
            '2026-07-01', '2026-07-01', NULL, 'clone-1',
            'program-1', '节目一', 'project-1'
        );
        INSERT INTO voice_clone_synthesis_records VALUES (
            'record-1', 'project-1', 'profile-1', 'person-1', 'program-1', '节目一',
            'qwen3-tts-vc-2026-01-22', '历史试听', '/tmp/history.wav',
            'https://example.com/history.wav', 'ready', NULL, '2026-07-01'
        );
    ''')
    connection.commit()
    connection.close()

    nullable_person = inspect_voice_clone_nullable_person_migration(
        database_path,
        apply=True,
    )
    assert nullable_person['schema_mutated'] is True
    assert nullable_person['schema_ready'] is True

    connection = sqlite3.connect(database_path)
    before_schema = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' "
        "AND name = 'voice_clone_synthesis_records'"
    ).fetchone()[0]
    before_rows = connection.execute(
        'SELECT * FROM voice_clone_synthesis_records'
    ).fetchall()
    connection.close()

    preview = inspect_voice_clone_reliability_migration(database_path)
    assert preview['schema_mutated'] is False
    assert preview['before']['migration_required'] is True

    connection = sqlite3.connect(database_path)
    assert connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' "
        "AND name = 'voice_clone_synthesis_records'"
    ).fetchone()[0] == before_schema
    assert connection.execute(
        'SELECT * FROM voice_clone_synthesis_records'
    ).fetchall() == before_rows
    connection.close()

    applied = inspect_voice_clone_reliability_migration(database_path, apply=True)
    assert applied['schema_mutated'] is True
    assert applied['schema_ready'] is True
    assert applied['before']['record_count'] == 1
    assert applied['after']['record_count'] == 1
    assert applied['foreign_key_violation_count'] == 0

    connection = sqlite3.connect(database_path)
    columns = {
        row[1]: row for row in connection.execute(
            'PRAGMA table_info(voice_clone_synthesis_records)'
        )
    }
    migrated_row = connection.execute(
        '''
        SELECT
            voice_clone_profile_id,
            voice_name_snapshot,
            provider_snapshot,
            target_model_snapshot
        FROM voice_clone_synthesis_records
        WHERE id = 'record-1'
        '''
    ).fetchone()
    indexes = {
        row[1] for row in connection.execute(
            'PRAGMA index_list(voice_clone_synthesis_records)'
        )
    }
    connection.close()

    assert columns['voice_clone_profile_id'][3] == 0
    assert migrated_row == (
        'profile-1',
        '历史音色',
        'qwen',
        'qwen3-tts-vc-2026-01-22',
    )
    assert indexes.issuperset({
        'ix_voice_clone_synthesis_records_project_id',
        'ix_voice_clone_synthesis_records_voice_clone_profile_id',
        'ix_voice_clone_synthesis_records_person_id',
        'ix_voice_clone_synthesis_records_program_key',
    })

    repeated = inspect_voice_clone_reliability_migration(database_path, apply=True)
    assert repeated['schema_mutated'] is False
    assert repeated['schema_ready'] is True
    assert repeated['after'] == applied['after']

    nullable_person_repeated = inspect_voice_clone_nullable_person_migration(
        database_path,
        apply=True,
    )
    assert nullable_person_repeated['schema_mutated'] is False
    reliability_repeated = inspect_voice_clone_reliability_migration(
        database_path,
        apply=True,
    )
    assert reliability_repeated['schema_mutated'] is False


def test_reliability_migration_creates_explicit_deletion_task_table_without_asset_fks(
    tmp_path,
) -> None:
    database_path = tmp_path / 'deletion-tasks.db'
    _create_reliability_database(
        database_path,
        snapshot_columns=tuple(SNAPSHOT_COLUMN_TYPES),
        profile_fk_on_delete='SET NULL',
    )

    preview = inspect_voice_clone_reliability_migration(database_path)

    assert 'voice_clone_deletion_tasks' in V2_EXPLICIT_MIGRATION_TABLES
    assert preview['schema_ready'] is False
    assert preview['before']['deletion_task_table_ready'] is False

    applied = inspect_voice_clone_reliability_migration(database_path, apply=True)

    connection = sqlite3.connect(database_path)
    columns = {
        row[1]
        for row in connection.execute(
            'PRAGMA table_info(voice_clone_deletion_tasks)'
        )
    }
    unique_indexes = [
        row
        for row in connection.execute(
            'PRAGMA index_list(voice_clone_deletion_tasks)'
        )
        if row[2]
    ]
    unique_columns = {
        tuple(
            item[2]
            for item in connection.execute(f'PRAGMA index_info({row[1]})')
        )
        for row in unique_indexes
    }
    foreign_keys = list(
        connection.execute('PRAGMA foreign_key_list(voice_clone_deletion_tasks)')
    )
    connection.close()

    assert applied['schema_mutated'] is True
    assert applied['schema_ready'] is True
    assert applied['after']['deletion_task_table_ready'] is True
    assert columns == DELETION_TASK_COLUMNS
    assert ('resource_key',) in unique_columns
    assert foreign_keys == []
    assert inspect_voice_clone_reliability_migration(
        database_path,
        apply=True,
    )['schema_mutated'] is False
