from __future__ import annotations

from sqlalchemy import create_engine, inspect

from app.db.schema_bootstrap import create_legacy_tables
from app.db.session import Base
from app.models.subtitle_pipeline_run import SubtitlePipelineRun  # noqa: F401
from scripts.migrate_v2_subtitle_pipeline_runs import inspect_subtitle_pipeline_run_migration


def test_pipeline_run_table_requires_explicit_migration() -> None:
    engine = create_engine('sqlite://')

    create_legacy_tables(engine)

    assert 'subtitle_pipeline_runs' not in inspect(engine).get_table_names()


def test_pipeline_run_migration_supports_dry_run_apply_and_repeat() -> None:
    engine = create_engine('sqlite://')
    Base.metadata.create_all(
        engine,
        tables=[table for table in Base.metadata.tables.values() if table.name != 'subtitle_pipeline_runs'],
    )

    dry_run = inspect_subtitle_pipeline_run_migration(engine, apply=False)
    applied = inspect_subtitle_pipeline_run_migration(engine, apply=True)
    repeated = inspect_subtitle_pipeline_run_migration(engine, apply=True)

    assert dry_run['schema_mutated'] is False
    assert dry_run['schema_ready'] is False
    assert dry_run['missing_tables'] == ['subtitle_pipeline_runs']
    assert applied['schema_mutated'] is True
    assert applied['schema_ready'] is True
    assert applied['created_tables'] == ['subtitle_pipeline_runs']
    assert repeated['schema_mutated'] is False
    assert repeated['schema_ready'] is True
