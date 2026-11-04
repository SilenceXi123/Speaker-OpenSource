from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services.deepseek_subtitle_client import DeepSeekSubtitlePlannerClient
from app.services.deepseek_subtitle_contract import (
    DeepSeekCorrectionSummary,
    DeepSeekLanguageProfile,
    DeepSeekLengthProfile,
    DeepSeekPlanGap,
    DeepSeekPlanToken,
    DeepSeekPlanWindow,
)
from app.services.provider_costs import ProviderCallExecutionError
from app.services.subtitle_pipeline_runs import PipelineRunLease


class _Response:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request('POST', 'https://api.deepseek.test/chat/completions')
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError('provider error', request=request, response=response)

    def json(self) -> dict:
        return self.payload


def _window(
    *,
    with_grammar_candidate: bool = False,
    with_script_boundary_hint: bool = False,
) -> DeepSeekPlanWindow:
    words = (
        ('我', 6260, 6420, ''),
        ('叫', 6420, 6660, ''),
        ('于', 6660, 6860, ''),
        ('伟', 6860, 7060, ''),
        ('学', 7060, 7220, '，'),
        ('来自', 7420, 7940, ''),
    )
    return DeepSeekPlanWindow(
        schema_version='deepseek-subtitle-window.v1',
        raw_snapshot_hash='1' * 64,
        window_id='window-' + ('2' * 64),
        utterance_id='utterance-1',
        window_token_start=0,
        window_token_end=len(words),
        core_token_start=0,
        core_token_end=len(words),
        tokens=tuple(
            DeepSeekPlanToken(
                token_id=f'token-{index}',
                token_index=index,
                text=text,
                punctuation=punctuation,
                start_ms=start_ms,
                end_ms=end_ms,
                relative_pause_ms=(None if index == 0 else start_ms - words[index - 1][2]),
                speaker_key='speaker_1',
                in_core=True,
            )
            for index, (text, start_ms, end_ms, punctuation) in enumerate(words)
        ),
        atoms=(),
        gaps=tuple(
            DeepSeekPlanGap(
                gap_id=f'gap-{index}',
                left_token_id=f'token-{index - 1}',
                right_token_id=f'token-{index}',
                left_token_index=index - 1,
                right_token_index=index,
                disposition='allowed',
                legal_gap=True,
                reason='word_timing',
                time_evidence='word_timing',
                relative_pause_ms=words[index][1] - words[index - 1][2],
                script_boundary_strength=(
                    'strong'
                    if with_script_boundary_hint and index == len(words) - 1
                    else None
                ),
                script_boundary_evidence_refs=(
                    (
                        'script-boundary:strong',
                        'script-line-boundary-evidence',
                        f'script-source:{"0" * 64}',
                    )
                    if with_script_boundary_hint and index == len(words) - 1
                    else ()
                ),
                core_owned=True,
            )
            for index in range(1, len(words))
        ),
        correction_candidates=(
            (
                DeepSeekCorrectionSummary(
                    candidate_id='correction-grammar-1',
                    token_ids=('token-1', 'token-2'),
                    source_text='叫于',
                    replacement_text='叫与',
                    candidate_type='grammar',
                    evidence_refs=('candidate-type:grammar', 'grammar-rule:test'),
                    core_owned=True,
                ),
            )
            if with_grammar_candidate
            else ()
        ),
        language_profile=DeepSeekLanguageProfile(
            language='zh',
            display_mode='cjk_character',
        ),
        length_profile=DeepSeekLengthProfile(
            profile_version='subtitle-length.v1',
            mode='smart',
            target_display_units=14,
            soft_max_display_units=16,
        ),
    )


def _lease() -> PipelineRunLease:
    return PipelineRunLease(
        run_id='run-1',
        owner='worker-1',
        lock_version=1,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


def _provider_executor(calls: list[dict]):
    def execute(context, **kwargs):
        call = {'context': context, **kwargs}
        calls.append(call)
        try:
            observation = kwargs['network_call']()
            call['observation'] = observation
            return observation.value
        except Exception as exc:
            raise ProviderCallExecutionError(type(exc).__name__) from None

    return execute


def test_client_sends_only_strict_window_contract_and_records_usage(monkeypatch) -> None:
    sent: list[dict] = []
    provider_calls: list[dict] = []

    def post(url, **kwargs):
        sent.append({'url': url, **kwargs})
        compact_window = json.loads(kwargs['json']['messages'][1]['content'])
        return _Response({
            'id': 'request-1',
            'choices': [{'message': {'content': json.dumps({
                'window_id': compact_window['window_id'],
                'boundary_after_token_indexes': [4],
                'correction_candidate_ids': [],
            })}}],
            'usage': {
                'prompt_tokens': 20,
                'completion_tokens': 5,
                'prompt_cache_hit_tokens': 7,
                'prompt_cache_miss_tokens': 13,
            },
        })

    monkeypatch.setattr(
        'app.services.deepseek_subtitle_client.execute_v2_provider_call',
        _provider_executor(provider_calls),
    )
    client = DeepSeekSubtitlePlannerClient(
        api_key='secret',
        base_url='https://api.deepseek.test',
        model='deepseek-chat',
        post=post,
    )

    result = client.plan_window(
        project_id='project-1',
        job_id='job-1',
        lease=_lease(),
        window=_window(),
    )

    assert result.status == 'accepted'
    assert result.attempts == 1
    assert result.validation is not None
    assert result.validation.boundary_votes[0].left_token_id == 'token-4'
    assert result.validation.boundary_votes[0].right_token_id == 'token-5'
    assert result.validation.boundary_votes[0].rating == 'preferred'
    assert sent[0]['json']['thinking'] == {'type': 'disabled'}
    assert sent[0]['json']['temperature'] == 0
    assert sent[0]['json']['max_tokens'] == 1024
    assert sent[0]['json']['response_format'] == {'type': 'json_object'}
    assert 'EXAMPLE JSON OUTPUT' in sent[0]['json']['messages'][0]['content']
    assert 'complete semantic segmentation' in sent[0]['json']['messages'][0]['content']
    assert 'not limited to grammatical sentences or independent clauses' in (
        sent[0]['json']['messages'][0]['content']
    )
    assert 'without using a character-count target' in (
        sent[0]['json']['messages'][0]['content']
    )
    assert 'duration is at least 500 ms' in sent[0]['json']['messages'][0]['content']
    assert '我叫张三｜来自星河科技' in sent[0]['json']['messages'][0]['content']
    assert 'verify every affiliation lead-in' in (
        sent[0]['json']['messages'][0]['content']
    )
    assert 'final subtitle text' in sent[0]['json']['messages'][0]['content']
    assert 'final timing' in sent[0]['json']['messages'][0]['content']
    assert '的/地/得' in sent[0]['json']['messages'][0]['content']
    compact_window = json.loads(sent[0]['json']['messages'][1]['content'])
    assert set(compact_window) == {
        'window_id',
        'language',
        'tokens',
        'correction_candidates',
        'quality_context',
    }
    assert [token['text'] for token in compact_window['tokens']] == [
        '我', '叫', '于', '伟', '学', '来自',
    ]
    assert compact_window['tokens'][4] == {
        'index': 4,
        'text': '学',
        'punctuation': '，',
        'start_ms': 7060,
        'end_ms': 7220,
        'speaker': 'speaker_1',
        'boundary_after': 'allowed',
        'pause_after_ms': 200,
    }
    assert compact_window['tokens'][5]['start_ms'] == 7420
    assert all(
        'script_boundary_after' not in token
        for token in compact_window['tokens']
    )

    assert provider_calls[0]['idempotency_key'] == (
        f'deepseek-subtitle:run-1:{_window().window_id}'
    )
    assert provider_calls[0]['attempt'] == 1
    assert len(provider_calls[0]['context'].window_id) <= 36
    assert provider_calls[0]['observation'].request_id == 'request-1'
    assert provider_calls[0]['observation'].usage == {
        'prompt_tokens': 20,
        'completion_tokens': 5,
        'prompt_cache_hit_tokens': 7,
        'prompt_cache_miss_tokens': 13,
    }
    assert {
        meter.meter: meter.quantity
        for meter in provider_calls[0]['observation'].meters
    } == {
        'output_tokens': 5,
        'cache_hit_tokens': 7,
        'cache_miss_tokens': 13,
    }


def test_quality_context_uses_real_window_evidence_without_inventing_confidence():
    from app.services.deepseek_subtitle_client import _compact_window_payload

    window = _window()
    tokens = list(window.tokens)
    tokens[2] = tokens[2].model_copy(update={'text': '300元', 'start_ms': None, 'end_ms': None})
    window = window.model_copy(update={'tokens': tuple(tokens)})
    payload = _compact_window_payload(window)
    context = payload['quality_context']
    assert context['scope'] == 'pre_plan_window'
    assert context['truncated'] is False
    numeric = next(issue for issue in context['issues'] if issue['code'] == 'preserve_numeric_fact')
    assert numeric['token_indexes'] == [2]
    assert numeric['source_text'] == '300元'
    assert numeric['start_ms'] is None and numeric['end_ms'] is None
    assert any(issue['code'] == 'missing_word_timing' for issue in context['issues'])
    assert all('confidence' not in issue for issue in context['issues'])


def test_quality_context_is_bounded_per_existing_planner_window():
    from app.services.deepseek_subtitle_client import _compact_window_payload

    window = _window()
    window = window.model_copy(update={'tokens': tuple(
        window.tokens[0].model_copy(update={'token_id': f'token-{index}', 'token_index': index, 'text': '300元'})
        for index in range(80)
    )})
    context = _compact_window_payload(window)['quality_context']
    assert len(context['issues']) <= 40
    assert context['truncated'] is True


def test_client_carries_script_hint_and_preserves_accepted_vote_evidence(
    monkeypatch,
) -> None:
    sent: list[dict] = []
    provider_calls: list[dict] = []

    def post(url, **kwargs):
        sent.append({'url': url, **kwargs})
        compact_window = json.loads(kwargs['json']['messages'][1]['content'])
        return _Response({
            'choices': [{'message': {'content': json.dumps({
                'window_id': compact_window['window_id'],
                'boundary_after_token_indexes': [4],
                'correction_candidate_ids': [],
            })}}],
        })

    monkeypatch.setattr(
        'app.services.deepseek_subtitle_client.execute_v2_provider_call',
        _provider_executor(provider_calls),
    )
    result = DeepSeekSubtitlePlannerClient(
        api_key='secret',
        base_url='https://api.deepseek.test',
        model='deepseek-chat',
        post=post,
    ).plan_window(
        project_id='project-1',
        job_id='job-1',
        lease=_lease(),
        window=_window(with_script_boundary_hint=True),
    )

    assert result.status == 'accepted'
    assert result.validation is not None
    assert result.validation.boundary_votes[0].evidence_refs == (
        'script-boundary:strong',
        'script-line-boundary-evidence',
        f'script-source:{"0" * 64}',
    )
    compact = json.loads(sent[0]['json']['messages'][1]['content'])
    assert compact['tokens'][4]['script_boundary_after'] == 'strong'
    instruction = sent[0]['json']['messages'][0]['content']
    assert 'supporting evidence, not a mandatory cut' in instruction
    assert 'must still return only allowed indexes' in instruction


def test_client_marks_grammar_candidate_in_same_deepseek_request(monkeypatch) -> None:
    sent: list[dict] = []
    provider_calls: list[dict] = []

    def post(url, **kwargs):
        sent.append({'url': url, **kwargs})
        compact_window = json.loads(kwargs['json']['messages'][1]['content'])
        return _Response({
            'choices': [{'message': {'content': json.dumps({
                'window_id': compact_window['window_id'],
                'boundary_after_token_indexes': [],
                'correction_candidate_ids': ['correction-grammar-1'],
            })}}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 5},
        })

    monkeypatch.setattr(
        'app.services.deepseek_subtitle_client.execute_v2_provider_call',
        _provider_executor(provider_calls),
    )
    window = _window(with_grammar_candidate=True)
    result = DeepSeekSubtitlePlannerClient(
        api_key='secret',
        base_url='https://api.deepseek.test',
        model='deepseek-chat',
        post=post,
    ).plan_window(
        project_id='project-1',
        job_id='job-1',
        lease=_lease(),
        window=window,
    )

    assert result.status == 'accepted'
    assert result.validation is not None
    assert result.validation.correction_candidate_ids == ('correction-grammar-1',)
    compact = json.loads(sent[0]['json']['messages'][1]['content'])
    assert compact['correction_candidates'] == [{
        'candidate_id': 'correction-grammar-1',
        'candidate_type': 'grammar',
        'token_indexes': [1, 2],
        'source_text': '叫于',
        'replacement_text': '叫与',
    }]
def test_client_does_not_retry_transport_or_contract_failure(monkeypatch) -> None:
    provider_calls: list[dict] = []
    request_count = 0

    def post(_url, **kwargs):
        nonlocal request_count
        request_count += 1
        raise httpx.ConnectError('offline')

    monkeypatch.setattr(
        'app.services.deepseek_subtitle_client.execute_v2_provider_call',
        _provider_executor(provider_calls),
    )
    client = DeepSeekSubtitlePlannerClient(
        api_key='secret',
        base_url='https://api.deepseek.test',
        model='deepseek-chat',
        post=post,
    )

    degraded = client.plan_window(
        project_id='project-1',
        job_id='job-1',
        lease=_lease(),
        window=_window(),
    )

    assert degraded.status == 'degraded'
    assert degraded.attempts == 1
    assert degraded.failure_code == 'transport_failure'
    assert request_count == 1
    assert [call['attempt'] for call in provider_calls] == [1]

    provider_calls.clear()
    client = DeepSeekSubtitlePlannerClient(
        api_key='secret',
        base_url='https://api.deepseek.test',
        model='deepseek-chat',
        post=lambda *_args, **_kwargs: _Response({
            'choices': [{'message': {'content': 'not-json'}}],
        }),
    )
    rejected = client.plan_window(
        project_id='project-1',
        job_id='job-1',
        lease=_lease(),
        window=_window(),
    )

    assert rejected.status == 'degraded'
    assert rejected.failure_code == 'response_contract_invalid'
    assert rejected.attempts == 1
    assert len(provider_calls) == 1


def test_client_normalizes_harmless_compact_response_variations(monkeypatch) -> None:
    provider_calls: list[dict] = []

    def post(_url, **kwargs):
        compact_window = json.loads(kwargs['json']['messages'][1]['content'])
        return _Response({
            'choices': [{'message': {'content': json.dumps({
                'window_id': compact_window['window_id'],
                'boundary_after_token_indexes': [5, 4, 4],
                'note': 'segmentation complete',
            })}}],
        })

    monkeypatch.setattr(
        'app.services.deepseek_subtitle_client.execute_v2_provider_call',
        _provider_executor(provider_calls),
    )
    result = DeepSeekSubtitlePlannerClient(
        api_key='secret',
        base_url='https://api.deepseek.test',
        model='deepseek-chat',
        post=post,
    ).plan_window(
        project_id='project-1',
        job_id='job-1',
        lease=_lease(),
        window=_window(),
    )

    assert result.status == 'accepted'
    assert result.validation is not None
    assert [
        (vote.left_token_id, vote.right_token_id)
        for vote in result.validation.boundary_votes
    ] == [('token-4', 'token-5')]
    assert result.validation.correction_candidate_ids == ()
    assert len(provider_calls) == 1


def test_client_rejects_missing_run_lease_before_network() -> None:
    client = DeepSeekSubtitlePlannerClient(
        api_key='secret',
        base_url='https://api.deepseek.test',
        model='deepseek-chat',
        post=lambda *_args, **_kwargs: pytest.fail('network must not run'),
    )

    with pytest.raises(ValueError, match='lease'):
        client.plan_window(
            project_id='project-1',
            job_id='job-1',
            lease=None,
            window=_window(),
        )
