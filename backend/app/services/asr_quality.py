from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
import statistics
import unicodedata
from typing import Any, Mapping, Sequence


REPORT_SCHEMA_VERSION = 'fun-asr-quality-sanitization.v1'
_MIN_WORDS = 80
_MIN_NORMALIZED_CHARS = 120
_ANCHOR_CHARS = 18
_MIN_ANCHOR_OCCURRENCES = 3
_MAX_FIRST_ANCHOR_OFFSET = 4
_LOW_SENTENCE_MEDIAN_CONFIDENCE = 0.10
_LOW_REPEAT_MEDIAN_CONFIDENCE = 0.05
_TRUSTED_REPEAT_MEDIAN_CONFIDENCE = 0.50


@dataclass(frozen=True)
class FunAsrQualityResult:
    payload: dict[str, Any]
    report: dict[str, Any]


def sanitize_funasr_payload(payload: Mapping[str, Any]) -> FunAsrQualityResult:
    """Collapse only repeated provider text backed by a single trusted confidence island.

    The provider occasionally fills a long music region by repeating one lyric block.
    We act only when the repeated blocks are textually explicit, at least three copies
    are present, the sentence-level confidence is pathological, and exactly one copy
    has normal word confidence. Ordinary spoken repetition and confidently recognized
    choruses are therefore preserved.
    """
    cleaned = deepcopy(dict(payload))
    anomalies: list[dict[str, Any]] = []
    transcripts = cleaned.get('transcripts') or []
    if not isinstance(transcripts, list):
        transcripts = []

    for transcript_index, transcript in enumerate(transcripts):
        if not isinstance(transcript, dict):
            continue
        sentences = transcript.get('sentences') or []
        if not isinstance(sentences, list):
            continue
        sanitized_indexes: set[int] = set()
        for sentence_index, sentence in enumerate(sentences):
            if not isinstance(sentence, dict):
                continue
            words = sentence.get('words') or []
            if not isinstance(words, list):
                continue
            trusted_span = _trusted_repeated_word_span(words)
            if trusted_span is None:
                continue
            start_index, end_index, diagnostic = trusted_span
            retained_words = words[start_index:end_index]
            if not retained_words:
                continue
            original_start = _safe_int(sentence.get('begin_time'))
            original_end = _safe_int(sentence.get('end_time'))
            retained_start = _safe_int(retained_words[0].get('begin_time'))
            retained_end = _safe_int(retained_words[-1].get('end_time'))
            sentence['words'] = retained_words
            sentence['text'] = _render_words(retained_words)
            if retained_start is not None:
                sentence['begin_time'] = retained_start
            if retained_end is not None:
                sentence['end_time'] = retained_end
            sanitized_indexes.add(sentence_index)
            anomalies.append({
                'type': 'low_confidence_repeated_text',
                'transcript_index': transcript_index,
                'sentence_index': sentence_index,
                'provider_sentence_id': str(sentence.get('sentence_id') or ''),
                'original_start_ms': original_start,
                'original_end_ms': original_end,
                'retained_start_ms': retained_start,
                'retained_end_ms': retained_end,
                'original_word_count': len(words),
                'retained_word_count': len(retained_words),
                'removed_word_count': len(words) - len(retained_words),
                **diagnostic,
            })
        for sentence_index in sorted(sanitized_indexes, reverse=True):
            trailing_index = sentence_index + 1
            if trailing_index >= len(sentences):
                continue
            trailing = sentences[trailing_index]
            if not isinstance(trailing, dict):
                continue
            previous_words = sentences[sentence_index].get('words') or []
            trailing_words = trailing.get('words') or []
            if not _is_low_confidence_trailing_repeat(previous_words, trailing_words):
                continue
            anomalies.append({
                'type': 'low_confidence_trailing_repeat',
                'transcript_index': transcript_index,
                'sentence_index': trailing_index,
                'provider_sentence_id': str(trailing.get('sentence_id') or ''),
                'original_start_ms': _safe_int(trailing.get('begin_time')),
                'original_end_ms': _safe_int(trailing.get('end_time')),
                'retained_start_ms': None,
                'retained_end_ms': None,
                'original_word_count': len(trailing_words),
                'retained_word_count': 0,
                'removed_word_count': len(trailing_words),
                'repeat_occurrence_count': 1,
                'sentence_median_confidence': round(
                    _median_available([_confidence(word.get('confidence')) for word in trailing_words]),
                    6,
                ),
                'retained_median_confidence': None,
                'discarded_min_median_confidence': None,
            })
            del sentences[trailing_index]

    return FunAsrQualityResult(
        payload=cleaned,
        report={
            'schema_version': REPORT_SCHEMA_VERSION,
            'policy': 'collapse_only_low_confidence_repetition_with_trusted_copy',
            'anomaly_count': len(anomalies),
            'removed_word_count': sum(item['removed_word_count'] for item in anomalies),
            'anomalies': anomalies,
        },
    )


def _trusted_repeated_word_span(
    words: Sequence[Mapping[str, Any]],
) -> tuple[int, int, dict[str, Any]] | None:
    if len(words) < _MIN_WORDS:
        return None
    confidences = [_confidence(word.get('confidence')) for word in words]
    available = [value for value in confidences if value is not None]
    if len(available) < int(len(words) * 0.8):
        return None
    sentence_median = statistics.median(available)
    if (
        sentence_median >= _LOW_SENTENCE_MEDIAN_CONFIDENCE
        or max(available, default=0.0) < _TRUSTED_REPEAT_MEDIAN_CONFIDENCE
    ):
        return None

    text, char_word_indexes = _normalized_text_with_word_indexes(words)
    if len(text) < _MIN_NORMALIZED_CHARS:
        return None
    candidates = _repeated_anchor_candidates(text)
    best: tuple[tuple[float, int, int], tuple[int, int, dict[str, Any]]] | None = None
    for positions in candidates:
        word_starts = tuple(dict.fromkeys(char_word_indexes[position] for position in positions))
        if len(word_starts) < _MIN_ANCHOR_OCCURRENCES:
            continue
        intervals = [
            (word_starts[index], word_starts[index + 1])
            for index in range(len(word_starts) - 1)
            if word_starts[index + 1] - word_starts[index] >= 12
        ]
        if len(intervals) < 2:
            continue
        scores = [
            _median_available(confidences[start:end])
            for start, end in intervals
        ]
        best_index = max(range(len(scores)), key=lambda index: scores[index])
        best_score = scores[best_index]
        low_scores = [score for index, score in enumerate(scores) if index != best_index]
        if (
            best_score < _TRUSTED_REPEAT_MEDIAN_CONFIDENCE
            or not low_scores
            or min(low_scores) > _LOW_REPEAT_MEDIAN_CONFIDENCE
        ):
            continue
        start_index, end_index = intervals[best_index]
        if end_index - start_index < 20:
            continue
        removed_count = len(words) - (end_index - start_index)
        if removed_count < end_index - start_index:
            continue
        diagnostic = {
            'repeat_occurrence_count': len(word_starts),
            'sentence_median_confidence': round(sentence_median, 6),
            'retained_median_confidence': round(best_score, 6),
            'discarded_min_median_confidence': round(min(low_scores), 6),
        }
        rank = (best_score, removed_count, end_index - start_index)
        candidate = (start_index, end_index, diagnostic)
        if best is None or rank > best[0]:
            best = (rank, candidate)
    return best[1] if best is not None else None


def _repeated_anchor_candidates(text: str) -> list[tuple[int, ...]]:
    positions_by_anchor: dict[str, list[int]] = {}
    for start in range(0, len(text) - _ANCHOR_CHARS + 1):
        anchor = text[start:start + _ANCHOR_CHARS]
        positions_by_anchor.setdefault(anchor, []).append(start)
    candidates: set[tuple[int, ...]] = set()
    for positions in positions_by_anchor.values():
        if len(positions) < _MIN_ANCHOR_OCCURRENCES:
            continue
        spaced: list[int] = []
        for position in positions:
            if not spaced or position - spaced[-1] >= _ANCHOR_CHARS:
                spaced.append(position)
        if (
            len(spaced) >= _MIN_ANCHOR_OCCURRENCES
            and spaced[0] <= _MAX_FIRST_ANCHOR_OFFSET
        ):
            candidates.add(tuple(spaced))
    return sorted(candidates, key=lambda item: (-len(item), item[-1] - item[0], item))


def _normalized_text_with_word_indexes(
    words: Sequence[Mapping[str, Any]],
) -> tuple[str, tuple[int, ...]]:
    characters: list[str] = []
    word_indexes: list[int] = []
    for word_index, word in enumerate(words):
        value = unicodedata.normalize('NFKC', str(word.get('text') or '')).lower()
        for character in value:
            if character.isalnum():
                characters.append(character)
                word_indexes.append(word_index)
    return ''.join(characters), tuple(word_indexes)


def _render_words(words: Sequence[Mapping[str, Any]]) -> str:
    return ''.join(
        f"{str(word.get('text') or '')}{str(word.get('punctuation') or '')}"
        for word in words
    ).strip()


def _is_low_confidence_trailing_repeat(
    previous_words: Sequence[Mapping[str, Any]],
    trailing_words: Sequence[Mapping[str, Any]],
) -> bool:
    if len(previous_words) < 20 or len(trailing_words) < 12:
        return False
    previous_text, _ = _normalized_text_with_word_indexes(previous_words)
    trailing_text, _ = _normalized_text_with_word_indexes(trailing_words)
    if len(trailing_text) < 24 or trailing_text not in previous_text:
        return False
    trailing_confidence = _median_available([
        _confidence(word.get('confidence'))
        for word in trailing_words
    ])
    previous_end = _safe_int(previous_words[-1].get('end_time'))
    trailing_start = _safe_int(trailing_words[0].get('begin_time'))
    return (
        trailing_confidence <= _LOW_REPEAT_MEDIAN_CONFIDENCE
        and previous_end is not None
        and trailing_start is not None
        and 0 <= trailing_start - previous_end <= 10_000
    )


def _median_available(values: Sequence[float | None]) -> float:
    available = [value for value in values if value is not None]
    return statistics.median(available) if available else 0.0


def _confidence(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        return None
    return result


def _safe_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
