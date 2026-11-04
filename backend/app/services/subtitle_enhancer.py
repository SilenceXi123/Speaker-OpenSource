from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from time import perf_counter
from typing import Any, Callable, Optional

import httpx

from app.core.config import settings
from app.schemas.provider_cost import ProviderCallContext, ProviderCallObservation, ProviderMeterObservation
from app.services.provider_costs import record_legacy_provider_observation
from app.services.script_context import format_script_context_for_prompt
from app.services.subtitles import (
    MAX_SUBTITLE_CHARS,
    MAX_SUBTITLE_LATIN_CHARS,
    build_segment_subtitle_parts,
    join_subtitle_parts,
    max_subtitle_chars_for_text,
    strip_subtitle_display_text,
)


logger = logging.getLogger(__name__)


class SubtitleEnhancerError(RuntimeError):
    pass


MAX_REFERENCE_TEXT_CHARS = 120
TITLE_SUFFIX_RE = re.compile(r'的(?:创始人|联合创始人|创办人|负责人|主持人|制作人|发言人|CEO|CTO|总经理|董事长)')
PERSON_NAME_RE = re.compile(r'[A-Za-z\u4e00-\u9fff·]{2,8}$')
COMMON_LOCATION_PREFIXES = (
    '深圳', '北京', '上海', '广州', '杭州', '南京', '苏州', '成都', '武汉', '西安',
    '重庆', '天津', '长沙', '厦门', '青岛', '宁波', '无锡', '佛山', '东莞', '珠海',
)
RISKY_BOUNDARY_BIGRAMS = {
    '悲观',
    '智能',
    '眼镜',
    '成为',
    '真正',
    '一个',
    '有趣',
    '这样',
    '科技',
    '现场',
    '调研',
    '十大',
    '璀璨',
    '东北',
    '平原',
    '绿色',
    '明珠',
    '行业',
    '观察',
    '正式',
    '会议',
    '会谈',
    '当中',
    '产生',
    '没有',
    '门槛',
}
RISKY_BOUNDARY_ADVERBIAL_SUFFIXES = ('一起', '共同', '一同')
RISKY_BOUNDARY_ADVERBIAL_STARTS = ('一起', '共同', '一同')
RISKY_BOUNDARY_OBJECT_MARKERS = ('与', '跟', '和', '同')
RISKY_BOUNDARY_PREDICATE_PREFIXES = (
    '入选',
    '进入',
    '获得',
    '成为',
    '打造',
    '推动',
    '完成',
    '来到',
    '参评',
    '列入',
    '纳入',
    '形成',
    '实现',
    '进行',
    '开展',
    '举办',
)
RISKY_BOUNDARY_NUMERAL_CHARS = set('一二三四五六七八九十百千万两1234567890')
RISKY_BOUNDARY_NUMERAL_FOLLOW_CHARS = set('大强佳项个届名类种条家位年月日')
RISKY_BOUNDARY_SUFFIXES = (
    '科技的创始人',
    '科技的负责人',
    '科技的主持人',
    '科技的联合创始人',
)
RISKY_BOUNDARY_START_CHARS = {'了', '着', '过', '的', '地', '得'}
LEXICAL_ATOM_TERMS = tuple(
    sorted(
        RISKY_BOUNDARY_BIGRAMS
        | {
            '慢慢',
            '行业',
            '璀璨',
            '观察',
            '会议',
            '会谈',
            '东北平原',
            '绿色明珠',
        },
        key=len,
        reverse=True,
    )
)


def enhance_subtitle_segments(
    segments: list[dict[str, Any]],
    reference_sentences: Optional[list[dict[str, Any]]] = None,
    script_context: Optional[dict[str, Any]] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    language_profile: str = 'zh',
    provider_context: Optional[ProviderCallContext] = None,
) -> tuple[list[dict[str, Any]], bool, Optional[str]]:
    if not segments:
        return segments, False, None
    if not settings.subtitle_enhancer_enabled:
        return segments, False, None
    if not settings.subtitle_enhancer_api_key_value:
        return segments, False, '未配置 SUBTITLE_ENHANCER_API_KEY，已跳过字幕增强'

    segment_index_map = {
        str(segment.get('segmentId') or f'segment-{index}'): index
        for index, segment in enumerate(segments)
    }
    enhanced_segments = [dict(segment) for segment in segments]
    applied = False

    try:
        batches = _build_batches(enhanced_segments, reference_sentences=reference_sentences)
        batches, skipped_payload_count = _limit_batches_for_job_budget(batches)
        warnings: list[str] = []
        deadline = _resolve_job_deadline()
        if skipped_payload_count:
            processed_count = sum(len(batch) for batch in batches)
            warnings.append(
                f'DeepSeek 字幕增强已按长音频预算优先处理 {processed_count} 段，'
                f'跳过 {skipped_payload_count} 段低优先级字幕，避免同步阶段长时间卡住'
            )
        requested_payloads = {
            str(payload.get('segmentId') or ''): payload
            for batch in batches
            for payload in batch
            if str(payload.get('segmentId') or '')
        }
        accepted_part_ids: set[str] = set()
        script_context_prompt = format_script_context_for_prompt(script_context)
        if batches:
            _emit_batch_progress(
                progress_callback,
                0,
                len(batches),
                f'DeepSeek 字幕增强准备处理 {len(batches)} 批',
            )
        result_maps, request_errors = _request_enhanced_batches_resilient(
            batches,
            script_context_prompt=script_context_prompt,
            language_profile=language_profile,
            deadline=deadline,
            progress_callback=progress_callback,
            progress_offset=0,
            total_batches=len(batches),
            stage_label='DeepSeek 字幕增强',
            provider_context=provider_context,
        )
        if request_errors:
            warnings.append(_format_batch_error_warning(request_errors))
        for result_map in result_maps:
            batch_applied, batch_accepted_part_ids = _apply_enhanced_result_map(
                enhanced_segments,
                segment_index_map,
                result_map,
            )
            applied = applied or batch_applied
            accepted_part_ids.update(batch_accepted_part_ids)

        retry_payloads = [
            {
                **payload,
                'retryHint': '上一轮 subtitleParts 未通过字幕边界质量检查，请重新规划；避免机械按字数切分，保持动宾结构、数词固定短语和“一起/共同/一同+动作谓语”完整。',
            }
            for segment_id, payload in requested_payloads.items()
            if segment_id not in accepted_part_ids
        ]
        retry_payloads, skipped_retry_count = _limit_retry_payloads(retry_payloads)
        if skipped_retry_count:
            warnings.append(f'DeepSeek 重试预算已触顶，跳过 {skipped_retry_count} 段未通过质量检查的重试')
        if retry_payloads and _deadline_expired(deadline):
            warnings.append('DeepSeek 字幕增强总时限已触顶，已保留完成批次并跳过重试')
            return enhanced_segments, applied, '；'.join(item for item in warnings if item) or None
        retry_batches = _build_retry_batches(retry_payloads)
        retry_result_maps, retry_errors = _request_enhanced_batches_resilient(
            retry_batches,
            script_context_prompt=script_context_prompt,
            language_profile=language_profile,
            deadline=deadline,
            progress_callback=progress_callback,
            progress_offset=len(batches),
            total_batches=len(batches) + len(retry_batches),
            stage_label='DeepSeek 字幕增强重试',
            provider_context=provider_context,
        )
        if retry_errors:
            warnings.append(_format_batch_error_warning(retry_errors))
        for result_map in retry_result_maps:
            batch_applied, batch_accepted_part_ids = _apply_enhanced_result_map(
                enhanced_segments,
                segment_index_map,
                result_map,
            )
            applied = applied or batch_applied
            accepted_part_ids.update(batch_accepted_part_ids)
    except SubtitleEnhancerError as exc:
        return segments, False, str(exc)

    return enhanced_segments, applied, '；'.join(item for item in warnings if item) or None


def _apply_enhanced_result_map(
    enhanced_segments: list[dict[str, Any]],
    segment_index_map: dict[str, int],
    result_map: dict[str, dict[str, Any]],
) -> tuple[bool, set[str]]:
    applied = False
    accepted_part_ids: set[str] = set()
    for segment_id, payload in result_map.items():
        segment_index = segment_index_map.get(segment_id)
        if segment_index is None:
            continue
        current_segment = dict(enhanced_segments[segment_index])
        current_text = str(current_segment.get('text') or '').strip()
        enhanced_text = _normalize_segment_text(payload.get('text'), current_text)
        token_parts, token_ranges = _build_subtitle_parts_from_token_ranges(
            current_segment,
            payload,
            enhanced_text=enhanced_text,
        )
        enhanced_parts = token_parts or _normalize_subtitle_parts(
            payload.get('subtitleParts'),
            expected_text=enhanced_text,
        )
        changed = False
        if enhanced_text and enhanced_text != current_text and (
            not enhanced_parts or strip_subtitle_display_text(enhanced_text) == join_subtitle_parts(enhanced_parts)
        ):
            current_segment['text'] = enhanced_text
            changed = True
        if enhanced_parts:
            current_segment['subtitleParts'] = enhanced_parts
            if token_ranges:
                current_segment['subtitlePartRanges'] = token_ranges
            accepted_part_ids.add(segment_id)
            changed = True
        if changed:
            enhanced_segments[segment_index] = current_segment
            applied = True
    return applied, accepted_part_ids


def _build_batches(
    segments: list[dict[str, Any]],
    *,
    reference_sentences: Optional[list[dict[str, Any]]] = None,
) -> list[list[dict[str, Any]]]:
    max_chars = max(int(settings.subtitle_enhancer_max_batch_chars), 800)
    max_segments = max(int(settings.subtitle_enhancer_max_batch_segments), 1)
    payloads: list[dict[str, Any]] = []

    for segment in segments:
        payload = _build_enhancement_payload(segment, reference_sentences=reference_sentences or [])
        if payload:
            payloads.append(payload)

    if not payloads:
        return []

    batches: list[list[dict[str, Any]]] = []
    for window_payloads in _split_payloads_into_windows(payloads):
        batches.extend(
            _pack_payload_batches(
                window_payloads,
                max_chars=max_chars,
                max_segments=max_segments,
            )
        )
    return batches


def _build_enhancement_payload(
    segment: dict[str, Any],
    *,
    reference_sentences: list[dict[str, Any]],
) -> dict[str, Any] | None:
    segment_text = str(segment.get('text') or '').strip()
    segment_parts = build_segment_subtitle_parts(segment)
    if not segment_text or not segment_parts:
        return None
    reference_text = _build_reference_text(segment, reference_sentences)
    if not _should_enhance_segment(segment_text, segment_parts, reference_text=reference_text):
        return None

    payload = {
        'segmentId': str(segment.get('segmentId') or ''),
        'speakerKey': str(segment.get('speakerKey') or ''),
        'startMs': _safe_int(segment.get('startMs')),
        'endMs': max(_safe_int(segment.get('endMs')), _safe_int(segment.get('startMs'))),
        'text': segment_text,
        'currentParts': [str(part.get('text') or '') for part in segment_parts],
    }
    tokens = _build_lexical_tokens(segment)
    if tokens:
        payload['tokens'] = tokens
    if reference_text:
        payload['referenceText'] = reference_text
    return payload


def _build_lexical_tokens(segment: dict[str, Any]) -> list[dict[str, Any]]:
    display_text = strip_subtitle_display_text(str(segment.get('text') or ''))
    if not display_text:
        return []
    units = _build_word_token_units(segment)
    if not units or join_subtitle_parts([str(unit['text']) for unit in units]) != display_text:
        units = _build_char_token_units(segment, display_text)
    merged = _merge_lexical_atom_units(units)
    return [
        {
            'tokenId': f'tok-{index}',
            'text': str(unit['text']),
            'startMs': int(unit['startMs']),
            'endMs': int(unit['endMs']),
        }
        for index, unit in enumerate(merged)
        if str(unit.get('text') or '')
    ]


def _build_word_token_units(segment: dict[str, Any]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    words = segment.get('words') or []
    for word in words:
        token_text = strip_subtitle_display_text(str(word.get('text') or ''))
        if not token_text:
            continue
        start_ms = _safe_int(word.get('startMs') or word.get('start_ms'))
        end_ms = _safe_int(word.get('endMs') or word.get('end_ms'))
        if end_ms <= start_ms:
            continue
        units.append({'text': token_text, 'startMs': start_ms, 'endMs': end_ms})
    return units


def _build_char_token_units(segment: dict[str, Any], display_text: str) -> list[dict[str, Any]]:
    start_ms = _safe_int(segment.get('startMs'))
    end_ms = max(_safe_int(segment.get('endMs')), start_ms + 1)
    duration_ms = max(end_ms - start_ms, len(display_text) or 1)
    units: list[dict[str, Any]] = []
    cursor = start_ms
    for index, char in enumerate(display_text):
        if index == len(display_text) - 1:
            next_cursor = end_ms
        else:
            next_cursor = start_ms + round(duration_ms * ((index + 1) / max(len(display_text), 1)))
            next_cursor = max(next_cursor, cursor + 1)
        units.append({'text': char, 'startMs': cursor, 'endMs': next_cursor})
        cursor = next_cursor
    return units


def _merge_lexical_atom_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(units):
        match_end = _find_lexical_atom_end(units, cursor)
        if match_end <= cursor and cursor + 1 < len(units):
            left = str(units[cursor].get('text') or '')
            right = str(units[cursor + 1].get('text') or '')
            if len(left) == len(right) == 1 and left == right and _is_cjk_char(left):
                match_end = cursor + 2
        if match_end > cursor:
            merged.append(_merge_units(units[cursor:match_end]))
            cursor = match_end
            continue
        merged.append(dict(units[cursor]))
        cursor += 1
    return merged


def _find_lexical_atom_end(units: list[dict[str, Any]], start_index: int) -> int:
    for atom in LEXICAL_ATOM_TERMS:
        text = ''
        for end_index in range(start_index, len(units)):
            text = join_subtitle_parts([text, str(units[end_index].get('text') or '')]) if text else str(units[end_index].get('text') or '')
            if text == atom:
                return end_index + 1
            if not atom.startswith(text):
                break
    return start_index


def _merge_units(units: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        'text': join_subtitle_parts([str(unit.get('text') or '') for unit in units]),
        'startMs': int(units[0].get('startMs') or 0),
        'endMs': int(units[-1].get('endMs') or 0),
    }


def _build_subtitle_parts_from_token_ranges(
    segment: dict[str, Any],
    payload: dict[str, Any],
    *,
    enhanced_text: str,
) -> tuple[list[str], list[dict[str, int]]]:
    tokens = _build_lexical_tokens(segment)
    if not tokens:
        return [], []
    ranges = _normalize_token_ranges(payload.get('subtitlePartTokenRanges'), tokens)
    if not ranges:
        return [], []

    source_text = join_subtitle_parts([str(token.get('text') or '') for token in tokens])
    enhanced_display_text = strip_subtitle_display_text(enhanced_text)
    token_texts = _map_enhanced_text_to_token_texts(tokens, enhanced_display_text, source_text)

    parts: list[str] = []
    part_ranges: list[dict[str, int]] = []
    for start_index, end_index in ranges:
        part_text = join_subtitle_parts(token_texts[start_index:end_index])
        if not part_text or len(part_text) > max_subtitle_chars_for_text(part_text):
            return [], []
        parts.append(part_text)
        part_ranges.append({
            'startMs': int(tokens[start_index].get('startMs') or 0),
            'endMs': int(tokens[end_index - 1].get('endMs') or 0),
        })

    if not parts or _has_risky_llm_boundaries(parts):
        return [], []
    if join_subtitle_parts(parts) != (enhanced_display_text or source_text):
        return [], []
    return parts, part_ranges


def _normalize_token_ranges(value: object, tokens: list[dict[str, Any]]) -> list[tuple[int, int]]:
    if not isinstance(value, list) or not value or not tokens:
        return []
    index_by_id = {str(token.get('tokenId') or ''): index for index, token in enumerate(tokens)}
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for item in value:
        if not isinstance(item, dict):
            return []
        start_token = str(item.get('startTokenId') or item.get('startToken') or '').strip()
        end_token = str(item.get('endTokenId') or item.get('endToken') or '').strip()
        if start_token not in index_by_id:
            return []
        start_index = index_by_id[start_token]
        end_index = _resolve_token_range_end_index(end_token, index_by_id, len(tokens))
        if end_index is None:
            return []
        if start_index != cursor or end_index <= start_index:
            return []
        ranges.append((start_index, end_index))
        cursor = end_index
    if cursor != len(tokens):
        return []
    return ranges


def _resolve_token_range_end_index(token_id: str, index_by_id: dict[str, int], token_count: int) -> Optional[int]:
    if token_id in index_by_id:
        return index_by_id[token_id]
    terminal_id = f'tok-{token_count}'
    if token_id == terminal_id:
        return token_count
    return None


def _map_enhanced_text_to_token_texts(
    tokens: list[dict[str, Any]],
    enhanced_display_text: str,
    source_text: str,
) -> list[str]:
    source_token_texts = [str(token.get('text') or '') for token in tokens]
    if not enhanced_display_text or enhanced_display_text == source_text:
        return source_token_texts
    if len(enhanced_display_text) != len(source_text):
        return source_token_texts

    mapped: list[str] = []
    cursor = 0
    for token_text in source_token_texts:
        token_length = len(token_text)
        mapped.append(enhanced_display_text[cursor:cursor + token_length])
        cursor += token_length
    if join_subtitle_parts(mapped) != enhanced_display_text:
        return source_token_texts
    return mapped


def _split_payloads_into_windows(payloads: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not payloads:
        return []

    window_ms = max(int(settings.subtitle_enhancer_window_seconds or 0) * 1000, 60_000)
    max_window_ms = max(int(settings.subtitle_enhancer_window_max_seconds or 0) * 1000, window_ms)
    min_gap_ms = max(int(settings.subtitle_enhancer_window_min_gap_ms or 0), 0)
    windows: list[list[dict[str, Any]]] = []
    current_window: list[dict[str, Any]] = []
    window_start_ms = _payload_start_ms(payloads[0])
    previous_end_ms = _payload_end_ms(payloads[0])

    for payload in payloads:
        start_ms = _payload_start_ms(payload)
        end_ms = _payload_end_ms(payload)
        elapsed_ms = max(0, start_ms - window_start_ms)
        gap_ms = max(0, start_ms - previous_end_ms)
        should_split = bool(current_window) and elapsed_ms >= window_ms and gap_ms >= min_gap_ms
        must_split = bool(current_window) and elapsed_ms >= max_window_ms
        if should_split or must_split:
            windows.append(current_window)
            current_window = []
            window_start_ms = start_ms
        current_window.append(payload)
        previous_end_ms = max(previous_end_ms, end_ms)

    if current_window:
        windows.append(current_window)
    return windows


def _payload_start_ms(payload: dict[str, Any]) -> int:
    return _safe_int(payload.get('startMs'))


def _payload_end_ms(payload: dict[str, Any]) -> int:
    return max(_safe_int(payload.get('endMs')), _payload_start_ms(payload))


def _build_retry_batches(payloads: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not payloads:
        return []
    return _pack_payload_batches(payloads, max_chars=900, max_segments=4)


def _limit_batches_for_job_budget(batches: list[list[dict[str, Any]]]) -> tuple[list[list[dict[str, Any]]], int]:
    payloads = [payload for batch in batches for payload in batch]
    max_payloads = max(int(settings.subtitle_enhancer_max_segments_per_job or 0), 0)
    if not payloads or max_payloads <= 0 or len(payloads) <= max_payloads:
        return batches, 0

    selected_payloads = _select_payloads_by_priority(payloads, max_payloads)
    return (
        _pack_payload_batches(
            selected_payloads,
            max_chars=max(int(settings.subtitle_enhancer_max_batch_chars), 800),
            max_segments=max(int(settings.subtitle_enhancer_max_batch_segments), 1),
        ),
        len(payloads) - len(selected_payloads),
    )


def _limit_retry_payloads(payloads: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    max_retry_payloads = max(int(settings.subtitle_enhancer_max_retry_segments or 0), 0)
    if not payloads or max_retry_payloads <= 0:
        return [], len(payloads)
    if len(payloads) <= max_retry_payloads:
        return payloads, 0
    selected_payloads = _select_payloads_by_priority(payloads, max_retry_payloads)
    return selected_payloads, len(payloads) - len(selected_payloads)


def _select_payloads_by_priority(payloads: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    indexed_payloads = list(enumerate(payloads))
    ranked = sorted(
        indexed_payloads,
        key=lambda item: (-_score_enhancement_payload(item[1]), item[0]),
    )
    selected_indexes = sorted(index for index, _payload in ranked[:limit])
    return [payloads[index] for index in selected_indexes]


def _score_enhancement_payload(payload: dict[str, Any]) -> int:
    text = str(payload.get('text') or '')
    current_parts = [str(item or '') for item in payload.get('currentParts') or []]
    reference_text = str(payload.get('referenceText') or '')
    display_text = strip_subtitle_display_text(text)
    score = len(display_text)
    if len(current_parts) > 1:
        score += 80 + (len(current_parts) * 8)
    if len(current_parts) > 2:
        score += 60
    if _has_risky_llm_boundaries(current_parts):
        score += 1000
    if _reference_text_has_meaningful_delta(text, reference_text):
        score += 180
    elif reference_text:
        score += 5
    return score


def _pack_payload_batches(
    payloads: list[dict[str, Any]],
    *,
    max_chars: int,
    max_segments: int,
) -> list[list[dict[str, Any]]]:
    max_chars = max(int(max_chars), 1)
    max_segments = max(int(max_segments), 1)
    batches: list[list[dict[str, Any]]] = []
    current_batch: list[dict[str, Any]] = []
    current_chars = 0

    for payload in payloads:
        payload_chars = len(str(payload.get('text') or '')) + sum(
            len(str(item or ''))
            for item in payload.get('currentParts') or []
        )
        should_split = current_batch and (
            len(current_batch) >= max_segments or current_chars + payload_chars > max_chars
        )
        if should_split:
            batches.append(current_batch)
            current_batch = []
            current_chars = 0
        current_batch.append(payload)
        current_chars += payload_chars

    if current_batch:
        batches.append(current_batch)
    return batches


def _should_enhance_segment(
    segment_text: str,
    segment_parts: list[dict[str, Any]],
    *,
    reference_text: str = '',
) -> bool:
    display_text = strip_subtitle_display_text(segment_text)
    if not display_text:
        return False
    if _reference_text_has_meaningful_delta(segment_text, reference_text):
        return True
    if len(display_text) <= max_subtitle_chars_for_text(display_text) and len(segment_parts) <= 1:
        return False
    return True


def _normalize_language_profile(language_profile: str | None) -> str:
    normalized = str(language_profile or '').strip().lower()
    return 'en' if normalized.startswith('en') else 'zh'


def _build_english_system_prompt(script_context_instruction: str) -> str:
    return script_context_instruction + (
        'You are an English subtitle and lyric caption planning assistant. '
        'Plan concise screen captions from the input JSON and do light cleanup only. '
        'Do not translate, and do not output Chinese text unless Chinese characters already appear in the input text or reference text. '
        'Do not invent lyrics, facts, speakers, or extra lines. '
        'If the text is already valid English, preserve the original words, casing, contractions, numbers, and meaning. '
        'If referenceText or script context is provided, use it only to restore obvious English words, spelling, casing, and spaces that match the current time span. '
        'If input contains tokens, plan subtitlePartTokenRanges from token boundaries first; tokens are indivisible words or phrases and must never be split. '
        'Return a JSON object exactly in this format: '
        '{"segments":[{"segmentId":"...","text":"...",'
        '"subtitlePartTokenRanges":[{"startTokenId":"tok-0","endTokenId":"tok-3"}],'
        '"subtitleParts":["legacy compatible field"]}]}. '
        'subtitlePartTokenRanges use half-open ranges: startTokenId included, endTokenId excluded; '
        'the final end may be tok-total, for example tok-9 for 9 tokens. '
        f'Each English subtitle part should target at most {MAX_SUBTITLE_LATIN_CHARS} characters. '
        'Split on English word boundaries and natural phrase boundaries; never split inside an English word, contraction, artist name, song title, or fixed lyric phrase. '
        'Preserve spaces between English words. '
        'subtitleParts are final on-screen captions, so remove display punctuation only when it does not change meaning. '
        'After removing display punctuation, joining subtitleParts must equal text. '
        'If adjacent English words are split across subtitleParts, do not add leading or trailing spaces; the system will restore one space at word boundaries. '
        'Prefer readable lyric phrasing over mechanical character counting. '
        'If retryHint is present, fix the highlighted boundary issue.'
    )


def _request_enhanced_batch(
    batch: list[dict[str, Any]],
    *,
    script_context_prompt: str = '',
    language_profile: str = 'zh',
    provider_context: Optional[ProviderCallContext] = None,
    observation_key: Optional[str] = None,
    attempt: int = 1,
) -> dict[str, dict[str, Any]]:
    prompt = json.dumps(batch, ensure_ascii=False)
    script_context_instruction = f'{script_context_prompt}\n' if script_context_prompt else ''
    english_system_prompt = (
        _build_english_system_prompt(script_context_instruction)
        if _normalize_language_profile(language_profile) == 'en'
        else None
    )
    request_payload = {
            'model': settings.subtitle_enhancer_model,
            'temperature': 0,
            'response_format': {'type': 'json_object'},
            'messages': [
                {
                    'role': 'system',
                    'content': english_system_prompt or script_context_instruction + (
                        '你是节目字幕短句规划助手。请根据输入 JSON 做字幕短句规划与轻量文本整理，'
                        '优先保证字幕像人工整理而不是机械按字数切分。禁止增删事实，禁止改动说话人身份，禁止扩写。'
                        '如果 text 没有明显错误，尽量保持与输入一致；如果某段提供 referenceText，可参考它做极轻量纠错，但不得改动事实。'
                        '输入通常来自约 10 分钟的节目时间窗口；startMs/endMs 仅用于理解停顿、节奏和相邻语境，不要输出它们。'
                        '如果输入包含 tokens，必须优先基于 tokens 规划字幕边界；tokens 是不可拆词法原子，'
                        '包括人名、地名、英文短语、双字词和重复词，禁止把任何 token 从中间拆开。'
                        '必须返回 JSON 对象，格式为 '
                        '{"segments":[{"segmentId":"...","text":"...",'
                        '"subtitlePartTokenRanges":[{"startTokenId":"tok-0","endTokenId":"tok-3"}],'
                        '"subtitleParts":["兼容旧字段"]}]}。'
                        'subtitlePartTokenRanges 使用半开区间：startTokenId 包含，endTokenId 不包含；'
                        '最后一条可以用 tok-总数 作为 endTokenId，例如 9 个 token 的终点是 tok-9。'
                        f'subtitleParts 可以重新规划条数：纯中文每条不得超过 {MAX_SUBTITLE_CHARS} 个汉字；'
                        f'英文或中英混合每条不得超过 {MAX_SUBTITLE_LATIN_CHARS} 个字符，并且必须按英文单词边界断句，保留英文单词之间的空格。'
                        'subtitleParts 面向最终屏幕字幕，请不要保留逗号、句号、问号、引号等展示标点；'
                        '去掉这些展示标点后，subtitleParts 拼接结果必须与 text 完全一致；'
                        '如果相邻英文单词分属两条 subtitleParts，不要在 part 末尾或开头塞空格，系统会按英文词边界恢复一个空格。'
                        '请优先保持完整词组、机构名、头衔、人名、自我介绍结构、主句与补充句的语义完整。'
                        '当台本/唱词原文参考中存在与当前 text 发音或上下文匹配的句子时，'
                        '应优先使用台本/唱词里的正确写法，尤其是歌词、英文词、艺名、大小写和空格；'
                        '但不能整段替换成与当前时间段明显不匹配的台本内容。'
                        '禁止把语法助词切到下一条开头，尤其不能让下一条以“了、着、过、的、地、得”开头，'
                        '例如应切成“也与美桃湾一起入选了 / 全省文旅赋能”，不能切成“也与美桃湾一起入选 / 了全省文旅赋能”。'
                        '不要把“一起、共同、一同”和后面的动作谓语拆到两条之间；'
                        '也不要把“与/跟/和 + 对象”和后面的“一起/共同/一同 + 动作”拆开，'
                        '例如应切成“佛山古镇呢 / 也与美桃湾一起入选了”，不能切成“佛山古镇呢也与美桃湾 / 一起入选了”。'
                        '不要切断“十大、百强、十佳、三项、三类”等数词固定短语。'
                        '如果输入里带 retryHint，请重点修复这些边界问题。'
                        '尤其注意以下句型：'
                        '1. 自我介绍/头衔句型要优先切成“开场/来源”“机构+头衔”“姓名”，'
                        '例如“大家好我是来自深圳智映无碍科技的创始人林志鹏”应优先规划为'
                        '["大家好我是来自深圳","智映无碍科技的创始人","林志鹏"]；'
                        '2. 动作承接句型要避免把主语留到上一条结尾，'
                        '例如“认为是的请举手我们现场调研一下”应优先规划为'
                        '["认为是的请举手","我们现场调研一下"]；'
                        '3. 禁止把双字词、固定短语和判断结构从中间切开，例如“最悲观的”不能切成“最悲/观的”。'
                    ),
                },
                {
                    'role': 'user',
                    'content': (
                        '请严格输出 JSON，不要附带解释。输入 JSON 如下：\n'
                        f'{prompt}'
                    ),
                },
            ],
        }
    started_at = _utcnow()
    try:
        response = httpx.post(
            f'{settings.subtitle_enhancer_base_url_value}/chat/completions',
            headers={
                'Authorization': f'Bearer {settings.subtitle_enhancer_api_key_value}',
                'Content-Type': 'application/json',
            },
            json=request_payload,
            timeout=max(int(settings.subtitle_enhancer_timeout_seconds), 30),
        )
        data = _read_json_response(response)
        try:
            content = data['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError) as exc:
            raise SubtitleEnhancerError(f'字幕增强返回结构异常: {data}') from exc

        if not isinstance(content, str):
            raise SubtitleEnhancerError('字幕增强没有返回可解析的文本 JSON')

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise SubtitleEnhancerError(f'字幕增强返回了非法 JSON: {content[:500]}') from exc
    except Exception as exc:
        _record_deepseek_observation(
            provider_context,
            observation_key=observation_key,
            attempt=attempt,
            status='failed',
            started_at=started_at,
            error=exc,
        )
        raise

    usage = _deepseek_usage(data)
    _record_deepseek_observation(
        provider_context,
        observation_key=observation_key,
        attempt=attempt,
        status='completed',
        request_id=str(data.get('request_id') or data.get('id') or '').strip() or None,
        usage=usage,
        meters=_deepseek_meters(usage),
        started_at=started_at,
    )

    result: dict[str, dict[str, Any]] = {}
    for item in parsed.get('segments') or []:
        segment_id = str(item.get('segmentId') or '').strip()
        if segment_id:
            result[segment_id] = item
    return result


def _request_enhanced_batches(
    batches: list[list[dict[str, Any]]],
    *,
    script_context_prompt: str = '',
    language_profile: str = 'zh',
    provider_context: Optional[ProviderCallContext] = None,
) -> list[dict[str, dict[str, Any]]]:
    if len(batches) <= 1:
        return [
            _call_request_enhanced_batch(
                batches[0],
                script_context_prompt,
                language_profile,
                provider_context,
                'DeepSeek 字幕增强',
            )
        ] if batches else []

    max_workers = _resolve_batch_max_workers(len(batches))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                _call_request_enhanced_batch,
                batch,
                script_context_prompt,
                language_profile,
                provider_context,
                'DeepSeek 字幕增强',
            )
            for batch in batches
        ]
        return [future.result() for future in futures]


def _request_enhanced_batches_resilient(
    batches: list[list[dict[str, Any]]],
    *,
    script_context_prompt: str = '',
    language_profile: str = 'zh',
    deadline: Optional[float] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    progress_offset: int = 0,
    total_batches: Optional[int] = None,
    stage_label: str = 'DeepSeek 字幕增强',
    provider_context: Optional[ProviderCallContext] = None,
) -> tuple[list[dict[str, dict[str, Any]]], list[str]]:
    if not batches:
        return [], []
    if _deadline_expired(deadline):
        return [], ['DeepSeek 字幕增强总时限已触顶，跳过剩余批次']
    total = max(int(total_batches or (progress_offset + len(batches))), 1)
    if len(batches) == 1:
        try:
            result = _call_request_enhanced_batch(
                batches[0],
                script_context_prompt,
                language_profile,
                provider_context,
                stage_label,
            )
            _emit_batch_progress(progress_callback, progress_offset + 1, total, f'{stage_label} 1/1 批完成')
            return [result], []
        except SubtitleEnhancerError as exc:
            _emit_batch_progress(progress_callback, progress_offset + 1, total, f'{stage_label} 1/1 批失败')
            return [], [str(exc)]
        except Exception as exc:  # pragma: no cover - provider/network safety net
            _emit_batch_progress(progress_callback, progress_offset + 1, total, f'{stage_label} 1/1 批失败')
            return [], [str(exc)]

    max_workers = _resolve_batch_max_workers(len(batches))
    results: list[dict[str, dict[str, Any]] | None] = [None] * len(batches)
    errors: list[str] = []
    completed_count = 0
    executor = ThreadPoolExecutor(max_workers=max_workers)
    future_indexes = {
        executor.submit(
            _call_request_enhanced_batch,
            batch,
            script_context_prompt,
            language_profile,
            provider_context,
            stage_label,
        ): index
        for index, batch in enumerate(batches)
    }
    try:
        remaining_seconds = _deadline_remaining_seconds(deadline)
        future_iterator = (
            as_completed(future_indexes, timeout=remaining_seconds)
            if remaining_seconds is not None
            else as_completed(future_indexes)
        )
        for future in future_iterator:
            completed_count += 1
            index = future_indexes[future]
            try:
                results[index] = future.result()
                _emit_batch_progress(
                    progress_callback,
                    progress_offset + completed_count,
                    total,
                    f'{stage_label} {completed_count}/{len(batches)} 批完成',
                )
            except SubtitleEnhancerError as exc:
                errors.append(f'第 {index + 1} 批: {exc}')
                _emit_batch_progress(
                    progress_callback,
                    progress_offset + completed_count,
                    total,
                    f'{stage_label} {completed_count}/{len(batches)} 批失败',
                )
            except Exception as exc:  # pragma: no cover - provider/network safety net
                errors.append(f'第 {index + 1} 批: {exc}')
                _emit_batch_progress(
                    progress_callback,
                    progress_offset + completed_count,
                    total,
                    f'{stage_label} {completed_count}/{len(batches)} 批失败',
                )
    except FutureTimeoutError:
        pending_count = sum(1 for future in future_indexes if not future.done())
        errors.append(f'DeepSeek 字幕增强总时限已触顶，跳过 {pending_count} 个未完成批次')
    finally:
        for future in future_indexes:
            if not future.done():
                future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
    return [result for result in results if result is not None], errors


def _call_request_enhanced_batch(
    batch: list[dict[str, Any]],
    script_context_prompt: str = '',
    language_profile: str = 'zh',
    provider_context: Optional[ProviderCallContext] = None,
    stage_label: str = 'DeepSeek 字幕增强',
) -> dict[str, dict[str, Any]]:
    normalized_profile = _normalize_language_profile(language_profile)
    provider_kwargs = {}
    if provider_context is not None:
        provider_kwargs = {
            'provider_context': provider_context,
            'observation_key': _deepseek_observation_key(provider_context, batch, stage_label),
        }
    if script_context_prompt or normalized_profile == 'en' or provider_kwargs:
        return _request_enhanced_batch(
            batch,
            script_context_prompt=script_context_prompt,
            language_profile=normalized_profile,
            **provider_kwargs,
        )
    return _request_enhanced_batch(batch)


def _deepseek_observation_key(
    context: ProviderCallContext,
    batch: list[dict[str, Any]],
    stage_label: str,
) -> str:
    segment_ids = [str(item.get('segmentId') or '') for item in batch]
    digest = sha256(json.dumps(segment_ids, ensure_ascii=False).encode('utf-8')).hexdigest()[:20]
    stage = 'retry' if '重试' in stage_label else 'primary'
    return f'deepseek:{context.job_id or context.project_id}:{stage}:{digest}'


def _record_deepseek_observation(
    context: Optional[ProviderCallContext],
    *,
    observation_key: Optional[str],
    attempt: int,
    status: str,
    request_id: Optional[str] = None,
    usage: Optional[dict[str, int | float]] = None,
    meters: Optional[list[ProviderMeterObservation]] = None,
    started_at: Optional[datetime] = None,
    error: Optional[Exception] = None,
) -> None:
    if context is None or not observation_key:
        return
    try:
        record_legacy_provider_observation(
            context,
            provider='deepseek',
            model=settings.subtitle_enhancer_model,
            operation='chat_completion',
            observed_call=ProviderCallObservation(
                idempotency_key=observation_key,
                attempt=attempt,
                status=status,
                request_id=request_id,
                usage=usage,
                meters=meters or [],
                started_at=started_at,
                completed_at=_utcnow(),
                error_code=type(error).__name__ if error else None,
                error_summary=f'{type(error).__name__}: DeepSeek batch failed' if error else None,
            ),
        )
    except Exception as exc:  # pragma: no cover - observation must stay fail-open
        logger.warning('DeepSeek provider observation skipped (%s)', type(exc).__name__)


def _deepseek_usage(data: dict[str, Any]) -> Optional[dict[str, int | float]]:
    raw_usage = data.get('usage')
    if not isinstance(raw_usage, dict):
        return None
    allowed = (
        'prompt_tokens',
        'completion_tokens',
        'total_tokens',
        'prompt_cache_hit_tokens',
        'prompt_cache_miss_tokens',
    )
    usage = {
        key: value
        for key in allowed
        if (value := _nonnegative_number(raw_usage.get(key))) is not None
    }
    return usage or None


def _deepseek_meters(usage: Optional[dict[str, int | float]]) -> list[ProviderMeterObservation]:
    if not usage:
        return [
            ProviderMeterObservation(meter='input_tokens', quantity=None, unit='token'),
            ProviderMeterObservation(meter='output_tokens', quantity=None, unit='token'),
        ]
    has_cache_split = (
        'prompt_cache_hit_tokens' in usage
        and 'prompt_cache_miss_tokens' in usage
    )
    mapping = [('completion_tokens', 'output_tokens')]
    if has_cache_split:
        mapping.extend((
            ('prompt_cache_hit_tokens', 'cache_hit_tokens'),
            ('prompt_cache_miss_tokens', 'cache_miss_tokens'),
        ))
    else:
        mapping.insert(0, ('prompt_tokens', 'input_tokens'))
    return [
        ProviderMeterObservation(
            meter=meter,
            quantity=Decimal(str(usage[key])),
            unit='token',
        )
        for key, meter in mapping
        if key in usage
    ]


def _nonnegative_number(value: Any) -> Optional[int | float]:
    if isinstance(value, bool):
        return None
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not quantity.is_finite() or quantity < 0:
        return None
    return int(quantity) if quantity == quantity.to_integral_value() else float(quantity)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _resolve_batch_max_workers(batch_count: int) -> int:
    max_workers = max(int(settings.subtitle_enhancer_max_workers or 1), 1)
    return min(max_workers, 8, max(int(batch_count), 1))


def _resolve_job_deadline() -> Optional[float]:
    timeout_seconds = max(int(settings.subtitle_enhancer_job_timeout_seconds or 0), 0)
    if timeout_seconds <= 0:
        return None
    return perf_counter() + timeout_seconds


def _deadline_expired(deadline: Optional[float]) -> bool:
    return deadline is not None and perf_counter() >= deadline


def _deadline_remaining_seconds(deadline: Optional[float]) -> Optional[float]:
    if deadline is None:
        return None
    return max(deadline - perf_counter(), 0.001)


def _emit_batch_progress(
    progress_callback: Optional[Callable[[int, int, str], None]],
    completed: int,
    total: int,
    message: str,
) -> None:
    if not progress_callback:
        return
    try:
        progress_callback(completed, total, message)
    except Exception:
        return


def _format_batch_error_warning(errors: list[str]) -> str:
    visible_errors = [item for item in errors if item][:3]
    suffix = f'；另有 {len(errors) - len(visible_errors)} 个批次失败' if len(errors) > len(visible_errors) else ''
    return f'部分 DeepSeek 字幕增强批次失败，已保留成功批次并回退其余片段：{"；".join(visible_errors)}{suffix}'


def _normalize_segment_text(value: object, fallback: str) -> str:
    normalized = str(value or '').strip()
    if not normalized:
        return fallback
    return normalized


def _normalize_subtitle_parts(value: object, *, expected_text: str) -> list[str]:
    if not isinstance(value, list):
        return []
    parts: list[str] = []
    for item in value:
        normalized = strip_subtitle_display_text(str(item or ''))
        if not normalized:
            return []
        if len(normalized) > max_subtitle_chars_for_text(normalized):
            return []
        parts.append(normalized)
    if not parts:
        return []
    expected_display_text = strip_subtitle_display_text(str(expected_text or ''))
    repaired = _repair_special_sentence_parts(expected_display_text, parts)
    if repaired:
        parts = repaired
    if join_subtitle_parts(parts) != expected_display_text:
        return []
    if _has_risky_llm_boundaries(parts):
        return []
    return parts


def _repair_special_sentence_parts(expected_text: str, parts: list[str]) -> list[str]:
    repaired_intro = _repair_intro_sentence_parts(expected_text)
    if repaired_intro:
        return repaired_intro
    return parts


def _repair_intro_sentence_parts(text: str) -> list[str]:
    normalized = strip_subtitle_display_text(str(text or ''))
    if not normalized:
        return []
    match = re.match(r'^(大家好)?(我是来自)(.+?)(的(?:创始人|联合创始人|创办人|负责人|主持人|制作人|发言人|CEO|CTO|总经理|董事长))([A-Za-z\u4e00-\u9fff·]{2,8})$', normalized)
    if not match:
        return []

    greeting = match.group(1) or ''
    lead = match.group(2)
    organization = match.group(3)
    title = match.group(4)
    name = match.group(5)
    if not PERSON_NAME_RE.fullmatch(name):
        return []

    prefix = greeting + lead
    company_title = organization + title
    if len(name) > MAX_SUBTITLE_CHARS or len(prefix) >= MAX_SUBTITLE_CHARS:
        return []

    min_head_len = max(0, len(company_title) - MAX_SUBTITLE_CHARS)
    max_head_len = min(len(organization), MAX_SUBTITLE_CHARS - len(prefix))
    if min_head_len > max_head_len:
        return []

    preferred_head_len = _pick_intro_org_head_len(organization, min_head_len, max_head_len)
    first = prefix + organization[:preferred_head_len]
    second = organization[preferred_head_len:] + title
    rebuilt = [part for part in (first, second, name) if part]
    if any(len(part) > MAX_SUBTITLE_CHARS for part in rebuilt):
        return []
    if join_subtitle_parts(rebuilt) != normalized:
        return []
    return rebuilt


def _pick_intro_org_head_len(organization: str, min_head_len: int, max_head_len: int) -> int:
    for prefix in COMMON_LOCATION_PREFIXES:
        if organization.startswith(prefix) and min_head_len <= len(prefix) <= max_head_len:
            return len(prefix)
    return min(max(min_head_len, 2), max_head_len)


def _has_risky_llm_boundaries(parts: list[str]) -> bool:
    for index in range(len(parts) - 1):
        left = parts[index]
        right = parts[index + 1]
        if not left or not right:
            return True
        pair = left[-1] + right[0]
        if right[0] in RISKY_BOUNDARY_START_CHARS:
            return True
        if pair in RISKY_BOUNDARY_BIGRAMS:
            return True
        if pair[0] == pair[1] and _is_cjk_char(pair[0]):
            return True
        if left[-1] in RISKY_BOUNDARY_NUMERAL_CHARS and right[0] in RISKY_BOUNDARY_NUMERAL_FOLLOW_CHARS:
            return True
        if any(left.endswith(suffix) for suffix in RISKY_BOUNDARY_ADVERBIAL_SUFFIXES) and any(
            right.startswith(prefix) for prefix in RISKY_BOUNDARY_PREDICATE_PREFIXES
        ):
            return True
        if any(right.startswith(prefix) for prefix in RISKY_BOUNDARY_ADVERBIAL_STARTS) and any(
            marker in left[-8:] for marker in RISKY_BOUNDARY_OBJECT_MARKERS
        ):
            return True
        if any(right.startswith(suffix) for suffix in RISKY_BOUNDARY_SUFFIXES):
            return True
    return False


def _build_reference_text(segment: dict[str, Any], reference_sentences: list[dict[str, Any]]) -> str:
    if not reference_sentences:
        return ''

    start_ms = _safe_int(segment.get('startMs'))
    end_ms = max(_safe_int(segment.get('endMs')), start_ms)
    matched_texts: list[str] = []
    nearest_text = ''
    nearest_distance = None

    for sentence in reference_sentences:
        text = str(sentence.get('text') or '').strip()
        if not text:
            continue
        sentence_start = _safe_int(sentence.get('startMs'))
        sentence_end = max(_safe_int(sentence.get('endMs')), sentence_start)
        overlap = min(end_ms, sentence_end) - max(start_ms, sentence_start)
        distance = min(abs(sentence_start - start_ms), abs(sentence_end - end_ms))
        if overlap >= 0 or distance <= 800:
            matched_texts.append(text)
            if len(''.join(matched_texts)) >= MAX_REFERENCE_TEXT_CHARS:
                break
        if nearest_distance is None or distance < nearest_distance:
            nearest_distance = distance
            nearest_text = text

    if matched_texts:
        return join_subtitle_parts(matched_texts)[:MAX_REFERENCE_TEXT_CHARS]
    return nearest_text[:MAX_REFERENCE_TEXT_CHARS]


def _reference_text_has_meaningful_delta(segment_text: str, reference_text: str) -> bool:
    reference_display = strip_subtitle_display_text(str(reference_text or ''))
    segment_display = strip_subtitle_display_text(str(segment_text or ''))
    if not reference_display or not segment_display:
        return False
    if reference_display == segment_display:
        return False
    if len(segment_display) <= max_subtitle_chars_for_text(segment_display) and (
        segment_display in reference_display or reference_display in segment_display
    ):
        return False
    return True


def _safe_int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _is_cjk_char(char: str) -> bool:
    if not char:
        return False
    codepoint = ord(char)
    return 0x3400 <= codepoint <= 0x4DBF or 0x4E00 <= codepoint <= 0x9FFF


def _read_json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SubtitleEnhancerError(f'字幕增强返回了非 JSON 响应: {response.text[:500]}') from exc
    if response.status_code >= 400:
        message = payload.get('message') or payload.get('code') or '请求失败'
        raise SubtitleEnhancerError(f'字幕增强请求失败: {message}')
    return payload
