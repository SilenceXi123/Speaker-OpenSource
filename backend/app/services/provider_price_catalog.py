from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cost_event import CostEvent
from app.models.price_catalog import PriceCatalogEntry
from app.models.provider_call import ProviderCall
from app.schemas.admin import PriceCatalogCreateRequest
from app.services.admin_usage import create_price_catalog_version


# 2026-07-21 00:00 Asia/Shanghai, stored as naive UTC like provider call facts.
OFFICIAL_PRICE_EFFECTIVE_FROM = datetime(2026, 7, 20, 16, 0, 0)
DEEPSEEK_PRICE_SOURCE = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing'
DASHSCOPE_PRICE_SOURCE = 'https://help.aliyun.com/zh/model-studio/model-pricing'


def official_price_catalog_entries() -> tuple[PriceCatalogCreateRequest, ...]:
    entries: list[PriceCatalogCreateRequest] = []
    deepseek_meters = (
        ('input_tokens', 1_000_000),
        ('cache_hit_tokens', 20_000),
        ('cache_miss_tokens', 1_000_000),
        ('output_tokens', 2_000_000),
    )
    for operation in ('chat_completion', 'subtitle_window_plan'):
        for meter, unit_price_microunits in deepseek_meters:
            entries.append(PriceCatalogCreateRequest(
                provider='deepseek',
                model='deepseek-v4-flash',
                operation=operation,
                meter=meter,
                effective_from=OFFICIAL_PRICE_EFFECTIVE_FROM,
                billing_unit_size=Decimal('1000000'),
                unit_price_microunits=unit_price_microunits,
                currency='CNY',
                source_note=(
                    f'Official list price retrieved 2026-07-21; {DEEPSEEK_PRICE_SOURCE}'
                ),
            ))
    entries.append(PriceCatalogCreateRequest(
        provider='dashscope',
        model='fun-asr',
        operation='transcription_submit',
        meter='audio_seconds',
        effective_from=OFFICIAL_PRICE_EFFECTIVE_FROM,
        billing_unit_size=Decimal('1'),
        unit_price_microunits=220,
        currency='CNY',
        source_note=(
            f'Official China (Beijing) list price retrieved 2026-07-21; '
            f'{DASHSCOPE_PRICE_SOURCE}'
        ),
    ))
    return tuple(entries)


def official_price_catalog_status(session: Session) -> dict[str, Any]:
    matching: list[str] = []
    missing: list[str] = []
    conflicts: list[str] = []
    expected_entries = official_price_catalog_entries()
    for expected in expected_entries:
        identity = _identity(expected)
        existing = _entry_at_effective_from(session, expected)
        if existing is None:
            missing.append(identity)
        elif _same_price(existing, expected):
            matching.append(identity)
        else:
            conflicts.append(identity)
    return {
        'ready': not missing and not conflicts,
        'effective_from': OFFICIAL_PRICE_EFFECTIVE_FROM.isoformat(),
        'expected_count': len(expected_entries),
        'matching_count': len(matching),
        'missing_count': len(missing),
        'conflict_count': len(conflicts),
        'matching': matching,
        'missing': missing,
        'conflicts': conflicts,
    }


def apply_official_price_catalog(session: Session) -> dict[str, Any]:
    before = official_price_catalog_status(session)
    if before['conflicts']:
        raise ValueError('official price catalog conflicts with existing effective versions')
    inserted = 0
    for expected in official_price_catalog_entries():
        if _entry_at_effective_from(session, expected) is not None:
            continue
        create_price_catalog_version(session, expected)
        inserted += 1
    after = official_price_catalog_status(session)
    return {'inserted_count': inserted, 'before': before, 'after': after}


def backfill_unpriced_catalog_events(session: Session) -> dict[str, int]:
    """Attach current catalog snapshots to old measured events that missed pricing."""
    rows = list(session.execute(
        select(CostEvent, ProviderCall)
        .join(ProviderCall, ProviderCall.id == CostEvent.provider_call_id)
        .where(
            CostEvent.price_catalog_entry_id.is_(None),
            CostEvent.amount_microunits.is_(None),
            CostEvent.cost_basis == 'list_price',
            CostEvent.measurement_status != 'unmetered',
        )
    ))
    updated = 0
    for event, call in rows:
        effective_at = call.started_at or call.completed_at or event.occurred_at
        price = session.scalar(
            select(PriceCatalogEntry)
            .where(
                PriceCatalogEntry.provider == call.provider,
                PriceCatalogEntry.model == call.model,
                PriceCatalogEntry.operation == call.operation,
                PriceCatalogEntry.meter == event.meter,
                PriceCatalogEntry.effective_from <= effective_at,
                (
                    PriceCatalogEntry.effective_to.is_(None)
                    | (PriceCatalogEntry.effective_to > effective_at)
                ),
            )
            .order_by(PriceCatalogEntry.effective_from.desc())
        )
        if price is None:
            continue
        raw_amount = event.quantity / price.billing_unit_size * Decimal(price.unit_price_microunits)
        event.price_catalog_entry_id = price.id
        event.billing_unit_size = price.billing_unit_size
        event.unit_price_microunits = price.unit_price_microunits
        event.amount_microunits = int(raw_amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        event.currency = price.currency
        updated += 1
    session.commit()
    return {'scanned_count': len(rows), 'updated_count': updated}


def _entry_at_effective_from(
    session: Session,
    expected: PriceCatalogCreateRequest,
) -> PriceCatalogEntry | None:
    return session.scalar(select(PriceCatalogEntry).where(
        PriceCatalogEntry.provider == expected.provider,
        PriceCatalogEntry.model == expected.model,
        PriceCatalogEntry.operation == expected.operation,
        PriceCatalogEntry.meter == expected.meter,
        PriceCatalogEntry.effective_from == expected.effective_from,
    ))


def _same_price(
    existing: PriceCatalogEntry,
    expected: PriceCatalogCreateRequest,
) -> bool:
    return (
        existing.effective_to is None
        and existing.billing_unit_size == expected.billing_unit_size
        and existing.unit_price_microunits == expected.unit_price_microunits
        and existing.currency == expected.currency
    )


def _identity(entry: PriceCatalogCreateRequest) -> str:
    return '/'.join((entry.provider, entry.model, entry.operation, entry.meter))
