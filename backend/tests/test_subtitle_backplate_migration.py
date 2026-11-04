from __future__ import annotations

import json

from sqlalchemy import create_engine, inspect, text

from scripts.migrate_v2_subtitle_backplates import inspect_subtitle_backplate_migration


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _build_engine():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    with engine.begin() as connection:
        connection.execute(text(
            'CREATE TABLE projects ('
            'id TEXT PRIMARY KEY, name TEXT, name_key TEXT, legacy_program_key TEXT'
            ')'
        ))
        connection.execute(text(
            'CREATE TABLE jobs ('
            'id TEXT PRIMARY KEY, project_id TEXT, program_key TEXT, program_name TEXT, '
            'subtitle_backplate_asset JSON'
            ')'
        ))
        connection.execute(
            text(
                'INSERT INTO projects (id, name, name_key, legacy_program_key) '
                'VALUES (:id, :name, :name_key, :legacy_program_key)'
            ),
            [
                {'id': 'project-a', 'name': '项目 A', 'name_key': '项目 a', 'legacy_program_key': '项目 a'},
                {'id': 'project-b', 'name': '项目 B', 'name_key': '项目 b', 'legacy_program_key': '项目 b'},
                {'id': 'project-c', 'name': '项目 C', 'name_key': '项目 c', 'legacy_program_key': '项目 c'},
                {'id': 'project-d', 'name': '项目 D', 'name_key': '项目 d', 'legacy_program_key': '共享节目'},
                {'id': 'project-e', 'name': '项目 E', 'name_key': '项目 e', 'legacy_program_key': '共享节目'},
            ],
        )
        connection.execute(
            text(
                'INSERT INTO jobs '
                '(id, project_id, program_key, program_name, subtitle_backplate_asset) '
                'VALUES (:id, :project_id, :program_key, :program_name, :asset)'
            ),
            [
                {
                    'id': 'a-legacy',
                    'project_id': 'project-a',
                    'program_key': '项目 a',
                    'program_name': '项目 A',
                    'asset': _json({
                        'file_name': 'a.png',
                        'object_key': 'legacy/a.png',
                        'local_path': '/library/a.png',
                    }),
                },
                {
                    'id': 'b-first',
                    'project_id': 'project-b',
                    'program_key': None,
                    'program_name': None,
                    'asset': _json({
                        'with_speaker': {
                            'file_name': 'b1.png',
                            'object_key': 'legacy/b1.png',
                        },
                    }),
                },
                {
                    'id': 'b-second',
                    'project_id': 'project-b',
                    'program_key': None,
                    'program_name': None,
                    'asset': _json({
                        'schema_version': 1,
                        'roles': {
                            'with_speaker': {
                                'file_name': 'b2.png',
                                'object_key': 'legacy/b2.png',
                            },
                        },
                    }),
                },
                {
                    'id': 'c-partial',
                    'project_id': 'project-c',
                    'program_key': None,
                    'program_name': None,
                    'asset': _json({
                        'without_speaker': {
                            'file_name': 'c.png',
                            'object_key': 'legacy/c.png',
                        },
                    }),
                },
                {
                    'id': 'unowned',
                    'project_id': None,
                    'program_key': '项目 a',
                    'program_name': '项目 A',
                    'asset': _json({
                        'file_name': 'unowned.png',
                        'object_key': 'legacy/unowned.png',
                    }),
                },
                {
                    'id': 'ownership-conflict',
                    'project_id': 'project-a',
                    'program_key': '项目 b',
                    'program_name': '项目 B',
                    'asset': _json({
                        'file_name': 'wrong.png',
                        'object_key': 'legacy/wrong.png',
                    }),
                },
                {
                    'id': 'ownership-ambiguous',
                    'project_id': 'project-d',
                    'program_key': '共享节目',
                    'program_name': '共享节目',
                    'asset': _json({
                        'file_name': 'ambiguous.png',
                        'object_key': 'legacy/ambiguous.png',
                    }),
                },
                {
                    'id': 'invalid-json',
                    'project_id': 'project-a',
                    'program_key': None,
                    'program_name': None,
                    'asset': '{not-json',
                },
            ],
        )
    return engine


def _job_snapshot(engine) -> list[tuple]:
    with engine.connect() as connection:
        rows = connection.execute(text(
            'SELECT id, project_id, program_key, program_name, subtitle_backplate_asset '
            'FROM jobs ORDER BY id'
        )).all()
    return [tuple(row) for row in rows]


def _project_assets(engine) -> dict[str, dict | None]:
    with engine.connect() as connection:
        rows = connection.execute(text(
            'SELECT id, subtitle_backplate_assets FROM projects ORDER BY id'
        )).all()
    return {
        project_id: json.loads(raw_assets) if raw_assets else None
        for project_id, raw_assets in rows
    }


def test_subtitle_backplate_migration_is_dry_run_safe_conservative_and_idempotent() -> None:
    engine = _build_engine()
    original_jobs = _job_snapshot(engine)

    preview = inspect_subtitle_backplate_migration(engine)

    assert preview['mode'] == 'dry-run'
    assert preview['planned_promotion_count'] == 3
    assert preview['conflict_count'] == 1
    assert preview['ownership_classification_counts']['unowned'] == 1
    assert preview['ownership_classification_counts']['legacy_ownership_conflict'] == 1
    assert preview['ownership_classification_counts']['ambiguous_legacy_ownership'] == 1
    assert preview['json_classification_counts']['legacy_single'] == 4
    assert 'subtitle_backplate_assets' not in {
        column['name'] for column in inspect(engine).get_columns('projects')
    }
    assert _job_snapshot(engine) == original_jobs

    applied = inspect_subtitle_backplate_migration(engine, apply=True)

    assert applied['project_asset_column_added'] is True
    assert applied['promoted_role_count'] == 3
    assert applied['updated_project_count'] == 2
    assets = _project_assets(engine)
    assert set(assets['project-a']['roles']) == {'with_speaker', 'without_speaker'}
    assert assets['project-a']['roles']['with_speaker']['asset_id'] == assets['project-a']['roles']['without_speaker']['asset_id']
    assert assets['project-a']['roles']['with_speaker']['library_scope'] == 'project'
    assert set(assets['project-c']['roles']) == {'without_speaker'}
    assert 'with_speaker' not in assets['project-c']['roles']
    assert assets['project-b'] is None
    assert assets['project-d'] is None
    assert _job_snapshot(engine) == original_jobs

    first_assets = _project_assets(engine)
    repeated = inspect_subtitle_backplate_migration(engine, apply=True)

    assert repeated['project_asset_column_added'] is False
    assert repeated['promoted_role_count'] == 0
    assert repeated['already_default_count'] == 3
    assert _project_assets(engine) == first_assets
    assert _job_snapshot(engine) == original_jobs
