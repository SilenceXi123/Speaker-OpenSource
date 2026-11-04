from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from app.services.subtitle_linguistic_spans import numeric_fact_signature


DIALOGUE_NORMALIZATION_SCHEMA = 'dialogue-broadcast-normalization.v1'
DIALOGUE_NORMALIZATION_VERSION = 4
_UNIT_RE = re.compile(r'[\u3400-\u9fff]|[A-Za-z0-9]+')
_ONLY_PUNCTUATION_RE = re.compile(r'^[\s，。！？、；：,.!?;:…—~～·]+$')
_LATIN_WORD_RE = re.compile(r'^[A-Za-z][A-Za-z0-9\'’ -]*$')


@dataclass(frozen=True)
class DialogueNormalizationResult:
    segments: list[dict[str, Any]]
    report: dict[str, Any]


@dataclass(frozen=True)
class _PhraseRule:
    pattern: re.Pattern[str]
    replacement: str
    reason: str
    translated_prefixes: tuple[str, ...] = ()


_PHRASE_RULES = (
    _PhraseRule(
        re.compile(r"(?<![A-Za-z0-9])let[\'’]?s\s+go(?![A-Za-z0-9])", re.IGNORECASE),
        '走起',
        'bilingual_lets_go',
        ('走起',),
    ),
    _PhraseRule(
        re.compile(
            r'(?<![A-Za-z0-9])you\s+got\s+it(?:\s+man)?(?![A-Za-z0-9])',
            re.IGNORECASE,
        ),
        '你做到了',
        'bilingual_you_got_it',
        ('你做到', '做到了'),
    ),
    _PhraseRule(
        re.compile(r'(?<![A-Za-z0-9])i\s+like\s+that(?![A-Za-z0-9])', re.IGNORECASE),
        '我喜欢这个',
        'bilingual_i_like_that',
        ('我喜欢',),
    ),
    _PhraseRule(
        re.compile(r'(?<![A-Za-z0-9])respect(?![A-Za-z0-9])', re.IGNORECASE),
        '尊敬',
        'bilingual_respect',
        ('尊敬',),
    ),
    _PhraseRule(
        re.compile(r'(?<![A-Za-z0-9])rapper(?![A-Za-z0-9])', re.IGNORECASE),
        '说唱歌手',
        'bilingual_rapper',
        ('说唱歌手',),
    ),
    _PhraseRule(
        re.compile(r'(?<![A-Za-z0-9])match(?![A-Za-z0-9])', re.IGNORECASE),
        '契合',
        'bilingual_match',
        ('契合',),
    ),
    _PhraseRule(
        re.compile(r'(?<![A-Za-z0-9])lay\s*back(?![A-Za-z0-9])', re.IGNORECASE),
        '往后',
        'bilingual_layback',
        ('往后',),
    ),
    _PhraseRule(
        re.compile(
            r'(?<![A-Za-z0-9])one\s+two\s+three(?![A-Za-z0-9])',
            re.IGNORECASE,
        ),
        '一二三',
        'bilingual_count_in',
        ('一二三',),
    ),
    _PhraseRule(
        re.compile(
            r'(?<![A-Za-z0-9])beat\s+switch(?![A-Za-z0-9]|\s*[（(]伴奏切换)',
            re.IGNORECASE,
        ),
        'Beat switch（伴奏切换）',
        'broadcast_gloss_beat_switch',
    ),
    _PhraseRule(
        re.compile(
            r'(?<![A-Za-z0-9])rage(?![A-Za-z0-9]|\s*[（(]说唱风格)',
            re.IGNORECASE,
        ),
        'Rage（说唱风格）',
        'broadcast_gloss_rage',
    ),
)
_DEDUPLICATED_PHRASES = ('尊敬', '走起', '你做到了', '我喜欢这个', '往后')
_TRAILING_LAUGHTER_RE = re.compile(r'(?:呀|啊)?哈(?:哈|[，,。.!！?？\s]){2,}$')
_INLINE_TRIPLE_RE = re.compile(r'([\u3400-\u9fff]{1,4})(?:\1){2,}')
_INLINE_DOUBLE_PHRASE_RE = re.compile(
    r'([\u3400-\u9fff]{2,8})(?:[\s，,。.!！？?、；;：:]*\1)+'
)
_SAFE_SINGLE_CHAR_REPEATS = {'哈', '啊', '哦', '好', '嗯', '哎', '唉'}
_SPLIT_PHRASE_FILLERS = {'那', '啊', '呀', '呢', '哦'}


def normalize_dialogue_segments(
    segments: Iterable[dict[str, Any]],
    *,
    runtime_lexicon_bundle: Mapping[str, Any] | None = None,
    enabled: bool = True,
) -> DialogueNormalizationResult:
    """Normalize broadcast dialogue without touching lyrics-corrected segments.

    The transformation is deliberately deterministic. Project vocabulary is the
    only source of proper-name replacements; no programme-specific names live in
    this module.
    """
    original = [deepcopy(segment) for segment in segments]
    report: dict[str, Any] = {
        'schema_version': DIALOGUE_NORMALIZATION_SCHEMA,
        'version': DIALOGUE_NORMALIZATION_VERSION,
        'enabled': bool(enabled),
        'status': 'not_applied' if enabled else 'disabled',
        'input_segment_count': len(original),
        'output_segment_count': len(original),
        'protected_lyrics_segment_count': 0,
        'changed_segment_count': 0,
        'removed_echo_segment_count': 0,
        'lexicon_replacement_count': 0,
        'bilingual_phrase_replacement_count': 0,
        'repetition_collapse_count': 0,
        'changes': [],
        'skipped_changes': [],
    }
    if not enabled:
        return DialogueNormalizationResult(original, report)
    lexicon_replacements = _build_lexicon_replacements(runtime_lexicon_bundle)
    working: list[dict[str, Any]] = []
    repeated_units: set[str] = set()

    for segment in original:
        if _is_lyrics_segment(segment):
            report['protected_lyrics_segment_count'] += 1
            working.append(segment)
            continue
        before = str(segment.get('text') or '')
        after = before
        reasons: list[str] = []
        before_counts = {key: report[key] for key in (
            'lexicon_replacement_count', 'bilingual_phrase_replacement_count', 'repetition_collapse_count',
        )}

        after, lexicon_count = _apply_lexicon_replacements(after, lexicon_replacements)
        if lexicon_count:
            report['lexicon_replacement_count'] += lexicon_count
            reasons.append('project_lexicon')

        after, phrase_count, phrase_reasons = _apply_phrase_rules(after)
        if phrase_count:
            report['bilingual_phrase_replacement_count'] += phrase_count
            reasons.extend(phrase_reasons)

        after, repetition_count, units = _collapse_inline_repetition(after)
        if repetition_count:
            repeated_units.update(units)
            report['repetition_collapse_count'] += repetition_count
            reasons.append('inline_repetition')

        after = _clean_spacing(after)
        if numeric_fact_signature(before) != numeric_fact_signature(after):
            _record_skipped_change(report, segment, before, after, 'numeric_fact_change')
            report.update(before_counts)
            working.append(segment)
            continue
        if after != before:
            if not after or _ONLY_PUNCTUATION_RE.fullmatch(after):
                report['removed_echo_segment_count'] += 1
                _record_change(report, segment, before, '', reasons)
                continue
            segment = _with_text(segment, after, reasons)
            _record_change(report, segment, before, after, reasons)
        working.append(segment)

    working = _stitch_split_bilingual_phrases(working, report)
    dedupe_terms = tuple(dict.fromkeys((*_DEDUPLICATED_PHRASES, *sorted(repeated_units))))
    working = _remove_adjacent_echoes(working, dedupe_terms, report)
    report['output_segment_count'] = len(working)
    report['changed_segment_count'] = len({
        str(change.get('segment_id') or '') for change in report['changes']
    })
    if report['changes'] or report['removed_echo_segment_count']:
        report['status'] = 'applied'
    return DialogueNormalizationResult(working, report)


def _is_lyrics_segment(segment: Mapping[str, Any]) -> bool:
    return bool(
        segment.get('lyricsCorrectionMode')
        or str(segment.get('timingEvidence') or '').startswith('lyrics_')
        or str(segment.get('segmentId') or '').startswith('lyrics-')
    )


def _build_lexicon_replacements(
    runtime_lexicon_bundle: Mapping[str, Any] | None,
) -> tuple[tuple[str, str], ...]:
    replacements: dict[tuple[str, str], tuple[str, str]] = {}
    for item in (runtime_lexicon_bundle or {}).get('terms') or []:
        if not isinstance(item, Mapping):
            continue
        if str(item.get('protection_level') or '').lower() not in {'hard', 'strong'}:
            continue
        canonical = str(item.get('display_text') or '').strip()
        if not canonical:
            continue
        term_type = str(item.get('term_type') or '').lower()
        is_name = term_type in {'person', 'name', 'script_roster', 'speaker'}
        normalized_term = str(item.get('normalized_term') or '').strip()
        display_base = re.split(r'[（(]', canonical, maxsplit=1)[0].strip()
        candidates = [
            *(str(alias or '').strip() for alias in item.get('aliases') or []),
            normalized_term,
            display_base,
        ]
        for alias in candidates:
            if not alias or not _safe_lexicon_alias(alias):
                continue
            if not is_name and _match_key(alias) == _match_key(canonical):
                continue
            key = (_match_key(alias), _match_key(canonical))
            if key[0] and key[0] != key[1]:
                replacements[key] = (alias, canonical)
    return tuple(sorted(
        replacements.values(),
        key=lambda item: (-len(_match_key(item[0])), item[0].casefold(), item[1]),
    ))


def _safe_lexicon_alias(alias: str) -> bool:
    compact = ''.join(_UNIT_RE.findall(alias))
    if not compact:
        return False
    if _LATIN_WORD_RE.fullmatch(alias):
        return len(compact) >= 3
    return len(compact) >= 2


def _apply_lexicon_replacements(
    text: str,
    replacements: tuple[tuple[str, str], ...],
) -> tuple[str, int]:
    changed = 0
    for alias, canonical in replacements:
        pattern = _alias_pattern(alias)

        def replace(match: re.Match[str]) -> str:
            nonlocal changed
            if _match_is_inside_canonical(text, match.start(), match.end(), alias, canonical):
                return match.group(0)
            changed += 1
            return canonical

        text = pattern.sub(replace, text)
    return text, changed


def _alias_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias).replace(r'\ ', r'\s*')
    if re.search(r'[A-Za-z0-9]', alias):
        return re.compile(rf'(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])', re.IGNORECASE)
    return re.compile(escaped)


def _match_is_inside_canonical(
    text: str,
    start: int,
    end: int,
    alias: str,
    canonical: str,
) -> bool:
    folded_alias = alias.casefold()
    folded_canonical = canonical.casefold()
    cursor = 0
    while True:
        offset = folded_canonical.find(folded_alias, cursor)
        if offset < 0:
            return False
        canonical_start = start - offset
        if canonical_start >= 0:
            candidate = text[canonical_start:canonical_start + len(canonical)]
            if candidate.casefold() == folded_canonical:
                return True
        cursor = offset + 1


def _apply_phrase_rules(text: str) -> tuple[str, int, list[str]]:
    count = 0
    reasons: list[str] = []
    for rule in _PHRASE_RULES:

        def replace(match: re.Match[str]) -> str:
            nonlocal count
            trailing = text[match.end():].lstrip(' ，,。.!！?？')
            replacement = '' if any(
                trailing.startswith(prefix) for prefix in rule.translated_prefixes
            ) else rule.replacement
            count += 1
            reasons.append(rule.reason)
            return replacement

        text = rule.pattern.sub(replace, text)
    if _match_key(text) == 'go':
        text = '走起'
        count += 1
        reasons.append('bilingual_standalone_go')
    return text, count, reasons


def _collapse_inline_repetition(text: str) -> tuple[str, int, set[str]]:
    count = 0
    units: set[str] = set()

    def collapse_triple(match: re.Match[str]) -> str:
        nonlocal count
        unit = match.group(1)
        if len(unit) == 1 and unit not in _SAFE_SINGLE_CHAR_REPEATS:
            return match.group(0)
        count += 1
        units.add(unit)
        return unit

    def collapse_double_phrase(match: re.Match[str]) -> str:
        nonlocal count
        unit = match.group(1)
        count += 1
        units.add(unit)
        return unit

    updated = _TRAILING_LAUGHTER_RE.sub('', text).rstrip()
    if updated != text:
        count += 1
        units.add('哈')
    updated = _INLINE_TRIPLE_RE.sub(collapse_triple, updated)
    updated = _INLINE_DOUBLE_PHRASE_RE.sub(collapse_double_phrase, updated)
    return updated, count, units


def _stitch_split_bilingual_phrases(
    segments: list[dict[str, Any]],
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    working = list(segments)
    drop_indexes: set[int] = set()
    for index in range(len(working) - 1):
        current = working[index]
        following = working[index + 1]
        if _is_lyrics_segment(current) or _is_lyrics_segment(following):
            continue
        gap_ms = int(following.get('startMs') or 0) - int(current.get('endMs') or 0)
        if gap_ms < 0 or gap_ms > 900:
            continue
        if _match_key(str(current.get('text') or '')) != 'you':
            continue
        following_before = str(following.get('text') or '')
        if not _match_key(following_before).startswith('gotit'):
            continue
        if not _same_known_speaker(current, following):
            _record_skipped_change(report, following, following_before, following_before, 'speaker_not_confirmed_same')
            continue
        current_before = str(current.get('text') or '')
        current_after = '你做到了'
        following_after = _clean_spacing(_remove_leading_term(following_before, 'got it'))
        working[index] = _with_text(current, current_after, ['bilingual_you_got_it_split'])
        _record_change(
            report,
            working[index],
            current_before,
            current_after,
            ['bilingual_you_got_it_split'],
        )
        report['bilingual_phrase_replacement_count'] += 1
        if not following_after or _match_key(following_after) in _SPLIT_PHRASE_FILLERS:
            drop_indexes.add(index + 1)
            report['removed_echo_segment_count'] += 1
            _record_change(
                report,
                following,
                following_before,
                '',
                ['bilingual_split_phrase_filler'],
            )
        elif following_after != following_before:
            working[index + 1] = _with_text(
                following,
                following_after,
                ['bilingual_you_got_it_split'],
            )
            _record_change(
                report,
                working[index + 1],
                following_before,
                following_after,
                ['bilingual_you_got_it_split'],
            )
    return [segment for index, segment in enumerate(working) if index not in drop_indexes]


def _remove_adjacent_echoes(
    segments: list[dict[str, Any]],
    terms: tuple[str, ...],
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for segment in segments:
        if _is_lyrics_segment(segment) or not result or _is_lyrics_segment(result[-1]):
            result.append(segment)
            continue
        previous = result[-1]
        gap_ms = int(segment.get('startMs') or 0) - int(previous.get('endMs') or 0)
        if gap_ms < 0 or gap_ms > 900:
            result.append(segment)
            continue
        before = str(segment.get('text') or '')
        after = before
        removed_terms: list[str] = []
        previous_key = _match_key(str(previous.get('text') or ''))
        for term in terms:
            term_key = _match_key(term)
            if not term_key or not previous_key.endswith(term_key):
                continue
            while _match_key(after).startswith(term_key):
                updated = _remove_leading_term(after, term)
                if updated == after:
                    break
                after = updated
                removed_terms.append(term)
        after = _clean_spacing(after)
        if after == before:
            result.append(segment)
            continue
        reason = (
            'speaker_not_confirmed_same' if not _same_known_speaker(previous, segment)
            else 'numeric_fact_change' if numeric_fact_signature(before) != numeric_fact_signature(after)
            else None
        )
        if reason:
            _record_skipped_change(report, segment, before, after, reason)
            result.append(segment)
            continue
        reasons = ['adjacent_echo']
        if not after or _ONLY_PUNCTUATION_RE.fullmatch(after):
            report['removed_echo_segment_count'] += 1
            _record_change(report, segment, before, '', reasons, removed_terms=removed_terms)
            continue
        updated_segment = _with_text(segment, after, reasons)
        _record_change(report, updated_segment, before, after, reasons, removed_terms=removed_terms)
        result.append(updated_segment)
    return result


def _same_known_speaker(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    unknown = {'', 'unknown', 'none', 'null', 'unassigned', 'speaker_unknown', 'speaker_none', 'speaker_null', 'speaker_-1', '-1'}
    matched = False
    for key in ('speakerKey', 'speakerId'):
        a = str(left.get(key) if left.get(key) is not None else '').strip()
        b = str(right.get(key) if right.get(key) is not None else '').strip()
        if a.lower() in unknown or b.lower() in unknown:
            if a or b:
                return False
            continue
        if a != b:
            return False
        matched = True
    return matched


def _record_skipped_change(report, segment, before, after, reason) -> None:
    report['skipped_changes'].append({
        'segment_id': str(segment.get('segmentId') or ''),
        'start_ms': segment.get('startMs'),
        'end_ms': segment.get('endMs'),
        'before': before, 'after': after, 'reason': reason,
    })


def _remove_leading_term(text: str, term: str) -> str:
    pattern = _alias_pattern(term)
    match = pattern.search(text)
    if match is None or text[:match.start()].strip(' ，,。.!！?？'):
        return text
    return text[:match.start()] + text[match.end():]


def _with_text(
    segment: dict[str, Any],
    text: str,
    reasons: list[str],
) -> dict[str, Any]:
    updated = deepcopy(segment)
    updated['text'] = text
    updated['subtitleParts'] = [text]
    ranges = list(updated.get('subtitlePartRanges') or [])
    if not ranges:
        ranges = [{
            'startMs': int(updated.get('startMs') or 0),
            'endMs': int(updated.get('endMs') or 0),
        }]
    updated['subtitlePartRanges'] = [ranges[0]]
    tags = list(updated.get('repairTags') or [])
    if 'dialogue_broadcast_normalized' not in tags:
        tags.append('dialogue_broadcast_normalized')
    updated['repairTags'] = tags
    updated['dialogueNormalizationReasons'] = list(dict.fromkeys(reasons))
    return updated


def _record_change(
    report: dict[str, Any],
    segment: Mapping[str, Any],
    before: str,
    after: str,
    reasons: list[str],
    *,
    removed_terms: list[str] | None = None,
) -> None:
    report['changes'].append({
        'segment_id': str(segment.get('segmentId') or ''),
        'start_ms': int(segment.get('startMs') or 0),
        'end_ms': int(segment.get('endMs') or 0),
        'before': before,
        'after': after,
        'reasons': list(dict.fromkeys(reasons)),
        'removed_terms': list(dict.fromkeys(removed_terms or [])),
    })


def _match_key(text: str) -> str:
    return ''.join(_UNIT_RE.findall(str(text or ''))).casefold()


def _clean_spacing(text: str) -> str:
    text = re.sub(r'[ \t]+', ' ', str(text or ''))
    text = re.sub(r'\s+([，。！？、；：,.!?;:])', r'\1', text)
    return text.strip()
