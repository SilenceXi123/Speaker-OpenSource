from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import Engine, create_engine

from app.db.schema_bootstrap import (
    SUBTITLE_PIPELINE_RUN_TABLES,
    subtitle_pipeline_run_schema_status,
)
from app.db.session import Base, configure_sqlite_engine, engine
from app.models.subtitle_pipeline_run import SubtitlePipelineRun  # noqa: F401


def inspect_subtitle_pipeline_run_migration(
    target_engine: Engine,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    initial_status = subtitle_pipeline_run_schema_status(target_engine)
    initial_missing = initial_status['missing_tables']
    repairable_indexes = []
    for table_name, issues in initial_status['invalid_tables'].items():
        has_non_index_damage = any(
            value
            for key, value in issues.items()
            if key != 'missing_indexes'
        )
        has_unsafe_index = any(index['unique'] for index in issues['missing_indexes'])
        if has_non_index_damage or has_unsafe_index:
            continue
        indexes_by_name = {
            index.name: index
            for index in Base.metadata.tables[table_name].indexes
        }
        repairable_indexes.extend(
            indexes_by_name[index['name']]
            for index in issues['missing_indexes']
        )

    if apply:
        if initial_missing:
            Base.metadata.create_all(
                bind=target_engine,
                tables=[Base.metadata.tables[name] for name in initial_missing],
            )
        for index in repairable_indexes:
            index.create(bind=target_engine, checkfirst=True)

    final_status = subtitle_pipeline_run_schema_status(target_engine)
    created_tables = (
        sorted(set(initial_missing) - set(final_status['missing_tables']))
        if apply
        else []
    )
    invalid_index_names = {
        index['name']
        for issues in final_status['invalid_tables'].values()
        for key in ('missing_indexes', 'invalid_indexes')
        for index in issues[key]
    }
    created_indexes = (
        sorted(
            index.name
            for index in repairable_indexes
            if index.name not in invalid_index_names
        )
        if apply
        else []
    )
    return {
        'mode': 'apply' if apply else 'dry-run',
        'schema_mutated': bool(created_tables or created_indexes),
        'planned_tables': sorted(SUBTITLE_PIPELINE_RUN_TABLES),
        'created_tables': created_tables,
        'created_indexes': created_indexes,
        'missing_tables': final_status['missing_tables'],
        'invalid_tables': final_status['invalid_tables'],
        'schema_ready': final_status['ready'],
        'error_code': final_status['error_code'],
    }


def _create_inspection_engine(runtime_engine: Engine) -> Engine:
    inspection_engine = create_engine(runtime_engine.url)
    configure_sqlite_engine(inspection_engine, enable_wal=False)
    return inspection_engine


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Preview or apply Speaker 2.0 subtitle pipeline run migration.',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Create the subtitle pipeline run table and declared constraints.',
    )
    args = parser.parse_args()
    inspection_engine = None
    target_engine = engine
    if args.apply:
        configure_sqlite_engine(engine, enable_wal=True)
    else:
        inspection_engine = _create_inspection_engine(engine)
        target_engine = inspection_engine
    try:
        report = inspect_subtitle_pipeline_run_migration(
            target_engine,
            apply=args.apply,
        )
    finally:
        if inspection_engine is not None:
            inspection_engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
