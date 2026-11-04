from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import Base
from app.services.provider_cost_readiness import build_provider_cost_readiness
from app.services.provider_price_catalog import apply_official_price_catalog


FACT_TABLES = ('provider_calls', 'cost_events', 'price_catalog')


def _session(*, migrated: bool = True):
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    if migrated:
        Base.metadata.create_all(
            bind=engine,
            tables=[Base.metadata.tables[name] for name in FACT_TABLES],
        )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_readiness_distinguishes_schema_catalog_and_switch(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'provider_cost_facts_enabled', False)
    missing_schema = _session(migrated=False)
    try:
        assert build_provider_cost_readiness(missing_schema).status == 'schema_not_ready'
    finally:
        missing_schema.close()

    session = _session()
    try:
        catalog_missing = build_provider_cost_readiness(session)
        assert catalog_missing.status == 'catalog_not_ready'
        assert catalog_missing.expected_price_count == 9
        assert catalog_missing.matching_price_count == 0

        apply_official_price_catalog(session)
        disabled = build_provider_cost_readiness(session)
        assert disabled.status == 'disabled'
        assert disabled.catalog_ready is True
        assert disabled.ready_for_recording is False

        monkeypatch.setattr(settings, 'provider_cost_facts_enabled', True)
        ready = build_provider_cost_readiness(session)
        assert ready.status == 'ready'
        assert ready.ready_for_recording is True
        assert ready.actual_bill_connected is False
        assert ready.configured_models['subtitle_model']
    finally:
        session.close()
