from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import os
import threading
from time import perf_counter

from app.db.session import SessionLocal
from app.schemas.provider_cost import ProviderCallContext
from app.services.fun_asr import FunAsrClient
from app.services.jobs import get_job, save_transcription_result, update_job_failure, update_job_processing, update_job_processing_metrics, update_speaker_mappings
from app.services.normalize import merge_funasr_payloads
from app.services.subtitle_pipeline_runs import (
    clear_pipeline_run_lease_for_owner,
    get_latest_pipeline_run,
)
from app.services.voiceprint_matching import match_job_voiceprints


_active_transcription_threads: dict[str, threading.Thread] = {}
_transcription_lock = threading.Lock()
logger = logging.getLogger(__name__)


def start_finalize_transcription_job(job_id: str, provider_task_meta: list[dict]) -> bool:
    with _transcription_lock:
        current = _active_transcription_threads.get(job_id)
        if current and current.is_alive():
            return False
        if not _prepare_pipeline_lease_for_finalize_restart(job_id):
            return False
        thread = threading.Thread(
            target=_run_finalize_transcription_job,
            args=(job_id, provider_task_meta),
            daemon=True,
        )
        _active_transcription_threads[job_id] = thread
        thread.start()
        return True


def is_finalize_transcription_job_running(job_id: str) -> bool:
    with _transcription_lock:
        current = _active_transcription_threads.get(job_id)
        return bool(current and current.is_alive())


def _prepare_pipeline_lease_for_finalize_restart(job_id: str) -> bool:
    """Clear only a lease whose owning local process is known to be gone."""
    session = SessionLocal()
    try:
        run = get_latest_pipeline_run(session, job_id)
        owner = str(run.lease_owner or '').strip() if run is not None else ''
        if not owner:
            return True
        if not _can_reclaim_finalize_lease_owner(job_id, owner):
            return False
        cleared = clear_pipeline_run_lease_for_owner(session, run.id, owner=owner)
        if cleared:
            logger.info('Reclaimed orphaned subtitle pipeline lease for job %s', job_id)
        return cleared
    except Exception as exc:  # pragma: no cover - recovery failure remains observable in job state
        logger.warning('Could not prepare subtitle pipeline lease for job %s (%s)', job_id, type(exc).__name__)
        return False
    finally:
        session.close()


def _process_is_alive(process_id: int) -> bool:
    if int(process_id) <= 0:
        return False
    try:
        os.kill(int(process_id), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _can_reclaim_finalize_lease_owner(
    job_id: str,
    owner: str,
    *,
    current_process_id: int | None = None,
    process_is_alive=None,
) -> bool:
    legacy_owner = f'job-finalize:{job_id}'
    normalized_owner = str(owner or '').strip()
    if normalized_owner == legacy_owner:
        # Compatibility for leases written before process-aware owners were added.
        return True
    owner_prefix = f'{legacy_owner}:pid:'
    if not normalized_owner.startswith(owner_prefix):
        return False
    try:
        owner_pid = int(normalized_owner[len(owner_prefix):])
    except ValueError:
        return False
    active_pid = os.getpid() if current_process_id is None else int(current_process_id)
    is_alive = _process_is_alive if process_is_alive is None else process_is_alive
    return owner_pid == active_pid or not is_alive(owner_pid)


def _run_finalize_transcription_job(job_id: str, provider_task_meta: list[dict]) -> None:
    session = SessionLocal()
    finalize_started = perf_counter()
    try:
        current_job = get_job(session, job_id)
        if current_job is None or current_job.status == 'aborted':
            return
        if isinstance(current_job.raw_payload, dict) and current_job.raw_payload.get('_apple_speech'):
            # Device evidence is already stored; never re-submit it to cloud ASR or infer a voice identity.
            save_transcription_result(session, job_id, 'apple-speech:device', current_job.raw_payload)
            return
        client = FunAsrClient()
        provider_context = _fun_asr_provider_context(current_job)
        update_job_processing(session, job_id, 'finalizing', 72, '百炼已完成，正在下载转写结果')
        fetch_started = perf_counter()
        payload_parts: list[tuple[dict, int, int]] = []
        transcription_urls: list[str] = []
        for payload, transcription_url, offset_ms, speaker_offset in _fetch_transcription_payload_parts(
            client,
            provider_task_meta,
            provider_context=provider_context,
        ):
            if _should_stop_finalize_job(session, job_id):
                return
            payload_parts.append((
                payload,
                offset_ms,
                speaker_offset,
            ))
            transcription_urls.append(transcription_url)
        update_job_processing_metrics(session, job_id, {
            'fetch_payload_seconds': perf_counter() - fetch_started,
        })

        if _should_stop_finalize_job(session, job_id):
            return
        update_job_processing(session, job_id, 'finalizing', 84, '已下载转写结果，正在合并时间轴')
        merge_started = perf_counter()
        merged_payload = merge_funasr_payloads(payload_parts)
        transcription_url = transcription_urls[0] if len(transcription_urls) == 1 else '\n'.join(transcription_urls)
        update_job_processing_metrics(session, job_id, {
            'merge_payload_seconds': perf_counter() - merge_started,
        })

        if _should_stop_finalize_job(session, job_id):
            return
        update_job_processing(session, job_id, 'subtitle_enhancing', 92, '正在执行字幕优化与结果入库')
        save_started = perf_counter()
        job = save_transcription_result(
            session=session,
            job_id=job_id,
            transcription_url=transcription_url,
            raw_payload=merged_payload,
        )
        update_job_processing_metrics(session, job_id, {
            'save_result_seconds': perf_counter() - save_started,
        })
        if _should_stop_finalize_job(session, job_id):
            return
        voiceprint_started = perf_counter()
        auto_mappings = match_job_voiceprints(session, job)
        update_job_processing_metrics(session, job_id, {
            'voiceprint_match_seconds': perf_counter() - voiceprint_started,
            'finalize_total_seconds': perf_counter() - finalize_started,
        })
        unmapped_auto_mappings = {
            speaker_key: speaker_name
            for speaker_key, speaker_name in auto_mappings.items()
            if speaker_key not in (job.speaker_mappings or {})
        }
        if unmapped_auto_mappings:
            update_speaker_mappings(session, job.id, unmapped_auto_mappings)
    except Exception as exc:  # pragma: no cover
        if not _should_stop_finalize_job(session, job_id):
            update_job_failure(session, job_id, str(exc))
    finally:
        session.close()
        with _transcription_lock:
            _active_transcription_threads.pop(job_id, None)


def _should_stop_finalize_job(session, job_id: str) -> bool:
    job = get_job(session, job_id)
    return job is None or job.status == 'aborted'


def _fetch_transcription_payload_parts(
    client: FunAsrClient,
    provider_task_meta: list[dict],
    *,
    provider_context: ProviderCallContext | None = None,
) -> list[tuple[dict, str, int, int]]:
    if len(provider_task_meta) <= 1:
        return [
            _fetch_transcription_payload_part(client, provider_task_meta[0], provider_context)
        ] if provider_task_meta else []

    max_workers = min(4, len(provider_task_meta))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(
            lambda entry: _fetch_transcription_payload_part(client, entry, provider_context),
            provider_task_meta,
        ))


def _fetch_transcription_payload_part(
    client: FunAsrClient,
    entry: dict,
    provider_context: ProviderCallContext | None = None,
) -> tuple[dict, str, int, int]:
    transcription_url = str(entry.get('transcription_url') or '').strip()
    if not transcription_url:
        raise RuntimeError('转写结果缺少 transcription_url')
    fetch_kwargs = {}
    if provider_context is not None:
        fetch_kwargs = {
            'provider_context': provider_context,
            'observation_key': (
                f"fun-asr:{provider_context.job_id}:{int(entry.get('chunk_index') or 0)}:result"
            ),
        }
    payload = client.fetch_transcription_payload(transcription_url, **fetch_kwargs)
    return (
        payload,
        transcription_url,
        int(entry.get('offset_ms') or 0),
        int(entry.get('speaker_offset') or 0),
    )


def _fun_asr_provider_context(job) -> ProviderCallContext | None:
    project_id = str(getattr(job, 'project_id', '') or '').strip()
    job_id = str(getattr(job, 'id', '') or '').strip()
    if not project_id or not job_id:
        return None
    return ProviderCallContext(
        project_id=project_id,
        job_id=job_id,
        pipeline_scope='subtitle_legacy',
    )
