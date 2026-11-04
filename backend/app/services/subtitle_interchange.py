from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.schemas.subtitle import SubtitleCue
from app.services.premiere_xml import (
    PremiereSubtitleFrameCollisionError,
    build_premiere_subtitle_xml,
)


SRT_TIMING_RE = re.compile(
    r'^(?P<start>\d{2,}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*'
    r'(?P<end>\d{2,}:\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?$'
)


class SrtConversionError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class SrtXmlConversionResult:
    xml_content: str
    cue_count: int
    frame_rate: str
    duration_ms: int


def decode_srt_bytes(payload: bytes) -> str:
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SrtConversionError(
        'unsupported_srt_encoding',
        'SRT 仅支持 UTF-8、UTF-8 BOM 或 GB18030 编码',
    )


def _iter_srt_blocks(text: str) -> list[tuple[int, list[str]]]:
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start_line = 1
    for line_number, line in enumerate(text.split('\n'), start=1):
        if not line.strip():
            if current:
                blocks.append((start_line, current))
                current = []
            continue
        if not current:
            start_line = line_number
        current.append(line)
    if current:
        blocks.append((start_line, current))
    return blocks


def _parse_srt_timestamp(
    value: str,
    *,
    cue_number: int,
    line_number: int,
) -> int:
    normalized = value.replace('.', ',')
    clock, milliseconds = normalized.split(',', 1)
    hours, minutes, seconds = (int(part) for part in clock.split(':'))
    if minutes >= 60 or seconds >= 60:
        raise SrtConversionError(
            'invalid_srt',
            'SRT 时间戳的分钟或秒超出范围',
            details={'cue_number': cue_number, 'line_number': line_number},
        )
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int(milliseconds)


def _validate_xml_1_0_text(value: str, *, details: dict[str, Any]) -> None:
    for character in value:
        code_point = ord(character)
        if (
            code_point in {0x9, 0xA, 0xD}
            or 0x20 <= code_point <= 0xD7FF
            or 0xE000 <= code_point <= 0xFFFD
            or 0x10000 <= code_point <= 0x10FFFF
        ):
            continue
        raise SrtConversionError(
            'invalid_srt',
            'SRT 包含 XML 1.0 不支持的字符',
            details={**details, 'code_point': f'U+{code_point:04X}'},
        )


def parse_srt_text(text: str) -> list[SubtitleCue]:
    normalized = str(text or '').replace('\r\n', '\n').replace('\r', '\n').removeprefix('\ufeff')
    blocks = _iter_srt_blocks(normalized)
    if not blocks:
        raise SrtConversionError(
            'invalid_srt',
            'SRT 没有可转换的字幕',
            details={'line_number': 1},
        )

    cues: list[SubtitleCue] = []
    for cue_number, (block_start, lines) in enumerate(blocks, start=1):
        timing_offset = 1 if lines[0].strip().isdigit() else 0
        timing_line_number = block_start + timing_offset
        if timing_offset >= len(lines):
            raise SrtConversionError(
                'invalid_srt',
                'SRT cue 缺少时间行',
                details={'cue_number': cue_number, 'line_number': timing_line_number},
            )
        match = SRT_TIMING_RE.fullmatch(lines[timing_offset].strip())
        if match is None:
            raise SrtConversionError(
                'invalid_srt',
                'SRT 时间行格式无效',
                details={'cue_number': cue_number, 'line_number': timing_line_number},
            )
        text_lines = lines[timing_offset + 1:]
        if not text_lines or not any(line.strip() for line in text_lines):
            raise SrtConversionError(
                'invalid_srt',
                'SRT cue 缺少正文',
                details={'cue_number': cue_number, 'line_number': timing_line_number + 1},
            )
        for line_number, text_line in enumerate(text_lines, start=timing_line_number + 1):
            _validate_xml_1_0_text(
                text_line,
                details={'cue_number': cue_number, 'line_number': line_number},
            )

        start_ms = _parse_srt_timestamp(
            match.group('start'), cue_number=cue_number, line_number=timing_line_number,
        )
        end_ms = _parse_srt_timestamp(
            match.group('end'), cue_number=cue_number, line_number=timing_line_number,
        )
        details = {'cue_number': cue_number, 'line_number': timing_line_number}
        if end_ms <= start_ms:
            raise SrtConversionError('invalid_srt', 'SRT cue 结束时间必须晚于开始时间', details=details)
        if cues and start_ms < cues[-1].start_ms:
            raise SrtConversionError('invalid_srt', 'SRT cue 时间顺序无效', details=details)
        if cues and start_ms < cues[-1].end_ms:
            raise SrtConversionError('overlapping_cues', 'SRT cue 时间重叠', details=details)

        cue_text = '\n'.join(text_lines)
        cues.append(SubtitleCue(
            index=cue_number,
            start_ms=start_ms,
            end_ms=end_ms,
            speaker_key=f'srt_{cue_number}',
            speaker_name='',
            full_text=cue_text,
            clean_text=cue_text,
        ))
    return cues


def convert_srt_bytes_to_premiere_xml(
    payload: bytes,
    *,
    source_name: str,
    frame_rate: str,
) -> SrtXmlConversionResult:
    text = decode_srt_bytes(payload)
    cues = parse_srt_text(text)
    sequence_name = f'{source_name.rsplit(".", 1)[0]} 字幕'
    _validate_xml_1_0_text(sequence_name, details={'field': 'source_name'})
    try:
        xml_content = build_premiere_subtitle_xml(
            sequence_name=sequence_name,
            cues=cues,
            frame_rate=frame_rate,
        )
    except PremiereSubtitleFrameCollisionError as error:
        raise SrtConversionError(
            'invalid_srt',
            'SRT cue 帧对齐后时间冲突',
            details={
                'cue_number': error.cue_index,
                'previous_cue_number': error.previous_cue_index,
                'frame_rate': error.frame_rate,
                'start_frame': error.start_frame,
                'previous_end_frame': error.previous_end_frame,
            },
        ) from error
    return SrtXmlConversionResult(
        xml_content=xml_content,
        cue_count=len(cues),
        frame_rate=frame_rate,
        duration_ms=max(cue.end_ms for cue in cues),
    )
