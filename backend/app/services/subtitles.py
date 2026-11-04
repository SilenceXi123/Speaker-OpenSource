from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable, Optional

from app.schemas.subtitle import SubtitleCue, SubtitleFormat, SubtitleVariant
from app.services.chinese_text import normalize_to_simplified_chinese
from app.services.subtitle_atoms import build_lexical_atoms
from app.services.subtitle_boundary_grammar import (
    grammar_boundary_penalty as _grammar_boundary_penalty,
    starts_with_object_head as _starts_with_object_head,
)
from app.services.subtitle_cue_validator import CueValidationReport, validate_subtitle_cues
from app.services.subtitle_protected_terms import resolve_subtitle_protected_terms

MAX_SUBTITLE_CHARS = 14
MAX_SUBTITLE_LATIN_CHARS = 42
DEFAULT_SUBTITLE_LENGTH_MODE = 'smart'
STRICT_SUBTITLE_LENGTH_MODE = 'strict'
MIN_SUBTITLE_MAX_CHARS = 6
MAX_SUBTITLE_MAX_CHARS = 30
SHORT_CUE_GAP_JOIN_MS = 160
FILLER_CUE_MAX_DURATION_MS = 1200
FILLER_PARTICLE_MAX_DURATION_MS = 5000
PHRASE_TAIL_MAX_CHARS = 4
PHRASE_TAIL_MAX_DURATION_MS = 1400
PHRASE_TAIL_MAX_GAP_MS = 800
PHRASE_TAIL_MAX_OVERLAP_MS = 120
STANDALONE_FILLERS = {'嗯', '啊', '哦', '哎', '唉', '呃', '额', '呀', '哇', '呢', '嘛', '哈', '哈哈', '呵', '呵呵', '嘿', '嘿嘿'}
STANDALONE_FILLER_CHARS = set('嗯啊哦哎唉呃额呀哇呢嘛哈呵嘿')
EDGE_FILLER_CHARS = set('嗯啊哦哎唉呃额呀哇呢嘛')
LAUGHTER_FILLERS = {'哈', '哈哈', '呵', '呵呵', '嘿', '嘿嘿'}
SHORT_REPLY_STEMS = {
    '好',
    '好的',
    '对',
    '是',
    '是的',
    '不是',
    '可以',
    '行',
    '不行',
    '知道',
    '听说过',
    '谢谢',
    '你好',
    '你们好',
}
DISCOURSE_FILLER_PREFIXES = ('其实', '就是', '那个', '这个', '然后', '那么', '所以', '但是', '不过', '还有', '而且')
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
DISCOURSE_PREFIXES_WITH_OPTIONAL_PARTICLE = ('其实', '就是', '然后', '那么')
DISCOURSE_PREFIXES_WITH_REQUIRED_PARTICLE = ('那个', '这个', '所以', '但是', '不过', '还有', '而且')
TAIL_FILLER_PHRASES = ('你知道吧', '你懂吧', '对不对', '是不是', '对吧', '是吧', '好吧')
EMBEDDED_ER_BEFORE_DISCOURSE_RE = re.compile(r'(?<=[0-9A-Za-z\u3400-\u9fff])呃(?=(?:就是|那个|这个|然后|那么|所以|但是|不过|还有|而且))')
SCRIPT_CORRECTION_SUFFIXES = ('古镇', '村', '镇', '市', '区', '县', '山', '湖', '河', '园', '街', '路', '湾')
SCRIPT_CORRECTION_LOCATION_RE = re.compile(r'[\u3400-\u9fff]{1,8}(?:古镇|村|镇|市|区|县|山|湖|河|园|街|路|湾)')
SCRIPT_CORRECTION_CJK_RE = re.compile(r'^[\u3400-\u9fff]{2,8}$')
SCRIPT_CORRECTION_UNSAFE_PREFIXES = ('这个', '那个', '这些', '那些', '我们', '你们', '他们', '大家', '这里', '那里')
SCRIPT_CORRECTION_CONFUSABLE_GROUPS = (
    '查扎喳渣楂',
    '济记纪计季际剂技既寄',
    '权全泉拳',
    '赢嬴盈营',
    '峰锋',
    '辨辩辫瓣',
    '梁粱良',
    '派帕',
    '杜肚',
)
MIN_SOFT_BREAK_CHARS = 4
PREFERRED_SOFT_BREAK_CHARS = 8
MIN_TAIL_CHARS = 4
MIN_TIMED_TAIL_CHARS = 2
SOFT_BREAK_MIN_GAP_MS = 320
CHINESE_ENUM_MARKER_RE = re.compile(r'^第[一二三四五六七八九十]+$')
SPLIT_LOOKBACK_CHARS = 6
SUBTITLE_SOFT_OVERFLOW_CHARS = 2
HARD_BREAK_RE = re.compile(r'[\n\r。！？!?；;…]+')
SOFT_BREAK_RE = re.compile(r'[，,、：:]+')
DISPLAY_PUNCTUATION = set('，。、！？；：、“”‘’（）()【】[]《》〈〉…,.!?;:·')
ASCII_ALNUM_RE = re.compile(r'[A-Za-z0-9]')
ASCII_SUFFIX_RE = re.compile(r'[A-Za-z0-9]{1,8}$')
LATIN_RE = re.compile(r'[A-Za-z]')
DASH_ARTIFACT_RE = re.compile(r'[—－–-]{2,}')
CJK_DOUBLE_ONE_ARTIFACT_RE = re.compile(
    r'(?<=[\u3400-\u9fff])一一(?!对应|列举|说明|列出|道来|回复|介绍|呈现|记录)(?=[\u3400-\u9fff])'
)
DISCOURSE_PREFIXES = {
    '那个',
    '那么',
    '所以',
    '然后',
    '就是',
    '其实',
    '但是',
    '不过',
    '因为',
    '如果',
    '还有',
    '而且',
}
SPLIT_PREFERRED_PREFIXES = tuple(
    sorted(
        DISCOURSE_PREFIXES
        | {
            '并且',
            '并不是',
            '不是',
            '只是',
            '还是',
            '同时',
            '但',
            '但这',
            '但现在',
            '可',
            '可是',
            '却',
            '又',
            '也',
            '再',
            '先',
        },
        key=len,
        reverse=True,
    )
)
SPLIT_PREFERRED_WORD_STARTS = (
    '我们',
    '你们',
    '他们',
    '大家',
    '用户',
    '客户',
    '老人',
    '现场',
    '一个',
    '这个',
    '这样',
    '这种',
    '这位',
    '智能眼镜',
    '智能手机',
    '创始人',
    '主持人',
    '负责人',
    '发言人',
    '制作人',
    '出品人',
    '投资人',
    '合伙人',
    '候选人',
    '年轻人',
    '机器人',
    '智能眼镜',
    '智能手机',
    '现实增强',
    '增强眼镜',
)
SPLIT_COPULA_PREFERRED_STARTS = (
    '未来',
    '现在',
    '接下来',
    '用户',
    '客户',
    '老人',
    '机器人',
    '我们',
    '你们',
    '他们',
)
PROTECTED_BOUNDARY_BIGRAMS = {
    '眼镜',
    '智能',
    '成为',
    '真正',
    '一个',
    '有趣',
    '科技',
    '这样',
    '调研',
    '入口',
    '未来',
    '个人',
    '大家',
    '现场',
    '璀璨',
    '东北',
    '平原',
    '绿色',
    '明珠',
    '行业',
    '观察',
    '怎么',
    '正式',
    '会议',
    '会谈',
    '当中',
    '产生',
    '没有',
    '门槛',
    '外号',
    '人工',
    '原力',
    '无限',
}
PROTECTED_BOUNDARY_TRIGRAMS = {
    '智能眼',
    '能眼镜',
    '真正的',
    '每一个',
    '有趣的',
    '科技的',
    '这样一',
    '调研一',
    '东北平',
    '北平原',
    '绿色明',
    '色明珠',
    '这个行',
    '个行业',
    '行业的',
    '的观察',
    '非正式',
    '式会议',
    '会议当',
    '议当中',
    '地产生',
    '人工智',
    '工智能',
    '原力无',
    '力无限',
}
PROTECTED_BOUNDARY_TERMS = tuple(
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
SPLIT_AVOID_END_CHARS = set('的地得了着呢吗吧啊呀嘛么')
SPLIT_AVOID_START_CHARS = set('的地得')


def normalize_subtitle_length_mode(value: object) -> str:
    normalized = str(value or DEFAULT_SUBTITLE_LENGTH_MODE).strip().lower()
    return STRICT_SUBTITLE_LENGTH_MODE if normalized == STRICT_SUBTITLE_LENGTH_MODE else DEFAULT_SUBTITLE_LENGTH_MODE


def normalize_subtitle_max_chars(value: object, *, default: int = MAX_SUBTITLE_CHARS) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(MIN_SUBTITLE_MAX_CHARS, min(MAX_SUBTITLE_MAX_CHARS, parsed))


def build_subtitle_length_profile(mode: object = None, max_chars: object = None) -> dict[str, int | str]:
    normalized_mode = normalize_subtitle_length_mode(mode)
    normalized_max = normalize_subtitle_max_chars(max_chars)
    if normalized_mode != STRICT_SUBTITLE_LENGTH_MODE:
        normalized_max = MAX_SUBTITLE_CHARS
    return {'mode': normalized_mode, 'max_chars': normalized_max}


def collect_subtitle_protected_terms(
    script_context: Optional[dict[str, Any]] = None,
    extra_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> tuple[str, ...]:
    return resolve_subtitle_protected_terms(
        script_context,
        extra_terms=extra_terms,
        builtin_terms=PROTECTED_BOUNDARY_TERMS,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )


def _subtitle_boundary_protected_terms(
    protected_terms: Iterable[str],
    *,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
) -> tuple[str, ...]:
    terms = tuple(term for term in protected_terms or () if str(term or ''))
    if subtitle_length_mode == STRICT_SUBTITLE_LENGTH_MODE:
        return terms
    return tuple(term for term in terms if not _is_overlong_unbreakable_term(str(term or '')))


def _subtitle_boundary_runtime_lexicon_bundle(
    runtime_lexicon_bundle: Optional[dict[str, Any]],
    *,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
) -> Optional[dict[str, Any]]:
    if not isinstance(runtime_lexicon_bundle, dict):
        return runtime_lexicon_bundle
    if subtitle_length_mode == STRICT_SUBTITLE_LENGTH_MODE:
        return runtime_lexicon_bundle
    bundle = deepcopy(runtime_lexicon_bundle)
    bundle['terms'] = [
        item
        for item in bundle.get('terms') or []
        if not _is_overlong_runtime_lexicon_term(item)
    ]
    return bundle


def _is_overlong_runtime_lexicon_term(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    raw_term = item.get('display_text') or item.get('normalized_term') or item.get('term')
    return _is_overlong_unbreakable_term(str(raw_term or ''))


def _is_overlong_unbreakable_term(term: str) -> bool:
    value = _strip_for_display(term)
    if not value:
        return False
    return len(value) > _soft_max_subtitle_chars(max_subtitle_chars_for_text(value))


def build_subtitle_cues(
    normalized_segments: list[dict],
    speaker_mappings: Optional[dict[str, str]] = None,
    *,
    export_frame_rate: str | int | None = None,
    remove_standalone_fillers: bool = False,
    script_context: Optional[dict[str, Any]] = None,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: object = None,
    subtitle_max_chars: object = None,
) -> list[SubtitleCue]:
    v2_final_cues = bool(normalized_segments) and all(
        segment.get('v2FinalCue') is True for segment in normalized_segments
    )
    length_profile = build_subtitle_length_profile(subtitle_length_mode, subtitle_max_chars)
    resolved_protected_terms = collect_subtitle_protected_terms(
        script_context,
        protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    boundary_protected_terms = _subtitle_boundary_protected_terms(
        resolved_protected_terms,
        subtitle_length_mode=str(length_profile['mode']),
    )
    boundary_runtime_lexicon_bundle = _subtitle_boundary_runtime_lexicon_bundle(
        runtime_lexicon_bundle,
        subtitle_length_mode=str(length_profile['mode']),
    )
    stable_segments = stabilize_subtitle_segments(
        normalized_segments,
        protected_terms=boundary_protected_terms,
        runtime_lexicon_bundle=boundary_runtime_lexicon_bundle,
        subtitle_length_mode=str(length_profile['mode']),
        subtitle_max_chars=int(length_profile['max_chars']),
    )
    cues = _build_raw_subtitle_cues(
        stable_segments,
        speaker_mappings,
        protected_terms=boundary_protected_terms,
        runtime_lexicon_bundle=boundary_runtime_lexicon_bundle,
        subtitle_length_mode=str(length_profile['mode']),
        subtitle_max_chars=int(length_profile['max_chars']),
    )
    if remove_standalone_fillers:
        cues = [
            cue
            for cue in cues
            if not _is_standalone_filler_cue(cue)
        ]
    _renumber_cues(cues)
    if v2_final_cues:
        return cues
    cues = _close_short_cue_gaps(cues, export_frame_rate=export_frame_rate)
    return _repair_invalid_cue_boundaries(
        cues,
        protected_terms=boundary_protected_terms,
        runtime_lexicon_bundle=boundary_runtime_lexicon_bundle,
    )


def build_standalone_filler_delete_overrides(
    normalized_segments: list[dict],
    speaker_mappings: Optional[dict[str, str]] = None,
    *,
    script_context: Optional[dict[str, Any]] = None,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: object = None,
    subtitle_max_chars: object = None,
) -> list[dict[str, int | str | bool]]:
    speaker_mappings = speaker_mappings or {}
    length_profile = build_subtitle_length_profile(subtitle_length_mode, subtitle_max_chars)
    resolved_protected_terms = collect_subtitle_protected_terms(
        script_context,
        protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    boundary_protected_terms = _subtitle_boundary_protected_terms(
        resolved_protected_terms,
        subtitle_length_mode=str(length_profile['mode']),
    )
    boundary_runtime_lexicon_bundle = _subtitle_boundary_runtime_lexicon_bundle(
        runtime_lexicon_bundle,
        subtitle_length_mode=str(length_profile['mode']),
    )
    overrides: list[dict[str, int | str | bool]] = []
    for record in _build_subtitle_part_records(
        stabilize_subtitle_segments(
            normalized_segments,
            protected_terms=boundary_protected_terms,
            runtime_lexicon_bundle=boundary_runtime_lexicon_bundle,
            subtitle_length_mode=str(length_profile['mode']),
            subtitle_max_chars=int(length_profile['max_chars']),
        ),
        speaker_mappings,
        protected_terms=boundary_protected_terms,
        runtime_lexicon_bundle=boundary_runtime_lexicon_bundle,
        subtitle_length_mode=str(length_profile['mode']),
        subtitle_max_chars=int(length_profile['max_chars']),
    ):
        clean_text = str(record.get('clean_text') or '')
        duration_ms = max(int(record.get('end_ms') or 0) - int(record.get('start_ms') or 0), 0)
        if _is_removable_standalone_filler(clean_text, duration_ms):
            overrides.append({'index': int(record['cue_index']), 'delete': True})
            continue
        cleanup = _build_edge_filler_cleanup_override(record)
        if cleanup:
            overrides.append(cleanup)
    return overrides


def build_script_text_correction_overrides(
    normalized_segments: list[dict],
    script_context: Optional[dict[str, Any]],
    speaker_mappings: Optional[dict[str, str]] = None,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: object = None,
    subtitle_max_chars: object = None,
) -> list[dict[str, int | str]]:
    terms = _collect_script_correction_terms(script_context)
    if not terms:
        return []
    length_profile = build_subtitle_length_profile(subtitle_length_mode, subtitle_max_chars)
    resolved_protected_terms = collect_subtitle_protected_terms(
        script_context,
        protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    boundary_protected_terms = _subtitle_boundary_protected_terms(
        resolved_protected_terms,
        subtitle_length_mode=str(length_profile['mode']),
    )
    boundary_runtime_lexicon_bundle = _subtitle_boundary_runtime_lexicon_bundle(
        runtime_lexicon_bundle,
        subtitle_length_mode=str(length_profile['mode']),
    )
    script_text = _normalize_script_correction_text(
        ''.join(str(line or '') for line in (script_context or {}).get('reference_lines') or [])
    )
    cues = _build_raw_subtitle_cues(
        stabilize_subtitle_segments(
            normalized_segments,
            protected_terms=boundary_protected_terms,
            runtime_lexicon_bundle=boundary_runtime_lexicon_bundle,
            subtitle_length_mode=str(length_profile['mode']),
            subtitle_max_chars=int(length_profile['max_chars']),
        ),
        speaker_mappings,
        protected_terms=boundary_protected_terms,
        runtime_lexicon_bundle=boundary_runtime_lexicon_bundle,
        subtitle_length_mode=str(length_profile['mode']),
        subtitle_max_chars=int(length_profile['max_chars']),
    )
    overrides: list[dict[str, int | str]] = []
    for cue in cues:
        corrected = _apply_script_term_corrections(cue.clean_text, terms, script_text)
        if corrected and corrected != cue.clean_text:
            overrides.append({'index': cue.index, 'clean_text': corrected})
    return overrides


def _build_raw_subtitle_cues(
    normalized_segments: list[dict],
    speaker_mappings: Optional[dict[str, str]] = None,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
    subtitle_max_chars: int = MAX_SUBTITLE_CHARS,
) -> list[SubtitleCue]:
    speaker_mappings = speaker_mappings or {}
    cues: list[SubtitleCue] = []
    for segment in normalized_segments or []:
        speaker_key = str(segment.get('speakerKey') or 'speaker_0')
        speaker_name = speaker_mappings.get(speaker_key, speaker_key)
        speaker_overrides = _normalize_speaker_override_items(segment.get('speakerOverrides'))
        start_ms = int(segment.get('startMs') or 0)
        end_ms = int(segment.get('endMs') or 0)
        segment_parts = build_segment_subtitle_parts(
            segment,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            subtitle_length_mode=subtitle_length_mode,
            subtitle_max_chars=subtitle_max_chars,
        )

        for part_index, part in enumerate(segment_parts):
            clean_text = _strip_for_display(str(part.get('text') or ''))
            cue_speaker_name = speaker_overrides.get(part_index, speaker_name)
            full_text = f'{cue_speaker_name}：{clean_text}' if clean_text else cue_speaker_name
            cue = SubtitleCue(
                index=len(cues) + 1,
                start_ms=int(part.get('start_ms') or start_ms),
                end_ms=int(part.get('end_ms') or end_ms),
                speaker_key=speaker_key,
                speaker_name=cue_speaker_name,
                full_text=full_text,
                clean_text=clean_text,
            )
            if cue.clean_text:
                cues.append(cue)
    _renumber_cues(cues)
    return cues


def stabilize_subtitle_segments(
    normalized_segments: list[dict],
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
    subtitle_max_chars: int = MAX_SUBTITLE_CHARS,
) -> list[dict]:
    stabilized: list[dict] = []
    for original_segment in normalized_segments or []:
        segment = deepcopy(original_segment)
        if not stabilized:
            stabilized.append(segment)
            continue
        previous = stabilized[-1]
        if not _should_merge_adjacent_segments(
            previous,
            segment,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            subtitle_length_mode=subtitle_length_mode,
            subtitle_max_chars=subtitle_max_chars,
        ):
            stabilized.append(segment)
            continue
        stabilized[-1] = _merge_adjacent_segments(
            previous,
            segment,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            subtitle_length_mode=subtitle_length_mode,
            subtitle_max_chars=subtitle_max_chars,
        )
    return stabilized


def _should_merge_adjacent_segments(
    previous: dict,
    current: dict,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
    subtitle_max_chars: int = MAX_SUBTITLE_CHARS,
) -> bool:
    if previous.get('v2FinalCue') or current.get('v2FinalCue'):
        return False
    if previous.get('speakerOverrides') or current.get('speakerOverrides'):
        return False
    previous_text = _segment_display_text(previous)
    current_text = _segment_display_text(current)
    if not previous_text or not current_text:
        return False
    if is_standalone_filler_text(previous_text) or is_standalone_filler_text(current_text):
        return False

    same_speaker = str(previous.get('speakerKey') or '') == str(current.get('speakerKey') or '')
    if same_speaker:
        return _has_unsafe_adjacent_text_boundary(
            previous_text,
            current_text,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        )
    return _is_short_phrase_tail_fragment(
        previous,
        current,
        previous_text,
        current_text,
        subtitle_length_mode=subtitle_length_mode,
        subtitle_max_chars=subtitle_max_chars,
    )


def _segment_display_text(segment: dict) -> str:
    parts = _normalize_subtitle_parts(segment.get('subtitleParts'))
    if parts and join_subtitle_parts(parts) == _strip_for_display(str(segment.get('text') or '')):
        return join_subtitle_parts(parts)
    return _strip_for_display(str(segment.get('text') or ''))


def _has_unsafe_adjacent_text_boundary(
    left_text: str,
    right_text: str,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> bool:
    left = _strip_for_display(left_text)
    right = _strip_for_display(right_text)
    if not left or not right:
        return False
    if _needs_ascii_space(left[-1], right[0]):
        return False
    combined = left + right
    return _protected_boundary_penalty(
        combined,
        len(left),
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ) > 0


def _is_short_phrase_tail_fragment(
    previous: dict,
    current: dict,
    previous_text: str,
    current_text: str,
    *,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
    subtitle_max_chars: int = MAX_SUBTITLE_CHARS,
) -> bool:
    text = _strip_for_display(current_text)
    if not text or len(text) > PHRASE_TAIL_MAX_CHARS:
        return False
    if is_standalone_filler_text(text):
        return False
    if is_standalone_filler_text(previous_text):
        return False
    if str(previous.get('text') or '').rstrip().endswith(('。', '！', '？', '!', '?', '；', ';')):
        return False
    gap_ms = int(current.get('startMs') or 0) - int(previous.get('endMs') or 0)
    if gap_ms < -PHRASE_TAIL_MAX_OVERLAP_MS or gap_ms > PHRASE_TAIL_MAX_GAP_MS:
        return False
    duration_ms = int(current.get('endMs') or 0) - int(current.get('startMs') or 0)
    if duration_ms <= 0 or duration_ms > PHRASE_TAIL_MAX_DURATION_MS:
        return False
    stem = text.strip(''.join(EDGE_FILLER_CHARS))
    if text in SHORT_REPLY_STEMS or stem in SHORT_REPLY_STEMS:
        return False
    if text[-1] in EDGE_FILLER_CHARS and stem:
        combined_text = join_subtitle_parts([previous_text, text])
        return len(combined_text) <= max_subtitle_chars_for_text_with_profile(
            combined_text,
            subtitle_length_mode=subtitle_length_mode,
            subtitle_max_chars=subtitle_max_chars,
        )
    return False


def _merge_adjacent_segments(
    previous: dict,
    current: dict,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
    subtitle_max_chars: int = MAX_SUBTITLE_CHARS,
) -> dict:
    merged = deepcopy(previous)
    previous_text = _segment_display_text(previous)
    current_text = _segment_display_text(current)
    combined_text = join_subtitle_parts([previous_text, current_text])
    merged['text'] = combined_text
    merged['startMs'] = int(previous.get('startMs') or 0)
    merged['endMs'] = max(int(previous.get('endMs') or 0), int(current.get('endMs') or 0))
    if previous.get('words') and current.get('words'):
        merged['words'] = list(previous.get('words') or []) + list(current.get('words') or [])
    else:
        merged.pop('words', None)
    merged.pop('speakerOverrides', None)
    parts = _split_segment_text(
        combined_text,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        subtitle_length_mode=subtitle_length_mode,
        subtitle_max_chars=subtitle_max_chars,
    )
    if parts:
        ranges = _split_time_ranges(
            int(merged.get('startMs') or 0),
            int(merged.get('endMs') or 0),
            [len(part) for part in parts],
        )
        merged['subtitleParts'] = parts
        merged['subtitlePartRanges'] = [
            {'startMs': part_start, 'endMs': part_end}
            for part_start, part_end in ranges
        ]
    else:
        merged.pop('subtitleParts', None)
        merged.pop('subtitlePartRanges', None)
    return merged


def _close_short_cue_gaps(cues: list[SubtitleCue], *, export_frame_rate: str | int | None = None) -> list[SubtitleCue]:
    gap_threshold_ms = _gap_join_threshold_ms(export_frame_rate)
    allow_cross_speaker = export_frame_rate is not None
    for index in range(len(cues) - 1):
        current = cues[index]
        following = cues[index + 1]
        gap_ms = int(following.start_ms) - int(current.end_ms)
        if gap_ms <= 0 or gap_ms > gap_threshold_ms:
            continue
        if not allow_cross_speaker and current.speaker_name != following.speaker_name:
            continue
        current.end_ms = int(following.start_ms)
    return cues


def _repair_invalid_cue_boundaries(
    cues: list[SubtitleCue],
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> list[SubtitleCue]:
    repaired = _merge_isolated_single_cjk_cues(list(cues or []))
    for _ in range(3):
        report = validate_subtitle_cues(
            repaired,
            protected_terms=protected_terms or (),
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        )
        merge_pairs = _validation_merge_pairs(report)
        if not merge_pairs:
            return repaired
        next_cues: list[SubtitleCue] = []
        cursor = 0
        changed = False
        while cursor < len(repaired):
            if cursor < len(repaired) - 1 and (cursor, cursor + 1) in merge_pairs:
                next_cues.append(_merge_subtitle_cues(repaired[cursor], repaired[cursor + 1]))
                cursor += 2
                changed = True
                continue
            next_cues.append(repaired[cursor])
            cursor += 1
        repaired = _merge_isolated_single_cjk_cues(next_cues)
        _renumber_cues(repaired)
        if not changed:
            return repaired
    return repaired


def _merge_isolated_single_cjk_cues(cues: list[SubtitleCue]) -> list[SubtitleCue]:
    repaired = list(cues or [])
    for _ in range(3):
        next_cues: list[SubtitleCue] = []
        changed = False
        cursor = 0
        while cursor < len(repaired):
            cue = repaired[cursor]
            if not _is_isolated_single_cjk_cue(cue):
                next_cues.append(cue)
                cursor += 1
                continue

            previous = next_cues[-1] if next_cues else None
            following = repaired[cursor + 1] if cursor + 1 < len(repaired) else None
            if previous is None and following is None:
                next_cues.append(cue)
                cursor += 1
                continue

            if _single_cjk_should_merge_with_previous(cue, previous, following):
                next_cues[-1] = _merge_subtitle_cues(previous, cue)
                cursor += 1
            elif following is not None:
                next_cues.append(_merge_subtitle_cues(cue, following, speaker_source='right'))
                cursor += 2
            else:
                next_cues.append(cue)
                cursor += 1
            changed = True

        repaired = next_cues
        _renumber_cues(repaired)
        if not changed:
            return repaired
    return repaired


def _single_cjk_should_merge_with_previous(
    cue: SubtitleCue,
    previous: Optional[SubtitleCue],
    following: Optional[SubtitleCue],
) -> bool:
    if previous is None:
        return False
    if following is None:
        return True
    if _same_cue_speaker(previous, cue):
        return True
    if _same_cue_speaker(following, cue):
        return False
    previous_gap = int(cue.start_ms) - int(previous.end_ms)
    following_gap = int(following.start_ms) - int(cue.end_ms)
    return previous_gap <= following_gap


def _same_cue_speaker(left: SubtitleCue, right: SubtitleCue) -> bool:
    return str(left.speaker_key or '') == str(right.speaker_key or '') or str(left.speaker_name or '') == str(right.speaker_name or '')


def _is_isolated_single_cjk_cue(cue: SubtitleCue) -> bool:
    clean_text = _strip_for_display(str(cue.clean_text or ''))
    return len(clean_text) == 1 and _is_cjk_char(clean_text) and not is_standalone_filler_text(clean_text)


def _validation_merge_pairs(report: CueValidationReport) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for issue in report.issues:
        if issue.code not in {'protected_term_split', 'lexical_atom_split', 'lexicon_hard_term_split'}:
            continue
        if issue.left_index is None or issue.right_index is None:
            continue
        pairs.add((issue.left_index - 1, issue.right_index - 1))
    return pairs


def _merge_subtitle_cues(left: SubtitleCue, right: SubtitleCue, *, speaker_source: str = 'left') -> SubtitleCue:
    clean_text = join_subtitle_parts([left.clean_text, right.clean_text])
    speaker_cue = right if speaker_source == 'right' else left
    full_text = f'{speaker_cue.speaker_name}：{clean_text}' if clean_text else speaker_cue.speaker_name
    return SubtitleCue(
        index=left.index,
        start_ms=min(int(left.start_ms), int(right.start_ms)),
        end_ms=max(int(left.end_ms), int(right.end_ms)),
        speaker_key=speaker_cue.speaker_key,
        speaker_name=speaker_cue.speaker_name,
        full_text=full_text,
        clean_text=clean_text,
    )


def _gap_join_threshold_ms(export_frame_rate: str | int | None) -> int:
    if export_frame_rate is None:
        return SHORT_CUE_GAP_JOIN_MS
    try:
        fps = int(str(export_frame_rate).strip())
    except (TypeError, ValueError):
        fps = 25
    if fps <= 0:
        fps = 25
    return int(round(10 * 1000 / fps))


def _is_standalone_filler_cue(cue: SubtitleCue) -> bool:
    duration_ms = max(int(cue.end_ms) - int(cue.start_ms), 0)
    return _is_removable_standalone_filler(cue.clean_text, duration_ms)


def is_standalone_filler_text(text: str) -> bool:
    value = _strip_for_display(text)
    if not value:
        return False
    if value in STANDALONE_FILLERS:
        return True
    if _is_discourse_only_filler_text(value):
        return True
    if _is_particle_only_filler_text(value):
        return True
    return len(value) <= 3 and all(char in STANDALONE_FILLER_CHARS for char in value)


def _is_removable_standalone_filler(text: str, duration_ms: int) -> bool:
    value = _strip_for_display(text)
    if not is_standalone_filler_text(value):
        return False
    if value in LAUGHTER_FILLERS or all(char in LAUGHTER_FILLERS for char in value):
        return duration_ms <= FILLER_CUE_MAX_DURATION_MS
    if _is_particle_only_filler_text(value) or all(char in EDGE_FILLER_CHARS for char in value):
        return duration_ms <= FILLER_PARTICLE_MAX_DURATION_MS
    if _is_discourse_only_filler_text(value):
        return duration_ms <= FILLER_PARTICLE_MAX_DURATION_MS
    return duration_ms <= FILLER_CUE_MAX_DURATION_MS


def _build_edge_filler_cleanup_override(record: dict[str, int | str]) -> Optional[dict[str, int | str]]:
    text = str(record.get('clean_text') or '')
    cleaned, leading_removed, trailing_removed = _strip_edge_and_discourse_fillers(text)
    if cleaned == text:
        return None
    if not cleaned:
        return {'index': int(record['cue_index']), 'delete': True}

    override: dict[str, int | str] = {
        'index': int(record['cue_index']),
        'clean_text': cleaned,
    }
    start_ms = int(record.get('start_ms') or 0)
    end_ms = int(record.get('end_ms') or 0)
    if end_ms > start_ms and (leading_removed or trailing_removed):
        ranges = _split_time_ranges(start_ms, end_ms, [1 for _ in text])
        first_kept_index = min(max(leading_removed, 0), len(ranges) - 1)
        last_kept_index = max(first_kept_index, len(ranges) - trailing_removed - 1)
        if leading_removed and first_kept_index < len(ranges):
            override['start_ms'] = int(ranges[first_kept_index][0])
        if trailing_removed and 0 <= last_kept_index < len(ranges):
            override['end_ms'] = int(ranges[last_kept_index][1])
    return override


def _strip_edge_and_discourse_fillers(text: str) -> tuple[str, int, int]:
    value = _strip_for_display(text)
    if len(value) <= 1:
        return value, 0, 0

    left = 0
    right = len(value)
    changed = True
    while changed:
        changed = False
        while right - left > 1 and value[left] in EDGE_FILLER_CHARS:
            left += 1
            changed = True
        while right - left > 1 and value[right - 1] in EDGE_FILLER_CHARS:
            right -= 1
            changed = True

        window = value[left:right]
        for prefix in DISCOURSE_PREFIXES_WITH_OPTIONAL_PARTICLE:
            removed = _leading_discourse_filler_length(window, prefix, require_particle=False)
            if removed:
                left += removed
                changed = True
                break
        if changed:
            continue
        for prefix in DISCOURSE_PREFIXES_WITH_REQUIRED_PARTICLE:
            removed = _leading_discourse_filler_length(window, prefix, require_particle=True)
            if removed:
                left += removed
                changed = True
                break
        if changed:
            continue
        for phrase in TAIL_FILLER_PHRASES:
            if right - left > len(phrase) and value[left:right].endswith(phrase):
                right -= len(phrase)
                changed = True
                break
    return EMBEDDED_ER_BEFORE_DISCOURSE_RE.sub('', value[left:right]), left, len(value) - right


def _is_particle_only_filler_text(value: str) -> bool:
    return 1 <= len(value) <= 8 and all(char in EDGE_FILLER_CHARS for char in value)


def _is_discourse_only_filler_text(value: str) -> bool:
    if value in DISCOURSE_ONLY_FILLERS:
        return True
    for prefix in DISCOURSE_FILLER_PREFIXES:
        suffix = value[len(prefix):]
        if value.startswith(prefix) and suffix and all(char in EDGE_FILLER_CHARS for char in suffix):
            return True
    return False


def _leading_discourse_filler_length(text: str, prefix: str, *, require_particle: bool) -> int:
    if not text.startswith(prefix):
        return 0
    prefix_end = len(prefix)
    if len(text) <= prefix_end + 1:
        return 0
    if prefix_end < len(text) and text[prefix_end] in EDGE_FILLER_CHARS:
        return prefix_end + 1
    if require_particle:
        return 0
    return prefix_end if len(text) >= prefix_end + 4 else 0


def _collect_script_correction_terms(script_context: Optional[dict[str, Any]]) -> list[str]:
    if not script_context:
        return []
    raw_terms: list[str] = []
    for key in ('places', 'people', 'programs', 'organizations', 'terms'):
        value = script_context.get(key)
        if isinstance(value, list):
            raw_terms.extend(str(item or '') for item in value)
    reference_lines = script_context.get('reference_lines')
    if isinstance(reference_lines, list):
        for line in reference_lines:
            raw_terms.extend(SCRIPT_CORRECTION_LOCATION_RE.findall(str(line or '')))

    terms: list[str] = []
    seen: set[str] = set()
    for raw_term in raw_terms:
        cleaned = _normalize_script_correction_text(raw_term)
        if not _is_script_correction_term(cleaned) or cleaned in seen:
            continue
        seen.add(cleaned)
        terms.append(cleaned)
        for location_term in SCRIPT_CORRECTION_LOCATION_RE.findall(cleaned):
            if _is_script_correction_term(location_term) and location_term not in seen:
                seen.add(location_term)
                terms.append(location_term)
    return sorted(terms, key=len, reverse=True)


def _apply_script_term_corrections(text: str, terms: list[str], script_text: str) -> str:
    corrected = _strip_for_display(text)
    if not corrected:
        return corrected
    for term in terms:
        if term in corrected:
            continue
        match = _find_script_term_match(corrected, term, script_text)
        if match is None:
            continue
        start, end = match
        corrected = corrected[:start] + term + corrected[end:]
    return corrected


def _find_script_term_match(text: str, term: str, script_text: str) -> Optional[tuple[int, int]]:
    length = len(term)
    if length < 2 or len(text) < length:
        return None
    best: tuple[float, int, int] | None = None
    for start in range(0, len(text) - length + 1):
        end = start + length
        candidate = text[start:end]
        if candidate == term or not _is_safe_script_correction_candidate(candidate, term, script_text):
            continue
        score = _script_term_similarity(candidate, term)
        if score < _script_term_threshold(term):
            continue
        if best is None or score > best[0]:
            best = (score, start, end)
    if best is None:
        return None
    return best[1], best[2]


def _is_safe_script_correction_candidate(candidate: str, term: str, script_text: str) -> bool:
    if not SCRIPT_CORRECTION_CJK_RE.match(candidate) or not SCRIPT_CORRECTION_CJK_RE.match(term):
        return False
    if any(candidate.startswith(prefix) for prefix in SCRIPT_CORRECTION_UNSAFE_PREFIXES):
        return False
    if _normalize_script_correction_text(candidate) in script_text:
        return False
    candidate_suffix = _script_term_suffix(candidate)
    term_suffix = _script_term_suffix(term)
    if candidate_suffix or term_suffix:
        return bool(candidate_suffix and candidate_suffix == term_suffix)
    return True


def _is_script_correction_term(term: str) -> bool:
    return bool(SCRIPT_CORRECTION_CJK_RE.match(term)) and not any(term.startswith(prefix) for prefix in SCRIPT_CORRECTION_UNSAFE_PREFIXES)


def _script_term_suffix(value: str) -> str:
    return next((suffix for suffix in SCRIPT_CORRECTION_SUFFIXES if value.endswith(suffix)), '')


def _script_term_similarity(source: str, target: str) -> float:
    if len(source) != len(target) or not source:
        return 0.0
    matched = 0
    for left, right in zip(source, target):
        if left == right or _script_chars_are_confusable(left, right):
            matched += 1
    return matched / len(source)


def _script_term_threshold(term: str) -> float:
    if len(term) <= 2:
        return 1.0
    return 0.72


def _script_chars_are_confusable(left: str, right: str) -> bool:
    return any(left in group and right in group for group in SCRIPT_CORRECTION_CONFUSABLE_GROUPS)


def _normalize_script_correction_text(text: str) -> str:
    simplified = normalize_to_simplified_chinese(str(text or ''))
    return re.sub(r'[^\u3400-\u9fffA-Za-z0-9]+', '', simplified).strip()


def _renumber_cues(cues: list[SubtitleCue]) -> None:
    for index, cue in enumerate(cues, start=1):
        cue.index = index


def apply_subtitle_speaker_overrides(
    normalized_segments: list[dict],
    overrides: list[dict],
    speaker_mappings: Optional[dict[str, str]] = None,
    *,
    script_context: Optional[dict[str, Any]] = None,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: object = None,
    subtitle_max_chars: object = None,
) -> list[dict]:
    speaker_mappings = speaker_mappings or {}
    length_profile = build_subtitle_length_profile(subtitle_length_mode, subtitle_max_chars)
    resolved_protected_terms = collect_subtitle_protected_terms(
        script_context,
        protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    boundary_protected_terms = _subtitle_boundary_protected_terms(
        resolved_protected_terms,
        subtitle_length_mode=str(length_profile['mode']),
    )
    boundary_runtime_lexicon_bundle = _subtitle_boundary_runtime_lexicon_bundle(
        runtime_lexicon_bundle,
        subtitle_length_mode=str(length_profile['mode']),
    )
    updated_segments = stabilize_subtitle_segments(
        normalized_segments,
        protected_terms=boundary_protected_terms,
        runtime_lexicon_bundle=boundary_runtime_lexicon_bundle,
        subtitle_length_mode=str(length_profile['mode']),
        subtitle_max_chars=int(length_profile['max_chars']),
    )
    records = {
        record['cue_index']: record
        for record in _build_subtitle_part_records(
            updated_segments,
            speaker_mappings,
            protected_terms=boundary_protected_terms,
            runtime_lexicon_bundle=boundary_runtime_lexicon_bundle,
            subtitle_length_mode=str(length_profile['mode']),
            subtitle_max_chars=int(length_profile['max_chars']),
        )
    }

    for item in overrides or []:
        if not isinstance(item, dict):
            continue
        try:
            cue_index = int(item.get('index') or item.get('cue_index') or 0)
        except (TypeError, ValueError):
            continue
        raw_name = item.get('speaker_name')
        if raw_name is None:
            raw_name = item.get('speakerName')
        speaker_name = _normalize_speaker_name(raw_name)
        record = records.get(cue_index)
        if not record or not speaker_name:
            continue

        segment = updated_segments[int(record['segment_index'])]
        speaker_overrides = _normalize_speaker_override_items(segment.get('speakerOverrides'))
        part_index = int(record['part_index'])
        if speaker_name == record['default_speaker_name']:
            speaker_overrides.pop(part_index, None)
        else:
            speaker_overrides[part_index] = speaker_name

        serialized = _serialize_speaker_override_items(speaker_overrides)
        if serialized:
            segment['speakerOverrides'] = serialized
        else:
            segment.pop('speakerOverrides', None)

    return updated_segments


def apply_subtitle_text_overrides(
    normalized_segments: list[dict],
    overrides: list[dict],
    speaker_mappings: Optional[dict[str, str]] = None,
    *,
    script_context: Optional[dict[str, Any]] = None,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: object = None,
    subtitle_max_chars: object = None,
) -> list[dict]:
    speaker_mappings = speaker_mappings or {}
    length_profile = build_subtitle_length_profile(subtitle_length_mode, subtitle_max_chars)
    resolved_protected_terms = collect_subtitle_protected_terms(
        script_context,
        protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    boundary_protected_terms = _subtitle_boundary_protected_terms(
        resolved_protected_terms,
        subtitle_length_mode=str(length_profile['mode']),
    )
    boundary_runtime_lexicon_bundle = _subtitle_boundary_runtime_lexicon_bundle(
        runtime_lexicon_bundle,
        subtitle_length_mode=str(length_profile['mode']),
    )
    updated_segments = stabilize_subtitle_segments(
        normalized_segments,
        protected_terms=boundary_protected_terms,
        runtime_lexicon_bundle=boundary_runtime_lexicon_bundle,
        subtitle_length_mode=str(length_profile['mode']),
        subtitle_max_chars=int(length_profile['max_chars']),
    )
    records = {
        record['cue_index']: record
        for record in _build_subtitle_part_records(
            updated_segments,
            speaker_mappings,
            protected_terms=boundary_protected_terms,
            runtime_lexicon_bundle=boundary_runtime_lexicon_bundle,
            subtitle_length_mode=str(length_profile['mode']),
            subtitle_max_chars=int(length_profile['max_chars']),
        )
    }
    mutations_by_segment: dict[int, list[dict[str, int | str | bool]]] = {}

    for item in overrides or []:
        if not isinstance(item, dict):
            continue
        try:
            cue_index = int(item.get('index') or item.get('cue_index') or 0)
        except (TypeError, ValueError):
            continue
        record = records.get(cue_index)
        if not record:
            continue
        segment_index = int(record['segment_index'])
        clean_text = _strip_for_display(str(item.get('clean_text') or item.get('cleanText') or ''))
        should_delete = bool(item.get('delete')) or not clean_text
        override_start_ms = _optional_int(item.get('start_ms') if 'start_ms' in item else item.get('startMs'))
        override_end_ms = _optional_int(item.get('end_ms') if 'end_ms' in item else item.get('endMs'))
        mutations_by_segment.setdefault(segment_index, []).append({
            'part_index': int(record['part_index']),
            'clean_text': clean_text,
            'delete': should_delete,
            'start_ms': override_start_ms,
            'end_ms': override_end_ms,
        })

    for segment_index, mutations in mutations_by_segment.items():
        if segment_index < 0 or segment_index >= len(updated_segments):
            continue
        segment = updated_segments[segment_index]
        segment_parts = [
            {
                'text': _strip_for_display(str(part.get('text') or '')),
                'start_ms': int(part.get('start_ms') or segment.get('startMs') or 0),
                'end_ms': int(part.get('end_ms') or segment.get('endMs') or 0),
            }
            for part in build_segment_subtitle_parts(
                segment,
                protected_terms=boundary_protected_terms,
                runtime_lexicon_bundle=boundary_runtime_lexicon_bundle,
                subtitle_length_mode=str(length_profile['mode']),
                subtitle_max_chars=int(length_profile['max_chars']),
            )
            if _strip_for_display(str(part.get('text') or ''))
        ]
        deleted_part_indexes: set[int] = set()

        for mutation in sorted(mutations, key=lambda item: int(item['part_index']), reverse=True):
            part_index = int(mutation['part_index'])
            if part_index < 0 or part_index >= len(segment_parts):
                continue
            if mutation.get('delete'):
                segment_parts.pop(part_index)
                deleted_part_indexes.add(part_index)
                continue
            clean_text = str(mutation.get('clean_text') or '')
            if clean_text:
                segment_parts[part_index]['text'] = clean_text
                part_start_ms = int(segment_parts[part_index].get('start_ms') or segment.get('startMs') or 0)
                part_end_ms = int(segment_parts[part_index].get('end_ms') or segment.get('endMs') or 0)
                override_start_ms = mutation.get('start_ms')
                override_end_ms = mutation.get('end_ms')
                if isinstance(override_start_ms, int):
                    part_start_ms = max(int(segment.get('startMs') or 0), min(override_start_ms, part_end_ms - 1))
                if isinstance(override_end_ms, int):
                    part_end_ms = min(int(segment.get('endMs') or part_end_ms), max(override_end_ms, part_start_ms + 1))
                segment_parts[part_index]['start_ms'] = part_start_ms
                segment_parts[part_index]['end_ms'] = part_end_ms

        _store_segment_subtitle_parts(segment, segment_parts)
        if deleted_part_indexes:
            _shift_speaker_overrides_after_deletes(segment, deleted_part_indexes)

    return updated_segments


def build_segment_subtitle_parts(
    segment: dict,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
    subtitle_max_chars: int = MAX_SUBTITLE_CHARS,
) -> list[dict[str, int | str]]:
    start_ms = int(segment.get('startMs') or 0)
    end_ms = int(segment.get('endMs') or 0)
    segment_text = str(segment.get('text') or '').strip()
    if not segment_text:
        return []
    if segment.get('v2FinalCue') is True:
        return [{'text': segment_text, 'start_ms': start_ms, 'end_ms': end_ms}]

    timed_parts = _build_timed_parts_from_words(
        segment,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        subtitle_length_mode=subtitle_length_mode,
        subtitle_max_chars=subtitle_max_chars,
    )
    if timed_parts:
        segment_parts = timed_parts
    else:
        parts = _split_segment_text(
            segment_text,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            subtitle_length_mode=subtitle_length_mode,
            subtitle_max_chars=subtitle_max_chars,
        )
        time_ranges = _split_time_ranges(start_ms, end_ms, [len(part) for part in parts]) if parts else [(start_ms, end_ms)]
        segment_parts = [
            {'text': part, 'start_ms': part_start, 'end_ms': part_end}
            for part, (part_start, part_end) in zip(parts or [''], time_ranges)
        ]

    override_parts = _normalize_subtitle_parts(segment.get('subtitleParts'))
    override_ranges = _normalize_subtitle_part_ranges(segment.get('subtitlePartRanges'), len(override_parts))
    override_parts_are_safe = _subtitle_parts_are_safe(
        override_parts,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        subtitle_length_mode=subtitle_length_mode,
        subtitle_max_chars=subtitle_max_chars,
    )
    override_crosses_timed_enum_break = _subtitle_parts_cross_timed_enum_break(override_parts, timed_parts)
    if (
        override_parts
        and override_parts_are_safe
        and not override_crosses_timed_enum_break
        and override_ranges
        and join_subtitle_parts(override_parts) == _strip_for_display(segment_text)
    ):
        return [
            {'text': part, 'start_ms': part_start, 'end_ms': part_end}
            for part, (part_start, part_end) in zip(override_parts, override_ranges)
            if part and part_end > part_start
        ]
    if (
        override_parts
        and override_parts_are_safe
        and not override_crosses_timed_enum_break
        and join_subtitle_parts(override_parts) == _strip_for_display(segment_text)
    ):
        overridden_parts = _rebuild_parts_from_override(segment_parts, override_parts)
        if overridden_parts:
            segment_parts = overridden_parts
    return segment_parts


def _split_segment_text(
    text: str,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
    subtitle_max_chars: int = MAX_SUBTITLE_CHARS,
) -> list[str]:
    normalized = ' '.join(text.split())
    if not normalized:
        return []

    pieces: list[str] = []
    for hard_piece in HARD_BREAK_RE.split(normalized):
        hard_piece = hard_piece.strip()
        if not hard_piece:
            continue
        cleaned_hard_piece = _strip_for_display(hard_piece)
        if not cleaned_hard_piece:
            continue
        max_chars = max_subtitle_chars_for_text_with_profile(
            cleaned_hard_piece,
            subtitle_length_mode=subtitle_length_mode,
            subtitle_max_chars=subtitle_max_chars,
        )
        if len(cleaned_hard_piece) <= max_chars:
            pieces.append(cleaned_hard_piece)
            continue
        soft_parts = _split_by_soft_breaks(hard_piece)
        for soft_part in soft_parts or [hard_piece]:
            cleaned = _strip_for_display(soft_part)
            if not cleaned:
                continue
            pieces.extend(_split_by_length(
                cleaned,
                max_subtitle_chars_for_text_with_profile(
                    cleaned,
                    subtitle_length_mode=subtitle_length_mode,
                    subtitle_max_chars=subtitle_max_chars,
                ),
                protected_terms=protected_terms,
                runtime_lexicon_bundle=runtime_lexicon_bundle,
                subtitle_length_mode=subtitle_length_mode,
            ))
    return pieces


def strip_subtitle_display_text(text: str) -> str:
    without_punctuation = ''.join(ch for ch in str(text or '').strip() if ch not in DISPLAY_PUNCTUATION)
    simplified = normalize_to_simplified_chinese(without_punctuation)
    return _normalize_display_spaces(simplified)


def _strip_for_display(text: str) -> str:
    normalized = DASH_ARTIFACT_RE.sub('', text)
    normalized = CJK_DOUBLE_ONE_ARTIFACT_RE.sub('', normalized)
    return strip_subtitle_display_text(normalized)


def _normalize_display_spaces(text: str) -> str:
    value = re.sub(r'\s+', ' ', str(text or '').strip())
    if ' ' not in value:
        return value
    chars: list[str] = []
    for index, char in enumerate(value):
        if char != ' ':
            chars.append(char)
            continue
        previous_char = _previous_non_space(value, index)
        next_char = _next_non_space(value, index)
        if _needs_ascii_space(previous_char, next_char):
            chars.append(' ')
    return ''.join(chars)


def _previous_non_space(text: str, index: int) -> str:
    for cursor in range(index - 1, -1, -1):
        if not text[cursor].isspace():
            return text[cursor]
    return ''


def _next_non_space(text: str, index: int) -> str:
    for cursor in range(index + 1, len(text)):
        if not text[cursor].isspace():
            return text[cursor]
    return ''


def _needs_ascii_space(left_char: str, right_char: str) -> bool:
    return _is_ascii_alnum(left_char) and _is_ascii_alnum(right_char)


def max_subtitle_chars_for_text(text: str) -> int:
    value = str(text or '')
    if not LATIN_RE.search(value):
        return MAX_SUBTITLE_CHARS
    latin_count = len(re.findall(r'[A-Za-z0-9]', value))
    cjk_count = sum(1 for char in value if _is_cjk_char(char))
    if (
        latin_count >= 10
        and latin_count >= cjk_count
        and re.search(r'[A-Za-z0-9]\s+[A-Za-z0-9]', value)
    ):
        return MAX_SUBTITLE_LATIN_CHARS
    return MAX_SUBTITLE_CHARS


def max_subtitle_chars_for_text_with_profile(
    text: str,
    *,
    subtitle_length_mode: str,
    subtitle_max_chars: int,
) -> int:
    if subtitle_length_mode == STRICT_SUBTITLE_LENGTH_MODE:
        return subtitle_max_chars
    return max_subtitle_chars_for_text(text)


def join_subtitle_parts(parts: list[str]) -> str:
    result = ''
    for part in parts:
        value = str(part or '')
        if not value:
            continue
        if result and _needs_ascii_space(result[-1], value[0]):
            result += ' '
        result += value
    return result


def _split_by_soft_breaks(text: str) -> list[str]:
    value = text.strip()
    if not value:
        return []

    pieces: list[str] = []
    current_chars: list[str] = []
    for index, char in enumerate(value):
        current_chars.append(char)
        if not SOFT_BREAK_RE.fullmatch(char):
            continue
        current_text = ''.join(current_chars)
        current_cleaned = _strip_for_display(current_text)
        remainder_cleaned = _strip_for_display(value[index + 1:])
        if (
            len(current_cleaned) >= MIN_SOFT_BREAK_CHARS
            and len(remainder_cleaned) >= MIN_TAIL_CHARS
            and len(current_cleaned) >= PREFERRED_SOFT_BREAK_CHARS
            and not _is_discourse_prefix(current_cleaned)
        ):
            pieces.append(current_cleaned)
            current_chars = []

    tail_cleaned = _strip_for_display(''.join(current_chars))
    if tail_cleaned:
        pieces.append(tail_cleaned)
    return pieces


def _split_by_length(
    text: str,
    max_chars: int,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
) -> list[str]:
    value = text.strip()
    if not value:
        return []
    if len(value) <= max_chars:
        return [value]
    if _is_single_unbreakable_atom(
        value,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ):
        return [value]
    soft_max_chars = _soft_max_subtitle_chars(max_chars, subtitle_length_mode=subtitle_length_mode)
    if len(value) <= soft_max_chars:
        natural_cut = _choose_natural_soft_split_index(
            value,
            max_chars,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            subtitle_length_mode=subtitle_length_mode,
        )
        if natural_cut is None:
            return [value]
        return [value[:natural_cut], value[natural_cut:]]
    if (
        (subtitle_length_mode == STRICT_SUBTITLE_LENGTH_MODE or max_chars > MAX_SUBTITLE_CHARS)
        and LATIN_RE.search(value)
        and re.search(r'[A-Za-z0-9]\s+[A-Za-z0-9]', value)
    ):
        return _split_latin_aware_by_length(
            value,
            max_chars,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        )

    chunks: list[str] = []
    remaining = value
    while len(remaining) > soft_max_chars:
        cut = _choose_split_index(
            remaining,
            max_chars,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            subtitle_length_mode=subtitle_length_mode,
        )
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        if len(remaining) > max_chars:
            natural_cut = _choose_natural_soft_split_index(
                remaining,
                max_chars,
                protected_terms=protected_terms,
                runtime_lexicon_bundle=runtime_lexicon_bundle,
                subtitle_length_mode=subtitle_length_mode,
            )
            if natural_cut is not None:
                chunks.extend([remaining[:natural_cut], remaining[natural_cut:]])
            else:
                chunks.append(remaining)
        else:
            chunks.append(remaining)
    return _rebalance_chunks(
        chunks,
        max_chars,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        subtitle_length_mode=subtitle_length_mode,
    )


def _split_latin_aware_by_length(
    text: str,
    max_chars: int,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> list[str]:
    tokens = text.split(' ')
    chunks: list[str] = []
    current = ''
    for token in tokens:
        if not token:
            continue
        candidate = f'{current} {token}' if current else token
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ''
        if len(token) <= max_chars:
            current = token
            continue
        chunks.append(token)
    if current:
        chunks.append(current)
    return chunks


def _split_time_ranges(start_ms: int, end_ms: int, weights: list[int]) -> list[tuple[int, int]]:
    total_weight = sum(max(weight, 1) for weight in weights) or 1
    total_duration = max(end_ms - start_ms, len(weights) or 1)
    ranges: list[tuple[int, int]] = []
    cursor = start_ms
    consumed_weight = 0

    for index, weight in enumerate(weights):
        safe_weight = max(weight, 1)
        consumed_weight += safe_weight
        if index == len(weights) - 1:
            next_cursor = end_ms
        else:
            ratio = consumed_weight / total_weight
            next_cursor = start_ms + round(total_duration * ratio)
            next_cursor = max(next_cursor, cursor + 1)
        ranges.append((cursor, next_cursor))
        cursor = next_cursor
    return ranges


def _build_timed_parts_from_words(
    segment: dict,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
    subtitle_max_chars: int = MAX_SUBTITLE_CHARS,
) -> list[dict[str, int | str]]:
    words = segment.get('words') or []
    if not words:
        return []

    parts: list[dict[str, int | str]] = []
    current_words: list[dict[str, int | str]] = []

    for index, word in enumerate(words):
        word_text = _strip_for_display(str(word.get('text') or ''))
        word_start = int(word.get('startMs') or 0)
        word_end = int(word.get('endMs') or 0)
        punctuation = str(word.get('punctuation') or '')

        if word_text and word_end > word_start:
            if current_words and _needs_ascii_space(str(current_words[-1].get('text') or '')[-1:], word_text[0]):
                word_text = f' {word_text}'
            current_words.append({
                'text': word_text,
                'start_ms': word_start,
                'end_ms': word_end,
            })

        next_word = words[index + 1] if index + 1 < len(words) else None
        next_word_text = _strip_for_display(str((next_word or {}).get('text') or ''))
        next_word_start = int((next_word or {}).get('startMs') or 0)
        gap_after_word_ms = max(0, next_word_start - word_end) if next_word else 0
        remainder_length = sum(
            len(_strip_for_display(str(item.get('text') or '')))
            for item in words[index + 1:]
        )
        current_text = ''.join(str(item['text']) for item in current_words)

        if _has_caption_break(
            punctuation,
            len(current_text),
            current_text=current_text,
            remainder_length=remainder_length,
            next_word_text=next_word_text,
            gap_after_word_ms=gap_after_word_ms,
        ) and current_words:
            parts.extend(_split_timed_word_group(
                current_words,
                protected_terms=protected_terms,
                runtime_lexicon_bundle=runtime_lexicon_bundle,
                subtitle_length_mode=subtitle_length_mode,
                subtitle_max_chars=subtitle_max_chars,
            ))
            current_words = []

    if current_words:
        parts.extend(_split_timed_word_group(
            current_words,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            subtitle_length_mode=subtitle_length_mode,
            subtitle_max_chars=subtitle_max_chars,
        ))

    normalized_text = _strip_for_display(str(segment.get('text') or ''))
    if join_subtitle_parts([str(part['text']) for part in parts]) != normalized_text:
        return []
    return parts


def _split_timed_word_group(
    words: list[dict[str, int | str]],
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
    subtitle_max_chars: int = MAX_SUBTITLE_CHARS,
) -> list[dict[str, int | str]]:
    char_units = _build_char_timing_units(words)
    if not char_units:
        return []

    text = ''.join(str(unit['char']) for unit in char_units)
    chunks = _split_by_length(
        text,
        max_subtitle_chars_for_text_with_profile(
            text,
            subtitle_length_mode=subtitle_length_mode,
            subtitle_max_chars=subtitle_max_chars,
        ),
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
        subtitle_length_mode=subtitle_length_mode,
    )
    if len(chunks) == 1:
        return [{
            'text': text,
            'start_ms': int(char_units[0]['start_ms']),
            'end_ms': int(char_units[-1]['end_ms']),
        }]

    parts: list[dict[str, int | str]] = []
    cursor = 0
    for chunk in chunks:
        next_cursor = cursor + len(chunk)
        chunk_units = char_units[cursor:next_cursor]
        if not chunk_units:
            continue
        parts.append({
            'text': ''.join(str(unit['char']) for unit in chunk_units),
            'start_ms': int(chunk_units[0]['start_ms']),
            'end_ms': int(chunk_units[-1]['end_ms']),
        })
        cursor = next_cursor
    return parts


def _build_char_timing_units(words: list[dict[str, int | str]]) -> list[dict[str, int | str]]:
    units: list[dict[str, int | str]] = []
    for word in words:
        text = str(word.get('text') or '')
        start_ms = int(word.get('start_ms') or 0)
        end_ms = int(word.get('end_ms') or 0)
        if not text or end_ms <= start_ms:
            continue
        ranges = _split_time_ranges(start_ms, end_ms, [1 for _ in text])
        units.extend(
            {
                'char': char,
                'start_ms': part_start,
                'end_ms': part_end,
            }
            for char, (part_start, part_end) in zip(text, ranges)
        )
    return units


def _normalize_subtitle_parts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    parts = []
    for item in value:
        cleaned = _strip_for_display(str(item or ''))
        if cleaned:
            parts.append(cleaned)
    return parts


def _subtitle_parts_are_safe(
    parts: list[str],
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
    subtitle_max_chars: int = MAX_SUBTITLE_CHARS,
) -> bool:
    if not parts:
        return False
    for part in parts:
        if len(part) > max_subtitle_chars_for_text_with_profile(
            part,
            subtitle_length_mode=subtitle_length_mode,
            subtitle_max_chars=subtitle_max_chars,
        ):
            return False
    return not _has_unsafe_subtitle_part_boundaries(
        parts,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )


def _subtitle_parts_cross_timed_enum_break(parts: list[str], timed_parts: list[dict[str, int | str]]) -> bool:
    if len(parts) < 1 or len(timed_parts) < 2:
        return False

    required_boundaries: set[int] = set()
    cursor = 0
    timed_texts = [_strip_for_display(str(part.get('text') or '')) for part in timed_parts]
    for index, text in enumerate(timed_texts[:-1]):
        cursor += len(text)
        if _is_chinese_enum_marker(text) or _is_chinese_enum_marker(timed_texts[index + 1]):
            required_boundaries.add(cursor)
    if not required_boundaries:
        return False

    override_boundaries: set[int] = set()
    cursor = 0
    for part in parts[:-1]:
        cursor += len(part)
        override_boundaries.add(cursor)
    return not required_boundaries.issubset(override_boundaries)


def _has_unsafe_subtitle_part_boundaries(
    parts: list[str],
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> bool:
    for index in range(len(parts) - 1):
        left = parts[index]
        right = parts[index + 1]
        if not left or not right:
            return True
        if _needs_ascii_space(left[-1], right[0]):
            continue
        combined = left + right
        if _is_protected_boundary(
            combined,
            len(left),
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        ):
            return True
        if _grammar_boundary_penalty(left, right) > 0:
            return True
    return False


def _normalize_subtitle_part_ranges(value: object, expected_count: int) -> list[tuple[int, int]]:
    if not isinstance(value, list) or expected_count <= 0 or len(value) != expected_count:
        return []
    ranges: list[tuple[int, int]] = []
    for item in value:
        if not isinstance(item, dict):
            return []
        try:
            part_start = int(item.get('startMs') or item.get('start_ms') or 0)
            part_end = int(item.get('endMs') or item.get('end_ms') or 0)
        except (TypeError, ValueError):
            return []
        if part_end <= part_start:
            return []
        ranges.append((part_start, part_end))
    return ranges


def _normalize_speaker_name(value: object) -> str:
    return ' '.join(str(value or '').split()).strip()[:255]


def _optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_speaker_override_items(value: object) -> dict[int, str]:
    if not isinstance(value, list):
        return {}
    overrides: dict[int, str] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            part_index = int(item.get('partIndex'))
        except (TypeError, ValueError):
            continue
        if part_index < 0:
            continue
        speaker_name = _normalize_speaker_name(item.get('speakerName'))
        if speaker_name:
            overrides[part_index] = speaker_name
    return overrides


def _serialize_speaker_override_items(overrides: dict[int, str]) -> list[dict[str, int | str]]:
    return [
        {'partIndex': part_index, 'speakerName': speaker_name}
        for part_index, speaker_name in sorted(overrides.items())
        if part_index >= 0 and speaker_name
    ]


def _build_subtitle_part_records(
    normalized_segments: list[dict],
    speaker_mappings: dict[str, str],
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
    subtitle_max_chars: int = MAX_SUBTITLE_CHARS,
) -> list[dict[str, int | str]]:
    records: list[dict[str, int | str]] = []
    cue_index = 1
    for segment_index, segment in enumerate(normalized_segments or []):
        speaker_key = str(segment.get('speakerKey') or 'speaker_0')
        default_speaker_name = speaker_mappings.get(speaker_key, speaker_key)
        for part_index, part in enumerate(
            build_segment_subtitle_parts(
                segment,
                protected_terms=protected_terms,
                runtime_lexicon_bundle=runtime_lexicon_bundle,
                subtitle_length_mode=subtitle_length_mode,
                subtitle_max_chars=subtitle_max_chars,
            )
        ):
            clean_text = _strip_for_display(str(part.get('text') or ''))
            if not clean_text:
                continue
            records.append({
                'cue_index': cue_index,
                'segment_index': segment_index,
                'part_index': part_index,
                'default_speaker_name': default_speaker_name,
                'clean_text': clean_text,
                'start_ms': int(part.get('start_ms') or segment.get('startMs') or 0),
                'end_ms': int(part.get('end_ms') or segment.get('endMs') or 0),
            })
            cue_index += 1
    return records


def _store_segment_subtitle_parts(segment: dict, segment_parts: list[dict[str, int | str]]) -> None:
    cleaned_parts = [
        {
            'text': _strip_for_display(str(part.get('text') or '')),
            'start_ms': int(part.get('start_ms') or segment.get('startMs') or 0),
            'end_ms': int(part.get('end_ms') or segment.get('endMs') or 0),
        }
        for part in segment_parts
        if _strip_for_display(str(part.get('text') or ''))
    ]
    if not cleaned_parts:
        segment['text'] = ''
        segment['subtitleParts'] = []
        segment['subtitlePartRanges'] = []
        segment.pop('speakerOverrides', None)
        return
    segment['text'] = join_subtitle_parts([str(part['text']) for part in cleaned_parts])
    segment['subtitleParts'] = [str(part['text']) for part in cleaned_parts]
    segment['subtitlePartRanges'] = [
        {'startMs': int(part['start_ms']), 'endMs': int(part['end_ms'])}
        for part in cleaned_parts
    ]


def _shift_speaker_overrides_after_deletes(segment: dict, deleted_part_indexes: set[int]) -> None:
    speaker_overrides = _normalize_speaker_override_items(segment.get('speakerOverrides'))
    if not speaker_overrides:
        return
    shifted: dict[int, str] = {}
    for part_index, speaker_name in speaker_overrides.items():
        if part_index in deleted_part_indexes:
            continue
        shift = sum(1 for deleted_index in deleted_part_indexes if deleted_index < part_index)
        shifted[part_index - shift] = speaker_name
    serialized = _serialize_speaker_override_items(shifted)
    if serialized:
        segment['speakerOverrides'] = serialized
    else:
        segment.pop('speakerOverrides', None)


def _rebuild_parts_from_override(
    segment_parts: list[dict[str, int | str]],
    override_parts: list[str],
) -> list[dict[str, int | str]]:
    if not segment_parts or not override_parts:
        return []

    char_units = _build_char_timing_units(segment_parts)
    if not char_units:
        return []

    merged_text = ''.join(str(unit['char']) for unit in char_units)
    if merged_text != join_subtitle_parts(override_parts):
        return []

    rebuilt: list[dict[str, int | str]] = []
    cursor = 0
    for part_text in override_parts:
        if cursor > 0 and cursor < len(char_units) and str(char_units[cursor].get('char') or '') == ' ':
            cursor += 1
        next_cursor = cursor + len(part_text)
        units = char_units[cursor:next_cursor]
        if not units:
            return []
        rebuilt.append({
            'text': ''.join(str(unit['char']) for unit in units),
            'start_ms': int(units[0]['start_ms']),
            'end_ms': int(units[-1]['end_ms']),
        })
        cursor = next_cursor
    return rebuilt


def _is_discourse_prefix(text: str) -> bool:
    return _strip_for_display(text) in DISCOURSE_PREFIXES


def _choose_split_index(
    text: str,
    max_chars: int,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
) -> int:
    upper_bound = min(_soft_max_subtitle_chars(max_chars, subtitle_length_mode=subtitle_length_mode), len(text) - 1)
    lower_bound = max(1, max_chars - SPLIT_LOOKBACK_CHARS)
    atom_boundaries = _atom_split_boundaries(
        text,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    semantic_boundaries = _semantic_split_boundaries(
        text,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    repeated_phrase_boundaries = _repeated_phrase_transition_boundaries(text)
    candidates = sorted({
        index
        for index in range(lower_bound, upper_bound + 1)
        if index in atom_boundaries
    } | {
        index
        for index in semantic_boundaries
        if MIN_SOFT_BREAK_CHARS <= index <= upper_bound and len(text) - index >= MIN_TAIL_CHARS
    } | {
        index
        for index in repeated_phrase_boundaries
        if MIN_SOFT_BREAK_CHARS <= index <= upper_bound and len(text) - index >= MIN_TAIL_CHARS
    })
    best_index = None
    best_score = None

    for index in candidates:
        if not _is_repeated_phrase_transition_boundary(text, index) and _is_protected_boundary(
            text,
            index,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        ):
            continue
        score = _score_split_candidate(
            text,
            index,
            max_chars,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        )
        if best_score is None or score > best_score:
            best_score = score
            best_index = index

    if best_index is not None:
        return best_index

    for index in sorted((boundary for boundary in atom_boundaries if 0 < boundary <= upper_bound), reverse=True):
        if not _is_protected_boundary(
            text,
            index,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        ):
            return index
    for index in sorted((boundary for boundary in atom_boundaries if boundary > upper_bound)):
        if index < len(text):
            return index
    return upper_bound


def _atom_split_boundaries(
    text: str,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> set[int]:
    return {
        atom.end
        for atom in build_lexical_atoms(
            text,
            protected_terms=collect_subtitle_protected_terms(
                extra_terms=protected_terms,
                runtime_lexicon_bundle=runtime_lexicon_bundle,
            ),
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        )
        if 0 < atom.end < len(text)
    }


def _is_single_unbreakable_atom(
    text: str,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> bool:
    value = str(text or '')
    atoms = build_lexical_atoms(
        value,
        protected_terms=collect_subtitle_protected_terms(
            extra_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        ),
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    return len(atoms) == 1 and atoms[0].start == 0 and atoms[0].end == len(value) and atoms[0].kind != 'char'


def _semantic_split_boundaries(
    text: str,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> set[int]:
    boundaries: set[int] = set()
    for atom in build_lexical_atoms(
        text,
        protected_terms=collect_subtitle_protected_terms(
            extra_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        ),
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ):
        if atom.kind != 'phrase_integrity':
            continue
        if 0 < atom.start < len(text):
            boundaries.add(atom.start)
        if 0 < atom.end < len(text):
            boundaries.add(atom.end)
    return boundaries


def _score_split_candidate(
    text: str,
    index: int,
    max_chars: int,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> int:
    prefix = text[:index]
    suffix = text[index:]
    score = -abs(max_chars - len(prefix)) * 3

    if len(prefix) < MIN_SOFT_BREAK_CHARS:
        score -= 40
    if len(suffix) < MIN_TAIL_CHARS:
        score -= 28
    if len(prefix) <= 2 or len(suffix) <= 2:
        score -= 80

    if not _is_repeated_phrase_transition_boundary(text, index):
        score -= _protected_boundary_penalty(
            text,
            index,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        )
    if prefix[-1] in SPLIT_AVOID_END_CHARS:
        score -= 12
    if suffix[0] in SPLIT_AVOID_START_CHARS:
        score -= 18
    score -= _grammar_boundary_penalty(prefix, suffix)
    score += _short_copula_boundary_bonus(prefix, suffix)
    if _starts_with_preferred_prefix(suffix):
        score += 14
    if _starts_with_preferred_word_start(suffix):
        score += 18
    score += _semantic_boundary_bonus(
        text,
        index,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )
    if suffix[0] in '但并而却也又再先还':
        score += 8
    if _is_repeated_phrase_transition_boundary(text, index):
        score += 80
    return score


def _short_copula_boundary_bonus(prefix: str, suffix: str) -> int:
    left = str(prefix or '')
    right = str(suffix or '')
    if not left.endswith('是') or len(left) > 6 or len(left) < MIN_SOFT_BREAK_CHARS:
        return 0
    if len(right) < MIN_TAIL_CHARS or _starts_with_object_head(right):
        return 0
    if any(right.startswith(start) for start in SPLIT_COPULA_PREFERRED_STARTS):
        return 42
    return 0


def _repeated_phrase_transition_boundaries(text: str) -> set[int]:
    return {
        index
        for index in range(3, len(text))
        if _is_repeated_phrase_transition_boundary(text, index)
    }


def _is_repeated_phrase_transition_boundary(text: str, index: int) -> bool:
    if index < 3 or index >= len(text):
        return False
    left = text[index - 3:index]
    right_char = text[index]
    return (
        len(left) == 3
        and left[1] == '一'
        and left[0] == left[2] == right_char
        and _is_cjk_char(right_char)
    )


def _starts_with_preferred_prefix(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in SPLIT_PREFERRED_PREFIXES)


def _starts_with_preferred_word_start(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in SPLIT_PREFERRED_WORD_STARTS)


def _rebalance_chunks(
    chunks: list[str],
    max_chars: int,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
) -> list[str]:
    if len(chunks) < 2:
        return chunks

    rebalanced = list(chunks)
    soft_max_chars = _soft_max_subtitle_chars(max_chars, subtitle_length_mode=subtitle_length_mode)
    for index in range(len(rebalanced) - 1):
        first = rebalanced[index]
        second = rebalanced[index + 1]
        combined = first + second
        if len(combined) <= soft_max_chars:
            continue

        current_boundary = len(first)
        lower_bound = max(MIN_SOFT_BREAK_CHARS, len(combined) - soft_max_chars)
        upper_bound = min(soft_max_chars, len(combined) - MIN_TAIL_CHARS)
        if lower_bound > upper_bound:
            continue

        best_boundary = current_boundary
        best_score = _score_pair_boundary(
            combined,
            current_boundary,
            max_chars,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
            subtitle_length_mode=subtitle_length_mode,
        )

        for boundary in range(lower_bound, upper_bound + 1):
            if boundary == current_boundary:
                continue
            candidate_score = (
                _score_pair_boundary(
                    combined,
                    boundary,
                    max_chars,
                    protected_terms=protected_terms,
                    runtime_lexicon_bundle=runtime_lexicon_bundle,
                    subtitle_length_mode=subtitle_length_mode,
                )
                - abs(boundary - current_boundary) * 2
            )
            if candidate_score > best_score:
                best_score = candidate_score
                best_boundary = boundary

        if best_boundary != current_boundary:
            rebalanced[index] = combined[:best_boundary]
            rebalanced[index + 1] = combined[best_boundary:]
    return rebalanced


def _score_pair_boundary(
    text: str,
    boundary: int,
    max_chars: int,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
) -> int:
    if boundary <= 0 or boundary >= len(text):
        return -10_000
    prefix = text[:boundary]
    suffix = text[boundary:]
    soft_max_chars = _soft_max_subtitle_chars(max_chars, subtitle_length_mode=subtitle_length_mode)
    if len(prefix) > soft_max_chars or len(suffix) > soft_max_chars:
        return -10_000
    if len(prefix) < MIN_SOFT_BREAK_CHARS or len(suffix) < MIN_TAIL_CHARS:
        return -10_000
    if not _is_repeated_phrase_transition_boundary(text, boundary) and _is_protected_boundary(
        text,
        boundary,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ):
        return -10_000
    return _score_split_candidate(
        text,
        boundary,
        max_chars,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    )


def _soft_max_subtitle_chars(
    max_chars: int,
    *,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
) -> int:
    if subtitle_length_mode == STRICT_SUBTITLE_LENGTH_MODE:
        return max_chars
    if max_chars == MAX_SUBTITLE_CHARS:
        return max_chars + SUBTITLE_SOFT_OVERFLOW_CHARS
    return max_chars


def _choose_natural_soft_split_index(
    text: str,
    max_chars: int,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
    subtitle_length_mode: str = DEFAULT_SUBTITLE_LENGTH_MODE,
) -> Optional[int]:
    if len(text) <= max_chars or len(text) > _soft_max_subtitle_chars(max_chars, subtitle_length_mode=subtitle_length_mode):
        return None
    candidates = sorted(
        boundary
        for boundary in (
            _atom_split_boundaries(
                text,
                protected_terms=protected_terms,
                runtime_lexicon_bundle=runtime_lexicon_bundle,
            )
            | _semantic_split_boundaries(
                text,
                protected_terms=protected_terms,
                runtime_lexicon_bundle=runtime_lexicon_bundle,
            )
        )
        if MIN_SOFT_BREAK_CHARS <= boundary <= max_chars
        and MIN_TAIL_CHARS <= len(text) - boundary <= max_chars
    )
    best_index = None
    best_score = None
    for index in candidates:
        if _is_protected_boundary(
            text,
            index,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        ):
            continue
        prefix = text[:index]
        suffix = text[index:]
        if prefix[-1] in SPLIT_AVOID_END_CHARS and prefix[-1] != '的':
            continue
        if suffix[0] in SPLIT_AVOID_START_CHARS:
            continue
        score = _score_split_candidate(
            text,
            index,
            max_chars,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        )
        if _semantic_boundary_bonus(
            text,
            index,
            protected_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        ) <= 0 and not (
            _starts_with_preferred_prefix(suffix)
            or _starts_with_preferred_word_start(suffix)
            or suffix[0] in '但并而却也又再先还'
        ):
            if score < -25:
                continue
        if best_score is None or score > best_score:
            best_score = score
            best_index = index
    return best_index


def _semantic_boundary_bonus(
    text: str,
    index: int,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> int:
    bonus = 0
    for atom in build_lexical_atoms(
        text,
        protected_terms=collect_subtitle_protected_terms(
            extra_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        ),
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ):
        if atom.kind == 'phrase_integrity' and index == atom.end:
            bonus = max(bonus, 60)
        elif atom.kind == 'phrase_integrity' and index == atom.start:
            bonus = max(bonus, 34)
        elif atom.kind in {'organization', 'protected_term'} and index == atom.end:
            bonus = max(bonus, 12)
    return bonus


def _protected_boundary_penalty(
    text: str,
    index: int,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> int:
    penalty = 0
    if _boundary_inside_lexical_atom(
        text,
        index,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ):
        penalty += 96
    if _boundary_inside_protected_term(
        text,
        index,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ):
        penalty += 96
    pair = text[index - 1:index + 1]
    if pair in PROTECTED_BOUNDARY_BIGRAMS:
        penalty += 32
    if len(pair) == 2 and pair[0] == pair[1] and _is_cjk_char(pair[0]):
        penalty += 32

    left_trigram = text[index - 2:index + 1] if index >= 2 else ''
    right_trigram = text[index - 1:index + 2] if index + 2 <= len(text) else ''
    if left_trigram in PROTECTED_BOUNDARY_TRIGRAMS:
        penalty += 18
    if right_trigram in PROTECTED_BOUNDARY_TRIGRAMS:
        penalty += 18
    return penalty


def _boundary_inside_lexical_atom(
    text: str,
    index: int,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> bool:
    if index <= 0 or index >= len(text):
        return False
    for atom in build_lexical_atoms(
        text,
        protected_terms=collect_subtitle_protected_terms(
            extra_terms=protected_terms,
            runtime_lexicon_bundle=runtime_lexicon_bundle,
        ),
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ):
        if atom.start < index < atom.end:
            return True
    return False


def _boundary_inside_protected_term(
    text: str,
    index: int,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> bool:
    if index <= 0 or index >= len(text):
        return False
    for term in collect_subtitle_protected_terms(
        extra_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ):
        start = text.find(term)
        while start != -1:
            end = start + len(term)
            if start < index < end:
                return True
            start = text.find(term, start + 1)
    return False


def _is_protected_boundary(
    text: str,
    index: int,
    *,
    protected_terms: Optional[Iterable[str]] = None,
    runtime_lexicon_bundle: Optional[dict[str, Any]] = None,
) -> bool:
    prefix = text[:index]
    suffix = text[index:]
    if not prefix or not suffix:
        return False

    if _protected_boundary_penalty(
        text,
        index,
        protected_terms=protected_terms,
        runtime_lexicon_bundle=runtime_lexicon_bundle,
    ) > 0:
        return True

    left_char = prefix[-1]
    right_char = suffix[0]
    if _is_ascii_alnum(left_char) and _is_ascii_alnum(right_char):
        return True
    if _is_ascii_alnum(left_char) and _is_cjk_char(right_char):
        return bool(ASCII_SUFFIX_RE.search(prefix))
    return False


def _is_ascii_alnum(char: str) -> bool:
    return bool(char) and bool(ASCII_ALNUM_RE.fullmatch(char))


def _is_cjk_char(char: str) -> bool:
    if not char:
        return False
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _has_caption_break(
    punctuation: str,
    current_length: int,
    *,
    current_text: str,
    remainder_length: int,
    next_word_text: str,
    gap_after_word_ms: int,
) -> bool:
    if gap_after_word_ms >= SOFT_BREAK_MIN_GAP_MS and next_word_text:
        if current_length >= MIN_SOFT_BREAK_CHARS and _is_chinese_enum_marker(next_word_text):
            return True
        if _is_chinese_enum_marker(current_text):
            return True
    if any(char in '。！？!?；;…' for char in punctuation):
        return True
    if current_length < MIN_SOFT_BREAK_CHARS:
        return False
    if not any(char in '，,、：:' for char in punctuation):
        return False
    if _is_discourse_prefix(current_text):
        return False
    if gap_after_word_ms >= SOFT_BREAK_MIN_GAP_MS and next_word_text and remainder_length >= MIN_TIMED_TAIL_CHARS:
        return True
    if remainder_length < MIN_TAIL_CHARS:
        return False
    return current_length >= PREFERRED_SOFT_BREAK_CHARS


def _is_chinese_enum_marker(text: str) -> bool:
    return bool(CHINESE_ENUM_MARKER_RE.fullmatch(_strip_for_display(text)))


def render_subtitle_content(cues: list[SubtitleCue], subtitle_format: SubtitleFormat, variant: SubtitleVariant) -> str:
    if subtitle_format == 'vtt':
        body = '\n\n'.join(_render_block(cue, subtitle_format, variant) for cue in cues)
        return f'WEBVTT\n\n{body}\n' if body else 'WEBVTT\n'
    return '\n\n'.join(_render_block(cue, subtitle_format, variant) for cue in cues) + ('\n' if cues else '')


def _render_block(cue: SubtitleCue, subtitle_format: SubtitleFormat, variant: SubtitleVariant) -> str:
    text = cue.full_text if variant == 'full' else cue.clean_text
    start = _format_timestamp(cue.start_ms, subtitle_format)
    end = _format_timestamp(cue.end_ms, subtitle_format)
    lines = []
    if subtitle_format == 'srt':
        lines.append(str(cue.index))
    lines.append(f'{start} --> {end}')
    lines.append(text)
    return '\n'.join(lines)


def _format_timestamp(ms: int, subtitle_format: SubtitleFormat) -> str:
    total_ms = max(ms, 0)
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    seconds = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    separator = '.' if subtitle_format == 'vtt' else ','
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}'
