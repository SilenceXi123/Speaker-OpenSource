from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from typing import Any, Optional
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Engine, inspect, text

from app.db.session import engine
from app.services.speaker_memory import build_program_key


ROLES = ('with_speaker', 'without_speaker')
ASSET_KEYS = ('asset_id', 'file_name', 'object_key', 'export_path', 'local_path', 'public_url')
HASH_KEYS = ('sha256', 'content_hash', 'file_hash', 'hash')


def inspect_subtitle_backplate_migration(
    target_engine: Engine,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    inspector = inspect(target_engine)
    table_names = set(inspector.get_table_names())
    if 'projects' not in table_names:
        raise RuntimeError('projects table is missing; run the M1 project migration first')
    if 'jobs' not in table_names:
        raise RuntimeError('jobs table is missing')

    project_columns = {column['name'] for column in inspector.get_columns('projects')}
    job_columns = {column['name'] for column in inspector.get_columns('jobs')}
    if 'id' not in project_columns:
        raise RuntimeError('projects table is missing required column: id')
    missing_job_columns = {'id', 'subtitle_backplate_asset'} - job_columns
    if missing_job_columns:
        missing = ', '.join(sorted(missing_job_columns))
        raise RuntimeError(f'jobs table is missing required columns: {missing}')

    had_project_asset_column = 'subtitle_backplate_assets' in project_columns
    if apply and not had_project_asset_column:
        with target_engine.begin() as connection:
            connection.execute(text(
                'ALTER TABLE projects ADD COLUMN subtitle_backplate_assets JSON'
            ))
        project_columns.add('subtitle_backplate_assets')

    project_select_columns = [
        column
        for column in ('id', 'name', 'name_key', 'legacy_program_key', 'subtitle_backplate_assets')
        if column in project_columns
    ]
    job_select_expressions = ['id', 'subtitle_backplate_asset']
    for column in ('project_id', 'program_key', 'program_name'):
        job_select_expressions.append(column if column in job_columns else f'NULL AS {column}')

    with target_engine.connect() as connection:
        project_rows = connection.execute(text(
            f"SELECT {', '.join(project_select_columns)} FROM projects ORDER BY id"
        )).mappings().all()
        job_rows = connection.execute(text(
            f"SELECT {', '.join(job_select_expressions)} FROM jobs ORDER BY id"
        )).mappings().all()

    known_project_ids = {str(row['id']) for row in project_rows}
    alias_owners: dict[str, set[str]] = defaultdict(set)
    for project in project_rows:
        project_id = str(project['id'])
        for key in ('id', 'name', 'name_key', 'legacy_program_key'):
            alias = build_program_key(project.get(key))
            if alias:
                alias_owners[alias].add(project_id)

    json_classification_ids: dict[str, list[str]] = defaultdict(list)
    ownership_classification_ids: dict[str, list[str]] = defaultdict(list)
    candidates: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    ineligible_assets: list[dict[str, Any]] = []

    for job in job_rows:
        job_id = str(job['id'])
        json_classification, role_assets = _classify_job_config(
            job.get('subtitle_backplate_asset')
        )
        json_classification_ids[json_classification].append(job_id)
        ownership_classification, project_id = _classify_job_ownership(
            job,
            known_project_ids,
            alias_owners,
        )
        ownership_classification_ids[ownership_classification].append(job_id)
        if ownership_classification != 'owned' or not project_id:
            continue

        for role, asset in role_assets.items():
            object_key = str(asset.get('object_key') or '').strip()
            fingerprint = _asset_fingerprint(asset)
            if not object_key or not fingerprint:
                ineligible_assets.append({
                    'job_id': job_id,
                    'project_id': project_id,
                    'role': role,
                    'reason': 'missing_object_key' if not object_key else 'missing_file_identity',
                })
                continue
            candidates[(project_id, role)][fingerprint].append({
                'job_id': job_id,
                'asset': asset,
            })

    existing_roles_by_project: dict[str, dict[str, dict]] = {}
    invalid_existing_project_configs: list[str] = []
    for project in project_rows:
        project_id = str(project['id'])
        raw_config = project.get('subtitle_backplate_assets')
        if _is_empty_json(raw_config):
            existing_roles_by_project[project_id] = {}
            continue
        parsed, valid_json = _decode_json(raw_config)
        roles = _extract_role_assets(parsed) if valid_json else {}
        if not roles:
            invalid_existing_project_configs.append(project_id)
        existing_roles_by_project[project_id] = roles

    planned_promotions: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    already_default_roles: list[dict[str, str]] = []
    for (project_id, role), fingerprint_groups in sorted(candidates.items()):
        fingerprints = sorted(fingerprint_groups)
        if len(fingerprints) != 1:
            conflicts.append({
                'project_id': project_id,
                'role': role,
                'reason': 'multiple_file_identities',
                'fingerprints': fingerprints,
                'job_ids': sorted({
                    item['job_id']
                    for group in fingerprint_groups.values()
                    for item in group
                }),
            })
            continue
        if project_id in invalid_existing_project_configs:
            conflicts.append({
                'project_id': project_id,
                'role': role,
                'reason': 'invalid_existing_project_config',
                'fingerprints': fingerprints,
            })
            continue

        fingerprint = fingerprints[0]
        references = fingerprint_groups[fingerprint]
        existing_asset = existing_roles_by_project[project_id].get(role)
        if existing_asset:
            if _asset_fingerprint(existing_asset) == fingerprint:
                already_default_roles.append({'project_id': project_id, 'role': role})
            else:
                conflicts.append({
                    'project_id': project_id,
                    'role': role,
                    'reason': 'existing_default_conflict',
                    'fingerprints': [
                        str(_asset_fingerprint(existing_asset) or 'missing'),
                        fingerprint,
                    ],
                })
            continue

        selected = min(
            references,
            key=lambda item: (
                item['job_id'],
                json.dumps(item['asset'], ensure_ascii=False, sort_keys=True, default=str),
            ),
        )
        promoted_asset = _build_project_asset(
            selected['asset'],
            project_id=project_id,
            role=role,
            fingerprint=fingerprint,
        )
        planned_promotions.append({
            'project_id': project_id,
            'role': role,
            'fingerprint': fingerprint,
            'job_ids': sorted(item['job_id'] for item in references),
            'asset': promoted_asset,
        })

    updated_project_ids: set[str] = set()
    promoted_role_count = 0
    if apply and planned_promotions:
        updates: dict[str, dict[str, dict]] = {
            project_id: {role: dict(asset) for role, asset in roles.items()}
            for project_id, roles in existing_roles_by_project.items()
        }
        for promotion in planned_promotions:
            updates[promotion['project_id']][promotion['role']] = promotion['asset']
        with target_engine.begin() as connection:
            for project_id in sorted({item['project_id'] for item in planned_promotions}):
                config = {
                    'schema_version': 1,
                    'roles': {
                        role: updates[project_id][role]
                        for role in ROLES
                        if role in updates[project_id]
                    },
                }
                result = connection.execute(
                    text(
                        'UPDATE projects SET subtitle_backplate_assets = :assets '
                        'WHERE id = :project_id'
                    ),
                    {
                        'project_id': project_id,
                        'assets': json.dumps(config, ensure_ascii=False, sort_keys=True),
                    },
                )
                if int(result.rowcount or 0):
                    updated_project_ids.add(project_id)
            promoted_role_count = len(planned_promotions)

    json_counts = Counter({key: len(value) for key, value in json_classification_ids.items()})
    ownership_counts = Counter({
        key: len(value) for key, value in ownership_classification_ids.items()
    })
    return {
        'mode': 'apply' if apply else 'dry-run',
        'schema_mutated': bool(apply and not had_project_asset_column),
        'project_asset_column_added': bool(apply and not had_project_asset_column),
        'subtitle_backplate_assets_column_added': bool(
            apply and not had_project_asset_column
        ),
        'project_count': len(project_rows),
        'job_count': len(job_rows),
        'json_classification_counts': dict(sorted(json_counts.items())),
        'json_classification_ids': {
            key: sorted(value) for key, value in sorted(json_classification_ids.items())
        },
        'ownership_classification_counts': dict(sorted(ownership_counts.items())),
        'ownership_classification_ids': {
            key: sorted(value) for key, value in sorted(ownership_classification_ids.items())
        },
        'candidate_project_role_count': len(candidates),
        'planned_promotion_count': len(planned_promotions),
        'promoted_role_count': promoted_role_count,
        'updated_project_count': len(updated_project_ids),
        'planned_promotions': planned_promotions,
        'conflict_count': len(conflicts),
        'conflicts': conflicts,
        'ineligible_asset_count': len(ineligible_assets),
        'ineligible_assets': ineligible_assets,
        'already_default_count': len(already_default_roles),
        'already_default_roles': already_default_roles,
        'invalid_existing_project_configs': sorted(invalid_existing_project_configs),
        'job_json_preserved': True,
        'legacy_fields_preserved': True,
    }


def _classify_job_config(raw_config: Any) -> tuple[str, dict[str, dict]]:
    if _is_empty_json(raw_config):
        return 'no_asset', {}
    parsed, valid_json = _decode_json(raw_config)
    if not valid_json or not isinstance(parsed, dict):
        return 'invalid_json', {}
    if not parsed:
        return 'empty_object', {}
    if isinstance(parsed.get('roles'), dict):
        classification = (
            'roles_wrapper_v1'
            if parsed.get('schema_version') == 1
            else 'roles_wrapper_legacy'
        )
        roles = _extract_role_assets(parsed)
        return (classification if roles else 'invalid_roles_wrapper'), roles
    if any(role in parsed for role in ROLES):
        roles = _extract_role_assets(parsed)
        return ('direct_role_map' if roles else 'invalid_direct_role_map'), roles
    if _looks_like_asset(parsed):
        return 'legacy_single', {
            role: {**_clean_asset(parsed), 'role': role}
            for role in ROLES
        }
    return 'invalid_shape', {}


def _classify_job_ownership(
    job: Any,
    known_project_ids: set[str],
    alias_owners: dict[str, set[str]],
) -> tuple[str, Optional[str]]:
    project_id = str(job.get('project_id') or '').strip()
    if not project_id:
        return 'unowned', None
    if project_id not in known_project_ids:
        return 'stale_project', None
    matched_project_ids = {
        candidate_project_id
        for key in ('program_key', 'program_name')
        for alias in [build_program_key(job.get(key))]
        if alias
        for candidate_project_id in alias_owners.get(alias, set())
    }
    if len(matched_project_ids) > 1:
        return 'ambiguous_legacy_ownership', None
    if matched_project_ids and matched_project_ids != {project_id}:
        return 'legacy_ownership_conflict', None
    return 'owned', project_id


def _extract_role_assets(config: Any) -> dict[str, dict]:
    if not isinstance(config, dict):
        return {}
    source = config.get('roles') if isinstance(config.get('roles'), dict) else config
    roles: dict[str, dict] = {}
    for role in ROLES:
        asset = source.get(role) if isinstance(source, dict) else None
        if isinstance(asset, dict) and _looks_like_asset(asset):
            roles[role] = {**_clean_asset(asset), 'role': role}
    if roles:
        return roles
    if _looks_like_asset(config):
        return {
            role: {**_clean_asset(config), 'role': role}
            for role in ROLES
        }
    return {}


def _build_project_asset(
    asset: dict,
    *,
    project_id: str,
    role: str,
    fingerprint: str,
) -> dict:
    copied = _clean_asset(asset)
    copied['asset_id'] = str(copied.get('asset_id') or uuid5(
        NAMESPACE_URL,
        f'speaker-v2:subtitle-backplate:{project_id}:{fingerprint}',
    ))
    copied['object_key'] = str(copied['object_key']).strip()
    copied['role'] = role
    copied['library_scope'] = 'project'
    return copied


def _asset_fingerprint(asset: Any) -> Optional[str]:
    if not isinstance(asset, dict):
        return None
    for key in HASH_KEYS:
        value = str(asset.get(key) or '').strip().casefold()
        if value:
            return f'hash:{value}'
    object_key = str(asset.get('object_key') or '').strip()
    if object_key:
        return f'object_key:{object_key}'
    return None


def _clean_asset(asset: dict) -> dict:
    return {
        key: value
        for key, value in dict(asset).items()
        if value is not None and key not in {'schema_version', 'roles'}
    }


def _looks_like_asset(value: Any) -> bool:
    return isinstance(value, dict) and any(value.get(key) for key in ASSET_KEYS)


def _decode_json(value: Any) -> tuple[Any, bool]:
    if isinstance(value, (dict, list)):
        return value, True
    if isinstance(value, str):
        try:
            return json.loads(value), True
        except json.JSONDecodeError:
            return None, False
    return value, value is None


def _is_empty_json(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


# Keep both spellings available for tests and one-off operator tooling.
inspect_subtitle_backplates_migration = inspect_subtitle_backplate_migration
inspect_subtitle_backplate_asset_migration = inspect_subtitle_backplate_migration


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Preview or apply Speaker 2.0 project subtitle backplate migration.'
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Add projects.subtitle_backplate_assets and promote only unambiguous defaults.',
    )
    args = parser.parse_args()
    report = inspect_subtitle_backplate_migration(engine, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
