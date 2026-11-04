from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.schemas.provider_cost import ProviderCallContext
from app.services import cosyvoice_voice_clone as transport
from app.services import qwen_audio_voice_clone as adapter


@pytest.mark.parametrize('model', adapter.QWEN_AUDIO_MODELS)
def test_clone_query_synthesis_and_delete_use_qwen_audio_protocol_and_costs(monkeypatch, model):
    calls, observations = [], []
    responses = iter([
        {'output': {'voice_id': 'new-voice'}, 'usage': {'count': 1}},
        {'output': {'status': 'DEPLOYING'}},
        {'output': {'status': 'OK', 'target_model': model}},
        {'output': {'audio': {'url': 'https://example.test/output.mp3'}},
         'usage': {'characters': 12, 'input_tokens': 7, 'output_tokens': 20}},
        {'output': {}, 'usage': {'count': 1}},
    ])
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: next(responses))
    monkeypatch.setattr(settings, 'voice_cloning_region', 'cn-beijing')
    monkeypatch.setattr(settings, 'dashscope_base_url', 'https://dashscope.aliyuncs.com')
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-key')
    monkeypatch.setattr(transport.httpx, 'post', post)
    monkeypatch.setattr(transport, 'record_legacy_provider_observation',
                        lambda _context, **kwargs: observations.append(kwargs))
    context = {'provider_context': ProviderCallContext(project_id='p', pipeline_scope='project_asset'),
               'observation_key': 'qwen-audio:test'}
    result = adapter.create_qwen_audio_voice(sample_public_url='https://example.test/sample.wav',
        target_model=model, voice_name='样本 Test_0123456789', language_hints=['zh', 'en'], **context)
    voice = result['output']['voice_id']
    assert adapter.query_qwen_audio_voice(voice)['output']['status'] == 'DEPLOYING'
    assert adapter.query_qwen_audio_voice(voice)['output']['status'] == 'OK'
    instruction = '自然亲切，语速稍慢。' * 6
    result = adapter.synthesize_qwen_audio_voice(target_model=model, voice_id=voice,
        text='朗读内容', instruction=instruction, language_hints=['zh', 'en'], **context)
    assert result['output']['audio']['url'] == 'https://example.test/output.mp3'
    assert adapter.delete_qwen_audio_voice(voice, target_model=model, **context)['output'] == {}

    create = calls[0][1]['json']
    assert create['model'] == 'voice-enrollment'
    assert create['input']['action'] == 'create_voice'
    assert create['input']['target_model'] == model
    assert create['input']['prefix'] == 'test012345'
    assert create['input']['language_hints'] == ['zh']
    assert 3 <= create['input']['max_prompt_audio_length'] <= 30
    for url, call in calls:
        assert url.startswith('https://dashscope.aliyuncs.com/api/v1/services/audio/tts/')
        assert call['headers']['Authorization'] == 'Bearer test-key'
    assert calls[1][1]['timeout'] <= 10
    assert calls[1][1]['json']['input'] == {'action': 'query_voice', 'voice_id': voice}
    assert calls[3][0].endswith('/SpeechSynthesizer')
    assert calls[3][1]['json'] == {'model': model, 'input': {
        'text': '朗读内容', 'voice': voice, 'format': 'mp3', 'sample_rate': 22050,
        'language_hints': ['zh'], 'instruction': instruction}}
    assert calls[4][1]['json']['input'] == {'action': 'delete_voice', 'voice_id': voice}
    assert all(item['provider'] == 'qwen_audio' and item['model'] == model for item in observations)
    assert [item['operation'] for item in observations] == ['create_voice', 'synthesize_voice', 'delete_voice']
    assert observations[0]['observed_call'].meters[0].quantity == 1
    assert observations[1]['observed_call'].usage == {'characters': 12, 'input_tokens': 7, 'output_tokens': 20}
    assert observations[2]['observed_call'].usage is None  # delete count is not a created voice


@pytest.mark.parametrize('region,model,instruction,message', [
    ('intl', adapter.QWEN_AUDIO_MODELS[0], '亲切', '北京'),
    ('cn-beijing', 'qwen3-tts-vc-2026-01-22', '亲切', '模型'),
    ('cn-beijing', adapter.QWEN_AUDIO_MODELS[0], '汉' * 1601, '1600'),
])
def test_invalid_request_fails_before_http(monkeypatch, region, model, instruction, message):
    monkeypatch.setattr(settings, 'voice_cloning_region', region)
    monkeypatch.setattr(transport.httpx, 'post', lambda *a, **k: pytest.fail('invalid request reached HTTP'))
    with pytest.raises(adapter.QwenAudioVoiceCloneError, match=message):
        adapter.synthesize_qwen_audio_voice(target_model=model, voice_id='voice', text='text', instruction=instruction)


@pytest.mark.parametrize('body,status,code', [
    ({'code': 'InvalidParameter', 'message': 'instruction rejected'}, 400, 'InvalidParameter'),
    ({'code': 'QuotaExceeded', 'message': 'quota'}, 200, 'QuotaExceeded'),
    ([], 200, None),
])
def test_provider_failure_is_preserved_and_never_retried_without_instruction(monkeypatch, body, status, code):
    calls, observations = [], []
    def post(url, **kwargs):
        calls.append(kwargs['json'])
        return SimpleNamespace(status_code=status, text='failure', json=lambda: body)
    monkeypatch.setattr(settings, 'voice_cloning_region', 'cn-beijing')
    monkeypatch.setattr(transport.httpx, 'post', post)
    monkeypatch.setattr(transport, 'record_legacy_provider_observation',
                        lambda _context, **kwargs: observations.append(kwargs))
    with pytest.raises(adapter.QwenAudioVoiceCloneError) as error:
        adapter.synthesize_qwen_audio_voice(target_model=adapter.QWEN_AUDIO_MODELS[0],
            voice_id='voice', text='text', instruction='亲切', observation_key='test:error',
            provider_context=ProviderCallContext(project_id='p', pipeline_scope='project_asset'))
    assert error.value.code == code
    assert len(calls) == 1
    assert calls[0]['input']['instruction'] == '亲切'
    assert observations[0]['provider'] == 'qwen_audio'
    assert observations[0]['observed_call'].status == 'failed'
