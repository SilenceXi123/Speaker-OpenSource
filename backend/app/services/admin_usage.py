from __future__ import annotations

from collections import defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.cost_event import CostEvent
from app.models.job import Job
from app.models.price_catalog import PriceCatalogEntry
from app.models.project import Project
from app.models.provider_call import ProviderCall
from app.schemas.admin import (
    AdminProviderCostReportResponse,
    PriceCatalogCreateRequest,
    PriceCatalogEntryResponse,
    ProviderCallCostResponse,
    ProviderDailyTokenResponse,
    ProviderProjectTokenResponse,
    ProviderTaskCostResponse,
)
from app.services.provider_costs import build_provider_cost_summary


CHINA_TZ = timezone(timedelta(hours=8))


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    normalized = str(value or '').strip()
    return normalized or None


def _provider_cost_filters(
    *,
    project_id: Optional[str],
    job_id: Optional[str],
    provider: Optional[str],
    model: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
):
    filters = []
    if project_id:
        filters.append(ProviderCall.project_id == project_id)
    if job_id:
        filters.append(ProviderCall.job_id == job_id)
    if provider:
        filters.append(ProviderCall.provider == provider)
    if model:
        filters.append(ProviderCall.model == model)
    if date_from:
        filters.append(ProviderCall.started_at >= _china_date_utc_boundary(date_from, end=False))
    if date_to:
        filters.append(ProviderCall.started_at < _china_date_utc_boundary(date_to, end=True))
    return filters


def _amount_buckets(events: list[CostEvent]) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    catalog: dict[str, int] = defaultdict(int)
    actual: dict[str, int] = defaultdict(int)
    for event in events:
        if event.amount_microunits is None:
            continue
        target = actual if event.cost_basis == 'actual_bill' else catalog if event.cost_basis == 'list_price' else None
        if target is not None:
            target[event.currency] += event.amount_microunits
    variance = {
        currency: actual.get(currency, 0) - catalog.get(currency, 0)
        for currency in sorted(set(actual) | set(catalog))
    }
    return dict(catalog), dict(actual), variance


def _event_counts(events: list[CostEvent]) -> dict[str, int]:
    return {
        'event_count': len(events),
        'measured_event_count': sum(item.measurement_status == 'measured' for item in events),
        'estimated_event_count': sum(item.measurement_status == 'estimated' for item in events),
        'unmetered_event_count': sum(item.measurement_status == 'unmetered' for item in events),
    }


def _token_totals(calls) -> dict[str, int]:
    prompt_tokens = sum(int(getattr(call, 'prompt_tokens', None) or 0) for call in calls)
    completion_tokens = sum(int(getattr(call, 'completion_tokens', None) or 0) for call in calls)
    total_tokens = sum(
        int(getattr(call, 'total_tokens', None))
        if getattr(call, 'total_tokens', None) is not None
        else int(getattr(call, 'prompt_tokens', None) or 0) + int(getattr(call, 'completion_tokens', None) or 0)
        for call in calls
    )
    return {
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'total_tokens': total_tokens,
        'prompt_cache_hit_tokens': sum(int(getattr(call, 'prompt_cache_hit_tokens', None) or 0) for call in calls),
        'prompt_cache_miss_tokens': sum(int(getattr(call, 'prompt_cache_miss_tokens', None) or 0) for call in calls),
    }


def _has_token_usage(call) -> bool:
    return any(
        getattr(call, field, None) is not None
        for field in ('prompt_tokens', 'completion_tokens', 'total_tokens')
    )


def _audio_seconds(events: list[CostEvent]) -> Decimal:
    """Return the observed audio duration once, excluding later bill mirrors."""
    return sum(
        (
            event.quantity
            for event in events
            if event.meter == 'audio_seconds'
            and event.cost_basis == 'list_price'
            and event.measurement_status != 'unmetered'
        ),
        Decimal('0'),
    )


def _fun_asr_audio_seconds(call, events: list[CostEvent]) -> Decimal:
    if call.provider != 'dashscope' or call.operation != 'transcription_submit':
        return Decimal('0')
    return _audio_seconds(events)


def _fun_asr_audio_seconds_for_calls(
    calls,
    events_by_call: dict[str, list[CostEvent]],
) -> Decimal:
    return sum(
        (_fun_asr_audio_seconds(call, events_by_call[call.id]) for call in calls),
        Decimal('0'),
    )


def _project_names(session: Session, calls: list[ProviderCall]) -> dict[str, str]:
    project_ids = {call.project_id for call in calls if call.project_id}
    names: dict[str, str] = {}
    if project_ids:
        projects = list(session.execute(
            select(Project.id, Project.name, Project.legacy_program_key).where(
                or_(Project.id.in_(project_ids), Project.legacy_program_key.in_(project_ids))
            )
        ))
        for project_id, project_name, legacy_program_key in projects:
            names[project_id] = project_name
            if legacy_program_key:
                names.setdefault(legacy_program_key, project_name)

    job_ids = {call.job_id for call in calls if call.job_id and call.project_id not in names}
    if job_ids:
        jobs = list(session.execute(
            select(Job.id, Job.project_id, Job.program_key, Job.program_name).where(Job.id.in_(job_ids))
        ))
        for _, job_project_id, program_key, program_name in jobs:
            project_key = str(job_project_id or program_key or '').strip()
            project_name = str(program_name or '').strip()
            if project_key and project_name:
                names.setdefault(project_key, project_name)
    return names


def _china_usage_date(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    timestamp = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(CHINA_TZ).strftime('%Y-%m-%d')


def _daily_token_rows(
    calls: list[ProviderCall],
    *,
    events_by_call: dict[str, list[CostEvent]],
    date_from: Optional[date],
    date_to: Optional[date],
) -> list[ProviderDailyTokenResponse]:
    grouped: dict[str, list[ProviderCall]] = defaultdict(list)
    for call in calls:
        usage_date = _china_usage_date(call.started_at)
        if usage_date:
            grouped[usage_date].append(call)

    dates = sorted(grouped)
    if date_from and date_to and date_from <= date_to and (date_to - date_from).days <= 366:
        dates = [
            (date_from + timedelta(days=index)).isoformat()
            for index in range((date_to - date_from).days + 1)
        ]
    return [
        ProviderDailyTokenResponse(
            usage_date=usage_date,
            call_count=len(grouped.get(usage_date, [])),
            token_call_count=sum(_has_token_usage(call) for call in grouped.get(usage_date, [])),
            audio_seconds=_fun_asr_audio_seconds_for_calls(
                grouped.get(usage_date, []),
                events_by_call,
            ),
            **_token_totals(grouped.get(usage_date, [])),
        )
        for usage_date in dates
    ]


def build_admin_provider_cost_report(
    session: Session,
    *,
    project_id: Optional[str] = None,
    job_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> AdminProviderCostReportResponse:
    normalized = {
        'project_id': _normalize_optional_text(project_id),
        'job_id': _normalize_optional_text(job_id),
        'provider': _normalize_optional_text(provider),
        'model': _normalize_optional_text(model),
    }
    calls = list(session.scalars(
        select(ProviderCall)
        .where(*_provider_cost_filters(**normalized, date_from=date_from, date_to=date_to))
        .order_by(ProviderCall.started_at.desc(), ProviderCall.id)
    ))
    project_names = _project_names(session, calls)
    call_ids = [item.id for item in calls]
    events = list(session.scalars(
        select(CostEvent)
        .where(CostEvent.provider_call_id.in_(call_ids))
        .order_by(CostEvent.occurred_at, CostEvent.id)
    )) if call_ids else []
    events_by_call: dict[str, list[CostEvent]] = defaultdict(list)
    for event in events:
        events_by_call[event.provider_call_id].append(event)

    response_calls: list[ProviderCallCostResponse] = []
    for call in calls:
        call_events = events_by_call[call.id]
        catalog, actual, variance = _amount_buckets(call_events)
        normalized_tokens = _token_totals([call])
        response_calls.append(ProviderCallCostResponse(
            id=call.id,
            project_id=call.project_id,
            job_id=call.job_id,
            run_id=call.run_id,
            provider=call.provider,
            model=call.model,
            operation=call.operation,
            status=call.status,
            started_at=call.started_at,
            completed_at=call.completed_at,
            duration_ms=call.duration_ms,
            prompt_tokens=call.prompt_tokens,
            completion_tokens=call.completion_tokens,
            total_tokens=normalized_tokens['total_tokens'] if _has_token_usage(call) else None,
            prompt_cache_hit_tokens=call.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=call.prompt_cache_miss_tokens,
            audio_seconds=_fun_asr_audio_seconds(call, call_events),
            catalog_estimated_amounts_by_currency=catalog,
            actual_amounts_by_currency=actual,
            actual_minus_catalog_by_currency=variance,
            **_event_counts(call_events),
        ))

    task_groups: dict[tuple[str, Optional[str]], list[ProviderCallCostResponse]] = defaultdict(list)
    for call in response_calls:
        task_groups[(call.project_id, call.job_id)].append(call)
    task_rows = []
    for (task_project_id, task_job_id), task_calls in task_groups.items():
        task_events = [event for call in task_calls for event in events_by_call[call.id]]
        catalog, actual, variance = _amount_buckets(task_events)
        task_rows.append(ProviderTaskCostResponse(
            project_id=task_project_id,
            project_name=project_names.get(task_project_id, task_project_id),
            job_id=task_job_id,
            call_count=len(task_calls),
            token_call_count=sum(call.total_tokens is not None for call in task_calls),
            audio_seconds=sum((call.audio_seconds for call in task_calls), Decimal('0')),
            **_token_totals(task_calls),
            catalog_estimated_amounts_by_currency=catalog,
            actual_amounts_by_currency=actual,
            actual_minus_catalog_by_currency=variance,
            calls=task_calls,
            **_event_counts(task_events),
        ))
    task_rows.sort(key=lambda item: (-item.total_tokens, item.project_name, item.job_id or ''))

    project_groups: dict[str, list[ProviderCall]] = defaultdict(list)
    for call in calls:
        project_groups[call.project_id].append(call)
    project_rows = []
    for grouped_project_id, project_calls in project_groups.items():
        project_events = [event for call in project_calls for event in events_by_call[call.id]]
        catalog, _, _ = _amount_buckets(project_events)
        project_rows.append(ProviderProjectTokenResponse(
            project_id=grouped_project_id,
            project_name=project_names.get(grouped_project_id, grouped_project_id),
            call_count=len(project_calls),
            token_call_count=sum(_has_token_usage(call) for call in project_calls),
            audio_seconds=_fun_asr_audio_seconds_for_calls(project_calls, events_by_call),
            catalog_estimated_amounts_by_currency=catalog,
            **_token_totals(project_calls),
        ))
    project_rows.sort(key=lambda item: (-item.total_tokens, -item.audio_seconds, item.project_name))

    catalog, actual, variance = _amount_buckets(events)
    summary_date_from = _china_date_utc_boundary(date_from, end=False) if date_from else None
    summary_date_to = _china_date_utc_boundary(date_to, end=True) if date_to else None
    return AdminProviderCostReportResponse(
        summary=build_provider_cost_summary(
            session,
            **normalized,
            date_from=summary_date_from,
            date_to=summary_date_to,
        ),
        **_token_totals(calls),
        token_call_count=sum(_has_token_usage(call) for call in calls),
        audio_seconds=_fun_asr_audio_seconds_for_calls(calls, events_by_call),
        catalog_estimated_amounts_by_currency=catalog,
        actual_amounts_by_currency=actual,
        actual_minus_catalog_by_currency=variance,
        projects=project_rows,
        daily=_daily_token_rows(
            calls,
            events_by_call=events_by_call,
            date_from=date_from,
            date_to=date_to,
        ),
        tasks=task_rows,
    )


def export_admin_provider_cost_csv(session: Session, **filters) -> str:
    report = build_admin_provider_cost_report(session, **filters)
    project_names = {project.project_id: project.project_name for project in report.projects}
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        '项目名称', '项目ID', '任务ID', '调用ID', 'Provider', '模型', '操作', '状态',
        '开始时间', '耗时(ms)', '音频时长(秒)', '输入Token', '输出Token', '总Token',
        '缓存命中Token', '缓存未命中Token', '实测事件', '非实测事件', '未计价事件',
        '目录价金额(微单位)', '账单金额(微单位)', '账单减目录价(微单位)',
    ])
    for task in report.tasks:
        for call in task.calls:
            writer.writerow([
                project_names.get(call.project_id, call.project_id),
                call.project_id,
                call.job_id or '',
                call.id,
                call.provider,
                call.model,
                call.operation,
                call.status,
                call.started_at.isoformat() if call.started_at else '',
                call.duration_ms if call.duration_ms is not None else '',
                call.audio_seconds,
                call.prompt_tokens if call.prompt_tokens is not None else '',
                call.completion_tokens if call.completion_tokens is not None else '',
                call.total_tokens if call.total_tokens is not None else '',
                call.prompt_cache_hit_tokens if call.prompt_cache_hit_tokens is not None else '',
                call.prompt_cache_miss_tokens if call.prompt_cache_miss_tokens is not None else '',
                call.measured_event_count,
                call.estimated_event_count,
                call.unmetered_event_count,
                call.catalog_estimated_amounts_by_currency,
                call.actual_amounts_by_currency,
                call.actual_minus_catalog_by_currency,
            ])
    return '\ufeff' + buffer.getvalue()


def list_price_catalog_entries(
    session: Session,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> list[PriceCatalogEntryResponse]:
    filters = []
    if provider:
        filters.append(PriceCatalogEntry.provider == provider)
    if model:
        filters.append(PriceCatalogEntry.model == model)
    entries = session.scalars(
        select(PriceCatalogEntry)
        .where(*filters)
        .order_by(PriceCatalogEntry.provider, PriceCatalogEntry.model, PriceCatalogEntry.operation,
                  PriceCatalogEntry.meter, PriceCatalogEntry.effective_from.desc())
    )
    return [PriceCatalogEntryResponse.model_validate(item) for item in entries]


def create_price_catalog_version(
    session: Session,
    payload: PriceCatalogCreateRequest,
) -> PriceCatalogEntryResponse:
    effective_from = _naive_utc(payload.effective_from)
    effective_to = _naive_utc(payload.effective_to) if payload.effective_to else None
    identity_filters = (
        PriceCatalogEntry.provider == payload.provider,
        PriceCatalogEntry.model == payload.model,
        PriceCatalogEntry.operation == payload.operation,
        PriceCatalogEntry.meter == payload.meter,
    )
    existing = list(session.scalars(
        select(PriceCatalogEntry)
        .where(*identity_filters)
        .order_by(PriceCatalogEntry.effective_from)
    ))
    previous_open = next(
        (item for item in reversed(existing) if item.effective_to is None and item.effective_from < effective_from),
        None,
    )
    can_append = effective_to is None and previous_open is not None and previous_open is existing[-1]
    for item in existing:
        item_end = item.effective_to or datetime.max
        payload_end = effective_to or datetime.max
        overlaps = item.effective_from < payload_end and effective_from < item_end
        if overlaps and not (can_append and item is previous_open):
            raise ValueError('price catalog effective interval overlap')
    if can_append:
        previous_open.effective_to = effective_from
    entry_data = payload.model_dump()
    entry_data.update(effective_from=effective_from, effective_to=effective_to)
    entry = PriceCatalogEntry(**entry_data)
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return PriceCatalogEntryResponse.model_validate(entry)


def _china_date_utc_boundary(value: date, *, end: bool) -> datetime:
    target_date = value + (timedelta(days=1) if end else timedelta())
    boundary = datetime.combine(target_date, datetime.min.time(), tzinfo=CHINA_TZ)
    return boundary.astimezone(timezone.utc).replace(tzinfo=None)


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
