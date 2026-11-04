from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services import cosyvoice_voice_clone as transport
from app.services import qwen_audio_next_voice_clone as adapter


def test_next_uses_reference_audio_and_prompt_with_workspace_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, 'sfm_workspace_id', 'speaker-space')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(settings, 'voice_cloning_region', 'cn-beijing')

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {'request_id': 'req-test', 'output': {
            'finish_reason': 'stop', 'audio': {'url': 'https://example.test/result.wav', 'duration': 2.3}}, 'usage': {'duration': 2}})

    monkeypatch.setattr(transport.httpx, 'post', post)
    result = adapter.synthesize_qwen_audio_next_voice(sample_public_url='https://example.test/reference.wav',
        text='今天你好。', instruction='温柔自然')
    assert result['output']['audio']['url'] == 'https://example.test/result.wav'
    url, request = calls[0]
    assert url == 'https://speaker-space.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer'
    assert request['headers']['Authorization'] == 'Bearer test-key'
    assert request['json']['model'] == 'qwen-audio-3.1-tts-next'
    body = request['json']['input']
    assert body['references'] == [{'audio_url': 'https://example.test/reference.wav'}]
    assert '@voice1' in body['text_prompt'] and '今天你好。' in body['text_prompt'] and '温柔自然' in body['text_prompt']
    assert body['format'] == 'wav' and body['sample_rate'] == 24000
    assert 'voice' not in body and 'instruction' not in body
    assert len(calls) == 1


@pytest.mark.parametrize('workspace,text,reference', [('', '你好', 'https://example.test/a.wav'),
    ('https://wrong.test', '你好', 'https://example.test/a.wav'),
    ('speaker-space', '汉' * 3001, 'https://example.test/a.wav'),
    ('speaker-space', '你好', '')])
def test_invalid_next_request_never_reaches_provider(monkeypatch, workspace, text, reference):
    monkeypatch.setattr(settings, 'sfm_workspace_id', workspace)
    monkeypatch.setattr(transport.httpx, 'post', lambda *a, **k: pytest.fail('invalid request reached HTTP'))
    with pytest.raises(RuntimeError):
        adapter.synthesize_qwen_audio_next_voice(sample_public_url=reference, text=text)
