from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from app.core.config import settings


class QwenFileTranscriptionError(RuntimeError):
    pass


def fetch_filetrans_reference_sentences(file_url: str) -> list[dict[str, Any]]:
    normalized_file_url = str(file_url or '').strip()
    if not normalized_file_url:
        raise QwenFileTranscriptionError('缺少可用于第二路识别的音频地址')
    if not settings.dashscope_api_key.strip():
        raise QwenFileTranscriptionError('未配置 DASHSCOPE_API_KEY')

    client = QwenFileTranscriptionClient()
    task_id = client.submit_transcription(normalized_file_url)
    payload = client.wait_for_result(task_id)
    sentences = normalize_qwen_filetrans_payload(payload)
    if not sentences:
        raise QwenFileTranscriptionError('第二路识别未返回可用句级结果')
    return sentences


def normalize_qwen_filetrans_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    transcripts = payload.get('transcripts') or []
    if isinstance(transcripts, list):
        candidates.extend(transcripts)
    candidates.append(payload)

    sentences: list[dict[str, Any]] = []
    for transcript in candidates:
        for sentence in transcript.get('sentences') or []:
            text = str(sentence.get('text') or '').strip()
            if not text:
                continue
            sentences.append({
                'startMs': _safe_int(sentence.get('begin_time')),
                'endMs': _safe_int(sentence.get('end_time')),
                'text': text,
            })

    if sentences:
        return sentences

    paragraphs = payload.get('paragraphs') or []
    for paragraph in paragraphs:
        text = str(paragraph.get('text') or '').strip()
        if not text:
            continue
        sentences.append({
            'startMs': _safe_int(paragraph.get('begin_time')),
            'endMs': _safe_int(paragraph.get('end_time')),
            'text': text,
        })
    return sentences


class QwenFileTranscriptionClient:
    def __init__(self) -> None:
        base_url = settings.dashscope_base_url.rstrip('/')
        self.submit_url = f'{base_url}/api/v1/services/audio/asr/transcription'
        self.task_query_base_url = f'{base_url}/api/v1/tasks'
        self.headers = {
            'Authorization': f'Bearer {settings.dashscope_api_key}',
            'Content-Type': 'application/json',
            'X-DashScope-Async': 'enable',
        }

    def submit_transcription(self, file_url: str) -> str:
        response = httpx.post(
            self.submit_url,
            headers=self.headers,
            json={
                'model': settings.subtitle_pro_asr_model,
                'input': {'file_url': file_url},
                'parameters': {
                    'channel_id': [0],
                    'enable_itn': False,
                    'enable_words': True,
                },
            },
            timeout=60,
        )
        data = _read_json_response(response)
        task_id = str((data.get('output') or {}).get('task_id') or '').strip()
        if not task_id:
            raise QwenFileTranscriptionError(f'第二路识别提交返回异常: {data}')
        return task_id

    def wait_for_result(self, task_id: str) -> dict[str, Any]:
        timeout_seconds = max(int(settings.subtitle_pro_asr_timeout_seconds), 30)
        poll_interval = max(float(settings.subtitle_pro_asr_poll_interval_seconds), 1.0)
        deadline = time.monotonic() + timeout_seconds
        last_status = 'PENDING'

        while time.monotonic() < deadline:
            response = httpx.get(
                f'{self.task_query_base_url}/{task_id}',
                headers=self.headers,
                timeout=60,
            )
            data = _read_json_response(response)
            output = data.get('output') or {}
            status = str(output.get('task_status') or '').strip().upper() or 'UNKNOWN'
            last_status = status
            if status in {'PENDING', 'RUNNING'}:
                time.sleep(poll_interval)
                continue
            if status != 'SUCCEEDED':
                raise QwenFileTranscriptionError(f'第二路识别任务状态异常: {status}')

            result = _extract_first_result(output)
            if result.get('subtask_status') not in {None, 'SUCCEEDED'}:
                raise QwenFileTranscriptionError(str(result.get('message') or '第二路识别子任务失败'))

            transcription_url = str(result.get('transcription_url') or '').strip()
            if transcription_url:
                payload_response = httpx.get(transcription_url, timeout=60)
                return _read_json_response(payload_response)

            actual_result = result.get('result')
            if isinstance(actual_result, dict):
                return actual_result
            raise QwenFileTranscriptionError('第二路识别缺少 transcription_url')

        raise QwenFileTranscriptionError(f'第二路识别超时，最后状态: {last_status}')


def _extract_first_result(output: dict[str, Any]) -> dict[str, Any]:
    results = output.get('results') or []
    if isinstance(results, list) and results:
        first_result = results[0]
        if isinstance(first_result, dict):
            return first_result
    return {}


def _safe_int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _read_json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise QwenFileTranscriptionError(f'第二路识别返回了非 JSON 响应: {response.text[:500]}') from exc
    if response.status_code >= 400:
        message = _read_error_message(payload) or '请求失败'
        raise QwenFileTranscriptionError(f'第二路识别请求失败: {message}')
    return payload


def _read_error_message(payload: object) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    for key in ('message', 'code'):
        value = str(payload.get(key) or '').strip()
        if value:
            return value
    output = payload.get('output')
    if isinstance(output, dict):
        for key in ('message', 'task_status'):
            value = str(output.get(key) or '').strip()
            if value:
                return value
    return None
