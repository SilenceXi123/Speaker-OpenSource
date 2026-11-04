from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_consumer_lock = threading.Lock()
_consumer_stop_event = threading.Event()
_consumer_thread: Optional[threading.Thread] = None
_consumer_state: dict[str, Any] = {
    'running': False,
    'started_at': '',
    'last_message_at': '',
    'last_task_id': '',
    'last_error': '',
}


def start_dashscope_event_consumer() -> bool:
    if not settings.dashscope_rocketmq_enabled or not settings.dashscope_rocketmq_is_configured:
        return False

    with _consumer_lock:
        global _consumer_thread
        if _consumer_thread and _consumer_thread.is_alive():
            return True
        _consumer_stop_event.clear()
        _consumer_thread = threading.Thread(
            target=_run_dashscope_event_consumer,
            name='dashscope-rocketmq-consumer',
            daemon=True,
        )
        _consumer_thread.start()
        return True


def stop_dashscope_event_consumer(timeout_seconds: float = 5.0) -> None:
    with _consumer_lock:
        thread = _consumer_thread
        _consumer_stop_event.set()
    if thread and thread.is_alive():
        thread.join(timeout=timeout_seconds)


def get_dashscope_event_consumer_status() -> dict[str, Any]:
    return {
        **_consumer_state,
        'enabled': bool(settings.dashscope_rocketmq_enabled),
        'configured': settings.dashscope_rocketmq_is_configured,
        'topic': settings.dashscope_rocketmq_topic,
        'consumer_group': settings.dashscope_rocketmq_consumer_group,
    }


def _run_dashscope_event_consumer() -> None:
    consumer = None
    try:
        try:
            from rocketmq import ClientConfiguration, Credentials, FilterExpression, SimpleConsumer
        except Exception as exc:  # pragma: no cover
            _set_consumer_state(running=False, last_error=f'RocketMQ SDK 不可用: {exc}')
            logger.warning('RocketMQ SDK 不可用，未启动 DashScope 事件消费者: %s', exc)
            return

        credentials = (
            Credentials(
                settings.dashscope_rocketmq_access_key.strip(),
                settings.dashscope_rocketmq_access_secret.strip(),
            )
            if settings.dashscope_rocketmq_access_key.strip() and settings.dashscope_rocketmq_access_secret.strip()
            else Credentials()
        )
        if settings.dashscope_rocketmq_instance_id.strip():
            config = ClientConfiguration(
                settings.dashscope_rocketmq_endpoints.strip(),
                credentials,
                settings.dashscope_rocketmq_instance_id.strip(),
            )
        else:
            config = ClientConfiguration(
                settings.dashscope_rocketmq_endpoints.strip(),
                credentials,
            )
        consumer = SimpleConsumer(
            config,
            settings.dashscope_rocketmq_consumer_group.strip(),
            {
                settings.dashscope_rocketmq_topic.strip(): FilterExpression(),
            },
        )
        consumer.startup()
        _set_consumer_state(running=True, started_at=_utc_now_iso(), last_error='')
        logger.info('DashScope RocketMQ 事件消费者已启动')

        while not _consumer_stop_event.is_set():
            try:
                messages = consumer.receive(
                    max(1, int(settings.dashscope_rocketmq_batch_size)),
                    max(15, int(settings.dashscope_rocketmq_invisible_duration_seconds)),
                )
            except Exception as exc:  # pragma: no cover
                _set_consumer_state(last_error=str(exc))
                logger.exception('接收 DashScope RocketMQ 事件失败')
                time.sleep(3)
                continue

            if not messages:
                continue

            for message in messages:
                should_ack = False
                try:
                    payload = _decode_rocketmq_message_body(message)
                    task_id = str((payload.get('data') or {}).get('task_id') or '').strip()
                    result = _dispatch_dashscope_event_payload(payload)
                    _set_consumer_state(
                        last_message_at=_utc_now_iso(),
                        last_task_id=task_id,
                        last_error='',
                    )
                    should_ack = bool(result.get('accepted') or result.get('ignored'))
                except ValueError as exc:
                    logger.warning('忽略无法解析的 DashScope RocketMQ 事件: %s', exc)
                    _set_consumer_state(last_error=str(exc))
                    should_ack = True
                except Exception as exc:  # pragma: no cover
                    _set_consumer_state(last_error=str(exc))
                    logger.exception('处理 DashScope RocketMQ 事件失败')
                finally:
                    if should_ack:
                        try:
                            consumer.ack(message)
                        except Exception as exc:  # pragma: no cover
                            _set_consumer_state(last_error=f'RocketMQ ack 失败: {exc}')
                            logger.exception('确认 DashScope RocketMQ 事件失败')
    finally:
        if consumer is not None:
            try:
                consumer.shutdown()
            except Exception:  # pragma: no cover
                logger.exception('关闭 DashScope RocketMQ 事件消费者失败')
        _set_consumer_state(running=False)


def _dispatch_dashscope_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    from app.api.v1.transcriptions import receive_dashscope_async_finish_event

    return receive_dashscope_async_finish_event(payload)


def _decode_rocketmq_message_body(message: Any) -> dict[str, Any]:
    raw_body = getattr(message, 'body', None)
    if raw_body is None:
        raise ValueError('消息缺少 body')
    if isinstance(raw_body, memoryview):
        raw_body = raw_body.tobytes()
    if isinstance(raw_body, (bytes, bytearray)):
        text = raw_body.decode('utf-8')
    elif isinstance(raw_body, str):
        text = raw_body
    else:
        raise ValueError(f'不支持的消息体类型: {type(raw_body)!r}')

    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError('消息体不是 JSON 对象')
    return payload


def _set_consumer_state(**patch: Any) -> None:
    with _consumer_lock:
        _consumer_state.update(patch)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
