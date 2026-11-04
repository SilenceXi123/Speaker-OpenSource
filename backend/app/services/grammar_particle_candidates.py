from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.services.chinese_text import normalize_to_simplified_chinese
from app.services.subtitle_pipeline_v2_schema import RawAsrToken, SemanticUtterance


GrammarParticle = Literal['的', '地', '得']
GRAMMAR_PARTICLE_PROFILE_VERSION = 'grammar-particle.v1'
_MATCH_TEXT_RE = re.compile(r'[^\u3400-\u9fffA-Za-z0-9]+')

# These rules deliberately cover only common, structurally clear broadcast Mandarin.
# Ambiguous constructions are left untouched and may be learned later from human edits.
_COMPLEMENT_VERBS = (
    '表达|表现|回答|解释|描述|分析|处理|完成|准备|配合|控制|掌握|'
    '说|讲|做|跑|写|唱|演|读|听|看|想|笑|哭|睡|走|跳|拍'
)
_COMPLEMENT_WORDS = (
    '清楚|明白|准确|完整|到位|漂亮|精彩|自然|认真|稳定|流畅|'
    '顺利|开心|难过|舒服|辛苦|很好|不错|很快|很慢|很对|很稳|'
    '好|快|慢|对|错|稳|顺'
)
_MANNER_WORDS = (
    '认认真真|清清楚楚|慢慢|轻轻|紧紧|深深|默默|大声|小声|'
    '认真|清楚|准确|快速|缓慢|开心|高兴|努力|仔细|耐心|热情|'
    '主动|积极|直接|正式|公开|共同|反复|不断|继续|重新|逐渐|'
    '流利|完整|顺利|自然'
)
_ACTION_VERBS = (
    '表达|表现|回答|解释|描述|分析|处理|完成|准备|配合|控制|掌握|'
    '介绍|说明|朗读|演唱|表演|拍摄|记录|观察|思考|交流|沟通|'
    '说|讲|做|跑|写|唱|演|读|听|看|想|走|跳|拍'
)
_ATTRIBUTIVE_WORDS = (
    '美丽|漂亮|重要|关键|明确|准确|完整|正式|公开|共同|全新|'
    '真正|主要|核心|基本|具体|详细|专业|优秀|精彩|自然|稳定|'
    '顺利|特别|特殊|独特|积极|直接|最终|现场|个人|团队'
)
_NOUN_HEADS = (
    '风景|景色|画面|声音|表现|回答|解释|描述|分析|处理|结果|'
    '答案|决定|选择|方式|方法|方案|内容|信息|问题|任务|工作|'
    '节目|舞台|环节|比赛|作品|歌曲|表演|选手|嘉宾|成员|团队|'
    '项目|机会|经验|能力|状态|效果|目标|要求|规则|计划'
)


@dataclass(frozen=True, slots=True)
class GrammarParticleProposal:
    rule_id: str
    utterance_id: str
    token_ids: tuple[str, ...]
    source_text: str
    replacement_text: str
    expected_particle: GrammarParticle
    confidence: float


@dataclass(frozen=True, slots=True)
class _Rule:
    rule_id: str
    pattern: re.Pattern[str]
    expected_particle: GrammarParticle
    confidence: float


_RULES = (
    _Rule(
        rule_id='complement-de',
        pattern=re.compile(
            rf'(?:{_COMPLEMENT_VERBS})(?P<particle>[的地])'
            rf'(?:很|太|更|真|特别|非常|十分|这么|那么)?(?:{_COMPLEMENT_WORDS})'
        ),
        expected_particle='得',
        confidence=0.97,
    ),
    _Rule(
        rule_id='adverbial-di',
        pattern=re.compile(
            rf'(?:{_MANNER_WORDS})(?P<particle>[的得])(?:{_ACTION_VERBS})'
        ),
        expected_particle='地',
        confidence=0.96,
    ),
    _Rule(
        rule_id='attributive-de',
        pattern=re.compile(
            rf'(?:{_ATTRIBUTIVE_WORDS})(?P<particle>[地得])(?:{_NOUN_HEADS})'
        ),
        expected_particle='的',
        confidence=0.96,
    ),
)

_FIXED_EXPRESSIONS = (
    '觉得', '获得', '取得', '值得', '舍得', '不得', '免得', '记得',
    '晓得', '懂得', '显得', '目的地', '根据地', '所在地', '发源地',
    '的确', '的话', '似的', '是的',
)


def build_grammar_particle_proposals(
    utterance: SemanticUtterance,
    tokens: list[RawAsrToken],
) -> tuple[GrammarParticleProposal, ...]:
    normalized_tokens = tuple(_normalize_token(token.text) for token in tokens)
    text = ''.join(normalized_tokens)
    if not text or not any(particle in text for particle in ('的', '地', '得')):
        return ()

    token_ranges: list[tuple[int, int]] = []
    cursor = 0
    for token_text in normalized_tokens:
        token_ranges.append((cursor, cursor + len(token_text)))
        cursor += len(token_text)
    protected_ranges = _expression_ranges(text)
    proposals: list[tuple[int, int, GrammarParticleProposal]] = []
    for rule in _RULES:
        for match in rule.pattern.finditer(text):
            particle_start = match.start('particle')
            if any(start <= particle_start < end for start, end in protected_ranges):
                continue
            first_token = _token_at(token_ranges, match.start())
            last_token = _token_at(token_ranges, match.end() - 1)
            if first_token is None or last_token is None:
                continue
            span_start = token_ranges[first_token][0]
            span_end = token_ranges[last_token][1]
            source_text = text[span_start:span_end]
            relative_particle = particle_start - span_start
            replacement_text = (
                source_text[:relative_particle]
                + rule.expected_particle
                + source_text[relative_particle + 1:]
            )
            proposal = GrammarParticleProposal(
                rule_id=rule.rule_id,
                utterance_id=utterance.utterance_id,
                token_ids=tuple(
                    token.token_id for token in tokens[first_token:last_token + 1]
                ),
                source_text=source_text,
                replacement_text=replacement_text,
                expected_particle=rule.expected_particle,
                confidence=rule.confidence,
            )
            proposals.append((first_token, last_token + 1, proposal))

    accepted: list[GrammarParticleProposal] = []
    occupied: set[int] = set()
    seen: set[tuple[tuple[str, ...], str]] = set()
    for start, end, proposal in sorted(
        proposals,
        key=lambda item: (-item[2].confidence, item[0], item[1], item[2].rule_id),
    ):
        identity = (proposal.token_ids, proposal.replacement_text)
        indexes = set(range(start, end))
        if identity in seen or indexes & occupied:
            continue
        seen.add(identity)
        occupied.update(indexes)
        accepted.append(proposal)
    return tuple(accepted)


def _normalize_token(value: str) -> str:
    simplified = normalize_to_simplified_chinese(str(value or ''))
    return _MATCH_TEXT_RE.sub('', simplified).lower()


def _expression_ranges(text: str) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    for expression in _FIXED_EXPRESSIONS:
        start = 0
        while True:
            position = text.find(expression, start)
            if position < 0:
                break
            ranges.append((position, position + len(expression)))
            start = position + 1
    return tuple(ranges)


def _token_at(ranges: list[tuple[int, int]], char_index: int) -> int | None:
    for index, (start, end) in enumerate(ranges):
        if start <= char_index < end:
            return index
    return None
