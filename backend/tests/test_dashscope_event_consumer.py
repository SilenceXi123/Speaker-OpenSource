import json

from app.core.config import settings
from app.services.dashscope_event_consumer import (
    _decode_rocketmq_message_body,
    get_dashscope_event_consumer_status,
    start_dashscope_event_consumer,
)


class _FakeMessage:
    def __init__(self, body):
        self.body = body


def test_decode_rocketmq_message_body_reads_json_bytes() -> None:
    payload = {'type': 'dashscope:System:AsyncTaskFinish', 'data': {'task_id': 'task-1'}}
    message = _FakeMessage(json.dumps(payload).encode('utf-8'))

    result = _decode_rocketmq_message_body(message)

    assert result == payload


def test_decode_rocketmq_message_body_rejects_non_object_json() -> None:
    message = _FakeMessage(json.dumps(['not-an-object']).encode('utf-8'))

    try:
        _decode_rocketmq_message_body(message)
    except ValueError as error:
        assert str(error) == '消息体不是 JSON 对象'
    else:
        raise AssertionError('expected ValueError')


def test_start_dashscope_event_consumer_returns_false_without_config(monkeypatch) -> None:
    original_values = {
        'dashscope_rocketmq_enabled': settings.dashscope_rocketmq_enabled,
        'dashscope_rocketmq_endpoints': settings.dashscope_rocketmq_endpoints,
        'dashscope_rocketmq_topic': settings.dashscope_rocketmq_topic,
        'dashscope_rocketmq_consumer_group': settings.dashscope_rocketmq_consumer_group,
    }
    try:
        monkeypatch.setattr(settings, 'dashscope_rocketmq_enabled', False)
        monkeypatch.setattr(settings, 'dashscope_rocketmq_endpoints', '')
        monkeypatch.setattr(settings, 'dashscope_rocketmq_topic', '')
        monkeypatch.setattr(settings, 'dashscope_rocketmq_consumer_group', '')
        assert start_dashscope_event_consumer() is False
        status = get_dashscope_event_consumer_status()
        assert status['enabled'] is False
        assert status['configured'] is False
    finally:
        for key, value in original_values.items():
            setattr(settings, key, value)
