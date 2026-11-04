from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.subtitles import (
    _protected_boundary_penalty,
    _strip_for_display,
    is_standalone_filler_text,
    join_subtitle_parts,
    max_subtitle_chars_for_text,
)


SAME_SPEAKER_CONTINUATION_GAP_MS = 1200
SHORT_TAIL_MAX_DURATION_MS = 1500
SHORT_TAIL_MAX_GAP_MS = 900
SHORT_TAIL_MAX_CHARS = 5
MAX_UTTERANCE_CHARS = 120
EDGE_PARTICLES = set('嗯啊哦哎唉呃额呀哇呢嘛')
INDEPENDENT_SHORT_REPLIES = {
    '好',
    '好的',
    '对',
    '对的',
    '是',
    '是的',
    '不是',
    '可以',
    '行',
    '不行',
    '知道',
    '不知道',
    '听说过',
    '没听过',
    '谢谢',
    '你好',
    '你们好',
    '哈哈',
}
NOUN_COMPLETION_TAILS = (
    '中心',
    '基地',
    '公园',
    '景区',
    '古镇',
    '村',
    '镇',
    '公司',
    '集团',
    '平台',
    '系统',
    '项目',
    '团队',
)


def rebuild_semantic_utterances(normalized_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rebuilt: list[dict[str, Any]] = []
    for item in normalized_segments or []:
        current = _normalize_utterance_segment(item)
        if not rebuilt:
            rebuilt.append(current)
            continue
        previous = rebuilt[-1]
        merge_reason = _merge_reason(previous, current)
        if not merge_reason:
            rebuilt.append(current)
            continue
        rebuilt[-1] = _merge_segments(previous, current, merge_reason)
    return rebuilt


def _normalize_utterance_segment(segment: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(segment)
    segment_id = str(normalized.get('segmentId') or f"seg-{id(normalized)}")
    normalized.setdefault('sourceSegmentIds', [segment_id])
    normalized.setdefault('repairTags', [])
    return normalized


def _merge_reason(previous: dict[str, Any], current: dict[str, Any]) -> str:
    if previous.get('speakerOverrides') or current.get('speakerOverrides'):
        return ''
    previous_text = _segment_text(previous)
    current_text = _segment_text(current)
    if not previous_text or not current_text:
        return ''
    if is_standalone_filler_text(previous_text) or is_standalone_filler_text(current_text):
        return ''
    same_speaker = str(previous.get('speakerKey') or '') == str(current.get('speakerKey') or '')
    if same_speaker and _is_same_speaker_continuation(previous, current, previous_text, current_text):
        return 'same_speaker_continuation'
    if not same_speaker and _is_short_tail_reassign(previous, current, previous_text, current_text):
        return 'short_tail_reassign'
    return ''


def _is_same_speaker_continuation(
    previous: dict[str, Any],
    current: dict[str, Any],
    previous_text: str,
    current_text: str,
) -> bool:
    gap_ms = _gap_ms(previous, current)
    if gap_ms < 0 or gap_ms > SAME_SPEAKER_CONTINUATION_GAP_MS:
        return False
    if _raw_text_has_terminal_punctuation(previous):
        return False
    combined = join_subtitle_parts([previous_text, current_text])
    if len(combined) > MAX_UTTERANCE_CHARS:
        return False
    return True


def _is_short_tail_reassign(
    previous: dict[str, Any],
    current: dict[str, Any],
    previous_text: str,
    current_text: str,
) -> bool:
    text = _strip_for_display(current_text)
    if not text or len(text) > SHORT_TAIL_MAX_CHARS:
        return False
    if _raw_text_has_terminal_punctuation(previous):
        return False
    duration_ms = _duration_ms(current)
    if duration_ms <= 0 or duration_ms > SHORT_TAIL_MAX_DURATION_MS:
        return False
    gap_ms = _gap_ms(previous, current)
    if gap_ms < -120 or gap_ms > SHORT_TAIL_MAX_GAP_MS:
        return False
    if _is_independent_short_reply(text):
        return False
    combined = join_subtitle_parts([previous_text, text])
    if len(combined) > max(max_subtitle_chars_for_text(combined), MAX_UTTERANCE_CHARS):
        return False
    if _has_cross_segment_protected_boundary(previous_text, text):
        return True
    stem = text.strip(''.join(EDGE_PARTICLES))
    if stem and text[-1] in EDGE_PARTICLES:
        return True
    return stem in NOUN_COMPLETION_TAILS or text in NOUN_COMPLETION_TAILS


def _merge_segments(previous: dict[str, Any], current: dict[str, Any], repair_tag: str) -> dict[str, Any]:
    merged = deepcopy(previous)
    previous_text = _segment_text(previous)
    current_text = _segment_text(current)
    source_ids = _source_segment_ids(previous) + _source_segment_ids(current)
    repair_tags = list(dict.fromkeys([
        *[str(item) for item in previous.get('repairTags') or []],
        *[str(item) for item in current.get('repairTags') or []],
        repair_tag,
    ]))

    merged['segmentId'] = 'utt-' + '+'.join(source_ids)
    merged['sourceSegmentIds'] = source_ids
    merged['repairTags'] = repair_tags
    merged['text'] = join_subtitle_parts([previous_text, current_text])
    merged['startMs'] = min(int(previous.get('startMs') or 0), int(current.get('startMs') or 0))
    merged['endMs'] = max(int(previous.get('endMs') or 0), int(current.get('endMs') or 0))
    merged['words'] = _merged_words(previous, current)
    merged.pop('subtitleParts', None)
    merged.pop('subtitlePartRanges', None)
    merged.pop('speakerOverrides', None)
    return merged


def _segment_text(segment: dict[str, Any]) -> str:
    return _strip_for_display(str(segment.get('text') or ''))


def _source_segment_ids(segment: dict[str, Any]) -> list[str]:
    raw_ids = segment.get('sourceSegmentIds')
    if isinstance(raw_ids, list) and raw_ids:
        return [str(item) for item in raw_ids if str(item)]
    return [str(segment.get('segmentId') or '')]


def _merged_words(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    previous_words = list(previous.get('words') or [])
    current_words = list(current.get('words') or [])
    if previous_words or current_words:
        return previous_words + current_words
    return []


def _gap_ms(previous: dict[str, Any], current: dict[str, Any]) -> int:
    return int(current.get('startMs') or 0) - int(previous.get('endMs') or 0)


def _duration_ms(segment: dict[str, Any]) -> int:
    return int(segment.get('endMs') or 0) - int(segment.get('startMs') or 0)


def _raw_text_has_terminal_punctuation(segment: dict[str, Any]) -> bool:
    raw_text = str(segment.get('text') or '').strip()
    return raw_text.endswith(('。', '！', '？', '!', '?', '；', ';'))


def _is_independent_short_reply(text: str) -> bool:
    value = _strip_for_display(text)
    stem = value.strip(''.join(EDGE_PARTICLES))
    return value in INDEPENDENT_SHORT_REPLIES or stem in INDEPENDENT_SHORT_REPLIES


def _has_cross_segment_protected_boundary(left_text: str, right_text: str) -> bool:
    left = _strip_for_display(left_text)
    right = _strip_for_display(right_text)
    if not left or not right:
        return False
    combined = left + right
    return _protected_boundary_penalty(combined, len(left)) > 0
