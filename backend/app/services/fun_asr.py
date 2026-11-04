from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import logging
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.db.session import SessionLocal
from app.schemas.provider_cost import ProviderCallContext, ProviderCallObservation, ProviderMeterObservation
from app.services.provider_costs import execute_v2_provider_call, record_legacy_provider_observation
from app.services.subtitle_pipeline_runs import (
    PipelineRunLease,
    assert_pipeline_run_lease,
)


logger = logging.getLogger(__name__)


class FunAsrError(RuntimeError):
    pass


class FunAsrClient:
    def __init__(self) -> None:
        if not settings.dashscope_api_key:
            raise FunAsrError('未配置 DASHSCOPE_API_KEY')
        self.base_url = settings.dashscope_base_url.rstrip('/')
        self.headers = {
            'Authorization': f'Bearer {settings.dashscope_api_key}',
            'Content-Type': 'application/json',
            'X-DashScope-Async': 'enable',
        }

    def submit_transcription(
        self,
        file_url: str,
        language_hints: Optional[list[str]],
        speaker_count: Optional[int],
        vocabulary_id: Optional[str] = None,
        *,
        provider_context: Optional[ProviderCallContext] = None,
        observation_key: Optional[str] = None,
        attempt: int = 1,
        audio_seconds: Optional[float] = None,
    ) -> dict[str, Any]:
        if provider_context is not None and provider_context.pipeline_scope == 'subtitle_v2':
            raise ValueError('subtitle_v2 must use submit_transcription_v2')
        payload = self._build_transcription_payload(
            file_url=file_url,
            language_hints=language_hints,
            speaker_count=speaker_count,
            vocabulary_id=vocabulary_id,
        )

        started_at = _utcnow()
        try:
            response = httpx.post(
                f'{self.base_url}/api/v1/services/audio/asr/transcription',
                headers=self.headers,
                json=payload,
                timeout=60,
            )
            data = self._read_json(response)
            result = {
                'task_id': data['output']['task_id'],
                'request_id': data.get('request_id'),
            }
        except Exception as exc:
            _record_observation(
                provider_context,
                operation='transcription_submit',
                observation_key=observation_key,
                attempt=attempt,
                status='failed',
                started_at=started_at,
                error=exc,
            )
            if isinstance(exc, KeyError):
                raise FunAsrError(f'百炼提交任务返回异常: {data}') from exc
            raise

        quantity = _audio_seconds_quantity(audio_seconds)
        _record_observation(
            provider_context,
            operation='transcription_submit',
            observation_key=observation_key,
            attempt=attempt,
            status='completed',
            request_id=result.get('request_id'),
            usage={'audio_seconds': float(quantity)} if quantity is not None else None,
            meters=[
                ProviderMeterObservation(
                    meter='audio_seconds',
                    quantity=quantity,
                    unit='second',
                )
            ],
            started_at=started_at,
            value=result,
        )
        return result

    def submit_transcription_v2(
        self,
        file_url: str,
        language_hints: Optional[list[str]],
        speaker_count: Optional[int],
        *,
        provider_context: ProviderCallContext,
        run_lease: PipelineRunLease,
        chunk_index: int,
        vocabulary_id: Optional[str] = None,
        attempt: int = 1,
        audio_seconds: Optional[float] = None,
    ) -> dict[str, Any]:
        context = ProviderCallContext.model_validate(provider_context)
        if context.pipeline_scope != 'subtitle_v2' or context.run_id != run_lease.run_id:
            raise ValueError('V2 Fun-ASR context and run lease do not match')
        if int(chunk_index) < 0:
            raise ValueError('chunk_index must be nonnegative')
        if int(attempt) < 1:
            raise ValueError('attempt must be at least 1')
        with SessionLocal() as run_session:
            assert_pipeline_run_lease(
                run_session,
                run_lease,
                project_id=context.project_id,
                job_id=context.job_id,
            )

        idempotency_key = f'fun-asr-v2:{run_lease.run_id}:{int(chunk_index)}'
        payload = self._build_transcription_payload(
            file_url=file_url,
            language_hints=language_hints,
            speaker_count=speaker_count,
            vocabulary_id=vocabulary_id,
        )

        def network_call() -> ProviderCallObservation:
            started_at = _utcnow()
            response = httpx.post(
                f'{self.base_url}/api/v1/services/audio/asr/transcription',
                headers=self.headers,
                json=payload,
                timeout=60,
            )
            data = self._read_json(response)
            result = {
                'task_id': data['output']['task_id'],
                'request_id': data.get('request_id'),
            }
            quantity = _audio_seconds_quantity(audio_seconds)
            return ProviderCallObservation(
                idempotency_key=idempotency_key,
                attempt=int(attempt),
                status='completed',
                request_id=result.get('request_id'),
                usage={'audio_seconds': float(quantity)} if quantity is not None else None,
                meters=[
                    ProviderMeterObservation(
                        meter='audio_seconds',
                        quantity=quantity,
                        unit='second',
                    )
                ],
                started_at=started_at,
                completed_at=_utcnow(),
                value=result,
            )

        return execute_v2_provider_call(
            context,
            run_lease_fencing_token=run_lease.fencing_token,
            provider='dashscope',
            model=settings.dashscope_asr_model,
            operation='transcription_submit',
            idempotency_key=idempotency_key,
            attempt=int(attempt),
            network_call=network_call,
        )

    def query_task(self, task_id: str) -> dict[str, Any]:
        response = httpx.get(
            f'{self.base_url}/api/v1/tasks/{task_id}',
            headers=self.headers,
            timeout=60,
        )
        return self._read_json(response)

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        response = httpx.post(
            f'{self.base_url}/api/v1/tasks/{task_id}/cancel',
            headers=self.headers,
            timeout=60,
        )
        return self._read_json(response)

    def fetch_transcription_payload(
        self,
        transcription_url: str,
        *,
        provider_context: Optional[ProviderCallContext] = None,
        observation_key: Optional[str] = None,
    ) -> dict[str, Any]:
        started_at = _utcnow()
        try:
            response = httpx.get(transcription_url, timeout=60)
            result = self._read_json(response)
        except Exception as exc:
            _record_observation(
                provider_context,
                operation='transcription_result_fetch',
                observation_key=observation_key,
                attempt=1,
                status='failed',
                started_at=started_at,
                error=exc,
            )
            raise
        _record_observation(
            provider_context,
            operation='transcription_result_fetch',
            observation_key=observation_key,
            attempt=1,
            status='completed',
            started_at=started_at,
        )
        return result

    @staticmethod
    def _build_transcription_payload(
        *,
        file_url: str,
        language_hints: Optional[list[str]],
        speaker_count: Optional[int],
        vocabulary_id: Optional[str],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'model': settings.dashscope_asr_model,
            'input': {'file_urls': [file_url]},
            'parameters': {
                'channel_id': [0],
                'diarization_enabled': True,
            },
        }
        if language_hints:
            payload['parameters']['language_hints'] = language_hints
        if speaker_count:
            payload['parameters']['speaker_count'] = speaker_count
        if vocabulary_id:
            payload['parameters']['vocabulary_id'] = vocabulary_id
        return payload

    @staticmethod
    def _read_json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise FunAsrError(f'百炼返回了非 JSON 响应: {response.text[:500]}') from exc
        if response.status_code >= 400:
            message = data.get('message') or data.get('code') or '请求失败'
            raise FunAsrError(f'百炼请求失败: {message}')
        return data


def _record_observation(
    context: Optional[ProviderCallContext],
    *,
    operation: str,
    observation_key: Optional[str],
    attempt: int,
    status: str,
    request_id: Optional[str] = None,
    usage: Optional[dict[str, Any]] = None,
    meters: Optional[list[ProviderMeterObservation]] = None,
    started_at: Optional[datetime] = None,
    error: Optional[Exception] = None,
    value: Any = None,
) -> None:
    if context is None or not observation_key:
        return
    try:
        record_legacy_provider_observation(
            context,
            provider='dashscope',
            model=settings.dashscope_asr_model,
            operation=operation,
            observed_call=ProviderCallObservation(
                idempotency_key=observation_key,
                attempt=attempt,
                status=status,
                request_id=request_id,
                usage=usage,
                meters=meters or [],
                started_at=started_at,
                completed_at=_utcnow(),
                error_code=type(error).__name__ if error else None,
                error_summary=str(error) if error else None,
                value=value,
            ),
        )
    except Exception as exc:  # pragma: no cover - observation must stay fail-open
        logger.warning('Fun-ASR provider observation skipped (%s)', type(exc).__name__)


def _audio_seconds_quantity(value: Optional[float]) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return quantity if quantity.is_finite() and quantity >= 0 else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
