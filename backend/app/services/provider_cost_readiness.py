from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.cost_event import CostEvent
from app.models.price_catalog import PriceCatalogEntry
from app.models.provider_call import ProviderCall
from app.schemas.admin import ProviderCostReadinessResponse
from app.services.provider_costs import provider_cost_schema_status
from app.services.provider_price_catalog import official_price_catalog_status


def build_provider_cost_readiness(session: Session) -> ProviderCostReadinessResponse:
    schema_ready = bool(provider_cost_schema_status(session.get_bind())['ready'])
    catalog = {
        'ready': False,
        'expected_count': 0,
        'matching_count': 0,
        'missing': [],
        'conflicts': [],
    }
    counts = {
        'provider_call_count': 0,
        'cost_event_count': 0,
        'price_catalog_entry_count': 0,
        'token_call_count': 0,
        'prompt_tokens': 0,
        'completion_tokens': 0,
        'total_tokens': 0,
    }
    if schema_ready:
        catalog = official_price_catalog_status(session)
        counts = {
            'provider_call_count': _count(session, ProviderCall),
            'cost_event_count': _count(session, CostEvent),
            'price_catalog_entry_count': _count(session, PriceCatalogEntry),
            'token_call_count': int(session.scalar(
                select(func.count()).select_from(ProviderCall).where(
                    ProviderCall.total_tokens.is_not(None),
                )
            ) or 0),
            'prompt_tokens': _sum(session, ProviderCall.prompt_tokens),
            'completion_tokens': _sum(session, ProviderCall.completion_tokens),
            'total_tokens': _sum(session, ProviderCall.total_tokens),
        }

    enabled = bool(settings.provider_cost_facts_enabled)
    catalog_ready = bool(catalog['ready'])
    ready = enabled and schema_ready and catalog_ready
    if not schema_ready:
        status = 'schema_not_ready'
    elif catalog['conflicts']:
        status = 'catalog_conflict'
    elif not catalog_ready:
        status = 'catalog_not_ready'
    elif not enabled:
        status = 'disabled'
    else:
        status = 'ready'

    voice_model = (
        settings.voice_cloning_cosyvoice_target_model
        if settings.voice_cloning_provider == 'cosyvoice'
        else settings.voice_cloning_qwen_target_model
    )
    return ProviderCostReadinessResponse(
        status=status,
        ready_for_recording=ready,
        enabled=enabled,
        schema_ready=schema_ready,
        catalog_ready=catalog_ready,
        actual_bill_connected=False,
        configured_models={
            'subtitle_provider': settings.subtitle_enhancer_provider,
            'subtitle_model': settings.subtitle_enhancer_model,
            'asr_provider': 'dashscope',
            'asr_model': settings.dashscope_asr_model,
            'voice_provider': settings.voice_cloning_provider,
            'voice_model': voice_model,
        },
        expected_price_count=int(catalog['expected_count']),
        matching_price_count=int(catalog['matching_count']),
        missing_prices=list(catalog['missing']),
        conflicting_prices=list(catalog['conflicts']),
        **counts,
    )


def _count(session: Session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _sum(session: Session, column) -> int:
    return int(session.scalar(select(func.coalesce(func.sum(column), 0))) or 0)
