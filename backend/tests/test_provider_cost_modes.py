from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, settings
from app.db.session import Base
from app.models.cost_event import CostEvent
from app.models.provider_call import ProviderCall
from app.schemas.provider_cost import (
    ProviderCallContext,
    ProviderCallObservation,
    ProviderMeterObservation,
)
from app.services import provider_costs
from app.services.provider_costs import (
    ProviderCallClaimUnavailableError,
    ProviderCallExecutionError,
    ProviderCostSchemaNotReadyError,
    execute_v2_provider_call,
    record_legacy_provider_observation,
)


FACT_TABLES = ('provider_calls', 'cost_events', 'price_catalog')


def _session_factory(tmp_path, *, migrated: bool = True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f'sqlite:///{tmp_path / "provider-facts.db"}',
        connect_args={'check_same_thread': False},
    )
    if migrated:
        Base.metadata.create_all(
            bind=engine,
            tables=[Base.metadata.tables[name] for name in FACT_TABLES],
        )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _legacy_context() -> ProviderCallContext:
    return ProviderCallContext(
        project_id='project-1',
        job_id='job-1',
        pipeline_scope='subtitle_legacy',
    )


def _v2_context() -> ProviderCallContext:
    return ProviderCallContext(
        project_id='project-1',
        job_id='job-1',
        run_id='run-1',
        pipeline_scope='subtitle_v2',
    )


def _observation(*, key: str = 'legacy-call') -> ProviderCallObservation:
    return ProviderCallObservation(
        idempotency_key=key,
        attempt=1,
        status='completed',
        request_id='request-1',
        started_at=datetime(2026, 7, 13, 12, 0, 0),
        completed_at=datetime(2026, 7, 13, 12, 0, 1),
        usage={'prompt_tokens': 10},
        meters=[
            ProviderMeterObservation(
                meter='input_tokens',
                quantity=Decimal('10'),
                unit='token',
            )
        ],
    )


def test_provider_cost_facts_setting_defaults_off() -> None:
    isolated = Settings(_env_file=None)
    assert isolated.provider_cost_facts_enabled is False


def test_legacy_observation_disabled_does_not_touch_database(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'provider_cost_facts_enabled', False)

    def forbidden_session():
        raise AssertionError('disabled observation must not open a session')

    monkeypatch.setattr(provider_costs, 'SessionLocal', forbidden_session)

    assert record_legacy_provider_observation(
        _legacy_context(),
        provider='deepseek',
        model='deepseek-v4-flash',
        operation='chat_completion',
        observed_call=_observation(),
    ) is None


def test_legacy_observation_is_fail_open_when_migration_is_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, 'provider_cost_facts_enabled', True)
    monkeypatch.setattr(provider_costs, 'SessionLocal', _session_factory(tmp_path, migrated=False))

    assert record_legacy_provider_observation(
        _legacy_context(),
        provider='deepseek',
        model='deepseek-v4-flash',
        operation='chat_completion',
        observed_call=_observation(),
    ) is None


def test_legacy_observation_is_fail_open_when_writer_raises(
    monkeypatch,
    tmp_path,
    caplog,
) -> None:
    monkeypatch.setattr(settings, 'provider_cost_facts_enabled', True)
    monkeypatch.setattr(provider_costs, 'SessionLocal', _session_factory(tmp_path))

    def broken_reservation(*args, **kwargs):
        raise RuntimeError('writer unavailable token=do-not-log-this')

    monkeypatch.setattr(provider_costs, 'reserve_provider_call', broken_reservation)

    assert record_legacy_provider_observation(
        _legacy_context(),
        provider='deepseek',
        model='deepseek-v4-flash',
        operation='chat_completion',
        observed_call=_observation(),
    ) is None
    assert 'do-not-log-this' not in caplog.text


def test_legacy_observation_records_fact_without_claiming_network(monkeypatch, tmp_path) -> None:
    factory = _session_factory(tmp_path)
    monkeypatch.setattr(settings, 'provider_cost_facts_enabled', True)
    monkeypatch.setattr(provider_costs, 'SessionLocal', factory)

    recorded = record_legacy_provider_observation(
        _legacy_context(),
        provider='deepseek',
        model='deepseek-v4-flash',
        operation='chat_completion',
        observed_call=_observation(),
    )

    assert recorded is not None
    assert recorded.status == 'completed'
    assert recorded.claim_owner is None
    session = factory()
    try:
        assert len(session.scalars(select(ProviderCall)).all()) == 1
        events = list(session.scalars(select(CostEvent)).all())
        assert len(events) == 1
        assert events[0].cost_basis == 'list_price'
    finally:
        session.close()


def test_distinct_legacy_failures_advance_attempt_without_overwriting(monkeypatch, tmp_path) -> None:
    factory = _session_factory(tmp_path)
    monkeypatch.setattr(settings, 'provider_cost_facts_enabled', True)
    monkeypatch.setattr(provider_costs, 'SessionLocal', factory)
    first = _observation(key='retry-call').model_copy(update={
        'attempt': 3,
        'status': 'failed',
        'request_id': None,
        'started_at': datetime(2026, 7, 13, 12, 0, 0),
        'completed_at': datetime(2026, 7, 13, 12, 0, 1),
        'usage': None,
        'meters': [],
        'error_code': 'TimeoutError',
        'error_summary': 'first failure',
    })
    second = first.model_copy(update={
        'started_at': datetime(2026, 7, 13, 12, 0, 2),
        'completed_at': datetime(2026, 7, 13, 12, 0, 3),
        'error_summary': 'second failure',
    })

    record_legacy_provider_observation(
        _legacy_context(),
        provider='dashscope',
        model='fun-asr',
        operation='transcription_submit',
        observed_call=first,
    )
    record_legacy_provider_observation(
        _legacy_context(),
        provider='dashscope',
        model='fun-asr',
        operation='transcription_submit',
        observed_call=second,
    )

    session = factory()
    try:
        calls = list(session.scalars(select(ProviderCall).order_by(ProviderCall.attempt)))
        assert [call.attempt for call in calls] == [3, 4]
        assert [call.error_summary for call in calls] == ['first failure', 'second failure']
    finally:
        session.close()


def test_v2_fails_closed_before_network_when_migration_or_run_token_is_missing(
    monkeypatch,
    tmp_path,
) -> None:
    network_calls = 0

    def network_call():
        nonlocal network_calls
        network_calls += 1
        return _observation(key='v2-call')

    monkeypatch.setattr(provider_costs, 'SessionLocal', _session_factory(tmp_path, migrated=False))
    with pytest.raises(ProviderCostSchemaNotReadyError):
        execute_v2_provider_call(
            _v2_context(),
            run_lease_fencing_token='run-lease-1',
            provider='deepseek',
            model='deepseek-v4-flash',
            operation='chat_completion',
            idempotency_key='v2-call',
            attempt=1,
            network_call=network_call,
        )
    assert network_calls == 0

    monkeypatch.setattr(provider_costs, 'SessionLocal', _session_factory(tmp_path / 'ready'))
    with pytest.raises(ValueError, match='run lease fencing token'):
        execute_v2_provider_call(
            _v2_context(),
            run_lease_fencing_token='',
            provider='deepseek',
            model='deepseek-v4-flash',
            operation='chat_completion',
            idempotency_key='v2-call',
            attempt=1,
            network_call=network_call,
        )
    assert network_calls == 0


def test_v2_claim_allows_network_exactly_once_and_records_usage(monkeypatch, tmp_path) -> None:
    factory = _session_factory(tmp_path)
    monkeypatch.setattr(provider_costs, 'SessionLocal', factory)
    network_calls = 0

    def network_call():
        nonlocal network_calls
        network_calls += 1
        observation = _observation(key='v2-call')
        return observation.model_copy(update={'value': {'answer': 'ok'}})

    result = execute_v2_provider_call(
        _v2_context(),
        run_lease_fencing_token='run-lease-1',
        provider='deepseek',
        model='deepseek-v4-flash',
        operation='chat_completion',
        idempotency_key='v2-call',
        attempt=1,
        network_call=network_call,
    )
    assert result == {'answer': 'ok'}

    with pytest.raises(ProviderCallClaimUnavailableError):
        execute_v2_provider_call(
            _v2_context(),
            run_lease_fencing_token='run-lease-2',
            provider='deepseek',
            model='deepseek-v4-flash',
            operation='chat_completion',
            idempotency_key='v2-call',
            attempt=1,
            network_call=network_call,
        )

    assert network_calls == 1
    session = factory()
    try:
        call = session.scalar(select(ProviderCall))
        assert call is not None
        assert call.status == 'completed'
        assert call.provider_request_id == 'request-1'
        assert call.prompt_tokens == 10
        assert len(session.scalars(select(CostEvent)).all()) == 1
    finally:
        session.close()


def test_v2_completion_and_cost_events_commit_atomically(monkeypatch, tmp_path) -> None:
    factory = _session_factory(tmp_path)
    monkeypatch.setattr(provider_costs, 'SessionLocal', factory)
    original_append = provider_costs.append_cost_event
    append_count = 0

    def fail_second_cost_event(*args, **kwargs):
        nonlocal append_count
        append_count += 1
        if append_count == 2:
            raise RuntimeError('cost writer failed')
        return original_append(*args, **kwargs)

    monkeypatch.setattr(provider_costs, 'append_cost_event', fail_second_cost_event)
    observation = _observation(key='atomic-call').model_copy(update={
        'meters': [
            ProviderMeterObservation(meter='input_tokens', quantity=10, unit='token'),
            ProviderMeterObservation(meter='output_tokens', quantity=5, unit='token'),
        ],
    })

    with pytest.raises(RuntimeError, match='cost writer failed'):
        execute_v2_provider_call(
            _v2_context(),
            run_lease_fencing_token='run-lease-1',
            provider='deepseek',
            model='deepseek-v4-flash',
            operation='chat_completion',
            idempotency_key='atomic-call',
            attempt=1,
            network_call=lambda: observation,
        )

    session = factory()
    try:
        call = session.scalar(select(ProviderCall))
        assert call is not None
        assert call.status == 'running'
        assert len(session.scalars(select(CostEvent)).all()) == 0
    finally:
        session.close()


def test_v2_redacts_provider_exception_before_reraising(monkeypatch, tmp_path) -> None:
    factory = _session_factory(tmp_path)
    monkeypatch.setattr(provider_costs, 'SessionLocal', factory)

    def failing_network_call():
        raise RuntimeError('Authorization: Bearer secret-provider-token')

    with pytest.raises(ProviderCallExecutionError) as captured:
        execute_v2_provider_call(
            _v2_context(),
            run_lease_fencing_token='run-lease-1',
            provider='deepseek',
            model='deepseek-v4-flash',
            operation='chat_completion',
            idempotency_key='failed-call',
            attempt=1,
            network_call=failing_network_call,
        )

    assert 'secret-provider-token' not in str(captured.value)
    session = factory()
    try:
        call = session.scalar(select(ProviderCall))
        assert call is not None
        assert call.status == 'failed'
        assert 'secret-provider-token' not in (call.error_summary or '')
    finally:
        session.close()
