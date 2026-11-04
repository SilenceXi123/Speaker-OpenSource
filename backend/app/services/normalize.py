from typing import Any


OVERLAP_DEDUP_WINDOW_MS = 10_000  # ponytail: 固定 10s 窗口覆盖 8s 默认重叠+ASR 抖动；重叠值未透传到合并层
_MATCH_PUNCTUATION = set('，。！？、,.!?;；:：""\'\'「」『』（）()~～…—')


def _normalize_match_text(text: str) -> str:
    return ''.join(ch for ch in (text or '') if not ch.isspace() and ch not in _MATCH_PUNCTUATION)


def _overlap_region_has_sentences(payload: dict[str, Any], window_ms: int) -> bool:
    for transcript in payload.get('transcripts') or []:
        for sentence in transcript.get('sentences') or []:
            if int(sentence.get('begin_time', 0)) < window_ms:
                return True
    return False


def normalize_funasr_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    transcripts = payload.get('transcripts') or []
    for transcript in transcripts:
        for sentence in transcript.get('sentences') or []:
            speaker_id = sentence.get('speaker_id', 0)
            segments.append({
                'segmentId': f"seg-{sentence.get('sentence_id', len(segments) + 1)}",
                'speakerKey': f'speaker_{speaker_id}',
                'speakerId': speaker_id,
                'startMs': sentence.get('begin_time', 0),
                'endMs': sentence.get('end_time', 0),
                'text': sentence.get('text', ''),
                'words': [
                    {
                        'startMs': word.get('begin_time', 0),
                        'endMs': word.get('end_time', 0),
                        'text': word.get('text', ''),
                        'punctuation': word.get('punctuation', ''),
                    }
                    for word in sentence.get('words') or []
                ],
            })
    return segments


def merge_funasr_payloads(payload_parts: list[tuple[dict[str, Any], int, int]]) -> dict[str, Any]:
    merged_sentences: list[dict[str, Any]] = []
    previous_tail: list[str] = []  # 上一分片重叠窗口内的规范化文本，用于整句去重
    for chunk_index, (payload, offset_ms, speaker_offset) in enumerate(payload_parts):
        boundary_ms = offset_ms
        next_boundary_ms = payload_parts[chunk_index + 1][1] if chunk_index + 1 < len(payload_parts) else None
        next_covered = (
            _overlap_region_has_sentences(payload_parts[chunk_index + 1][0], OVERLAP_DEDUP_WINDOW_MS)
            if chunk_index + 1 < len(payload_parts)
            else False
        )
        tail: list[str] = []
        transcripts = payload.get('transcripts') or []
        for transcript in transcripts:
            for sentence in transcript.get('sentences') or []:
                original_words = sentence.get('words') or []
                words = [dict(word) for word in original_words]
                words_changed = False
                if original_words:
                    if next_boundary_ms is not None and next_covered:
                        # 前一片：裁剪掉落在重叠区（边界之后）的词，后一片完整覆盖该区域
                        filtered = [
                            word
                            for word in words
                            if int(word.get('end_time', 0)) + offset_ms <= next_boundary_ms
                        ]
                        words_changed = len(filtered) != len(words)
                        words = filtered
                    if chunk_index:
                        # 后一片：裁剪掉边界之前（ASR 抖动）的词，前一片已覆盖
                        filtered = [
                            word
                            for word in words
                            if int(word.get('begin_time', 0)) + offset_ms >= boundary_ms
                        ]
                        words_changed = words_changed or len(filtered) != len(words)
                        words = filtered
                    if not words:
                        # 整句落在重叠区，丢弃该版本
                        continue
                    begin_ms = int(words[0].get('begin_time', 0)) + offset_ms
                    end_ms = int(words[-1].get('end_time', 0)) + offset_ms
                else:
                    begin_ms = int(sentence.get('begin_time', 0)) + offset_ms
                    end_ms = int(sentence.get('end_time', 0)) + offset_ms
                text = str(sentence.get('text') or '')
                if words_changed:
                    # 裁剪过词后，句子文本从保留词重建，保证文本与词一致
                    text = ''.join((word.get('text') or '') + (word.get('punctuation') or '') for word in words)
                normalized_text = _normalize_match_text(text)
                if (
                    chunk_index
                    and normalized_text
                    and begin_ms < boundary_ms + OVERLAP_DEDUP_WINDOW_MS
                    # 同一句话在两片中被切成不同长度时，后片文本通常包含于前片完整文本
                    and any(normalized_text in prev_text for prev_text in previous_tail)
                ):
                    continue
                # ponytail: 词级边界裁剪保证时间单调；ASR 对同一句听写不一致时仍可能残留片段文本重复，观测到再上模糊拼接
                if (
                    next_boundary_ms is not None
                    and normalized_text
                    and end_ms > next_boundary_ms - OVERLAP_DEDUP_WINDOW_MS
                ):
                    tail.append(normalized_text)
                merged_words = []
                for word in words:
                    merged_words.append({
                        **word,
                        'begin_time': int(word.get('begin_time', 0)) + offset_ms,
                        'end_time': int(word.get('end_time', 0)) + offset_ms,
                    })
                speaker_id = int(sentence.get('speaker_id', 0)) + speaker_offset
                merged_sentences.append({
                    **sentence,
                    'sentence_id': f'{chunk_index + 1}-{sentence.get("sentence_id", len(merged_sentences) + 1)}',
                    'speaker_id': speaker_id,
                    'begin_time': begin_ms,
                    'end_time': end_ms,
                    'text': text,
                    'words': merged_words,
                })
        previous_tail = tail
    return {
        'transcripts': [
            {
                'sentences': merged_sentences,
            }
        ]
    }
