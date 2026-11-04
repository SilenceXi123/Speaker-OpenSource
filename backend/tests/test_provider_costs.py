from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.cost_event import CostEvent
from app.models.price_catalog import PriceCatalogEntry
from app.models.provider_call import ProviderCall
from app.schemas.provider_cost import ProviderCallContext
from app.services.provider_costs import (
    ProviderCostError,
    ProviderCallFencingError,
    append_cost_event,
    build_provider_cost_summary,
    claim_provider_call_attempt,
    complete_provider_call,
    fail_provider_call,
    reserve_provider_call,
)


FACT_TABLES = ('provider_calls', 'cost_events', 'price_catalog')


def _session_factory(database_url: str = 'sqlite:///:memory:'):
    engine = create_engine(database_url, connect_args={'check_same_thread': False})
    Base.metadata.create_all(
        bind=engine,
        tables=[Base.metadata.tables[name] for name in FACT_TABLES],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _context(**overrides) -> ProviderCallContext:
    values = {
        'project_id': 'project-1',
        'job_id': 'job-1',
        'pipeline_scope': 'subtitle_legacy',
    }
    values.update(overrides)
    return ProviderCallContext(**values)


def _reserve(session, *, key: str = 'call-key', attempt: int = 1) -> ProviderCall:
    return reserve_provider_call(
        session,
        _context(),
        provider='deepseek',
        model='deepseek-v4-flash',
        operation='chat_completion',
        idempotency_key=key,
        attempt=attempt,
    )


def test_provider_call_context_enforces_pipeline_identity() -> None:
    with pytest.raises(ValidationError, match='project_id'):
        ProviderCallContext(project_id='', pipeline_scope='project_asset')

    asset = ProviderCallContext(project_id='project-1', pipeline_scope='project_asset')
    assert asset.job_id is None
    assert asset.run_id is None

    with pytest.raises(ValidationError, match='job_id'):
        ProviderCallContext(project_id='project-1', pipeline_scope='subtitle_legacy')

    with pytest.raises(ValidationError, match='run_id'):
        ProviderCallContext(
            project_id='project-1',
            job_id='job-1',
            pipeline_scope='subtitle_v2',
        )

    v2 = ProviderCallContext(
        project_id='project-1',
        job_id='job-1',
        run_id='run-1',
        pipeline_scope='subtitle_v2',
    )
    assert v2.run_id == 'run-1'


def test_reserve_provider_call_is_idempotent_for_key_and_attempt() -> None:
    factory = _session_factory()
    session = factory()
    try:
        first = _reserve(session)
        second = _reserve(session)

        assert second.id == first.id
        assert session.scalar(select(ProviderCall).where(ProviderCall.idempotency_key == 'call-key'))
        assert len(session.scalars(select(ProviderCall)).all()) == 1
    finally:
        session.close()


def test_claim_uses_expiry_and_lock_version_as_fencing_token(tmp_path) -> None:
    factory = _session_factory(f'sqlite:///{tmp_path / "claims.db"}')
    creator = factory()
    call = _reserve(creator)
    creator.close()

    now = datetime(2026, 7, 13, 8, 0, 0)
    first_session = factory()
    second_session = factory()
    try:
        first_claim = claim_provider_call_attempt(
            first_session,
            call.id,
            owner='worker-a',
            now=now,
            ttl_seconds=30,
        )
        competing_claim = claim_provider_call_attempt(
            second_session,
            call.id,
            owner='worker-b',
            now=now,
            ttl_seconds=30,
        )

        assert first_claim is not None
        assert first_claim.owner == 'worker-a'
        assert first_claim.lock_version == 1
        assert competing_claim is None

        takeover = claim_provider_call_attempt(
            second_session,
            call.id,
            owner='worker-b',
            now=now + timedelta(seconds=31),
            ttl_seconds=30,
        )
        assert takeover is not None
        assert takeover.lock_version == 2

        with pytest.raises(ProviderCallFencingError):
            complete_provider_call(
                first_session,
                call.id,
                fencing_token=first_claim,
                request_id='stale-request',
                usage={'prompt_tokens': 1},
                completed_at=now + timedelta(seconds=32),
            )
    finally:
        first_session.close()
        second_session.close()


def test_complete_accepts_only_current_claim_and_persists_safe_usage() -> None:
    factory = _session_factory()
    session = factory()
    try:
        call = _reserve(session)
        started_at = datetime(2026, 7, 13, 9, 0, 0)
        claim = claim_provider_call_attempt(
            session,
            call.id,
            owner='worker-a',
            now=started_at,
            ttl_seconds=60,
        )
        assert claim is not None

        completed = complete_provider_call(
            session,
            call.id,
            fencing_token=claim,
            request_id='request-123',
            usage={
                'prompt_tokens': 100,
                'completion_tokens': 25,
                'total_tokens': 125,
                'prompt_cache_hit_tokens': 20,
                'prompt_cache_miss_tokens': 80,
                'reasoning_tokens': 7,
                'prompt': 'must not be stored',
                'token': 'secret-token',
            },
            completed_at=started_at + timedelta(seconds=2),
        )

        assert completed.status == 'completed'
        assert completed.provider_request_id == 'request-123'
        assert completed.duration_ms == 2_000
        assert completed.prompt_tokens == 100
        assert completed.completion_tokens == 25
        assert completed.usage_payload == {
            'prompt_tokens': 100,
            'completion_tokens': 25,
            'total_tokens': 125,
            'prompt_cache_hit_tokens': 20,
            'prompt_cache_miss_tokens': 80,
            'reasoning_tokens': 7,
        }
    finally:
        session.close()


def test_complete_handles_sqlite_naive_start_and_aware_completion() -> None:
    factory = _session_factory()
    session = factory()
    try:
        call = _reserve(session)
        started_at = datetime(2026, 7, 13, 9, 0, 0, tzinfo=timezone.utc)
        claim = claim_provider_call_attempt(
            session,
            call.id,
            owner='worker-a',
            now=started_at,
            ttl_seconds=60,
        )
        assert claim is not None

        completed = complete_provider_call(
            session,
            call.id,
            fencing_token=claim,
            request_id='request-aware',
            usage={},
            completed_at=started_at + timedelta(seconds=2),
        )

        assert completed.status == 'completed'
        assert completed.duration_ms == 2_000
    finally:
        session.close()


def test_fail_redacts_secrets_and_rejects_stale_completion() -> None:
    factory = _session_factory()
    session = factory()
    try:
        call = _reserve(session)
        now = datetime(2026, 7, 13, 10, 0, 0)
        claim = claim_provider_call_attempt(
            session,
            call.id,
            owner='worker-a',
            now=now,
            ttl_seconds=30,
        )
        assert claim is not None

        failed = fail_provider_call(
            session,
            call.id,
            fencing_token=claim,
            error_code='PROVIDER_500',
            error_summary=(
                'Authorization: Bearer bearer-secret '
                'token=secret-token "prompt":"private-json-prompt" '
                '"access_token":"private-access-token" password=private-password '
                'api_key=sk-secret12345678 '
                'url=https://oss.example.com/a.wav?Signature=secret-signature '
                + ('provider response body ' * 80)
            ),
            completed_at=now + timedelta(seconds=1),
        )

        assert failed.status == 'failed'
        assert failed.error_code == 'PROVIDER_500'
        assert len(failed.error_summary or '') <= 512
        assert 'bearer-secret' not in (failed.error_summary or '')
        assert 'secret-token' not in (failed.error_summary or '')
        assert 'private-json-prompt' not in (failed.error_summary or '')
        assert 'private-access-token' not in (failed.error_summary or '')
        assert 'private-password' not in (failed.error_summary or '')
        assert 'sk-secret12345678' not in (failed.error_summary or '')
        assert 'secret-signature' not in (failed.error_summary or '')

        with pytest.raises(ProviderCallFencingError):
            complete_provider_call(
                session,
                call.id,
                fencing_token=claim,
                request_id='too-late',
                usage={},
                completed_at=now + timedelta(seconds=2),
            )
    finally:
        session.close()


def test_cost_event_is_idempotent_and_snapshots_effective_price() -> None:
    factory = _session_factory()
    session = factory()
    try:
        call = _reserve(session)
        call.started_at = datetime(2026, 7, 13, 11, 0, 0)
        session.add_all([
            PriceCatalogEntry(
                provider='deepseek',
                model='deepseek-v4-flash',
                operation='chat_completion',
                meter='input_tokens',
                effective_from=datetime(2026, 1, 1),
                effective_to=datetime(2026, 7, 1),
                billing_unit_size=Decimal('1000'),
                unit_price_microunits=1_000_000,
                currency='CNY',
            ),
            PriceCatalogEntry(
                provider='deepseek',
                model='deepseek-v4-flash',
                operation='chat_completion',
                meter='input_tokens',
                effective_from=datetime(2026, 7, 1),
                billing_unit_size=Decimal('1000'),
                unit_price_microunits=2_000_000,
                currency='CNY',
            ),
        ])
        session.commit()

        first = append_cost_event(
            session,
            call.id,
            meter='input_tokens',
            quantity=Decimal('1250'),
            unit='token',
            cost_basis='list_price',
        )
        second = append_cost_event(
            session,
            call.id,
            meter='input_tokens',
            quantity=Decimal('1250'),
            unit='token',
            cost_basis='list_price',
        )

        assert second.id == first.id
        assert first.billing_unit_size == Decimal('1000.000000')
        assert first.unit_price_microunits == 2_000_000
        assert first.amount_microunits == 2_500_000
        assert first.currency == 'CNY'
        assert len(session.scalars(select(CostEvent)).all()) == 1
    finally:
        session.close()


def test_cost_event_replay_rejects_conflicting_measurement() -> None:
    factory = _session_factory()
    session = factory()
    try:
        call = _reserve(session)
        append_cost_event(
            session,
            call.id,
            meter='input_tokens',
            quantity=Decimal('10'),
            unit='token',
            cost_basis='provider_usage',
        )

        with pytest.raises(ProviderCostError, match='conflicts'):
            append_cost_event(
                session,
                call.id,
                meter='input_tokens',
                quantity=Decimal('11'),
                unit='token',
                cost_basis='provider_usage',
            )
    finally:
        session.close()


def test_summary_keeps_currency_totals_separate() -> None:
    factory = _session_factory()
    session = factory()
    try:
        first = _reserve(session, key='cny-call')
        second = reserve_provider_call(
            session,
            _context(),
            provider='dashscope',
            model='fun-asr',
            operation='file_transcription',
            idempotency_key='usd-call',
            attempt=1,
        )
        session.add_all([
            CostEvent(
                provider_call_id=first.id,
                project_id='project-1',
                job_id='job-1',
                meter='input_tokens',
                cost_basis='list_price',
                measurement_status='measured',
                quantity=Decimal('1'),
                unit='token',
                amount_microunits=100,
                currency='CNY',
                occurred_at=datetime(2026, 7, 13, 12, 0, 0),
            ),
            CostEvent(
                provider_call_id=second.id,
                project_id='project-1',
                job_id='job-1',
                meter='audio_seconds',
                cost_basis='list_price',
                measurement_status='measured',
                quantity=Decimal('1'),
                unit='second',
                amount_microunits=200,
                currency='USD',
                occurred_at=datetime(2026, 7, 13, 12, 0, 0),
            ),
        ])
        session.commit()

        summary = build_provider_cost_summary(session, project_id='project-1')

        assert summary.total_amount_microunits is None
        assert summary.amounts_by_currency == {'CNY': 100, 'USD': 200}
        assert [item.total_amount_microunits for item in summary.breakdowns] == [200, 100]
    finally:
        session.close()


def test_missing_usage_is_explicitly_unmetered_and_summary_is_filterable() -> None:
    factory = _session_factory()
    session = factory()
    try:
        call = _reserve(session)
        event = append_cost_event(
            session,
            call.id,
            meter='output_tokens',
            quantity=None,
            unit='token',
            cost_basis='provider_usage',
        )

        assert event.measurement_status == 'unmetered'
        assert event.quantity == Decimal('0.000000')
        assert event.amount_microunits is None

        summary = build_provider_cost_summary(session, project_id='project-1')
        assert summary.call_count == 1
        assert summary.event_count == 1
        assert summary.unmetered_event_count == 1
        assert summary.total_amount_microunits == 0

        empty = build_provider_cost_summary(session, project_id='other-project')
        assert empty.call_count == 0
        assert empty.event_count == 0
    finally:
        session.close()
