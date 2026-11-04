from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.v1 import admin as admin_api
from app.core.config import settings
from app.db.session import Base
from app.models.cost_event import CostEvent
from app.models.price_catalog import PriceCatalogEntry
from app.models.project import Project
from app.models.provider_call import ProviderCall
from app.schemas.admin import PriceCatalogCreateRequest
from app.services.admin_auth import build_admin_session_token
from app.services.admin_usage import (
    build_admin_provider_cost_report,
    create_price_catalog_version,
    export_admin_provider_cost_csv,
)
from app.services.provider_costs import append_cost_event
from main import app


def _session_factory(database_url: str = 'sqlite:///:memory:'):
    engine = create_engine(database_url, connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _configure_admin_session(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'admin_username', 'admin')
    monkeypatch.setattr(settings, 'admin_password_hash', 'configured-password-hash')
    monkeypatch.setattr(settings, 'admin_session_secret', 'session-secret')
    monkeypatch.setattr(settings, 'admin_session_cookie_name', 'speaker_admin_session')
    monkeypatch.setattr(settings, 'admin_session_max_age_seconds', 3600)


def _authenticated_client(monkeypatch, factory) -> TestClient:
    _configure_admin_session(monkeypatch)
    monkeypatch.setattr(admin_api, 'SessionLocal', factory)
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set(settings.admin_session_cookie_name, build_admin_session_token('admin'))
    return client


def _add_call(
    session,
    *,
    call_id: str,
    project_id: str,
    job_id: str,
    provider: str,
    model: str,
    operation: str,
    started_at: datetime,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    prompt_cache_hit_tokens: int | None = None,
    prompt_cache_miss_tokens: int | None = None,
) -> ProviderCall:
    call = ProviderCall(
        id=call_id,
        project_id=project_id,
        job_id=job_id,
        provider=provider,
        model=model,
        operation=operation,
        idempotency_key=f'key:{call_id}',
        attempt=1,
        status='completed',
        usage_payload={'private_usage_detail': 'must-not-leak'},
        started_at=started_at,
        completed_at=started_at,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=prompt_cache_miss_tokens,
    )
    session.add(call)
    session.commit()
    return call


def _add_event(
    session,
    call: ProviderCall,
    *,
    event_id: str,
    meter: str,
    cost_basis: str,
    measurement_status: str,
    amount: int | None,
    occurred_at: datetime,
    quantity: Decimal = Decimal('10'),
    unit: str = 'token',
) -> None:
    session.add(
        CostEvent(
            id=event_id,
            provider_call_id=call.id,
            project_id=call.project_id,
            job_id=call.job_id,
            meter=meter,
            cost_basis=cost_basis,
            measurement_status=measurement_status,
            quantity=quantity,
            unit=unit,
            amount_microunits=amount,
            currency='CNY',
            occurred_at=occurred_at,
        )
    )
    session.commit()


def _seed_cost_facts(session) -> None:
    session.add_all([
        Project(id='project-a', name='节目 A', name_key='节目 a'),
        Project(id='project-b', name='节目 B', name_key='节目 b'),
    ])
    session.commit()
    deepseek = _add_call(
        session,
        call_id='call-deepseek',
        project_id='project-a',
        job_id='job-a',
        provider='deepseek',
        model='deepseek-v4-flash',
        operation='chat_completion',
        started_at=datetime(2026, 7, 14, 8, 0, 0),
        prompt_tokens=120,
        completion_tokens=30,
        total_tokens=150,
        prompt_cache_hit_tokens=80,
        prompt_cache_miss_tokens=40,
    )
    _add_event(
        session,
        deepseek,
        event_id='event-list-input',
        meter='input_tokens',
        cost_basis='list_price',
        measurement_status='measured',
        amount=120,
        occurred_at=datetime(2026, 7, 14, 8, 0, 1),
    )
    _add_event(
        session,
        deepseek,
        event_id='event-actual-input',
        meter='input_tokens',
        cost_basis='actual_bill',
        measurement_status='measured',
        amount=100,
        occurred_at=datetime(2026, 7, 14, 8, 0, 2),
    )
    _add_event(
        session,
        deepseek,
        event_id='event-list-output',
        meter='output_tokens',
        cost_basis='list_price',
        measurement_status='estimated',
        amount=40,
        occurred_at=datetime(2026, 7, 14, 8, 0, 3),
    )
    _add_event(
        session,
        deepseek,
        event_id='event-unmetered-cache',
        meter='cache_tokens',
        cost_basis='provider_usage',
        measurement_status='unmetered',
        amount=None,
        occurred_at=datetime(2026, 7, 14, 8, 0, 4),
    )

    dashscope = _add_call(
        session,
        call_id='call-dashscope',
        project_id='project-a',
        job_id='job-b',
        provider='dashscope',
        model='fun-asr',
        operation='transcription_submit',
        started_at=datetime(2026, 7, 13, 8, 0, 0),
    )
    _add_event(
        session,
        dashscope,
        event_id='event-asr',
        meter='audio_seconds',
        cost_basis='list_price',
        measurement_status='measured',
        amount=300,
        occurred_at=datetime(2026, 7, 13, 8, 0, 1),
        quantity=Decimal('6223.8'),
        unit='second',
    )

    other_project = _add_call(
        session,
        call_id='call-other-project',
        project_id='project-b',
        job_id='job-c',
        provider='deepseek',
        model='deepseek-v4-flash',
        operation='chat_completion',
        started_at=datetime(2026, 7, 14, 9, 0, 0),
        prompt_tokens=40,
        completion_tokens=10,
        total_tokens=50,
    )
    _add_event(
        session,
        other_project,
        event_id='event-other-project',
        meter='input_tokens',
        cost_basis='list_price',
        measurement_status='measured',
        amount=999,
        occurred_at=datetime(2026, 7, 14, 9, 0, 1),
    )



def test_provider_cost_report_ranks_projects_and_fills_daily_series() -> None:
    session = _session_factory()()
    try:
        _seed_cost_facts(session)

        report = build_admin_provider_cost_report(
            session,
            date_from=date(2026, 7, 13),
            date_to=date(2026, 7, 15),
        )

        assert [(row.project_name, row.total_tokens) for row in report.projects] == [
            ('节目 A', 150),
            ('节目 B', 50),
        ]
        assert report.token_call_count == 2
        assert report.audio_seconds == Decimal('6223.8')
        assert report.summary.call_count == 3
        assert [row.usage_date for row in report.daily] == [
            '2026-07-13',
            '2026-07-14',
            '2026-07-15',
        ]
        assert [row.total_tokens for row in report.daily] == [0, 200, 0]
        assert [row.token_call_count for row in report.daily] == [0, 2, 0]
        assert [row.audio_seconds for row in report.daily] == [Decimal('6223.8'), 0, 0]
        assert report.projects[0].audio_seconds == Decimal('6223.8')
        asr_task = next(task for task in report.tasks if task.job_id == 'job-b')
        assert asr_task.audio_seconds == Decimal('6223.8')
        assert asr_task.total_tokens == 0
        assert asr_task.calls[0].audio_seconds == Decimal('6223.8')
        assert asr_task.calls[0].total_tokens is None
    finally:
        session.close()


def test_provider_cost_report_filters_and_groups_exact_tokens() -> None:
    session = _session_factory()()
    try:
        _seed_cost_facts(session)

        report = build_admin_provider_cost_report(
            session,
            project_id='project-a',
            job_id='job-a',
            provider='deepseek',
            model='deepseek-v4-flash',
            date_from=date(2026, 7, 14),
            date_to=date(2026, 7, 14),
        )

        assert report.summary.call_count == 1
        assert report.summary.event_count == 4
        assert report.summary.measured_event_count == 2
        assert report.summary.estimated_event_count == 1
        assert report.summary.unmetered_event_count == 1
        assert report.catalog_estimated_amounts_by_currency == {'CNY': 160}
        assert report.actual_amounts_by_currency == {'CNY': 100}
        assert report.actual_minus_catalog_by_currency == {'CNY': -60}
        assert report.prompt_tokens == 120
        assert report.completion_tokens == 30
        assert report.total_tokens == 150
        assert report.prompt_cache_hit_tokens == 80
        assert report.prompt_cache_miss_tokens == 40
        assert report.token_call_count == 1
        assert report.audio_seconds == 0
        assert report.projects[0].project_id == 'project-a'
        assert report.projects[0].project_name == '节目 A'
        assert report.projects[0].total_tokens == 150
        assert len(report.daily) == 1
        assert report.daily[0].usage_date == '2026-07-14'
        assert report.daily[0].total_tokens == 150
        assert [task.job_id for task in report.tasks] == ['job-a']
        assert report.tasks[0].project_name == '节目 A'
        assert report.tasks[0].token_call_count == 1
        assert [call.id for call in report.tasks[0].calls] == ['call-deepseek']
        assert report.tasks[0].prompt_tokens == 120
        assert report.tasks[0].calls[0].total_tokens == 150
        assert report.tasks[0].calls[0].usage_payload is None

        csv_text = export_admin_provider_cost_csv(
            session,
            project_id='project-a',
            job_id='job-a',
            provider='deepseek',
            model='deepseek-v4-flash',
            date_from=date(2026, 7, 14),
            date_to=date(2026, 7, 14),
        )
        assert 'deepseek-v4-flash' in csv_text
        assert '输入Token' in csv_text
        assert ',120,30,150,80,40,' in csv_text
        assert '节目 A' in csv_text

        actual_only = _add_call(
            session,
            call_id='call-actual-only',
            project_id='project-a',
            job_id='job-actual-only',
            provider='deepseek',
            model='deepseek-v4-flash',
            operation='chat_completion',
            started_at=datetime(2026, 7, 14, 10, 0, 0),
        )
        _add_event(
            session,
            actual_only,
            event_id='event-actual-only',
            meter='input_tokens',
            cost_basis='actual_bill',
            measurement_status='measured',
            amount=75,
            occurred_at=datetime(2026, 7, 14, 10, 0, 1),
        )
        actual_only_report = build_admin_provider_cost_report(session, job_id='job-actual-only')
        assert actual_only_report.catalog_estimated_amounts_by_currency == {}
        assert actual_only_report.actual_amounts_by_currency == {'CNY': 75}
        assert actual_only_report.actual_minus_catalog_by_currency == {'CNY': 75}

        china_day_boundary = _add_call(
            session,
            call_id='call-china-boundary',
            project_id='project-a',
            job_id='job-china-boundary',
            provider='deepseek',
            model='deepseek-v4-flash',
            operation='chat_completion',
            started_at=datetime(2026, 7, 13, 16, 30, 0),
        )
        _add_event(
            session,
            china_day_boundary,
            event_id='event-china-boundary',
            meter='input_tokens',
            cost_basis='list_price',
            measurement_status='measured',
            amount=10,
            occurred_at=datetime(2026, 7, 13, 16, 30, 1),
        )
        china_day_report = build_admin_provider_cost_report(
            session,
            job_id='job-china-boundary',
            date_from=date(2026, 7, 14),
            date_to=date(2026, 7, 14),
        )
        assert china_day_report.summary.call_count == 1
    finally:
        session.close()


def test_provider_cost_admin_endpoints_require_login_and_apply_filters(monkeypatch, tmp_path) -> None:
    factory = _session_factory(f'sqlite:///{tmp_path / "provider-costs.db"}')
    session = factory()
    _seed_cost_facts(session)
    session.close()
    _configure_admin_session(monkeypatch)
    monkeypatch.setattr(admin_api, 'SessionLocal', factory)

    anonymous = TestClient(app, raise_server_exceptions=False)
    assert anonymous.get('/api/v1/admin/provider-costs').status_code == 401
    assert anonymous.get('/api/v1/admin/provider-costs/readiness').status_code == 401
    assert anonymous.post('/api/v1/admin/provider-costs/prices', json={}).status_code == 401

    client = _authenticated_client(monkeypatch, factory)
    response = client.get(
        '/api/v1/admin/provider-costs',
        params={
            'project_id': 'project-a',
            'job_id': 'job-a',
            'provider': 'deepseek',
            'model': 'deepseek-v4-flash',
            'date_from': '2026-07-14',
            'date_to': '2026-07-14',
        },
    )

    assert response.status_code == 200
    assert response.json()['summary']['call_count'] == 1
    assert response.json()['token_call_count'] == 1
    assert response.json()['total_tokens'] == 150
    assert response.json()['audio_seconds'] == '0'
    assert response.json()['projects'][0]['project_name'] == '节目 A'
    assert response.json()['daily'][0]['usage_date'] == '2026-07-14'
    assert response.json()['tasks'][0]['job_id'] == 'job-a'
    assert 'must-not-leak' not in response.text

    readiness_response = client.get('/api/v1/admin/provider-costs/readiness')
    assert readiness_response.status_code == 200
    assert readiness_response.json()['schema_ready'] is True
    assert readiness_response.json()['expected_price_count'] == 9

    csv_response = client.get('/api/v1/admin/provider-costs/export.csv', params={'job_id': 'job-a'})
    assert csv_response.status_code == 200
    assert 'must-not-leak' not in csv_response.text

    price_response = client.post('/api/v1/admin/provider-costs/prices', json={
        'provider': 'deepseek',
        'model': 'deepseek-v4-flash',
        'operation': 'chat_completion',
        'meter': 'input_tokens',
        'effective_from': '2026-07-01T00:00:00',
        'billing_unit_size': '1000',
        'unit_price_microunits': 100,
        'currency': 'cny',
    })
    assert price_response.status_code == 200
    assert price_response.json()['currency'] == 'CNY'
    assert len(client.get('/api/v1/admin/provider-costs/prices').json()) == 1
    assert client.get('/api/v1/admin/usage/summary').status_code == 404
    assert client.get('/api/v1/admin/usage/daily').status_code == 404
    assert client.get('/api/v1/admin/project-profiles').status_code == 404


def test_price_catalog_versions_append_and_preserve_cost_snapshots() -> None:
    session = _session_factory()()
    try:
        first = create_price_catalog_version(
            session,
            PriceCatalogCreateRequest(
                provider='deepseek',
                model='deepseek-v4-flash',
                operation='chat_completion',
                meter='input_tokens',
                effective_from=datetime(2026, 1, 1),
                billing_unit_size=Decimal('1000'),
                unit_price_microunits=100,
                currency='CNY',
            ),
        )
        call = _add_call(
            session,
            call_id='priced-call',
            project_id='project-a',
            job_id='job-priced',
            provider='deepseek',
            model='deepseek-v4-flash',
            operation='chat_completion',
            started_at=datetime(2026, 6, 1),
        )
        event = append_cost_event(
            session,
            call.id,
            meter='input_tokens',
            quantity=Decimal('1000'),
            unit='token',
            cost_basis='list_price',
        )

        second = create_price_catalog_version(
            session,
            PriceCatalogCreateRequest(
                provider='deepseek',
                model='deepseek-v4-flash',
                operation='chat_completion',
                meter='input_tokens',
                effective_from=datetime(2026, 7, 1, 8, tzinfo=timezone(timedelta(hours=8))),
                billing_unit_size=Decimal('1000'),
                unit_price_microunits=250,
                currency='CNY',
            ),
        )

        assert first.id != second.id
        assert session.get(PriceCatalogEntry, first.id).effective_to == datetime(2026, 7, 1)
        assert len(session.scalars(select(PriceCatalogEntry)).all()) == 2
        snapshot = session.get(CostEvent, event.id)
        assert snapshot.price_catalog_entry_id == first.id
        assert snapshot.unit_price_microunits == 100
        assert snapshot.amount_microunits == 100

        with pytest.raises(ValueError, match='overlap'):
            create_price_catalog_version(
                session,
                PriceCatalogCreateRequest(
                    provider='deepseek',
                    model='deepseek-v4-flash',
                    operation='chat_completion',
                    meter='input_tokens',
                    effective_from=datetime(2026, 7, 15),
                    effective_to=datetime(2026, 8, 1),
                    billing_unit_size=Decimal('1000'),
                    unit_price_microunits=300,
                    currency='CNY',
                ),
            )
    finally:
        session.close()
