from __future__ import annotations

import pytest

from app.services.subtitle_linguistic_spans import resolve_linguistic_spans


@pytest.mark.parametrize('number_range', ['3到4厘米', '三到四厘米', '15至50元'])
def test_numeric_range_is_one_protected_span(number_range):
    spans = resolve_linguistic_spans(f'厚度{number_range}左右', include_v2_token_spans=True)
    assert any(span.text == number_range and span.span_type == 'numeric_expression' for span in spans)


def _span_texts(text: str) -> set[str]:
    return {span.text for span in resolve_linguistic_spans(text)}


def _runtime_bundle(term: str, *, normalized: str | None = None, level: str = 'strong') -> dict:
    return {
        'bundle_version': 'runtime_lexicon_bundle.v1',
        'term_set_hash': f'test-{term}-{normalized or term}-{level}',
        'terms': [
            {
                'display_text': term,
                'normalized_term': normalized or term,
                'term_type': 'project_term',
                'protection_level': level,
            }
        ],
    }


def test_resolve_linguistic_spans_detects_corporate_names_without_discourse_prefixes() -> None:
    spans = _span_texts('下各位他们是北京元空智能科技有限公司创始人彭大为')

    assert '北京元空智能科技有限公司' in spans
    assert '下各位他们是北京元空智能科技有限公司' not in spans


def test_resolve_linguistic_spans_detects_corporate_names_after_haiyou_prefix() -> None:
    spans = _span_texts('还有元力无限智能科技杭州有限公司联合创始人刘洋')

    assert '元力无限智能科技杭州有限公司' in spans
    assert '还有元力无限智能科技杭州有限公司' not in spans


def test_resolve_linguistic_spans_prefers_phrase_integrity_over_common_subtokens() -> None:
    spans = _span_texts('用最聪明的大脑去理解创业的魅力')

    assert {'最聪明的大脑', '创业的魅力'}.issubset(spans)
    assert '理解' in spans


def test_common_phrase_span_beats_jieba_sub_tokens() -> None:
    spans = resolve_linguistic_spans('这两位教练呢会教您二位学习一下桨步的动作')

    by_text = {span.text: span for span in spans}
    assert by_text['学习一下'].span_type == 'common_term'
    assert '学习' not in by_text
    assert '一下' not in by_text


def test_resolve_linguistic_spans_detects_generic_phrase_integrity_patterns() -> None:
    spans = _span_texts('刚刚我我毫不犹豫地投下了这一票')
    spans.update(_span_texts('这Peter这首歌是一首很感人的歌有亲人的人都会有这种感触'))
    spans.update(_span_texts('但是我觉得这就是创业的魅力所在'))

    assert '投下了这一票' in spans
    assert '很感人的歌' in spans
    assert '有这种感触' in spans
    assert '创业的魅力' in spans


def test_resolve_linguistic_spans_protects_fixed_production_phrases() -> None:
    spans = resolve_linguistic_spans('现在倒计时开始请放狠话')
    by_text = {span.text: span for span in spans}

    assert by_text['倒计时开始'].span_type == 'phrase_integrity'
    assert by_text['放狠话'].span_type == 'phrase_integrity'


def test_resolve_linguistic_spans_protects_symmetric_pair_terms() -> None:
    spans = resolve_linguistic_spans('空对空系统支持端到端控制和一对一通信')
    by_text = {span.text: span for span in spans}

    assert by_text['空对空'].span_type == 'phrase_integrity'
    assert by_text['端到端'].span_type == 'phrase_integrity'
    assert by_text['一对一'].span_type == 'phrase_integrity'


def test_runtime_soft_span_beats_default_common_word_spans() -> None:
    spans = resolve_linguistic_spans(
        '用最聪明大脑理解创业',
        runtime_lexicon_bundle=_runtime_bundle('聪明大脑', level='soft'),
    )

    by_text = {span.text: span for span in spans}
    assert by_text['聪明大脑'].span_type == 'runtime_lexicon_soft'
    assert '聪明' not in by_text
    assert '大脑' not in by_text


def test_runtime_bundle_tokenizer_emits_project_term_without_global_jieba_word() -> None:
    spans = resolve_linguistic_spans(
        '我们支持蓝鲸智造方案',
        runtime_lexicon_bundle=_runtime_bundle('蓝鲸 智造', normalized='蓝鲸智造', level='strong'),
    )

    assert any(span.text == '蓝鲸智造' for span in spans)


def test_v2_numeric_and_enumeration_spans_are_opt_in_for_shadow_atoms() -> None:
    text = '第一项在2026年7月18日跑3.5公里并完成50万次测试'

    legacy = resolve_linguistic_spans(text)
    v2 = resolve_linguistic_spans(text, include_v2_token_spans=True)

    assert not any(
        span.span_type in {'numeric_expression', 'enumeration_marker'}
        for span in legacy
    )
    assert any(span.span_type == 'enumeration_marker' and span.text == '第一项' for span in v2)
    assert any(span.span_type == 'numeric_expression' and span.text == '2026年7月18日' for span in v2)
    assert any(span.span_type == 'numeric_expression' and span.text == '3.5公里' for span in v2)
    assert any(span.span_type == 'numeric_expression' and span.text == '50万次' for span in v2)
