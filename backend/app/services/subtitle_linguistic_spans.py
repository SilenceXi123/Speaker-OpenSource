from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from app.services.lexicon_tokenizer import build_jieba_tokenizer
from app.services.subtitle_protected_terms import normalize_runtime_lexicon_bundle, runtime_lexicon_terms


ASCII_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[._+#/'’-][A-Za-z0-9]+)*")
SELF_INTRO_ALIAS_NAME_RE = re.compile(
    r"(?:大家好\s*[，,、]?\s*)?(?:我叫(?:做)?|我是)"
    r"\s*[A-Za-z][A-Za-z0-9._+#/'’-]{0,31}"
    r"\s*[，,、]\s*[\u3400-\u9fff]{2,4}(?=[，,。！？!?、\s]|$)"
)
DATE_EXPRESSION_RE = re.compile(
    r'(?:19|20)\d{2}年(?:1[0-2]|0?[1-9])月(?:(?:3[01]|[12]\d|0?[1-9])日)?'
)
NUMBER_UNIT_RE = re.compile(
    r'\d+(?:\.\d+)?'
    r'(?:%|％|kg|km|cm|mm|°C|℃|g|m|'
    r'[十百千万亿]?(?:公里|千米|厘米|毫米|米|公斤|千克|克|吨|分钟|小时|秒|元|年|月|日|岁|人|个|次|集|期))'
)
_NUMERIC_VALUE = r'(?:\d+(?:[.,]\d+)*|[零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟]+(?:点[零〇一二两三四五六七八九\d]+)?)'
_NUMERIC_FACT_RE = re.compile(
    rf'(?:百分之)?[正负+\-]?{_NUMERIC_VALUE}'
    rf'(?:(?:到|至|[-~～—/:：]){_NUMERIC_VALUE})*'
    r'(?:%|％|kg|km|cm|mm|°C|℃|g|m|公里|千米|厘米|毫米|米|公斤|千克|克|吨|分钟|小时|秒|元|年|月|日|岁|人|个|次|集|期)?'
)


def numeric_fact_signature(text: str) -> tuple[str, ...]:
    # ponytail: preserve numeric spelling too; equivalence-aware ITN needs separate audited rules.
    return tuple(_NUMERIC_FACT_RE.findall(re.sub(r'\s+', '', str(text or ''))))


SYMMETRIC_PAIR_RE = re.compile(
    r'(?P<unit>[\u3400-\u9fff]{1,2}?)(?:对|到)(?P=unit)'
)
ENUMERATION_RE = re.compile(
    r'第(?:[一二三四五六七八九十百千万]+|[0-9]+)(?:项|点|个|条|步|部分|章|节)?'
)
CORPORATE_SUFFIXES = (
    '有限责任公司',
    '股份有限公司',
    '集团有限公司',
    '有限公司',
)
ORG_CONTEXT_TRIGGERS = (
    '我是来自于',
    '我是来自',
    '来自于',
    '来自',
    '他们是',
    '我们是',
    '还有',
    '以及',
    '包括',
    '属于',
    '服务于',
    '任职于',
    '就职于',
    '是',
)
ORG_LEADING_TRIM_PREFIXES = (
    '下各位',
    '各位',
    '大家',
    '他们',
    '我们',
    '这个',
    '那个',
    '还有',
    '以及',
    '包括',
    '和',
    '与',
    '及',
    '是',
)
ORG_REQUIRED_MARKERS = (
    '人工智能',
    '智能科技',
    '教育科技',
    '信息技术',
    '网络科技',
    '文化传媒',
    '生物科技',
    '数智',
    '科技',
)
COMMON_CJK_TERMS = tuple(
    sorted(
        {
            '工作人员',
            '联合创始人',
            '创始人',
            '人工智能',
            '智能科技',
            '教育科技',
            '信息技术',
            '网络科技',
            '文化传媒',
            '生物科技',
            '科技有限公司',
            '有限责任公司',
            '股份有限公司',
            '集团有限公司',
            '有限公司',
            '原力无限',
            '元力无限',
            '外号',
            '聪明',
            '创业',
            '大脑',
            '机器人',
            '力控',
            '魅力',
            '行业',
            '观察',
            '慢慢',
            '璀璨',
            '非正式',
            '会议',
            '会谈',
            '当中',
            '产生',
            '东北',
            '平原',
            '绿色',
            '明珠',
            '赛艇中心',
            '国际赛艇中心',
            '亚洲青年赛艇锦标赛',
            '沈阳赛艇国际公开赛',
            '赛艇锦标赛',
            '国际公开赛',
            '蓝海数智体验馆',
            '体验馆',
            '工作室',
            '研究院',
            '实验室',
            '委员会',
            '协会',
            '主持人',
            '负责人',
            '发言人',
            '制作人',
            '出品人',
            '投资人',
            '合伙人',
            '候选人',
            '年轻人',
            '每一个',
            '有趣',
            '真正',
            '成为',
            '这样',
            '这种',
            '没有',
            '门槛',
            '未来',
            '现场',
            '大家',
            '我们',
            '你们',
            '他们',
            '来自',
            '学习一下',
            '希望',
            '做到',
            '所在',
        },
        key=len,
        reverse=True,
    )
)
FIXED_PHRASE_INTEGRITY_TERMS = (
    '倒计时开始',
    '放狠话',
)
DEGREE_ADVERBS = (
    '非常',
    '特别',
    '比较',
    '十分',
    '格外',
    '很',
    '挺',
    '更',
    '最',
    '太',
)
DEMONSTRATIVE_PREFIXES = (
    '这一',
    '这个',
    '这种',
    '这样',
    '这些',
    '那一',
    '那个',
    '那种',
    '那样',
    '那些',
    '一',
)
PHRASE_LEFT_BOUNDARY_CHARS = set('，,。！？!?；;：:、 \t\n\r的地得')
PHRASE_RIGHT_BOUNDARY_CHARS = set('，,。！？!?；;：:、 \t\n\r但并而却也又再还会都就有是')
PHRASE_TAIL_FILLER_CHARS = set('啊呀呢吧嘛哦')
DEGREE_LEFT_CONTEXT_CHARS = set('个种位些点段颗件份条张部首用')
EXISTENTIAL_LEFT_CONTEXT_CHARS = set('会都也就还能可要想')
DE_PHRASE_RIGHT_HEADS = {
    '魅力',
    '感触',
    '观点',
    '好处',
    '价值',
    '意义',
    '特点',
    '能力',
    '方式',
    '原因',
    '问题',
    '机会',
}


@dataclass(frozen=True)
class LinguisticSpan:
    start: int
    end: int
    text: str
    span_type: str


def resolve_linguistic_spans(
    text: str,
    *,
    protected_terms: Iterable[str] = (),
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    include_v2_token_spans: bool = False,
) -> tuple[LinguisticSpan, ...]:
    value = str(text or '')
    if not value:
        return ()

    candidates: list[LinguisticSpan] = []
    candidates.extend(_iter_protected_term_spans(value, protected_terms))
    candidates.extend(_iter_runtime_lexicon_spans(value, runtime_lexicon_bundle))
    candidates.extend(_iter_corporate_org_spans(value))
    candidates.extend(_iter_phrase_integrity_spans(value))
    if include_v2_token_spans:
        candidates.extend(_iter_numeric_expression_spans(value))
        candidates.extend(_iter_enumeration_spans(value))
    candidates.extend(_iter_optional_cws_spans(value, runtime_lexicon_bundle))
    candidates.extend(_iter_common_term_spans(value))
    candidates.extend(_iter_ascii_word_spans(value))
    candidates.extend(_iter_reduplication_spans(value))
    return _select_non_overlapping_spans(candidates)


def _iter_runtime_lexicon_spans(
    text: str,
    runtime_lexicon_bundle: Optional[dict[str, Any]],
) -> list[LinguisticSpan]:
    spans: list[LinguisticSpan] = []
    for level in ('hard', 'strong', 'soft'):
        for term in runtime_lexicon_terms(runtime_lexicon_bundle, protection_levels=(level,)):
            if len(term) < 2:
                continue
            start = text.find(term)
            while start != -1:
                spans.append(LinguisticSpan(start, start + len(term), term, f'runtime_lexicon_{level}'))
                start = text.find(term, start + 1)
    return spans


def _iter_protected_term_spans(text: str, protected_terms: Iterable[str]) -> list[LinguisticSpan]:
    spans: list[LinguisticSpan] = []
    for term in sorted({str(item or '').strip() for item in protected_terms}, key=len, reverse=True):
        if len(term) < 2:
            continue
        start = text.find(term)
        while start != -1:
            spans.append(LinguisticSpan(start, start + len(term), term, 'protected_term'))
            start = text.find(term, start + 1)
    return spans


def _iter_corporate_org_spans(text: str) -> list[LinguisticSpan]:
    spans: list[LinguisticSpan] = []
    for suffix in CORPORATE_SUFFIXES:
        start = text.find(suffix)
        while start != -1:
            suffix_end = start + len(suffix)
            span_start = _corporate_org_start(text, start)
            if span_start is not None:
                candidate = text[span_start:suffix_end]
                if _is_valid_corporate_org(candidate, suffix):
                    spans.append(LinguisticSpan(span_start, suffix_end, candidate, 'organization'))
            start = text.find(suffix, start + 1)
    return spans


def _iter_phrase_integrity_spans(text: str) -> list[LinguisticSpan]:
    spans = [
        LinguisticSpan(match.start(), match.end(), match.group(0), 'phrase_integrity')
        for match in SELF_INTRO_ALIAS_NAME_RE.finditer(text)
    ]
    spans.extend(_iter_verb_aspect_object_spans(text))
    spans.extend(_iter_verb_yixia_spans(text))
    spans.extend(_iter_degree_noun_phrase_spans(text))
    spans.extend(_iter_existential_demonstrative_spans(text))
    spans.extend(_iter_common_de_phrase_spans(text))
    spans.extend(
        LinguisticSpan(match.start(), match.end(), match.group(0), 'phrase_integrity')
        for match in SYMMETRIC_PAIR_RE.finditer(text)
    )
    for term in FIXED_PHRASE_INTEGRITY_TERMS:
        start = text.find(term)
        while start != -1:
            spans.append(LinguisticSpan(start, start + len(term), term, 'phrase_integrity'))
            start = text.find(term, start + 1)
    return spans


def _iter_verb_aspect_object_spans(text: str) -> list[LinguisticSpan]:
    spans: list[LinguisticSpan] = []
    for aspect_index, char in enumerate(text):
        if char not in '了着过':
            continue
        verb_start = _verb_phrase_start(text, aspect_index)
        if verb_start is None:
            continue
        object_end = _object_phrase_end(text, aspect_index + 1)
        if object_end is None:
            continue
        spans.append(LinguisticSpan(verb_start, object_end, text[verb_start:object_end], 'phrase_integrity'))
    return spans


def _iter_verb_yixia_spans(text: str) -> list[LinguisticSpan]:
    spans: list[LinguisticSpan] = []
    start = text.find('一下')
    while start != -1:
        verb_start = _verb_phrase_start(text, start)
        if verb_start is not None:
            spans.append(LinguisticSpan(verb_start, start + 2, text[verb_start:start + 2], 'phrase_integrity'))
        start = text.find('一下', start + 1)
    return spans


def _iter_degree_noun_phrase_spans(text: str) -> list[LinguisticSpan]:
    spans: list[LinguisticSpan] = []
    for adverb in DEGREE_ADVERBS:
        start = text.find(adverb)
        while start != -1:
            span = _degree_noun_phrase_at(text, start, adverb)
            if span is not None:
                spans.append(span)
            start = text.find(adverb, start + 1)
    return spans


def _iter_existential_demonstrative_spans(text: str) -> list[LinguisticSpan]:
    spans: list[LinguisticSpan] = []
    for start, char in enumerate(text):
        if char != '有' or not _has_existential_left_boundary(text, start):
            continue
        end = _object_phrase_end(text, start + 1)
        if end is None:
            continue
        spans.append(LinguisticSpan(start, end, text[start:end], 'phrase_integrity'))
    return spans


def _iter_common_de_phrase_spans(text: str) -> list[LinguisticSpan]:
    spans: list[LinguisticSpan] = []
    terms = [term for term in COMMON_CJK_TERMS if len(term) >= 2]
    for left in terms:
        start = text.find(left)
        while start != -1:
            middle = start + len(left)
            if middle < len(text) and text[middle] == '的':
                for right in terms:
                    if right not in DE_PHRASE_RIGHT_HEADS:
                        continue
                    phrase_end = middle + 1 + len(right)
                    if text.startswith(right, middle + 1):
                        spans.append(LinguisticSpan(start, phrase_end, text[start:phrase_end], 'phrase_integrity'))
                        break
            start = text.find(left, start + 1)
    return spans


def _corporate_org_start(text: str, suffix_start: int) -> int | None:
    block_start = suffix_start
    while block_start > 0 and _is_cjk_char(text[block_start - 1]):
        block_start -= 1
    block_start = max(block_start, suffix_start - 24)
    candidate_prefix = text[block_start:suffix_start]
    local_start = 0

    for trigger in ORG_CONTEXT_TRIGGERS:
        pos = candidate_prefix.rfind(trigger)
        if pos != -1:
            local_start = max(local_start, pos + len(trigger))

    candidate = candidate_prefix[local_start:]
    changed = True
    while changed:
        changed = False
        for prefix in ORG_LEADING_TRIM_PREFIXES:
            if candidate.startswith(prefix) and len(candidate) > len(prefix) + 1:
                local_start += len(prefix)
                candidate = candidate[len(prefix):]
                changed = True
                break

    if not candidate:
        return None
    return block_start + local_start


def _is_valid_corporate_org(candidate: str, suffix: str) -> bool:
    if len(candidate) < len(suffix) + 2 or len(candidate) > 24:
        return False
    prefix = candidate[:-len(suffix)]
    if len(prefix) < 2:
        return False
    if any(marker in candidate for marker in ORG_REQUIRED_MARKERS):
        return True
    return len(prefix) >= 4


def _iter_optional_cws_spans(
    text: str,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> list[LinguisticSpan]:
    try:
        if runtime_lexicon_bundle is not None:
            tokenizer = build_jieba_tokenizer(normalize_runtime_lexicon_bundle(runtime_lexicon_bundle))
        else:
            import jieba  # type: ignore

            tokenizer = jieba
    except Exception:
        return []

    spans: list[LinguisticSpan] = []
    cursor = 0
    for token in tokenizer.cut(text, cut_all=False):
        token = str(token or '')
        if not token:
            continue
        start = text.find(token, cursor)
        if start == -1:
            start = text.find(token)
        if start == -1:
            continue
        end = start + len(token)
        cursor = end
        if len(token) >= 2 and any(_is_cjk_char(char) for char in token):
            spans.append(LinguisticSpan(start, end, token, 'cws'))
    return spans


def _iter_common_term_spans(text: str) -> list[LinguisticSpan]:
    spans: list[LinguisticSpan] = []
    for term in COMMON_CJK_TERMS:
        start = text.find(term)
        while start != -1:
            spans.append(LinguisticSpan(start, start + len(term), term, 'common_term'))
            start = text.find(term, start + 1)
    return spans


def _degree_noun_phrase_at(text: str, start: int, adverb: str) -> LinguisticSpan | None:
    if not _has_degree_left_boundary(text, start):
        return None
    adjective_start = start + len(adverb)
    de_index = text.find('的', adjective_start + 1, min(len(text), adjective_start + 5))
    if de_index == -1 or de_index == adjective_start:
        return None
    if not all(_is_cjk_char(char) for char in text[adjective_start:de_index]):
        return None
    noun_end = _noun_phrase_end(text, de_index + 1)
    if noun_end is None:
        return None
    return LinguisticSpan(start, noun_end, text[start:noun_end], 'phrase_integrity')


def _verb_phrase_start(text: str, end: int) -> int | None:
    cursor = end - 1
    count = 0
    while cursor >= 0 and count < 4 and _is_cjk_char(text[cursor]):
        if text[cursor] in '的地得':
            break
        cursor -= 1
        count += 1
    start = cursor + 1
    if end - start < 1:
        return None
    if not _has_phrase_left_boundary(text, start):
        return None
    return start


def _object_phrase_end(text: str, start: int) -> int | None:
    for prefix in DEMONSTRATIVE_PREFIXES:
        if not text.startswith(prefix, start):
            continue
        noun_start = start + len(prefix)
        noun_end = _noun_phrase_end(text, noun_start)
        if noun_end is not None:
            return noun_end
    return None


def _noun_phrase_end(text: str, start: int) -> int | None:
    if start >= len(text) or not _is_cjk_char(text[start]):
        return None
    best_end: int | None = None
    max_end = min(len(text), start + 4)
    for end in range(start + 1, max_end + 1):
        noun = text[start:end]
        if not all(_is_cjk_char(char) for char in noun):
            break
        next_char = text[end] if end < len(text) else ''
        if not next_char or next_char in PHRASE_RIGHT_BOUNDARY_CHARS or next_char in PHRASE_TAIL_FILLER_CHARS:
            best_end = end
            break
        if noun in COMMON_CJK_TERMS:
            best_end = end
    return best_end


def _has_phrase_left_boundary(text: str, start: int) -> bool:
    return start <= 0 or text[start - 1] in PHRASE_LEFT_BOUNDARY_CHARS or not _is_cjk_char(text[start - 1])


def _has_degree_left_boundary(text: str, start: int) -> bool:
    return _has_phrase_left_boundary(text, start) or text[start - 1:start] in DEGREE_LEFT_CONTEXT_CHARS


def _has_existential_left_boundary(text: str, start: int) -> bool:
    return _has_phrase_left_boundary(text, start) or text[start - 1:start] in EXISTENTIAL_LEFT_CONTEXT_CHARS


def _iter_ascii_word_spans(text: str) -> list[LinguisticSpan]:
    return [
        LinguisticSpan(match.start(), match.end(), match.group(0), 'ascii_word')
        for match in ASCII_WORD_RE.finditer(text)
    ]


def _iter_numeric_expression_spans(text: str) -> list[LinguisticSpan]:
    spans = [
        LinguisticSpan(match.start(), match.end(), match.group(0), 'numeric_expression')
        for pattern in (DATE_EXPRESSION_RE, NUMBER_UNIT_RE)
        for match in pattern.finditer(text)
    ]
    spans.extend(
        LinguisticSpan(match.start(), match.end(), match.group(0), 'numeric_expression')
        for match in _NUMERIC_FACT_RE.finditer(text)
        if re.search(r'[到至~～—-]', match.group(0))
    )
    return spans


def _iter_enumeration_spans(text: str) -> list[LinguisticSpan]:
    return [
        LinguisticSpan(match.start(), match.end(), match.group(0), 'enumeration_marker')
        for match in ENUMERATION_RE.finditer(text)
    ]


def _iter_reduplication_spans(text: str) -> list[LinguisticSpan]:
    spans: list[LinguisticSpan] = []
    for index in range(len(text) - 1):
        char = text[index]
        if char == text[index + 1] and _is_cjk_char(char):
            spans.append(LinguisticSpan(index, index + 2, text[index:index + 2], 'cjk_reduplication'))
    return spans


def _select_non_overlapping_spans(candidates: list[LinguisticSpan]) -> tuple[LinguisticSpan, ...]:
    occupied = [False for _ in range(max((span.end for span in candidates), default=0))]
    selected: list[LinguisticSpan] = []
    priority = {
        'protected_term': 0,
        'runtime_lexicon_hard': 1,
        'organization': 2,
        'runtime_lexicon_strong': 3,
        'runtime_lexicon_soft': 4,
        'numeric_expression': 5,
        'enumeration_marker': 6,
        'phrase_integrity': 7,
        'common_term': 8,
        'cws': 9,
        'ascii_word': 10,
        'cjk_reduplication': 11,
    }
    for span in sorted(
        candidates,
        key=lambda item: (priority.get(item.span_type, 99), -(item.end - item.start), item.start),
    ):
        if span.start < 0 or span.end <= span.start or span.end > len(occupied):
            continue
        if any(occupied[span.start:span.end]):
            continue
        for index in range(span.start, span.end):
            occupied[index] = True
        selected.append(span)
    return tuple(sorted(selected, key=lambda item: item.start))


def _is_cjk_char(char: str) -> bool:
    if not char:
        return False
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )
