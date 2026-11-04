from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.cost_event import CostEvent
from app.models.price_catalog import PriceCatalogEntry
from app.models.provider_call import ProviderCall
from app.services.provider_costs import append_cost_event
from app.services.provider_price_catalog import (
    OFFICIAL_PRICE_EFFECTIVE_FROM,
    apply_official_price_catalog,
    backfill_unpriced_catalog_events,
    official_price_catalog_status,
)


FACT_TABLES = ('provider_calls', 'cost_events', 'price_catalog')


def _session():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(
        bind=engine,
        tables=[Base.metadata.tables[name] for name in FACT_TABLES],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_official_price_catalog_apply_is_complete_and_idempotent() -> None:
    session = _session()
    try:
        preview = official_price_catalog_status(session)
        assert preview['ready'] is False
        assert preview['missing_count'] == 9
        assert preview['conflict_count'] == 0

        first = apply_official_price_catalog(session)
        assert first['inserted_count'] == 9
        assert first['after']['ready'] is True
        assert session.scalar(select(func.count()).select_from(PriceCatalogEntry)) == 9

        second = apply_official_price_catalog(session)
        assert second['inserted_count'] == 0
        assert second['after']['ready'] is True
    finally:
        session.close()


def test_official_catalog_prices_cache_split_without_double_charging_input() -> None:
    session = _session()
    try:
        apply_official_price_catalog(session)
        call = ProviderCall(
            project_id='project-1',
            job_id='job-1',
            run_id='run-1',
            provider='deepseek',
            model='deepseek-v4-flash',
            operation='subtitle_window_plan',
            idempotency_key='deepseek-price-test',
            attempt=1,
            status='completed',
            started_at=datetime(2026, 7, 21, 1, 0, 0),
        )
        session.add(call)
        session.commit()

        for meter, quantity in (
            ('cache_hit_tokens', Decimal('100000')),
            ('cache_miss_tokens', Decimal('100000')),
            ('output_tokens', Decimal('50000')),
        ):
            append_cost_event(
                session,
                call.id,
                meter=meter,
                quantity=quantity,
                unit='token',
                cost_basis='list_price',
            )

        events = list(session.scalars(select(CostEvent).where(
            CostEvent.provider_call_id == call.id,
        )))
        assert {event.meter for event in events} == {
            'cache_hit_tokens', 'cache_miss_tokens', 'output_tokens',
        }
        assert sum(event.amount_microunits or 0 for event in events) == 202_000
    finally:
        session.close()


def test_official_catalog_prices_fun_asr_submit_and_backfills_old_duration() -> None:
    session = _session()
    try:
        call = ProviderCall(
            project_id='rap-project',
            job_id='rap-job',
            provider='dashscope',
            model='fun-asr',
            operation='transcription_submit',
            idempotency_key='fun-asr-price-test',
            attempt=1,
            status='completed',
            started_at=datetime(2026, 7, 21, 1, 0, 0),
        )
        session.add(call)
        session.commit()
        event = CostEvent(
            provider_call_id=call.id,
            project_id=call.project_id,
            job_id=call.job_id,
            meter='audio_seconds',
            cost_basis='list_price',
            measurement_status='measured',
            quantity=Decimal('6223.8'),
            unit='second',
            currency='CNY',
            occurred_at=call.started_at,
        )
        session.add(event)
        session.commit()

        apply_official_price_catalog(session)
        result = backfill_unpriced_catalog_events(session)
        session.refresh(event)

        assert result['updated_count'] == 1
        assert event.price_catalog_entry_id is not None
        assert event.unit_price_microunits == 220
        assert event.amount_microunits == 1_369_236
    finally:
        session.close()


def test_official_price_catalog_rejects_conflicting_effective_version() -> None:
    session = _session()
    try:
        session.add(PriceCatalogEntry(
            provider='deepseek',
            model='deepseek-v4-flash',
            operation='chat_completion',
            meter='input_tokens',
            effective_from=OFFICIAL_PRICE_EFFECTIVE_FROM,
            billing_unit_size=Decimal('1000000'),
            unit_price_microunits=999,
            currency='CNY',
        ))
        session.commit()

        status = official_price_catalog_status(session)
        assert status['conflict_count'] == 1
        with pytest.raises(ValueError, match='conflicts'):
            apply_official_price_catalog(session)
        assert session.scalar(select(func.count()).select_from(PriceCatalogEntry)) == 1
    finally:
        session.close()
