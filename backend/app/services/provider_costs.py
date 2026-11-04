from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import logging
import re
from typing import Any, Callable, Mapping, Optional

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.schema_bootstrap import provider_cost_schema_status
from app.db.session import SessionLocal
from app.models.cost_event import CostEvent
from app.models.price_catalog import PriceCatalogEntry
from app.models.provider_call import ProviderCall
from app.schemas.provider_cost import (
    ProviderCallClaim,
    ProviderCallContext,
    ProviderCallObservation,
    ProviderCostBreakdown,
    ProviderCostSummary,
)


logger = logging.getLogger(__name__)

_USAGE_SUFFIXES = ('_tokens', '_seconds', '_bytes', '_count', '_milliseconds')
_TOKEN_COLUMNS = (
    'prompt_tokens',
    'completion_tokens',
    'total_tokens',
    'prompt_cache_hit_tokens',
    'prompt_cache_miss_tokens',
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r'(?i)["\']?\b(api[_-]?key|access[_-]?key|access[_-]?token|refresh[_-]?token|'
    r'password|secret|token|authorization|signature|prompt)\b["\']?'
    r'\s*[:=]\s*(?:"[^"]*"|\'[^\']*\'|\S+)'
)
_BEARER_RE = re.compile(r'(?i)\bbearer\s+\S+')
_URL_RE = re.compile(r'https?://\S+', re.IGNORECASE)
_ERROR_CODE_RE = re.compile(r'[^A-Za-z0-9_.:-]+')
_QUANTITY_QUANTUM = Decimal('0.000001')


class ProviderCostError(RuntimeError):
    pass


class ProviderCostSchemaNotReadyError(ProviderCostError):
    pass


class ProviderCallClaimUnavailableError(ProviderCostError):
    pass


class ProviderCallFencingError(ProviderCostError):
    pass


class ProviderCallExecutionError(ProviderCostError):
    pass


def reserve_provider_call(
    session: Session,
    context: ProviderCallContext,
    *,
    provider: str,
    model: str,
    operation: str,
    idempotency_key: str,
    attempt: int,
) -> ProviderCall:
    context = ProviderCallContext.model_validate(context)
    provider = _required_text(provider, 'provider', 64)
    model = _required_text(model, 'model', 255)
    operation = _required_text(operation, 'operation', 64)
    idempotency_key = _required_text(idempotency_key, 'idempotency_key', 255)
    if int(attempt) < 1:
        raise ValueError('attempt must be at least 1')

    existing = _find_reserved_call(session, idempotency_key, int(attempt))
    if existing is not None:
        _validate_reservation_identity(existing, context, provider, model, operation)
        return existing

    provider_call = ProviderCall(
        project_id=context.project_id,
        job_id=context.job_id,
        run_id=context.run_id,
        window_id=context.window_id,
        provider=provider,
        model=model,
        operation=operation,
        idempotency_key=idempotency_key,
        attempt=int(attempt),
        status='pending',
    )
    session.add(provider_call)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _find_reserved_call(session, idempotency_key, int(attempt))
        if existing is None:
            raise
        _validate_reservation_identity(existing, context, provider, model, operation)
        return existing
    session.refresh(provider_call)
    return provider_call


def claim_provider_call_attempt(
    session: Session,
    call_id: str,
    *,
    owner: str,
    now: datetime,
    ttl_seconds: int,
) -> Optional[ProviderCallClaim]:
    owner = _required_text(owner, 'owner', 255)
    if int(ttl_seconds) <= 0:
        raise ValueError('ttl_seconds must be positive')
    snapshot = session.get(ProviderCall, str(call_id))
    if snapshot is None:
        raise ValueError('provider call not found')

    expected_version = int(snapshot.lock_version or 0)
    expires_at = now + timedelta(seconds=int(ttl_seconds))
    eligible = or_(
        ProviderCall.status == 'pending',
        and_(
            ProviderCall.status == 'running',
            ProviderCall.claim_expires_at.is_not(None),
            ProviderCall.claim_expires_at <= now,
        ),
    )
    result = session.execute(
        update(ProviderCall)
        .where(
            ProviderCall.id == str(call_id),
            ProviderCall.lock_version == expected_version,
            eligible,
        )
        .values(
            status='running',
            started_at=func.coalesce(ProviderCall.started_at, now),
            claim_owner=owner,
            claim_expires_at=expires_at,
            lock_version=ProviderCall.lock_version + 1,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    return ProviderCallClaim(
        call_id=str(call_id),
        owner=owner,
        lock_version=expected_version + 1,
        expires_at=expires_at,
    )


def complete_provider_call(
    session: Session,
    call_id: str,
    *,
    fencing_token: ProviderCallClaim,
    request_id: Optional[str],
    usage: Optional[Mapping[str, Any]],
    completed_at: datetime,
    _commit: bool = True,
) -> ProviderCall:
    call = session.get(ProviderCall, str(call_id))
    if call is None:
        raise ValueError('provider call not found')
    safe_usage = _safe_usage(usage)
    values: dict[str, Any] = {
        'status': 'completed',
        'provider_request_id': _optional_text(request_id, 255),
        'completed_at': completed_at,
        'duration_ms': _duration_ms(call.started_at, completed_at),
        'usage_payload': safe_usage or None,
        'error_code': None,
        'error_summary': None,
        'claim_owner': None,
        'claim_expires_at': None,
    }
    values.update({name: _usage_integer(safe_usage.get(name)) for name in _TOKEN_COLUMNS})
    _terminal_update(session, call_id, fencing_token, values, commit=_commit)
    return call


def fail_provider_call(
    session: Session,
    call_id: str,
    *,
    fencing_token: ProviderCallClaim,
    error_code: Optional[str],
    error_summary: Optional[str],
    completed_at: datetime,
    _commit: bool = True,
) -> ProviderCall:
    call = session.get(ProviderCall, str(call_id))
    if call is None:
        raise ValueError('provider call not found')
    _terminal_update(
        session,
        call_id,
        fencing_token,
        {
            'status': 'failed',
            'completed_at': completed_at,
            'duration_ms': _duration_ms(call.started_at, completed_at),
            'error_code': _safe_error_code(error_code),
            'error_summary': _safe_error_summary(error_summary),
            'claim_owner': None,
            'claim_expires_at': None,
        },
        commit=_commit,
    )
    return call


def append_cost_event(
    session: Session,
    call_id: str,
    *,
    meter: str,
    quantity: Optional[Decimal],
    unit: str,
    cost_basis: str,
    _commit: bool = True,
) -> CostEvent:
    meter = _required_text(meter, 'meter', 64)
    unit = _required_text(unit, 'unit', 32)
    cost_basis = _required_text(cost_basis, 'cost_basis', 32)
    normalized_quantity = _normalize_quantity(quantity)
    measurement_status = 'unmetered' if quantity is None else 'measured'
    existing = _find_cost_event(session, call_id, meter, cost_basis)
    if existing is not None:
        _validate_cost_event_replay(existing, normalized_quantity, unit, measurement_status)
        return existing
    provider_call = session.get(ProviderCall, str(call_id))
    if provider_call is None:
        raise ValueError('provider call not found')

    effective_at = provider_call.started_at or provider_call.completed_at or _utcnow()
    price = None if quantity is None else _effective_price(session, provider_call, meter, effective_at)
    amount_microunits = None
    if price is not None:
        if price.billing_unit_size <= 0:
            raise ValueError('billing_unit_size must be positive')
        raw_amount = (
            normalized_quantity
            / price.billing_unit_size
            * Decimal(price.unit_price_microunits)
        )
        amount_microunits = int(raw_amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    event = CostEvent(
        provider_call_id=provider_call.id,
        project_id=provider_call.project_id,
        job_id=provider_call.job_id,
        run_id=provider_call.run_id,
        window_id=provider_call.window_id,
        meter=meter,
        cost_basis=cost_basis,
        measurement_status=measurement_status,
        quantity=normalized_quantity,
        unit=unit,
        price_catalog_entry_id=price.id if price is not None else None,
        billing_unit_size=price.billing_unit_size if price is not None else None,
        unit_price_microunits=price.unit_price_microunits if price is not None else None,
        amount_microunits=amount_microunits,
        currency=price.currency if price is not None else 'CNY',
        occurred_at=effective_at,
    )
    if not _commit:
        try:
            with session.begin_nested():
                session.add(event)
                session.flush()
        except IntegrityError:
            existing = _find_cost_event(session, call_id, meter, cost_basis)
            if existing is None:
                raise
            _validate_cost_event_replay(existing, normalized_quantity, unit, measurement_status)
            return existing
        return event

    session.add(event)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _find_cost_event(session, call_id, meter, cost_basis)
        if existing is None:
            raise
        _validate_cost_event_replay(existing, normalized_quantity, unit, measurement_status)
        return existing
    session.refresh(event)
    return event


def build_provider_cost_summary(
    session: Session,
    *,
    project_id: Optional[str] = None,
    job_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    date_from: Optional[date | datetime] = None,
    date_to: Optional[date | datetime] = None,
) -> ProviderCostSummary:
    call_filters = []
    event_filters = []
    if project_id:
        call_filters.append(ProviderCall.project_id == str(project_id))
        event_filters.append(CostEvent.project_id == str(project_id))
    if job_id:
        call_filters.append(ProviderCall.job_id == str(job_id))
        event_filters.append(CostEvent.job_id == str(job_id))
    if provider:
        call_filters.append(ProviderCall.provider == str(provider))
        event_filters.append(ProviderCall.provider == str(provider))
    if model:
        call_filters.append(ProviderCall.model == str(model))
        event_filters.append(ProviderCall.model == str(model))
    if date_from is not None:
        lower = _datetime_boundary(date_from, end=False)
        call_filters.append(ProviderCall.started_at >= lower)
        event_filters.append(CostEvent.occurred_at >= lower)
    if date_to is not None:
        upper = _datetime_boundary(date_to, end=True)
        call_filters.append(ProviderCall.started_at < upper)
        event_filters.append(CostEvent.occurred_at < upper)

    calls = list(session.scalars(select(ProviderCall).where(*call_filters)))
    rows = list(
        session.execute(
            select(CostEvent, ProviderCall.provider, ProviderCall.model)
            .join(ProviderCall, ProviderCall.id == CostEvent.provider_call_id)
            .where(*event_filters)
            .order_by(CostEvent.occurred_at, CostEvent.id)
        )
    )
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    totals = _empty_counts()
    amounts_by_currency: dict[str, int] = {}
    currencies: set[str] = set()
    for event, provider, model in rows:
        _accumulate_counts(totals, event)
        _accumulate_amount(amounts_by_currency, event)
        currencies.add(event.currency)
        bucket = buckets.setdefault(
            (provider, model),
            {
                'counts': _empty_counts(),
                'call_ids': set(),
                'currencies': set(),
                'amounts_by_currency': {},
            },
        )
        _accumulate_counts(bucket['counts'], event)
        _accumulate_amount(bucket['amounts_by_currency'], event)
        bucket['call_ids'].add(event.provider_call_id)
        bucket['currencies'].add(event.currency)

    breakdowns = [
        ProviderCostBreakdown(
            provider=provider,
            model=model,
            call_count=len(value['call_ids']),
            currencies=sorted(value['currencies']),
            amounts_by_currency=value['amounts_by_currency'],
            total_amount_microunits=_single_currency_total(value['amounts_by_currency']),
            **value['counts'],
        )
        for (provider, model), value in sorted(buckets.items())
    ]
    return ProviderCostSummary(
        call_count=len(calls),
        currencies=sorted(currencies),
        amounts_by_currency=amounts_by_currency,
        total_amount_microunits=_single_currency_total(amounts_by_currency),
        breakdowns=breakdowns,
        **totals,
    )


def record_legacy_provider_observation(
    context: ProviderCallContext,
    *,
    provider: str,
    model: str,
    operation: str,
    observed_call: ProviderCallObservation | Mapping[str, Any],
) -> Optional[ProviderCall]:
    if not settings.provider_cost_facts_enabled:
        return None
    try:
        context = ProviderCallContext.model_validate(context)
        if context.pipeline_scope == 'subtitle_v2':
            raise ValueError('legacy observation cannot use subtitle_v2 scope')
        observation = ProviderCallObservation.model_validate(observed_call)
        with SessionLocal() as session:
            if not provider_cost_schema_status(session.get_bind())['ready']:
                return None
            observation = _advance_distinct_legacy_attempt(session, observation)
            provider_call = reserve_provider_call(
                session,
                context,
                provider=provider,
                model=model,
                operation=operation,
                idempotency_key=observation.idempotency_key,
                attempt=observation.attempt,
            )
            _apply_legacy_observation(session, provider_call, observation)
            _append_observation_meters(session, provider_call.id, observation)
            session.refresh(provider_call)
            session.expunge(provider_call)
            return provider_call
    except Exception as exc:
        logger.warning(
            'Legacy provider cost observation skipped (%s)',
            type(exc).__name__,
        )
        return None


def _advance_distinct_legacy_attempt(
    session: Session,
    observation: ProviderCallObservation,
) -> ProviderCallObservation:
    existing = _find_reserved_call(
        session,
        observation.idempotency_key,
        observation.attempt,
    )
    if existing is None or _same_legacy_observation(existing, observation):
        return observation
    latest_attempt = session.scalar(
        select(func.max(ProviderCall.attempt)).where(
            ProviderCall.idempotency_key == observation.idempotency_key,
        )
    )
    return observation.model_copy(
        update={'attempt': max(observation.attempt, int(latest_attempt or 0) + 1)},
    )


def _same_legacy_observation(
    existing: ProviderCall,
    observation: ProviderCallObservation,
) -> bool:
    return (
        existing.status == observation.status
        and existing.provider_request_id == _optional_text(observation.request_id, 255)
        and existing.started_at == observation.started_at
        and existing.error_code == _safe_error_code(observation.error_code)
        and existing.error_summary == _safe_error_summary(observation.error_summary)
    )


def execute_v2_provider_call(
    context: ProviderCallContext,
    *,
    run_lease_fencing_token: object,
    provider: str,
    model: str,
    operation: str,
    idempotency_key: str,
    attempt: int,
    network_call: Callable[[], Any],
):
    context = ProviderCallContext.model_validate(context)
    if context.pipeline_scope != 'subtitle_v2':
        raise ValueError('required_fencing is only available for subtitle_v2')
    lease_token = str(run_lease_fencing_token or '').strip()
    if not lease_token:
        raise ValueError('run lease fencing token is required')

    with SessionLocal() as session:
        status = provider_cost_schema_status(session.get_bind())
        if not status['ready']:
            raise ProviderCostSchemaNotReadyError('provider cost facts migration is required')
        provider_call = reserve_provider_call(
            session,
            context,
            provider=provider,
            model=model,
            operation=operation,
            idempotency_key=idempotency_key,
            attempt=attempt,
        )
        owner = _v2_claim_owner(context.run_id or '', lease_token)
        claim = claim_provider_call_attempt(
            session,
            provider_call.id,
            owner=owner,
            now=_utcnow(),
            ttl_seconds=300,
        )
        if claim is None:
            raise ProviderCallClaimUnavailableError('provider call attempt is already claimed')

        try:
            raw_result = network_call()
            observation = _coerce_network_observation(
                raw_result,
                idempotency_key=idempotency_key,
                attempt=attempt,
            )
        except Exception as exc:
            try:
                fail_provider_call(
                    session,
                    provider_call.id,
                    fencing_token=claim,
                    error_code=type(exc).__name__,
                    error_summary=str(exc),
                    completed_at=_utcnow(),
                    _commit=False,
                )
                session.commit()
            except Exception:
                session.rollback()
                raise
            raise ProviderCallExecutionError(
                f'provider call failed: {type(exc).__name__}'
            ) from None

        completed_at = observation.completed_at or _utcnow()
        if observation.status == 'failed':
            try:
                fail_provider_call(
                    session,
                    provider_call.id,
                    fencing_token=claim,
                    error_code=observation.error_code,
                    error_summary=observation.error_summary,
                    completed_at=completed_at,
                    _commit=False,
                )
                _append_observation_meters(
                    session,
                    provider_call.id,
                    observation,
                    commit=False,
                )
                session.commit()
            except Exception:
                session.rollback()
                raise
            raise ProviderCallExecutionError('provider call reported failure')

        try:
            complete_provider_call(
                session,
                provider_call.id,
                fencing_token=claim,
                request_id=observation.request_id,
                usage=observation.usage,
                completed_at=completed_at,
                _commit=False,
            )
            _append_observation_meters(
                session,
                provider_call.id,
                observation,
                commit=False,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        return observation.value


def _coerce_network_observation(
    result: Any,
    *,
    idempotency_key: str,
    attempt: int,
) -> ProviderCallObservation:
    observation = ProviderCallObservation.model_validate(result)
    if observation.idempotency_key != idempotency_key or observation.attempt != int(attempt):
        raise ValueError('network observation identity does not match reserved provider call')
    return observation


def _append_observation_meters(
    session: Session,
    call_id: str,
    observation: ProviderCallObservation,
    *,
    commit: bool = True,
) -> None:
    for meter in observation.meters:
        append_cost_event(
            session,
            call_id,
            meter=meter.meter,
            quantity=meter.quantity,
            unit=meter.unit,
            cost_basis=meter.cost_basis,
            _commit=commit,
        )


def _find_reserved_call(session: Session, idempotency_key: str, attempt: int):
    return session.scalar(
        select(ProviderCall).where(
            ProviderCall.idempotency_key == idempotency_key,
            ProviderCall.attempt == attempt,
        )
    )


def _validate_reservation_identity(
    provider_call: ProviderCall,
    context: ProviderCallContext,
    provider: str,
    model: str,
    operation: str,
) -> None:
    expected = (
        context.project_id,
        context.job_id,
        context.run_id,
        context.window_id,
        provider,
        model,
        operation,
    )
    actual = (
        provider_call.project_id,
        provider_call.job_id,
        provider_call.run_id,
        provider_call.window_id,
        provider_call.provider,
        provider_call.model,
        provider_call.operation,
    )
    if actual != expected:
        raise ProviderCostError('idempotency key is already reserved for another provider call')


def _terminal_update(
    session: Session,
    call_id: str,
    token: ProviderCallClaim,
    values: Mapping[str, Any],
    *,
    commit: bool = True,
) -> None:
    if token.call_id != str(call_id):
        raise ProviderCallFencingError('fencing token does not match provider call')
    result = session.execute(
        update(ProviderCall)
        .where(
            ProviderCall.id == str(call_id),
            ProviderCall.status == 'running',
            ProviderCall.claim_owner == token.owner,
            ProviderCall.lock_version == token.lock_version,
        )
        .values(**dict(values))
    )
    if result.rowcount != 1:
        session.rollback()
        raise ProviderCallFencingError('provider call fencing token is stale')
    if commit:
        session.commit()
    else:
        session.flush()


def _apply_legacy_observation(
    session: Session,
    provider_call: ProviderCall,
    observation: ProviderCallObservation,
) -> None:
    safe_usage = _safe_usage(observation.usage)
    provider_call.status = observation.status
    provider_call.provider_request_id = _optional_text(observation.request_id, 255)
    provider_call.started_at = observation.started_at
    provider_call.completed_at = observation.completed_at or _utcnow()
    provider_call.duration_ms = _duration_ms(
        provider_call.started_at,
        provider_call.completed_at,
    )
    provider_call.usage_payload = safe_usage or None
    for name in _TOKEN_COLUMNS:
        setattr(provider_call, name, _usage_integer(safe_usage.get(name)))
    if observation.status == 'failed':
        provider_call.error_code = _safe_error_code(observation.error_code)
        provider_call.error_summary = _safe_error_summary(observation.error_summary)
    else:
        provider_call.error_code = None
        provider_call.error_summary = None
    session.commit()
    session.refresh(provider_call)


def _find_cost_event(session: Session, call_id: str, meter: str, cost_basis: str):
    return session.scalar(
        select(CostEvent).where(
            CostEvent.provider_call_id == str(call_id),
            CostEvent.meter == meter,
            CostEvent.cost_basis == cost_basis,
        )
    )


def _validate_cost_event_replay(
    existing: CostEvent,
    quantity: Decimal,
    unit: str,
    measurement_status: str,
) -> None:
    if (
        existing.quantity != quantity
        or existing.unit != unit
        or existing.measurement_status != measurement_status
    ):
        raise ProviderCostError('cost event replay conflicts with existing measurement')


def _effective_price(
    session: Session,
    provider_call: ProviderCall,
    meter: str,
    effective_at: datetime,
):
    return session.scalar(
        select(PriceCatalogEntry)
        .where(
            PriceCatalogEntry.provider == provider_call.provider,
            PriceCatalogEntry.model == provider_call.model,
            PriceCatalogEntry.operation == provider_call.operation,
            PriceCatalogEntry.meter == meter,
            PriceCatalogEntry.effective_from <= effective_at,
            or_(
                PriceCatalogEntry.effective_to.is_(None),
                PriceCatalogEntry.effective_to > effective_at,
            ),
        )
        .order_by(PriceCatalogEntry.effective_from.desc(), PriceCatalogEntry.id.desc())
        .limit(1)
    )


def _normalize_quantity(quantity: Optional[Decimal]) -> Decimal:
    if quantity is None:
        return Decimal('0.000000')
    try:
        value = quantity if isinstance(quantity, Decimal) else Decimal(str(quantity))
        if not value.is_finite() or value < 0:
            raise ValueError
        return value.quantize(_QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('quantity must be a finite non-negative decimal') from exc


def _safe_usage(usage: Optional[Mapping[str, Any]]) -> dict[str, int | float]:
    if not isinstance(usage, Mapping):
        return {}
    safe: dict[str, int | float] = {}
    for raw_key, value in usage.items():
        key = str(raw_key).strip().lower()
        if not key or not key.endswith(_USAGE_SUFFIXES):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            continue
        numeric = float(value) if isinstance(value, (float, Decimal)) else int(value)
        if numeric < 0:
            continue
        safe[key] = numeric
    return safe


def _usage_integer(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def _safe_error_code(value: Optional[str]) -> Optional[str]:
    raw = str(value or '').strip()
    if (
        _URL_RE.search(raw)
        or _BEARER_RE.search(raw)
        or _SENSITIVE_ASSIGNMENT_RE.search(raw)
    ):
        return 'REDACTED'
    normalized = _ERROR_CODE_RE.sub('_', raw)[:64]
    return normalized or None


def _safe_error_summary(value: Optional[str]) -> Optional[str]:
    normalized = str(value or '').replace('\r', ' ').replace('\n', ' ')
    normalized = _URL_RE.sub('[url redacted]', normalized)
    normalized = _BEARER_RE.sub('Bearer [redacted]', normalized)
    normalized = _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f'{match.group(1)}=[redacted]', normalized)
    normalized = ' '.join(normalized.split())[:512]
    return normalized or None


def _duration_ms(started_at: Optional[datetime], completed_at: datetime) -> Optional[int]:
    if started_at is None:
        return None
    normalized_start = (
        started_at
        if started_at.utcoffset() is None
        else started_at.astimezone(timezone.utc).replace(tzinfo=None)
    )
    normalized_completion = (
        completed_at
        if completed_at.utcoffset() is None
        else completed_at.astimezone(timezone.utc).replace(tzinfo=None)
    )
    return max(0, int((normalized_completion - normalized_start).total_seconds() * 1000))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _required_text(value: object, field: str, max_length: int) -> str:
    normalized = str(value or '').strip()
    if not normalized:
        raise ValueError(f'{field} is required')
    if len(normalized) > max_length:
        raise ValueError(f'{field} exceeds {max_length} characters')
    return normalized


def _optional_text(value: object, max_length: int) -> Optional[str]:
    normalized = str(value or '').strip()
    return normalized[:max_length] or None


def _v2_claim_owner(run_id: str, lease_token: str) -> str:
    token_hash = sha256(lease_token.encode('utf-8')).hexdigest()[:16]
    return f'v2:{run_id}:{token_hash}'


def _datetime_boundary(value: date | datetime, *, end: bool) -> datetime:
    if isinstance(value, datetime):
        return value
    boundary = datetime.combine(value, datetime.min.time())
    return boundary + timedelta(days=1) if end else boundary


def _empty_counts() -> dict[str, int]:
    return {
        'event_count': 0,
        'measured_event_count': 0,
        'estimated_event_count': 0,
        'unmetered_event_count': 0,
    }


def _accumulate_counts(counts: dict[str, int], event: CostEvent) -> None:
    counts['event_count'] += 1
    key = f'{event.measurement_status}_event_count'
    counts[key] += 1


def _accumulate_amount(amounts: dict[str, int], event: CostEvent) -> None:
    if event.amount_microunits is None:
        return
    amounts[event.currency] = amounts.get(event.currency, 0) + int(event.amount_microunits)


def _single_currency_total(amounts: Mapping[str, int]) -> Optional[int]:
    if not amounts:
        return 0
    if len(amounts) == 1:
        return next(iter(amounts.values()))
    return None
