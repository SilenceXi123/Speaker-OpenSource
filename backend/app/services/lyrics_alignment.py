from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import re
from statistics import median
from pathlib import Path
from typing import Any, Iterable

from pypinyin import Style, lazy_pinyin

from app.services.chinese_text import normalize_to_simplified_chinese


LYRICS_REFERENCE_SCHEMA = 'lyrics-reference.v1'
LYRICS_CORRECTION_SCHEMA = 'lyrics-correction.v1'
TIMED_GROUP_GAP_MS = 12_000
TIMED_REPLACEMENT_GAP_MS = 5_000
TIMED_ANCHOR_OFFSET_TOLERANCE_MS = 30_000
TIMED_ABSOLUTE_OFFSET_TOLERANCE_MS = 3_500
TIMED_ANCHOR_OUTLIER_MIN_TOLERANCE_MS = 1_500
TIMED_ANCHOR_OUTLIER_MAD_MULTIPLIER = 3.0
TIMED_ACTIVITY_GAP_MS = 8_000
TIMED_UNANCHORED_PREROLL_MS = 12_000
MIN_TIMED_ANCHOR_COUNT = 3
MIN_TIMED_MATCHED_UNIT_COUNT = 12
MAX_UNTIMED_ANCHOR_GAP_MS = 30_000
MIN_CUE_DURATION_MS = 360
SRT_TIMING_RE = re.compile(
    r'(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*'
    r'(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})'
)
UNIT_RE = re.compile(r'[\u4e00-\u9fff]|[A-Za-z]+(?:[\'’-][A-Za-z]+)*|\d+')
LYRICS_METADATA_RE = re.compile(
    r'^(?:作词|作曲|编曲|演唱|歌手|歌曲|歌词|制作人|混音|母带)\s*[：:]'
    r'|^[\[【(（]\s*(?:verse|chorus|hook|intro|outro|bridge|pre-?chorus|副歌|主歌|前奏|尾奏|间奏)'
    r'(?:\s*\d+)?\s*[\]】)）]$',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LyricsCorrectionResult:
    segments: list[dict[str, Any]]
    report: dict[str, Any]


def build_lyrics_reference(
    *,
    source_name: str,
    extracted_text: str,
    raw_payload: bytes | None = None,
) -> dict[str, Any] | None:
    """Build a durable lyrics reference while preserving SRT timing when available."""
    raw_text = _decode_payload(raw_payload) if raw_payload else str(extracted_text or '')
    timed_lines = _parse_srt_lines(raw_text)
    if timed_lines:
        lines = _assign_timed_groups(timed_lines)
        timing_mode = 'timed_srt'
    else:
        source_suffix = Path(str(source_name or '')).suffix.lower()
        lyrics_text = raw_text if raw_payload and source_suffix in {'.txt', '.md', '.srt', '.lrc'} else extracted_text
        lines = _parse_untimed_lines(str(lyrics_text or raw_text))
        timing_mode = 'untimed_text'
    if not lines:
        return None
    digest_source = '\n'.join(
        f"{line.get('start_ms', '')}|{line.get('end_ms', '')}|{line['text']}"
        for line in lines
    )
    return {
        'schema_version': LYRICS_REFERENCE_SCHEMA,
        'source_name': str(source_name or '歌词').strip() or '歌词',
        'timing_mode': timing_mode,
        'line_count': len(lines),
        'group_count': len({int(line.get('group_index') or 0) for line in lines}),
        'reference_hash': hashlib.sha256(digest_source.encode('utf-8')).hexdigest(),
        'lines': lines,
    }


def attach_lyrics_reference(
    context: dict[str, Any],
    reference: dict[str, Any] | None,
) -> dict[str, Any]:
    updated = deepcopy(context)
    references = list(updated.get('lyrics_references') or [])
    if reference:
        reference_hash = str(reference.get('reference_hash') or '')
        references = [
            item for item in references
            if str(item.get('reference_hash') or '') != reference_hash
        ]
        references.append(deepcopy(reference))
    updated['lyrics_references'] = references
    updated['lyrics_correction_mode'] = bool(references)
    summary = dict(updated.get('summary') or {})
    summary['lyrics_reference_count'] = len(references)
    summary['lyrics_line_count'] = sum(int(item.get('line_count') or 0) for item in references)
    updated['summary'] = summary
    return updated


def apply_lyrics_correction_mode(
    segments: Iterable[dict[str, Any]],
    script_context: dict[str, Any] | None,
    *,
    source_duration_ms: int | None = None,
) -> LyricsCorrectionResult:
    original = [deepcopy(segment) for segment in segments]
    enabled = bool((script_context or {}).get('lyrics_correction_mode'))
    references = list((script_context or {}).get('lyrics_references') or [])
    base_report: dict[str, Any] = {
        'schema_version': LYRICS_CORRECTION_SCHEMA,
        'enabled': enabled,
        'status': 'disabled' if not enabled else 'not_applied',
        'source_count': len(references),
        'lyrics_line_count': sum(int(item.get('line_count') or 0) for item in references),
        'applied_line_count': 0,
        'replaced_segment_count': 0,
        'alignment_anchor_count': 0,
        'applications': [],
        'warnings': [],
    }
    if not enabled:
        return LyricsCorrectionResult(original, base_report)
    if not references:
        base_report['warnings'].append('已开启歌词校对模式，但没有可用歌词文件')
        return LyricsCorrectionResult(original, base_report)

    working = original
    replaced_ids: set[str] = set()
    for reference_index, reference in enumerate(references):
        if str(reference.get('schema_version') or '') != LYRICS_REFERENCE_SCHEMA:
            base_report['warnings'].append('忽略了无法识别的旧版歌词数据')
            continue
        mode = str(reference.get('timing_mode') or '')
        if mode == 'timed_srt':
            applications = _build_timed_applications(
                working,
                reference,
                source_duration_ms=source_duration_ms,
            )
        else:
            applications = _build_untimed_applications(
                working,
                reference,
                source_duration_ms=source_duration_ms,
            )
        if not applications:
            base_report['warnings'].append(
                f"{reference.get('source_name') or '歌词'} 未找到足够可靠的时间锚点，已保留原字幕"
            )
            continue
        for application_index, application in enumerate(applications):
            working, replaced = _apply_reference_group(
                working,
                reference=reference,
                application=application,
                reference_index=reference_index,
                application_index=application_index,
            )
            replaced_ids.update(replaced)
            base_report['applications'].append({
                'source_name': reference.get('source_name'),
                'reference_hash': reference.get('reference_hash'),
                'timing_mode': mode,
                'group_index': application['group_index'],
                'start_ms': application['start_ms'],
                'end_ms': application['end_ms'],
                'line_count': len(application['lines']),
                'anchor_count': int(application.get('anchor_count') or 0),
                'discarded_anchor_count': int(application.get('discarded_anchor_count') or 0),
                'matched_unit_count': int(application.get('matched_unit_count') or 0),
                'alignment_method': application.get('alignment_method'),
                'offset_ms': application.get('offset_ms'),
                'confidence': round(float(application.get('confidence') or 1.0), 4),
            })
            base_report['applied_line_count'] += len(application['lines'])
            base_report['alignment_anchor_count'] += int(application.get('anchor_count') or 0)

    base_report['replaced_segment_count'] = len(replaced_ids)
    if base_report['applied_line_count']:
        base_report['status'] = 'applied'
    return LyricsCorrectionResult(working, base_report)


def _parse_srt_lines(text: str) -> list[dict[str, Any]]:
    normalized = str(text or '').replace('\ufeff', '').replace('\r\n', '\n')
    blocks = re.split(r'\n\s*\n', normalized.strip())
    lines: list[dict[str, Any]] = []
    for block in blocks:
        block_lines = [item.strip() for item in block.splitlines() if item.strip()]
        timing_index = next(
            (index for index, item in enumerate(block_lines) if SRT_TIMING_RE.search(item)),
            None,
        )
        if timing_index is None:
            continue
        match = SRT_TIMING_RE.search(block_lines[timing_index])
        if match is None:
            continue
        cue_text = _clean_lyrics_text(' '.join(block_lines[timing_index + 1:]))
        if not cue_text:
            continue
        start_ms = _parse_srt_timestamp(match.group('start'))
        end_ms = _parse_srt_timestamp(match.group('end'))
        if end_ms <= start_ms:
            continue
        lines.append({
            'line_id': f'line-{len(lines) + 1}',
            'order_index': len(lines),
            'text': cue_text,
            'start_ms': start_ms,
            'end_ms': end_ms,
        })
    return lines


def _assign_timed_groups(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    group_index = 0
    previous_end: int | None = None
    for line in sorted(lines, key=lambda item: (item['start_ms'], item['end_ms'])):
        if previous_end is not None and int(line['start_ms']) - previous_end > TIMED_GROUP_GAP_MS:
            group_index += 1
        result.append({**line, 'group_index': group_index})
        previous_end = int(line['end_ms'])
    return result


def _parse_untimed_lines(text: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    group_index = 0
    previous_was_content = False
    for raw_line in str(text or '').replace('\r\n', '\n').splitlines():
        cleaned = _clean_lyrics_text(raw_line)
        if not cleaned:
            if previous_was_content:
                group_index += 1
            previous_was_content = False
            continue
        if re.fullmatch(r'\d+', cleaned) or SRT_TIMING_RE.search(cleaned):
            continue
        if LYRICS_METADATA_RE.search(cleaned):
            continue
        result.append({
            'line_id': f'line-{len(result) + 1}',
            'order_index': len(result),
            'group_index': group_index,
            'text': cleaned,
        })
        previous_was_content = True
    return result


def _build_timed_applications(
    segments: list[dict[str, Any]],
    reference: dict[str, Any],
    *,
    source_duration_ms: int | None,
) -> list[dict[str, Any]]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for line in reference.get('lines') or []:
        if int(line.get('end_ms') or 0) > int(line.get('start_ms') or 0):
            groups[int(line.get('group_index') or 0)].append(deepcopy(line))
    if not groups:
        return []

    duration_limit = int(source_duration_ms or 0) or max(
        (int(item.get('endMs') or 0) for item in segments),
        default=0,
    )
    all_lines = [
        line
        for _, lines in sorted(groups.items())
        for line in sorted(lines, key=lambda item: (int(item['start_ms']), int(item['end_ms'])))
    ]
    alignment = _find_timed_reference_alignment(segments, all_lines)
    if alignment:
        offset_ms = int(round(float(alignment['offset_ms'])))
        if abs(offset_ms) <= TIMED_ABSOLUTE_OFFSET_TOLERANCE_MS:
            alignment_method = 'episode_absolute_srt_verified'
            timed_lines = all_lines
        else:
            alignment_method = 'relative_srt_anchor_warp'
            timed_lines = _warp_timed_lines(
                all_lines,
                anchors=list(alignment['anchors']),
                alignment_offset_ms=float(alignment['offset_ms']),
                duration_limit=duration_limit,
            )
            timed_lines = _trim_untrusted_leading_timed_lines(
                timed_lines,
                segments=segments,
                anchors=list(alignment['anchors']),
            )
    elif _can_trust_direct_srt_timing(
        all_lines,
        source_duration_ms=duration_limit,
    ):
        alignment_method = 'episode_absolute_srt'
        offset_ms = 0
        timed_lines = all_lines
        alignment = {
            'anchors': [],
            'anchor_line_ids': set(),
            'matched_unit_count': 0,
            'confidence': 1.0,
        }
    else:
        return []

    timed_by_id = {str(line.get('line_id')): line for line in timed_lines}
    applications: list[dict[str, Any]] = []
    anchor_line_ids = set(alignment.get('anchor_line_ids') or set())
    for group_index, original_lines in sorted(groups.items()):
        mapped_lines = [
            deepcopy(timed_by_id[str(line.get('line_id'))])
            for line in original_lines
            if str(line.get('line_id')) in timed_by_id
        ]
        if not mapped_lines:
            continue
        local_anchor_count = sum(
            1 for line in original_lines if str(line.get('line_id')) in anchor_line_ids
        )
        applications.append({
            'group_index': group_index,
            'start_ms': min(int(line['start_ms']) for line in mapped_lines),
            'end_ms': max(int(line['end_ms']) for line in mapped_lines),
            'lines': mapped_lines,
            'replacement_windows': _build_timed_replacement_windows(mapped_lines),
            'anchor_count': local_anchor_count,
            'discarded_anchor_count': int(alignment.get('discarded_anchor_count') or 0),
            'matched_unit_count': int(alignment.get('matched_unit_count') or 0),
            'alignment_method': alignment_method,
            'offset_ms': offset_ms,
            'confidence': float(alignment.get('confidence') or 1.0),
        })
    return applications


def _find_timed_reference_alignment(
    segments: list[dict[str, Any]],
    lines: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not segments or not lines:
        return None
    asr_units: list[str] = []
    asr_segment_indexes: list[int] = []
    for segment_index, segment in enumerate(segments):
        for unit in _units(segment.get('text')):
            asr_units.append(unit)
            asr_segment_indexes.append(segment_index)
    lyric_units: list[str] = []
    lyric_line_indexes: list[int] = []
    for line_index, line in enumerate(lines):
        for unit in _units(line.get('text')):
            lyric_units.append(unit)
            lyric_line_indexes.append(line_index)
    if not asr_units or not lyric_units:
        return None

    matched_segments: dict[int, list[int]] = defaultdict(list)
    matched_unit_count: dict[int, int] = defaultdict(int)
    matcher = SequenceMatcher(None, asr_units, lyric_units, autojunk=False)
    for block in matcher.get_matching_blocks():
        if block.size < 2:
            continue
        for offset in range(block.size):
            line_index = lyric_line_indexes[block.b + offset]
            matched_segments[line_index].append(asr_segment_indexes[block.a + offset])
            matched_unit_count[line_index] += 1

    candidates: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines):
        unit_count = len(_units(line.get('text')))
        matched_count = matched_unit_count.get(line_index, 0)
        coverage = matched_count / max(unit_count, 1)
        segment_indexes = matched_segments.get(line_index) or []
        if matched_count < 3 or coverage < 0.30 or not segment_indexes:
            continue
        episode_midpoints = [
            (
                int(segments[index].get('startMs') or 0)
                + int(segments[index].get('endMs') or 0)
            ) / 2
            for index in segment_indexes
        ]
        reference_center = (
            int(line.get('start_ms') or 0) + int(line.get('end_ms') or 0)
        ) / 2
        episode_center = float(median(episode_midpoints))
        candidates.append({
            'line_id': str(line.get('line_id')),
            'line_index': line_index,
            'reference_center': reference_center,
            'episode_center': episode_center,
            'offset_ms': episode_center - reference_center,
            'matched_unit_count': matched_count,
            'coverage': coverage,
        })
    selected = _select_consistent_offset_cluster(candidates)
    if len(selected) < MIN_TIMED_ANCHOR_COUNT:
        return None
    total_matched = sum(int(item['matched_unit_count']) for item in selected)
    if total_matched < MIN_TIMED_MATCHED_UNIT_COUNT:
        return None
    selected, discarded_anchor_count = _discard_timed_anchor_outliers(selected)
    if len(selected) < MIN_TIMED_ANCHOR_COUNT:
        return None
    total_matched = sum(int(item['matched_unit_count']) for item in selected)
    if total_matched < MIN_TIMED_MATCHED_UNIT_COUNT:
        return None
    selected.sort(key=lambda item: (float(item['reference_center']), int(item['line_index'])))
    monotonic: list[dict[str, Any]] = []
    for candidate in selected:
        if monotonic and float(candidate['episode_center']) < float(monotonic[-1]['episode_center']):
            continue
        monotonic.append(candidate)
    if len(monotonic) < MIN_TIMED_ANCHOR_COUNT:
        return None
    total_matched = sum(int(item['matched_unit_count']) for item in monotonic)
    if total_matched < MIN_TIMED_MATCHED_UNIT_COUNT:
        return None
    return {
        'anchors': monotonic,
        'anchor_line_ids': {str(item['line_id']) for item in monotonic},
        'offset_ms': _weighted_timed_anchor_offset(monotonic),
        'matched_unit_count': total_matched,
        'discarded_anchor_count': discarded_anchor_count,
        'confidence': sum(float(item['coverage']) for item in monotonic) / len(monotonic),
    }


def _select_consistent_offset_cluster(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda item: float(item['offset_ms']))
    best: list[dict[str, Any]] = []
    best_score = (-1, -1)
    left = 0
    for right, candidate in enumerate(ordered):
        while (
            float(candidate['offset_ms']) - float(ordered[left]['offset_ms'])
            > TIMED_ANCHOR_OFFSET_TOLERANCE_MS
        ):
            left += 1
        cluster = ordered[left:right + 1]
        score = (
            sum(int(item['matched_unit_count']) for item in cluster),
            len(cluster),
        )
        if score > best_score:
            best = list(cluster)
            best_score = score
    return best


def _discard_timed_anchor_outliers(
    anchors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if len(anchors) <= MIN_TIMED_ANCHOR_COUNT:
        return anchors, 0
    center_offset = float(median(float(item['offset_ms']) for item in anchors))
    deviations = [abs(float(item['offset_ms']) - center_offset) for item in anchors]
    median_absolute_deviation = float(median(deviations))
    tolerance = max(
        TIMED_ANCHOR_OUTLIER_MIN_TOLERANCE_MS,
        median_absolute_deviation * TIMED_ANCHOR_OUTLIER_MAD_MULTIPLIER,
    )
    retained = sorted(
        anchors,
        key=lambda item: (float(item['reference_center']), int(item['line_index'])),
    )
    while (
        len(retained) > MIN_TIMED_ANCHOR_COUNT
        and abs(float(retained[0]['offset_ms']) - center_offset) > tolerance
    ):
        retained.pop(0)
    while (
        len(retained) > MIN_TIMED_ANCHOR_COUNT
        and abs(float(retained[-1]['offset_ms']) - center_offset) > tolerance
    ):
        retained.pop()
    if (
        len(retained) < MIN_TIMED_ANCHOR_COUNT
        or sum(int(item['matched_unit_count']) for item in retained) < MIN_TIMED_MATCHED_UNIT_COUNT
    ):
        return anchors, 0
    return retained, len(anchors) - len(retained)


def _weighted_timed_anchor_offset(anchors: list[dict[str, Any]]) -> float:
    ordered = sorted(anchors, key=lambda item: float(item['offset_ms']))
    total_weight = sum(max(1, int(item.get('matched_unit_count') or 0)) for item in ordered)
    midpoint = total_weight / 2
    cumulative = 0
    for anchor in ordered:
        cumulative += max(1, int(anchor.get('matched_unit_count') or 0))
        if cumulative >= midpoint:
            return float(anchor['offset_ms'])
    return float(ordered[-1]['offset_ms'])


def _warp_timed_lines(
    lines: list[dict[str, Any]],
    *,
    anchors: list[dict[str, Any]],
    alignment_offset_ms: float,
    duration_limit: int,
) -> list[dict[str, Any]]:
    if not anchors or duration_limit <= 0:
        return []
    mapped: list[dict[str, Any]] = []
    for line in lines:
        start_ms = int(round(_map_reference_time(
            float(line['start_ms']),
            anchors,
            trailing_offset_ms=alignment_offset_ms,
        )))
        end_ms = int(round(_map_reference_time(
            float(line['end_ms']),
            anchors,
            trailing_offset_ms=alignment_offset_ms,
        )))
        start_ms = max(0, min(duration_limit, start_ms))
        end_ms = max(start_ms + MIN_CUE_DURATION_MS, min(duration_limit, end_ms))
        if end_ms > duration_limit:
            start_ms = max(0, duration_limit - MIN_CUE_DURATION_MS)
            end_ms = duration_limit
        if end_ms <= start_ms:
            continue
        mapped.append({**line, 'start_ms': start_ms, 'end_ms': end_ms})
    return mapped


def _trim_untrusted_leading_timed_lines(
    lines: list[dict[str, Any]],
    *,
    segments: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Avoid projecting a long, unmatched studio intro over episode dialogue."""
    if not lines or not anchors or not segments:
        return lines
    first_anchor_time = float(anchors[0]['episode_center'])
    activity_start = first_anchor_time
    ordered_segments = sorted(
        segments,
        key=lambda item: (int(item.get('startMs') or 0), int(item.get('endMs') or 0)),
    )
    for segment in reversed(ordered_segments):
        segment_start = int(segment.get('startMs') or 0)
        segment_end = int(segment.get('endMs') or 0)
        if segment_start > first_anchor_time:
            continue
        if activity_start - segment_end > TIMED_ACTIVITY_GAP_MS:
            break
        activity_start = min(activity_start, float(segment_start))
    trusted_start = max(0, int(round(activity_start)) - TIMED_UNANCHORED_PREROLL_MS)
    return [line for line in lines if int(line.get('end_ms') or 0) >= trusted_start]


def _map_reference_time(
    value: float,
    anchors: list[dict[str, Any]],
    *,
    trailing_offset_ms: float,
) -> float:
    if value <= float(anchors[0]['reference_center']):
        return value + float(anchors[0]['offset_ms'])
    if value >= float(anchors[-1]['reference_center']):
        return value + trailing_offset_ms
    for left, right in zip(anchors, anchors[1:]):
        left_reference = float(left['reference_center'])
        right_reference = float(right['reference_center'])
        if left_reference <= value <= right_reference:
            if right_reference <= left_reference:
                return value + float(left['offset_ms'])
            ratio = (value - left_reference) / (right_reference - left_reference)
            left_episode = float(left['episode_center'])
            right_episode = float(right['episode_center'])
            return left_episode + (right_episode - left_episode) * ratio
    return value + float(anchors[-1]['offset_ms'])


def _can_trust_direct_srt_timing(
    lines: list[dict[str, Any]],
    *,
    source_duration_ms: int,
) -> bool:
    if not lines or source_duration_ms <= 0:
        return True
    maximum_end = max(int(line.get('end_ms') or 0) for line in lines)
    minimum_start = min(int(line.get('start_ms') or 0) for line in lines)
    if maximum_end > source_duration_ms + TIMED_ANCHOR_OFFSET_TOLERANCE_MS:
        return False
    if source_duration_ms <= 10 * 60_000:
        return True
    return (
        maximum_end >= source_duration_ms * 0.60
        or minimum_start >= source_duration_ms * 0.10
    )


def _build_timed_replacement_windows(lines: list[dict[str, Any]]) -> list[dict[str, int]]:
    windows = [
        {
            'start_ms': max(0, int(line['start_ms']) - 180),
            'end_ms': int(line['end_ms']) + 180,
        }
        for line in sorted(lines, key=lambda item: (int(item['start_ms']), int(item['end_ms'])))
    ]
    merged: list[dict[str, int]] = []
    for window in windows:
        if (
            merged
            and int(window['start_ms']) - int(merged[-1]['end_ms'])
            <= TIMED_REPLACEMENT_GAP_MS
        ):
            merged[-1]['end_ms'] = max(merged[-1]['end_ms'], window['end_ms'])
        else:
            merged.append(dict(window))
    return merged


def _build_untimed_applications(
    segments: list[dict[str, Any]],
    reference: dict[str, Any],
    *,
    source_duration_ms: int | None,
) -> list[dict[str, Any]]:
    lines = [deepcopy(line) for line in reference.get('lines') or [] if _units(line.get('text'))]
    if not lines or not segments:
        return []
    asr_units: list[str] = []
    asr_segment_indexes: list[int] = []
    for segment_index, segment in enumerate(segments):
        for unit in _units(segment.get('text')):
            asr_units.append(unit)
            asr_segment_indexes.append(segment_index)
    lyric_units: list[str] = []
    lyric_line_indexes: list[int] = []
    for line_index, line in enumerate(lines):
        for unit in _units(line.get('text')):
            lyric_units.append(unit)
            lyric_line_indexes.append(line_index)
    if not asr_units or not lyric_units:
        return []

    matched_segments: dict[int, list[int]] = defaultdict(list)
    matched_unit_count: dict[int, int] = defaultdict(int)
    matcher = SequenceMatcher(None, asr_units, lyric_units, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            line_index = lyric_line_indexes[block.b + offset]
            matched_segments[line_index].append(asr_segment_indexes[block.a + offset])
            matched_unit_count[line_index] += 1

    anchors: dict[int, float] = {}
    line_coverages: dict[int, float] = {}
    for line_index, line in enumerate(lines):
        unit_count = len(_units(line.get('text')))
        matched_count = matched_unit_count.get(line_index, 0)
        coverage = matched_count / max(unit_count, 1)
        minimum_matches = 2 if unit_count <= 5 else 3
        segment_indexes = matched_segments.get(line_index) or []
        if matched_count < minimum_matches or coverage < 0.28 or not segment_indexes:
            continue
        midpoints = [
            (int(segments[index].get('startMs') or 0) + int(segments[index].get('endMs') or 0)) / 2
            for index in segment_indexes
        ]
        anchors[line_index] = float(median(midpoints))
        line_coverages[line_index] = coverage

    groups: dict[int, list[int]] = defaultdict(list)
    for line_index, line in enumerate(lines):
        groups[int(line.get('group_index') or 0)].append(line_index)
    applications: list[dict[str, Any]] = []
    duration_limit = int(source_duration_ms or 0) or max(int(item.get('endMs') or 0) for item in segments)
    for group_index, line_indexes in sorted(groups.items()):
        anchored = [index for index in line_indexes if index in anchors]
        required_anchor_count = 1 if len(line_indexes) == 1 else 2
        if len(anchored) < required_anchor_count:
            continue
        anchor_times = [anchors[index] for index in anchored]
        if any(
            right - left > MAX_UNTIMED_ANCHOR_GAP_MS
            for left, right in zip(anchor_times, anchor_times[1:])
        ):
            continue
        timed_lines = _interpolate_untimed_lines(
            [lines[index] for index in line_indexes],
            line_indexes=line_indexes,
            anchors=anchors,
            duration_limit=duration_limit,
        )
        if not timed_lines:
            continue
        confidence = sum(line_coverages[index] for index in anchored) / len(anchored)
        applications.append({
            'group_index': group_index,
            'start_ms': timed_lines[0]['start_ms'],
            'end_ms': timed_lines[-1]['end_ms'],
            'lines': timed_lines,
            'anchor_count': len(anchored),
            'confidence': confidence,
        })
    return applications


def _interpolate_untimed_lines(
    lines: list[dict[str, Any]],
    *,
    line_indexes: list[int],
    anchors: dict[int, float],
    duration_limit: int,
) -> list[dict[str, Any]]:
    local_anchors = {
        local_index: anchors[global_index]
        for local_index, global_index in enumerate(line_indexes)
        if global_index in anchors
    }
    if not local_anchors:
        return []
    unit_counts = [max(1, len(_units(line.get('text')))) for line in lines]
    centers: list[float] = []
    anchor_positions = sorted(local_anchors)
    for index in range(len(lines)):
        if index in local_anchors:
            centers.append(local_anchors[index])
            continue
        left = max((item for item in anchor_positions if item < index), default=None)
        right = min((item for item in anchor_positions if item > index), default=None)
        if left is not None and right is not None:
            span_weights = unit_counts[left:right + 1]
            elapsed = sum(span_weights[:index - left]) + unit_counts[index] / 2
            total = max(sum(span_weights), 1)
            centers.append(local_anchors[left] + (local_anchors[right] - local_anchors[left]) * elapsed / total)
        elif left is not None:
            centers.append(local_anchors[left] + 260 * sum(unit_counts[left + 1:index + 1]))
        elif right is not None:
            centers.append(local_anchors[right] - 260 * sum(unit_counts[index:right]))
    for index in range(1, len(centers)):
        centers[index] = max(centers[index], centers[index - 1] + MIN_CUE_DURATION_MS)
    boundaries: list[int] = []
    first_half = max(MIN_CUE_DURATION_MS // 2, min(3000, unit_counts[0] * 130))
    boundaries.append(max(0, int(round(centers[0] - first_half))))
    for left, right in zip(centers, centers[1:]):
        boundaries.append(int(round((left + right) / 2)))
    last_half = max(MIN_CUE_DURATION_MS // 2, min(3000, unit_counts[-1] * 130))
    boundaries.append(min(duration_limit, int(round(centers[-1] + last_half))))
    if boundaries[-1] <= boundaries[0]:
        return []
    result: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        start_ms = max(0, boundaries[index])
        end_ms = max(start_ms + MIN_CUE_DURATION_MS, boundaries[index + 1])
        end_ms = min(duration_limit, end_ms)
        if end_ms <= start_ms:
            return []
        result.append({**line, 'start_ms': start_ms, 'end_ms': end_ms})
    return result


def _apply_reference_group(
    segments: list[dict[str, Any]],
    *,
    reference: dict[str, Any],
    application: dict[str, Any],
    reference_index: int,
    application_index: int,
) -> tuple[list[dict[str, Any]], set[str]]:
    start_ms = int(application['start_ms'])
    end_ms = int(application['end_ms'])
    replacement_windows = application.get('replacement_windows') or [
        {'start_ms': start_ms, 'end_ms': end_ms},
    ]
    retained: list[dict[str, Any]] = []
    replaced: set[str] = set()
    source_segments: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        segment_start = int(segment.get('startMs') or 0)
        segment_end = int(segment.get('endMs') or 0)
        midpoint = (segment_start + segment_end) / 2
        if any(
            int(window.get('start_ms') or 0) <= midpoint <= int(window.get('end_ms') or 0)
            for window in replacement_windows
        ):
            source_segments.append(segment)
            replaced.add(str(segment.get('segmentId') or f'index-{index}'))
        else:
            retained.append(segment)
    inserted = [
        _build_lyrics_segment(
            line,
            source_segments=source_segments,
            source_name=str(reference.get('source_name') or '歌词'),
            reference_hash=str(reference.get('reference_hash') or ''),
            timing_mode=str(reference.get('timing_mode') or ''),
            unique_suffix=f'{reference_index}-{application_index}-{line_index}',
        )
        for line_index, line in enumerate(application['lines'])
    ]
    result = retained + inserted
    result.sort(key=lambda item: (int(item.get('startMs') or 0), int(item.get('endMs') or 0)))
    return result, replaced


def _build_lyrics_segment(
    line: dict[str, Any],
    *,
    source_segments: list[dict[str, Any]],
    source_name: str,
    reference_hash: str,
    timing_mode: str,
    unique_suffix: str,
) -> dict[str, Any]:
    start_ms = int(line['start_ms'])
    end_ms = int(line['end_ms'])
    text = str(line.get('text') or '').strip()
    nearest = min(
        source_segments,
        key=lambda item: abs(
            ((int(item.get('startMs') or 0) + int(item.get('endMs') or 0)) / 2)
            - ((start_ms + end_ms) / 2)
        ),
        default={},
    )
    identity = hashlib.sha256(
        f'{reference_hash}|{unique_suffix}|{start_ms}|{end_ms}|{text}'.encode('utf-8')
    ).hexdigest()[:16]
    return {
        'segmentId': f'lyrics-{identity}',
        'speakerKey': nearest.get('speakerKey') or 'speaker_0',
        'speakerId': nearest.get('speakerId'),
        'startMs': start_ms,
        'endMs': end_ms,
        'text': text,
        'words': [],
        'subtitleParts': [text],
        'subtitlePartRanges': [{'startMs': start_ms, 'endMs': end_ms}],
        'sourceTokenIds': [],
        'timingEvidence': 'lyrics_srt' if timing_mode == 'timed_srt' else 'lyrics_auto_alignment',
        'v2FinalCue': True,
        'lyricsCorrectionMode': True,
        'lyricsSourceName': source_name,
        'lyricsReferenceHash': reference_hash,
        'lyricsLineId': line.get('line_id'),
    }


def _units(text: Any) -> list[str]:
    normalized = normalize_to_simplified_chinese(str(text or '')).casefold()
    result: list[str] = []
    for token in UNIT_RE.findall(normalized):
        if re.fullmatch(r'[\u4e00-\u9fff]', token):
            result.extend(
                item.casefold()
                for item in lazy_pinyin(token, style=Style.NORMAL, errors='ignore')
                if item
            )
        else:
            result.append(token.casefold().replace('’', "'"))
    return result


def _parse_srt_timestamp(value: str) -> int:
    hours, minutes, tail = value.replace('.', ',').split(':')
    seconds, millis = tail.split(',')
    millis = (millis + '000')[:3]
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def _decode_payload(payload: bytes | None) -> str:
    if not payload:
        return ''
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode('utf-8', errors='ignore')


def _clean_lyrics_text(value: Any) -> str:
    text = re.sub(r'<[^>]+>', '', str(value or ''))
    return re.sub(r'[ \t]+', ' ', text).strip()
