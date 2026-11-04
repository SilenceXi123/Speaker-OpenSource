from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable, Optional

from app.services.chinese_text import normalize_to_simplified_chinese


RUNTIME_LEXICON_BUNDLE_VERSION = 'runtime_lexicon_bundle.v1'
DEFAULT_PROTECTED_BOUNDARY_TERMS = tuple(
    sorted(
        {
            '工作人员',
            '沈阳',
            '赛艇中心',
            '国际赛艇中心',
            '亚洲青年赛艇锦标赛',
            '沈阳赛艇国际公开赛',
            '赛艇锦标赛',
            '国际公开赛',
            '东北平原',
            '绿色明珠',
            '非正式',
            '工作人员李丹',
            '外号',
            '人工智能',
            '原力无限',
        },
        key=len,
        reverse=True,
    )
)
SCRIPT_PROTECTED_TERM_KEYS = ('people', 'places', 'programs', 'organizations', 'terms')
COMMON_SCRIPT_PROTECTED_STOPWORDS = {
    '这个',
    '那个',
    '这些',
    '那些',
    '我们',
    '你们',
    '他们',
    '大家',
    '就是',
    '然后',
    '因为',
    '所以',
    '但是',
    '如果',
    '一个',
    '没有',
    '可以',
    '不是',
    '还是',
    '现在',
    '今天',
    '这里',
    '那里',
}
DISPLAY_PUNCTUATION = set('，。、！？；：、“”‘’（）()【】[]《》〈〉…,.!?;:·')
EDGE_FILLER_CHARS = set('嗯啊哦哎唉呃额呀哇呢嘛')
DISCOURSE_ONLY_FILLERS = {
    '其实',
    '就是',
    '那个',
    '这个',
    '然后',
    '那么',
    '所以',
    '但是',
    '不过',
    '还有',
    '而且',
    '对吧',
    '是吧',
    '好吧',
    '对不对',
    '是不是',
    '你知道吧',
    '你懂吧',
}
SCRIPT_LOCATION_RE = re.compile(r'[\u3400-\u9fff]{1,8}(?:古镇|村|镇|市|区|县|山|湖|河|园|街|路|湾)')
SCRIPT_NAMED_SUFFIXES = ('中心', '实验室', '体验馆', '工作室', '研究院', '学院', '大学', '集团', '公司', '委员会', '协会')
MIXED_CONJUNCTION_FRAGMENT_RE = re.compile(r'[和与及或][A-Za-z][A-Za-z0-9+\-_.]*$')


def resolve_subtitle_protected_terms(
    script_context: Optional[dict[str, Any]] = None,
    *,
    extra_terms: Optional[Iterable[str]] = None,
    builtin_terms: Iterable[str] = DEFAULT_PROTECTED_BOUNDARY_TERMS,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> tuple[str, ...]:
    terms: list[str] = [str(item or '') for item in builtin_terms]
    for item in extra_terms or ():
        terms.append(str(item or ''))
    terms.extend(runtime_lexicon_terms(runtime_lexicon_bundle, protection_levels=('hard',)))
    if script_context:
        terms.extend(_script_context_terms(script_context))

    # Boundary scans reuse the same terms; cap both key size and cache entries.
    if len(terms) <= 1024 and all(isinstance(term, str) and len(term) <= 64 for term in terms):
        return _resolve_short_protected_terms(tuple(terms))
    return _clean_protected_terms(terms)


@lru_cache(maxsize=128)
def _resolve_short_protected_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    return _clean_protected_terms(terms)


def _clean_protected_terms(terms: Iterable[str]) -> tuple[str, ...]:
    cleaned_terms: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = _normalize_protected_term(term)
        if not _is_valid_protected_term(cleaned) or cleaned in seen:
            continue
        seen.add(cleaned)
        cleaned_terms.append(cleaned)
    return tuple(sorted(cleaned_terms, key=len, reverse=True))


def empty_runtime_lexicon_bundle() -> dict[str, Any]:
    return {
        'bundle_version': RUNTIME_LEXICON_BUNDLE_VERSION,
        'tenant_id': None,
        'project_id': None,
        'domain_id': None,
        'job_id': None,
        'generated_at': None,
        'term_set_hash': '',
        'source_counts': {},
        'protection_counts': {},
        'terms': [],
        'disabled_terms': [],
        'conflict_report': [],
        'learned_corrections': [],
        'canonical_corrections': [],
    }


def normalize_runtime_lexicon_bundle(runtime_lexicon_bundle: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(runtime_lexicon_bundle, dict):
        return empty_runtime_lexicon_bundle()
    normalized = empty_runtime_lexicon_bundle()
    normalized.update(runtime_lexicon_bundle)
    if not isinstance(normalized.get('terms'), list):
        normalized['terms'] = []
    if not isinstance(normalized.get('disabled_terms'), list):
        normalized['disabled_terms'] = []
    if not isinstance(normalized.get('conflict_report'), list):
        normalized['conflict_report'] = []
    if not isinstance(normalized.get('learned_corrections'), list):
        normalized['learned_corrections'] = []
    if not isinstance(normalized.get('canonical_corrections'), list):
        normalized['canonical_corrections'] = []
    if not normalized.get('bundle_version'):
        normalized['bundle_version'] = RUNTIME_LEXICON_BUNDLE_VERSION
    return normalized


def runtime_lexicon_terms(
    runtime_lexicon_bundle: Optional[dict[str, Any]],
    *,
    protection_levels: Iterable[str],
) -> tuple[str, ...]:
    bundle = normalize_runtime_lexicon_bundle(runtime_lexicon_bundle)
    allowed_levels = {str(level or '').strip().lower() for level in protection_levels}
    terms: list[str] = []
    seen: set[str] = set()
    for item in bundle.get('terms') or []:
        if not isinstance(item, dict):
            continue
        level = str(item.get('protection_level') or '').strip().lower()
        if level not in allowed_levels:
            continue
        raw_term = item.get('display_text') or item.get('normalized_term') or item.get('term')
        term = _normalize_protected_term(str(raw_term or ''))
        if not _is_valid_protected_term(term) or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return tuple(sorted(terms, key=len, reverse=True))


def _script_context_terms(script_context: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in SCRIPT_PROTECTED_TERM_KEYS:
        value = script_context.get(key)
        if isinstance(value, list):
            terms.extend(str(item or '') for item in value)
    for line in script_context.get('reference_lines') or []:
        terms.extend(_extract_reference_protected_terms(str(line or '')))
    return terms


def _extract_reference_protected_terms(line: str) -> list[str]:
    text = _strip_for_display(line)
    if not text:
        return []
    candidates: list[str] = []
    candidates.extend(SCRIPT_LOCATION_RE.findall(text))
    for suffix in SCRIPT_NAMED_SUFFIXES:
        pattern = re.compile(rf'[\u3400-\u9fff]{{2,10}}{suffix}')
        candidates.extend(pattern.findall(text))
    return candidates


def _normalize_protected_term(term: str) -> str:
    return _strip_for_display(str(term or ''))


def _is_valid_protected_term(term: str) -> bool:
    if len(term) < 2 or len(term) > 24:
        return False
    if term in DISCOURSE_ONLY_FILLERS or term in COMMON_SCRIPT_PROTECTED_STOPWORDS:
        return False
    if MIXED_CONJUNCTION_FRAGMENT_RE.search(term):
        return False
    if all(char in EDGE_FILLER_CHARS for char in term):
        return False
    return True


def _strip_for_display(text: str) -> str:
    without_punctuation = ''.join(ch for ch in str(text or '').strip() if ch not in DISPLAY_PUNCTUATION)
    return normalize_to_simplified_chinese(without_punctuation)
