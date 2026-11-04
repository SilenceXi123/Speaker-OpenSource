from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import Float, Integer, Numeric, create_engine, func, inspect
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.schema_bootstrap import V2_EXPLICIT_MIGRATION_TABLES, create_legacy_tables
from app.db.session import Base
from app.models.cost_event import CostEvent
from app.models.job_event import JobEvent  # noqa: F401
from app.models.price_catalog import PriceCatalogEntry
from app.models.provider_call import ProviderCall


EXPLICIT_TABLES = {'provider_calls', 'cost_events', 'price_catalog'}
BACKEND_DIR = Path(__file__).resolve().parents[1]


def _memory_engine():
    return create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})


def test_provider_cost_tables_are_registered_and_can_be_created_explicitly() -> None:
    assert EXPLICIT_TABLES <= set(Base.metadata.tables)
    assert EXPLICIT_TABLES <= V2_EXPLICIT_MIGRATION_TABLES

    engine = _memory_engine()
    Base.metadata.create_all(
        bind=engine,
        tables=[Base.metadata.tables[name] for name in EXPLICIT_TABLES],
    )

    assert set(inspect(engine).get_table_names()) == EXPLICIT_TABLES


def test_legacy_bootstrap_excludes_explicit_migration_tables() -> None:
    engine = _memory_engine()

    create_legacy_tables(engine)

    table_names = set(inspect(engine).get_table_names())
    assert 'job_events' in table_names
    assert table_names.isdisjoint(EXPLICIT_TABLES)


def test_main_bootstrap_excludes_explicit_migration_tables(tmp_path: Path) -> None:
    database_path = tmp_path / 'speaker.db'
    env = os.environ.copy()
    env.update({
        'PYTHONPATH': str(BACKEND_DIR),
        'PYTHONPYCACHEPREFIX': str(tmp_path / 'pycache'),
        'PYTHONDONTWRITEBYTECODE': '1',
        'SQLITE_PATH': str(database_path),
    })

    subprocess.run(
        [sys.executable, '-c', 'import main'],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    engine = create_engine(f'sqlite:///{database_path}')
    table_names = set(inspect(engine).get_table_names())
    assert 'job_events' in table_names
    assert table_names.isdisjoint(EXPLICIT_TABLES)


def test_provider_call_token_usage_columns_are_nullable_integers_and_persist() -> None:
    token_values = {
        'prompt_tokens': 100,
        'completion_tokens': 25,
        'total_tokens': 125,
        'prompt_cache_hit_tokens': 40,
        'prompt_cache_miss_tokens': 60,
    }
    columns = ProviderCall.__table__.c

    for name in token_values:
        assert isinstance(columns[name].type, Integer)
        assert columns[name].nullable

    engine = _memory_engine()
    ProviderCall.__table__.create(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        provider_call = ProviderCall(
            project_id='project-1',
            provider='deepseek',
            model='deepseek-chat',
            operation='chat_completion',
            idempotency_key='token-call',
            attempt=1,
            usage_payload={'reasoning_tokens': 7},
            **token_values,
        )
        session.add(provider_call)
        session.commit()
        session.expire_all()

        persisted = session.get(ProviderCall, provider_call.id)
        assert persisted is not None
        assert {name: getattr(persisted, name) for name in token_values} == token_values
        assert persisted.usage_payload == {'reasoning_tokens': 7}
    finally:
        session.close()


def test_provider_call_none_usage_payload_is_stored_as_sql_null() -> None:
    engine = _memory_engine()
    ProviderCall.__table__.create(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add_all([
            ProviderCall(
                id='explicit-none',
                project_id='project-1',
                provider='deepseek',
                model='deepseek-chat',
                operation='chat_completion',
                idempotency_key='explicit-none',
                attempt=1,
                usage_payload=None,
            ),
            ProviderCall(
                id='omitted',
                project_id='project-1',
                provider='deepseek',
                model='deepseek-chat',
                operation='chat_completion',
                idempotency_key='omitted',
                attempt=1,
            ),
        ])
        session.commit()

        matches = (
            session.query(ProviderCall.id)
            .filter(ProviderCall.usage_payload.is_(None))
            .all()
        )
        assert {row[0] for row in matches} == {'explicit-none', 'omitted'}
    finally:
        session.close()


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (
            Decimal('999999999999.999999'),
            Decimal('999999999999.999999'),
        ),
        (
            Decimal('-999999999999.999999'),
            Decimal('-999999999999.999999'),
        ),
        (Decimal('0.1'), Decimal('0.100000')),
        (Decimal('1.25'), Decimal('1.250000')),
        (Decimal('1.2345674'), Decimal('1.234567')),
        (Decimal('1.2345675'), Decimal('1.234568')),
    ],
    ids=[
        'positive-domain-boundary',
        'negative-domain-boundary',
        'tenth',
        'fractional',
        'quantize-down',
        'quantize-up',
    ],
)
def test_decimal_quantities_round_trip_exactly_on_sqlite(
    value: Decimal,
    expected: Decimal,
) -> None:
    engine = _memory_engine()
    CostEvent.__table__.create(bind=engine)
    PriceCatalogEntry.__table__.create(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add_all([
            CostEvent(
                id='cost-event',
                provider_call_id='call-1',
                project_id='project-1',
                meter='input_tokens',
                cost_basis='provider_usage',
                quantity=value,
                unit='token',
                billing_unit_size=value,
                currency='CNY',
            ),
            PriceCatalogEntry(
                id='catalog-entry',
                provider='deepseek',
                model='deepseek-chat',
                operation='chat_completion',
                meter='input_tokens',
                effective_from=datetime(2026, 7, 1),
                billing_unit_size=value,
                unit_price_microunits=2_000_000,
                currency='CNY',
            ),
        ])
        session.commit()
        session.expire_all()

        cost_event = session.get(CostEvent, 'cost-event')
        catalog_entry = session.get(PriceCatalogEntry, 'catalog-entry')
        assert cost_event is not None
        assert catalog_entry is not None
        assert cost_event.quantity == expected
        assert cost_event.billing_unit_size == expected
        assert catalog_entry.billing_unit_size == expected

        expected_storage = int(expected * Decimal('1000000'))
        with engine.connect() as connection:
            cost_storage = connection.exec_driver_sql(
                'SELECT typeof(quantity), quantity, '
                'typeof(billing_unit_size), billing_unit_size '
                "FROM cost_events WHERE id = 'cost-event'"
            ).one()
            catalog_storage = connection.exec_driver_sql(
                'SELECT typeof(billing_unit_size), billing_unit_size '
                "FROM price_catalog WHERE id = 'catalog-entry'"
            ).one()
        assert cost_storage == ('integer', expected_storage, 'integer', expected_storage)
        assert catalog_storage == ('integer', expected_storage)
    finally:
        session.close()


@pytest.mark.parametrize(
    'value',
    [
        Decimal('1000000000000.000000'),
        Decimal('-1000000000000.000000'),
    ],
    ids=['above-domain-maximum', 'below-domain-minimum'],
)
def test_decimal_quantities_reject_values_outside_numeric_domain(value: Decimal) -> None:
    decimal_type = CostEvent.__table__.c.quantity.type

    with pytest.raises(ValueError, match=r'Numeric\(18, 6\)'):
        decimal_type.process_bind_param(value, sqlite.dialect())


@pytest.mark.parametrize(
    'value',
    [Decimal('NaN'), Decimal('Infinity'), Decimal('-Infinity')],
    ids=['nan', 'positive-infinity', 'negative-infinity'],
)
def test_decimal_quantities_reject_non_finite_values(value: Decimal) -> None:
    decimal_type = CostEvent.__table__.c.quantity.type

    with pytest.raises(ValueError, match='decimal value must be finite'):
        decimal_type.process_bind_param(value, sqlite.dialect())


def test_cost_event_quantity_queries_keep_numeric_semantics_on_sqlite() -> None:
    engine = _memory_engine()
    CostEvent.__table__.create(bind=engine)
    session = sessionmaker(bind=engine)()
    values = [
        Decimal('-10'),
        Decimal('-2'),
        Decimal('0'),
        Decimal('2'),
        Decimal('10'),
    ]
    try:
        session.add_all([
            CostEvent(
                provider_call_id=f'call-{index}',
                project_id='project-1',
                meter='input_tokens',
                cost_basis='provider_usage',
                quantity=value,
                unit='token',
                currency='CNY',
            )
            for index, value in enumerate(values)
        ])
        session.commit()

        ordered = session.query(CostEvent.quantity).order_by(CostEvent.quantity).all()
        greater_than_two = (
            session.query(CostEvent.quantity)
            .filter(CostEvent.quantity > Decimal('2'))
            .all()
        )
        total = session.query(func.sum(CostEvent.quantity)).scalar()

        assert [row[0] for row in ordered] == values
        assert [row[0] for row in greater_than_two] == [Decimal('10')]
        assert isinstance(total, Decimal)
        assert total == Decimal('0.000000')
    finally:
        session.close()


@pytest.mark.parametrize('measurement_status', ['measured', 'estimated', 'unmetered'])
def test_cost_event_accepts_valid_measurement_status(measurement_status: str) -> None:
    engine = _memory_engine()
    CostEvent.__table__.create(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        event = CostEvent(
            provider_call_id=f'call-{measurement_status}',
            project_id='project-1',
            meter='input_tokens',
            cost_basis='provider_usage',
            measurement_status=measurement_status,
            quantity=Decimal('1.000000'),
            unit='token',
            currency='CNY',
        )
        session.add(event)
        session.commit()
        assert event.measurement_status == measurement_status
    finally:
        session.close()


def test_cost_event_rejects_invalid_measurement_status() -> None:
    engine = _memory_engine()
    CostEvent.__table__.create(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add(CostEvent(
            provider_call_id='call-invalid-status',
            project_id='project-1',
            meter='input_tokens',
            cost_basis='provider_usage',
            measurement_status='derived',
            quantity=Decimal('1.000000'),
            unit='token',
            currency='CNY',
        ))
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.close()


@pytest.mark.parametrize(
    ('model', 'first_kwargs', 'duplicate_kwargs'),
    [
        (
            ProviderCall,
            {
                'project_id': 'project-1',
                'provider': 'dashscope',
                'model': 'fun-asr',
                'operation': 'transcribe',
                'idempotency_key': 'call-key',
                'attempt': 1,
                'status': 'pending',
            },
            {
                'project_id': 'project-1',
                'provider': 'dashscope',
                'model': 'fun-asr',
                'operation': 'transcribe',
                'idempotency_key': 'call-key',
                'attempt': 1,
                'status': 'pending',
            },
        ),
        (
            CostEvent,
            {
                'provider_call_id': 'call-1',
                'project_id': 'project-1',
                'meter': 'audio_seconds',
                'cost_basis': 'provider_usage',
                'measurement_status': 'measured',
                'quantity': Decimal('1.250000'),
                'unit': 'second',
                'unit_price_microunits': 20,
                'amount_microunits': 25,
                'currency': 'CNY',
            },
            {
                'provider_call_id': 'call-1',
                'project_id': 'project-1',
                'meter': 'audio_seconds',
                'cost_basis': 'provider_usage',
                'measurement_status': 'measured',
                'quantity': Decimal('2.000000'),
                'unit': 'second',
                'unit_price_microunits': 20,
                'amount_microunits': 40,
                'currency': 'CNY',
            },
        ),
        (
            PriceCatalogEntry,
            {
                'provider': 'deepseek',
                'model': 'deepseek-chat',
                'operation': 'chat_completion',
                'meter': 'input_tokens',
                'effective_from': datetime(2026, 7, 1),
                'billing_unit_size': Decimal('1000000.000000'),
                'unit_price_microunits': 2_000_000,
                'currency': 'CNY',
            },
            {
                'provider': 'deepseek',
                'model': 'deepseek-chat',
                'operation': 'chat_completion',
                'meter': 'input_tokens',
                'effective_from': datetime(2026, 7, 1),
                'billing_unit_size': Decimal('1000000.000000'),
                'unit_price_microunits': 3_000_000,
                'currency': 'CNY',
            },
        ),
    ],
    ids=['provider-call', 'cost-event', 'price-catalog'],
)
def test_fact_identity_constraints_reject_duplicates(
    model,
    first_kwargs,
    duplicate_kwargs,
) -> None:
    engine = _memory_engine()
    model.__table__.create(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add(model(**first_kwargs))
        session.commit()
        session.add(model(**duplicate_kwargs))
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.close()


def test_context_fields_have_individual_indexes() -> None:
    expected = {(name,) for name in ('project_id', 'job_id', 'run_id', 'window_id')}

    for model in (ProviderCall, CostEvent):
        indexed = {
            tuple(column.name for column in index.columns)
            for index in model.__table__.indexes
        }
        assert expected <= indexed


def test_money_quantities_and_fencing_fields_use_stable_types() -> None:
    cost_columns = CostEvent.__table__.c
    catalog_columns = PriceCatalogEntry.__table__.c
    call_columns = ProviderCall.__table__.c

    for column in (
        cost_columns.quantity,
        cost_columns.billing_unit_size,
        catalog_columns.billing_unit_size,
    ):
        dialect_type = column.type.dialect_impl(postgresql.dialect())
        numeric_type = getattr(dialect_type, 'impl', dialect_type)
        assert isinstance(numeric_type, Numeric)
        assert (numeric_type.precision, numeric_type.scale) == (18, 6)

        sqlite_type = column.type.dialect_impl(sqlite.dialect())
        sqlite_storage_type = getattr(sqlite_type, 'impl', sqlite_type)
        assert isinstance(sqlite_storage_type, Integer)

    for column in (
        cost_columns.unit_price_microunits,
        cost_columns.amount_microunits,
        catalog_columns.unit_price_microunits,
    ):
        assert isinstance(column.type, Integer)
        assert not isinstance(column.type, Float)

    assert {'claim_owner', 'claim_expires_at', 'lock_version'} <= set(call_columns.keys())
    assert isinstance(call_columns.lock_version.type, Integer)
    assert not call_columns.lock_version.nullable
    assert not call_columns.idempotency_key.nullable
    assert not call_columns.attempt.nullable
