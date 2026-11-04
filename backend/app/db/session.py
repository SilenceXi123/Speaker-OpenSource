from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


@contextmanager
def _sqlite_autocommit_cursor(dbapi_connection):
    uses_autocommit = hasattr(dbapi_connection, 'autocommit')
    attribute = 'autocommit' if uses_autocommit else 'isolation_level'
    previous_value = getattr(dbapi_connection, attribute)
    setattr(dbapi_connection, attribute, True if uses_autocommit else None)
    cursor = None
    try:
        cursor = dbapi_connection.cursor()
        yield cursor
    finally:
        if cursor is not None:
            cursor.close()
        setattr(dbapi_connection, attribute, previous_value)


def _set_sqlite_connection_pragmas(dbapi_connection) -> None:
    with _sqlite_autocommit_cursor(dbapi_connection) as cursor:
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.execute('PRAGMA busy_timeout=5000')


def _set_sqlite_wal(dbapi_connection) -> None:
    with _sqlite_autocommit_cursor(dbapi_connection) as cursor:
        cursor.execute('PRAGMA journal_mode=WAL')
        row = cursor.fetchone()
        returned_mode = str(row[0]).lower() if row else 'unknown'
        if returned_mode != 'wal':
            cursor.execute('PRAGMA database_list')
            database_path = next(
                (
                    path
                    for _, name, path in cursor.fetchall()
                    if name == 'main'
                ),
                '<unknown>',
            )
            raise RuntimeError(
                f'SQLite WAL required for {database_path!r}; '
                f'journal_mode returned {returned_mode!r}'
            )


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    _set_sqlite_connection_pragmas(dbapi_connection)


def _configure_sqlite_wal(dbapi_connection, _connection_record) -> None:
    _set_sqlite_wal(dbapi_connection)


def configure_sqlite_engine(
    target_engine: Engine,
    *,
    enable_wal: bool = True,
) -> None:
    if target_engine.dialect.name != 'sqlite':
        return

    database = target_engine.url.database
    is_memory = (
        database in (None, '', ':memory:')
        or target_engine.url.query.get('mode') == 'memory'
        or (
            database == 'file::memory:'
            and str(target_engine.url.query.get('uri')).lower() == 'true'
        )
    )
    if not event.contains(target_engine, 'connect', _configure_sqlite_connection):
        event.listen(target_engine, 'connect', _configure_sqlite_connection)
    if (
        enable_wal
        and not is_memory
        and not event.contains(target_engine, 'connect', _configure_sqlite_wal)
    ):
        event.listen(target_engine, 'connect', _configure_sqlite_wal)


def _ensure_voice_clone_review_delete_policy(connection) -> None:
    rows = connection.execute(text(
        'PRAGMA foreign_key_list(voice_clone_identity_reviews)'
    )).fetchall()
    policy = next((str(row[6]).upper() for row in rows if row[3] == 'sample_id'), None)
    if policy in {None, 'SET NULL'}:
        return
    connection.execute(text(
        'DROP TABLE IF EXISTS voice_clone_identity_reviews_replacement'
    ))
    connection.execute(text(
        'CREATE TABLE voice_clone_identity_reviews_replacement ('
        'id VARCHAR(36) NOT NULL PRIMARY KEY, '
        'candidate_identity_id VARCHAR(36) NOT NULL, '
        'sample_id VARCHAR(36), submitted_name VARCHAR(255) NOT NULL, '
        'match_score FLOAT NOT NULL, status VARCHAR(32) NOT NULL, '
        'resolved_by VARCHAR(255), resolution_reason TEXT, '
        'created_at DATETIME NOT NULL, resolved_at DATETIME, '
        'FOREIGN KEY(candidate_identity_id) REFERENCES voice_clone_identities(id), '
        'FOREIGN KEY(sample_id) REFERENCES voice_clone_samples(id) ON DELETE SET NULL)'
    ))
    columns = (
        'id, candidate_identity_id, sample_id, submitted_name, match_score, '
        'status, resolved_by, resolution_reason, created_at, resolved_at'
    )
    connection.execute(text(
        f'INSERT INTO voice_clone_identity_reviews_replacement ({columns}) '
        f'SELECT {columns} FROM voice_clone_identity_reviews'
    ))
    connection.execute(text('DROP TABLE voice_clone_identity_reviews'))
    connection.execute(text(
        'ALTER TABLE voice_clone_identity_reviews_replacement '
        'RENAME TO voice_clone_identity_reviews'
    ))


engine = create_engine(settings.sqlite_url, connect_args={'check_same_thread': False})
configure_sqlite_engine(engine, enable_wal=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def ensure_schema() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if 'jobs' not in table_names:
        return

    existing_columns = {column['name'] for column in inspector.get_columns('jobs')}
    alter_statements = []
    if 'kind' not in existing_columns:
        alter_statements.append("ALTER TABLE jobs ADD COLUMN kind TEXT DEFAULT 'transcription'")
    if 'source_local_path' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN source_local_path TEXT')
    if 'source_export_path' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN source_export_path TEXT')
    if 'source_media_kind' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN source_media_kind TEXT')
    if 'export_frame_rate' not in existing_columns:
        alter_statements.append("ALTER TABLE jobs ADD COLUMN export_frame_rate TEXT DEFAULT '25'")
    if 'subtitle_strategy' not in existing_columns:
        alter_statements.append("ALTER TABLE jobs ADD COLUMN subtitle_strategy TEXT DEFAULT 'enhanced'")
    if 'project_id' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN project_id TEXT')
    if 'program_key' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN program_key TEXT')
    if 'program_name' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN program_name TEXT')
    if 'speaker_mappings' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN speaker_mappings JSON')
    if 'video_source_url' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN video_source_url TEXT')
    if 'video_source_local_path' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN video_source_local_path TEXT')
    if 'video_source_export_path' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN video_source_export_path TEXT')
    if 'render_output_url' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN render_output_url TEXT')
    if 'render_status' not in existing_columns:
        alter_statements.append("ALTER TABLE jobs ADD COLUMN render_status TEXT DEFAULT 'idle'")
    if 'render_progress' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN render_progress INTEGER DEFAULT 0')
    if 'render_message' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN render_message TEXT')
    if 'processing_progress' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN processing_progress INTEGER DEFAULT 0')
    if 'processing_message' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN processing_message TEXT')
    if 'processing_metrics' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN processing_metrics JSON')
    if 'pipeline_artifacts' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN pipeline_artifacts JSON')
    if 'script_context' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN script_context JSON')
    if 'script_source_name' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN script_source_name TEXT')
    if 'remove_standalone_fillers' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN remove_standalone_fillers BOOLEAN DEFAULT 0')
    if 'provider_task_meta' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN provider_task_meta JSON')
    if 'source_duration_seconds' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN source_duration_seconds FLOAT')
    if 'lower_third_assets' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN lower_third_assets JSON')
    if 'matched_lower_thirds' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN matched_lower_thirds JSON')
    if 'subtitle_backplate_asset' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN subtitle_backplate_asset JSON')
    if 'subtitle_enhanced' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN subtitle_enhanced BOOLEAN DEFAULT 0')
    if 'subtitle_enhancement_error' not in existing_columns:
        alter_statements.append('ALTER TABLE jobs ADD COLUMN subtitle_enhancement_error TEXT')

    if 'projects' in table_names:
        project_columns = {column['name'] for column in inspector.get_columns('projects')}
        if 'knowledge_domain' not in project_columns:
            alter_statements.append(
                "ALTER TABLE projects ADD COLUMN knowledge_domain TEXT DEFAULT 'broadcast'"
            )
        if 'subtitle_backplate_assets' not in project_columns:
            alter_statements.append(
                'ALTER TABLE projects ADD COLUMN subtitle_backplate_assets JSON'
            )

    if 'voiceprint_samples' in table_names:
        sample_columns = {column['name'] for column in inspector.get_columns('voiceprint_samples')}
        if 'usage_types' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN usage_types JSON')
        if 'clone_readiness_status' not in sample_columns:
            alter_statements.append("ALTER TABLE voiceprint_samples ADD COLUMN clone_readiness_status TEXT DEFAULT 'unknown'")
        if 'clone_readiness_message' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN clone_readiness_message TEXT')
        if 'audio_public_url' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN audio_public_url TEXT')
        if 'clone_audio_local_path' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN clone_audio_local_path TEXT')
        if 'clone_audio_public_url' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN clone_audio_public_url TEXT')
        if 'clone_sample_rate_hz' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN clone_sample_rate_hz INTEGER')
        if 'clone_channel_count' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN clone_channel_count INTEGER')
        if 'waveform_ready' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN waveform_ready BOOLEAN DEFAULT 0')
        if 'source_type' not in sample_columns:
            alter_statements.append("ALTER TABLE voiceprint_samples ADD COLUMN source_type TEXT DEFAULT 'manual_upload'")
        if 'review_status' not in sample_columns:
            alter_statements.append("ALTER TABLE voiceprint_samples ADD COLUMN review_status TEXT DEFAULT 'confirmed'")
        if 'source_job_id' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN source_job_id TEXT')
        if 'source_speaker_key' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN source_speaker_key TEXT')
        if 'source_start_ms' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN source_start_ms INTEGER')
        if 'source_end_ms' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN source_end_ms INTEGER')
        if 'confidence_score' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN confidence_score FLOAT')
        if 'duplicate_of_sample_id' not in sample_columns:
            alter_statements.append('ALTER TABLE voiceprint_samples ADD COLUMN duplicate_of_sample_id TEXT')

    if 'speaker_memories' in table_names:
        speaker_memory_columns = {column['name'] for column in inspector.get_columns('speaker_memories')}
        if 'project_id' not in speaker_memory_columns:
            alter_statements.append('ALTER TABLE speaker_memories ADD COLUMN project_id TEXT')
        if 'lower_third_assets' not in speaker_memory_columns:
            alter_statements.append('ALTER TABLE speaker_memories ADD COLUMN lower_third_assets JSON')

    if 'voice_clone_profiles' in table_names:
        clone_profile_columns = {column['name'] for column in inspector.get_columns('voice_clone_profiles')}
        if 'source_clone_sample_id' not in clone_profile_columns:
            alter_statements.append('ALTER TABLE voice_clone_profiles ADD COLUMN source_clone_sample_id TEXT')
        if 'program_key' not in clone_profile_columns:
            alter_statements.append('ALTER TABLE voice_clone_profiles ADD COLUMN program_key TEXT')
        if 'program_name' not in clone_profile_columns:
            alter_statements.append('ALTER TABLE voice_clone_profiles ADD COLUMN program_name TEXT')
        if 'identity_id' not in clone_profile_columns:
            alter_statements.append('ALTER TABLE voice_clone_profiles ADD COLUMN identity_id TEXT')

    if 'voice_clone_samples' in table_names:
        clone_sample_columns = {column['name'] for column in inspector.get_columns('voice_clone_samples')}
        if 'program_key' not in clone_sample_columns:
            alter_statements.append('ALTER TABLE voice_clone_samples ADD COLUMN program_key TEXT')
        if 'program_name' not in clone_sample_columns:
            alter_statements.append('ALTER TABLE voice_clone_samples ADD COLUMN program_name TEXT')
        if 'content_sha256' not in clone_sample_columns:
            alter_statements.append('ALTER TABLE voice_clone_samples ADD COLUMN content_sha256 TEXT')
        if 'file_size_bytes' not in clone_sample_columns:
            alter_statements.append('ALTER TABLE voice_clone_samples ADD COLUMN file_size_bytes BIGINT')
        if 'last_used_at' not in clone_sample_columns:
            alter_statements.append('ALTER TABLE voice_clone_samples ADD COLUMN last_used_at DATETIME')
        if 'name_key' not in clone_sample_columns:
            alter_statements.append('ALTER TABLE voice_clone_samples ADD COLUMN name_key TEXT')
        if 'identity_id' not in clone_sample_columns:
            alter_statements.append('ALTER TABLE voice_clone_samples ADD COLUMN identity_id TEXT')
        if 'embedding' not in clone_sample_columns:
            alter_statements.append('ALTER TABLE voice_clone_samples ADD COLUMN embedding JSON')
        if 'embedding_status' not in clone_sample_columns:
            alter_statements.append('ALTER TABLE voice_clone_samples ADD COLUMN embedding_status TEXT')
        if 'embedding_provider_name' not in clone_sample_columns:
            alter_statements.append('ALTER TABLE voice_clone_samples ADD COLUMN embedding_provider_name TEXT')
        if 'embedding_model_id' not in clone_sample_columns:
            alter_statements.append('ALTER TABLE voice_clone_samples ADD COLUMN embedding_model_id TEXT')
        if 'embedding_message' not in clone_sample_columns:
            alter_statements.append('ALTER TABLE voice_clone_samples ADD COLUMN embedding_message TEXT')

    if 'canonical_passages' in table_names:
        passage_columns = {
            column['name'] for column in inspector.get_columns('canonical_passages')
        }
        if 'import_batch_id' not in passage_columns:
            alter_statements.append(
                'ALTER TABLE canonical_passages ADD COLUMN import_batch_id TEXT'
            )
        if 'reviewed_by' not in passage_columns:
            alter_statements.append(
                'ALTER TABLE canonical_passages ADD COLUMN reviewed_by TEXT'
            )
        if 'reviewed_at' not in passage_columns:
            alter_statements.append(
                'ALTER TABLE canonical_passages ADD COLUMN reviewed_at DATETIME'
            )

    if 'canonical_passage_variants' in table_names:
        variant_columns = {
            column['name']
            for column in inspector.get_columns('canonical_passage_variants')
        }
        if 'import_batch_id' not in variant_columns:
            alter_statements.append(
                'ALTER TABLE canonical_passage_variants ADD COLUMN import_batch_id TEXT'
            )
        if 'reviewed_by' not in variant_columns:
            alter_statements.append(
                'ALTER TABLE canonical_passage_variants ADD COLUMN reviewed_by TEXT'
            )
        if 'reviewed_at' not in variant_columns:
            alter_statements.append(
                'ALTER TABLE canonical_passage_variants ADD COLUMN reviewed_at DATETIME'
            )

    with engine.begin() as connection:
        for statement in alter_statements:
            connection.execute(text(statement))
        if 'voice_clone_identity_reviews' in table_names:
            _ensure_voice_clone_review_delete_policy(connection)
        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_jobs_project_id ON jobs(project_id)'))
        if 'canonical_passages' in table_names:
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_canonical_passages_import_batch_id '
                'ON canonical_passages(import_batch_id)'
            ))
        if 'canonical_passage_variants' in table_names:
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_canonical_passage_variants_import_batch_id '
                'ON canonical_passage_variants(import_batch_id)'
            ))
        if 'voice_clone_samples' in table_names:
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_voice_clone_samples_name_key '
                'ON voice_clone_samples(name_key)'
            ))
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_voice_clone_samples_identity_id '
                'ON voice_clone_samples(identity_id)'
            ))
            connection.execute(text('DROP TRIGGER IF EXISTS trg_voice_clone_samples_name_limit'))
            connection.execute(text('DROP TRIGGER IF EXISTS trg_voice_clone_samples_identity_limit'))
            connection.execute(text('DROP TRIGGER IF EXISTS trg_voice_clone_samples_identity_limit_update'))
            connection.execute(text(
                "CREATE TRIGGER trg_voice_clone_samples_identity_limit "
                "BEFORE INSERT ON voice_clone_samples "
                "WHEN NEW.identity_id IS NOT NULL AND ("
                "SELECT COUNT(*) FROM voice_clone_samples "
                "WHERE identity_id = NEW.identity_id "
                "AND julianday(created_at) > julianday('now', '-72 hours')"
                ") >= 3 BEGIN "
                "SELECT RAISE(ABORT, 'voice_clone_sample_identity_limit'); "
                "END"
            ))
            connection.execute(text(
                "CREATE TRIGGER trg_voice_clone_samples_identity_limit_update "
                "BEFORE UPDATE OF identity_id ON voice_clone_samples "
                "WHEN NEW.identity_id IS NOT NULL AND ("
                "SELECT COUNT(*) FROM voice_clone_samples "
                "WHERE identity_id = NEW.identity_id AND id <> NEW.id "
                "AND julianday(created_at) > julianday('now', '-72 hours')"
                ") >= 3 BEGIN "
                "SELECT RAISE(ABORT, 'voice_clone_sample_identity_limit'); "
                "END"
            ))
        if 'voice_clone_profiles' in table_names:
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_voice_clone_profiles_identity_id '
                'ON voice_clone_profiles(identity_id)'
            ))
        if 'voice_clone_identity_reviews' in table_names:
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_voice_clone_identity_reviews_sample_id '
                'ON voice_clone_identity_reviews(sample_id)'
            ))
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_voice_clone_identity_reviews_candidate_identity_id '
                'ON voice_clone_identity_reviews(candidate_identity_id)'
            ))
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_voice_clone_identity_reviews_status '
                'ON voice_clone_identity_reviews(status)'
            ))
        if 'projects' in table_names:
            connection.execute(text(
                "CREATE TRIGGER IF NOT EXISTS trg_jobs_require_active_project "
                "BEFORE INSERT ON jobs "
                "WHEN NEW.project_id IS NOT NULL AND NOT EXISTS ("
                "SELECT 1 FROM projects WHERE id = NEW.project_id AND status = 'active'"
                ") BEGIN SELECT RAISE(ABORT, 'project_not_active'); END"
            ))
