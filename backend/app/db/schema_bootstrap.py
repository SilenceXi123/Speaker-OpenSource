from sqlalchemy import CheckConstraint, UniqueConstraint, inspect
from sqlalchemy.engine import Connection, Engine

from app.db.session import Base


PROVIDER_COST_FACT_TABLES = frozenset({
    'provider_calls',
    'cost_events',
    'price_catalog',
})
SUBTITLE_PIPELINE_RUN_TABLES = frozenset({'subtitle_pipeline_runs'})
VOICE_CLONE_RELIABILITY_TABLES = frozenset({'voice_clone_deletion_tasks'})
V2_EXPLICIT_MIGRATION_TABLES = frozenset(
    PROVIDER_COST_FACT_TABLES
    | SUBTITLE_PIPELINE_RUN_TABLES
    | VOICE_CLONE_RELIABILITY_TABLES
)


def _provider_cost_metadata_tables():
    from app.models.cost_event import CostEvent
    from app.models.price_catalog import PriceCatalogEntry
    from app.models.provider_call import ProviderCall

    _ = CostEvent, PriceCatalogEntry, ProviderCall
    return {
        name: Base.metadata.tables[name]
        for name in PROVIDER_COST_FACT_TABLES
    }


def _subtitle_pipeline_run_metadata_tables():
    from app.models.subtitle_pipeline_run import SubtitlePipelineRun

    _ = SubtitlePipelineRun
    return {
        name: Base.metadata.tables[name]
        for name in SUBTITLE_PIPELINE_RUN_TABLES
    }


def _normalize_check_sql(sqltext: object) -> str:
    return ' '.join(str(sqltext).split()).lower()


def _column_contract(column_type, nullable: bool, dialect) -> dict[str, object]:
    dialect_type = column_type.dialect_impl(dialect)
    return {
        'type': dialect.type_compiler.process(dialect_type).upper(),
        'nullable': bool(nullable),
    }


def _provider_cost_table_issues(inspector, table) -> dict[str, object]:
    actual_columns = {
        column['name']: column
        for column in inspector.get_columns(table.name)
    }
    invalid_columns = []
    for column in sorted(table.columns, key=lambda item: item.name):
        actual_column = actual_columns.get(column.name)
        if actual_column is None:
            continue
        expected = _column_contract(
            column.type,
            column.nullable,
            inspector.bind.dialect,
        )
        actual = _column_contract(
            actual_column['type'],
            actual_column['nullable'],
            inspector.bind.dialect,
        )
        mismatches = []
        if expected['type'] != actual['type']:
            mismatches.append('type')
        if expected['nullable'] != actual['nullable']:
            mismatches.append('nullable')
        if mismatches:
            invalid_columns.append({
                'name': column.name,
                'expected': expected,
                'actual': actual,
                'mismatches': mismatches,
            })

    expected_primary_key = [column.name for column in table.primary_key.columns]
    actual_primary_key = list(
        inspector.get_pk_constraint(table.name).get('constrained_columns') or []
    )
    primary_key = (
        None
        if actual_primary_key == expected_primary_key
        else {'expected': expected_primary_key, 'actual': actual_primary_key}
    )

    actual_unique_constraints = {
        constraint.get('name'): list(constraint.get('column_names') or [])
        for constraint in inspector.get_unique_constraints(table.name)
    }
    missing_unique_constraints = []
    invalid_unique_constraints = []
    expected_unique_constraints = sorted(
        (
            constraint
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        ),
        key=lambda constraint: constraint.name or '',
    )
    for constraint in expected_unique_constraints:
        expected_columns = [column.name for column in constraint.columns]
        if constraint.name not in actual_unique_constraints:
            missing_unique_constraints.append({
                'name': constraint.name,
                'columns': expected_columns,
            })
        elif actual_unique_constraints[constraint.name] != expected_columns:
            invalid_unique_constraints.append({
                'name': constraint.name,
                'expected_columns': expected_columns,
                'actual_columns': actual_unique_constraints[constraint.name],
            })

    actual_checks = {
        constraint.get('name'): _normalize_check_sql(constraint.get('sqltext'))
        for constraint in inspector.get_check_constraints(table.name)
    }
    missing_check_constraints = []
    invalid_check_constraints = []
    expected_checks = sorted(
        (
            constraint
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint) and constraint.name
        ),
        key=lambda constraint: constraint.name,
    )
    for constraint in expected_checks:
        expected_sql = _normalize_check_sql(constraint.sqltext)
        if constraint.name not in actual_checks:
            missing_check_constraints.append({
                'name': constraint.name,
                'sqltext': expected_sql,
            })
        elif actual_checks[constraint.name] != expected_sql:
            invalid_check_constraints.append({
                'name': constraint.name,
                'expected_sqltext': expected_sql,
                'actual_sqltext': actual_checks[constraint.name],
            })

    actual_indexes = {
        index.get('name'): index
        for index in inspector.get_indexes(table.name)
    }
    missing_indexes = []
    invalid_indexes = []
    for index in sorted(table.indexes, key=lambda item: item.name or ''):
        expected_columns = [column.name for column in index.columns]
        actual_index = actual_indexes.get(index.name)
        expected_unique = bool(index.unique)
        if actual_index is None:
            missing_indexes.append({
                'name': index.name,
                'columns': expected_columns,
                'unique': expected_unique,
            })
            continue

        actual_index_columns = list(actual_index.get('column_names') or [])
        actual_unique = bool(actual_index.get('unique'))
        if (
            actual_index_columns != expected_columns
            or actual_unique != expected_unique
        ):
            invalid_indexes.append({
                'name': index.name,
                'expected_columns': expected_columns,
                'actual_columns': actual_index_columns,
                'expected_unique': expected_unique,
                'actual_unique': actual_unique,
            })

    return {
        'missing_columns': sorted(
            set(table.columns.keys()) - set(actual_columns)
        ),
        'invalid_columns': invalid_columns,
        'primary_key': primary_key,
        'missing_unique_constraints': missing_unique_constraints,
        'invalid_unique_constraints': invalid_unique_constraints,
        'missing_check_constraints': missing_check_constraints,
        'invalid_check_constraints': invalid_check_constraints,
        'missing_indexes': missing_indexes,
        'invalid_indexes': invalid_indexes,
    }


def provider_cost_schema_status(bind: Engine | Connection) -> dict[str, object]:
    metadata_tables = _provider_cost_metadata_tables()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    missing_tables = sorted(PROVIDER_COST_FACT_TABLES - existing_tables)
    invalid_tables = {}
    for table_name in sorted(PROVIDER_COST_FACT_TABLES & existing_tables):
        issues = _provider_cost_table_issues(
            inspector,
            metadata_tables[table_name],
        )
        if any(value for value in issues.values()):
            invalid_tables[table_name] = issues

    ready = not missing_tables and not invalid_tables
    return {
        'ready': ready,
        'error_code': None if ready else 'migration_required',
        'missing_tables': missing_tables,
        'invalid_tables': invalid_tables,
    }


def subtitle_pipeline_run_schema_status(bind: Engine | Connection) -> dict[str, object]:
    metadata_tables = _subtitle_pipeline_run_metadata_tables()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    missing_tables = sorted(SUBTITLE_PIPELINE_RUN_TABLES - existing_tables)
    invalid_tables = {}
    for table_name in sorted(SUBTITLE_PIPELINE_RUN_TABLES & existing_tables):
        issues = _provider_cost_table_issues(
            inspector,
            metadata_tables[table_name],
        )
        if any(value for value in issues.values()):
            invalid_tables[table_name] = issues

    ready = not missing_tables and not invalid_tables
    return {
        'ready': ready,
        'error_code': None if ready else 'migration_required',
        'missing_tables': missing_tables,
        'invalid_tables': invalid_tables,
    }


def create_legacy_tables(bind: Engine | Connection) -> None:
    Base.metadata.create_all(
        bind=bind,
        tables=[
            table
            for table in Base.metadata.tables.values()
            if table.name not in V2_EXPLICIT_MIGRATION_TABLES
        ],
    )
