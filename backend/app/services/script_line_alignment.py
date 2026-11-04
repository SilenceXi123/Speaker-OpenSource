from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re
from typing import Any

from app.services.chinese_text import normalize_to_simplified_chinese
from app.services.subtitle_pipeline_v2_schema import (
    RawAsrSnapshot,
    SemanticUtteranceArtifact,
)


_MATCH_CHAR_RE = re.compile(r'[\u3400-\u9fffA-Za-z0-9]')
_NON_MATCH_CHAR_RE = re.compile(r'[^\u3400-\u9fffA-Za-z0-9]+')
_MIN_EXACT_LINE_MATCH_CHARS = 6


@dataclass(frozen=True)
class MatchTextView:
    raw_text: str
    match_text: str
    normalized_to_raw: tuple[int, ...]

    def display_slice(self, start: int, end: int) -> str:
        if start < 0 or end <= start or end > len(self.match_text):
            raise ValueError('display slice must reference a non-empty match range')
        selected_raw = set(self.normalized_to_raw[start:end])
        raw_start = min(selected_raw)
        raw_end = max(selected_raw) + 1
        display = ''.join(
            char
            for index, char in enumerate(self.raw_text[raw_start:raw_end], start=raw_start)
            if index in selected_raw or char.isspace()
        )
        return re.sub(r'\s+', ' ', display).strip()


@dataclass(frozen=True)
class AlignedTokenSlice:
    token_index: int
    script_match_start: int
    script_match_end: int


@dataclass(frozen=True)
class ExactScriptLineAlignment:
    script_line_id: str
    script_line_order: int
    script_source_hash: str
    script_source_order: int
    script_source_line_order: int
    script_line_number: int
    script_fragment_index: int
    ends_physical_line: bool
    script_text: str
    script_match_text: str
    raw_match_start: int
    raw_match_end: int
    token_index_by_match_char: tuple[int, ...]
    token_slices: tuple[AlignedTokenSlice, ...]
    right_token_index_after_line: int | None


def normalize_match_text(value: str) -> str:
    simplified = normalize_to_simplified_chinese(str(value or ''))
    return _NON_MATCH_CHAR_RE.sub('', simplified).lower()


def build_match_text_view(value: str) -> MatchTextView | None:
    raw_text = str(value or '')
    if any(
        len(char.lower()) != 1
        or len(char.casefold()) != 1
        or char.lower() != char.casefold()
        for char in raw_text
    ):
        return None
    match_text = normalize_match_text(raw_text)
    normalized_to_raw = tuple(
        raw_index
        for raw_index, raw_char in enumerate(raw_text)
        if _MATCH_CHAR_RE.fullmatch(raw_char)
    )
    if len(normalized_to_raw) != len(match_text):
        return None
    return MatchTextView(
        raw_text=raw_text,
        match_text=match_text,
        normalized_to_raw=normalized_to_raw,
    )


def build_exact_script_line_alignments(
    *,
    raw_snapshot: RawAsrSnapshot,
    semantic_artifact: SemanticUtteranceArtifact,
    script_snapshot: Any,
    min_match_chars: int = _MIN_EXACT_LINE_MATCH_CHARS,
) -> tuple[ExactScriptLineAlignment, ...]:
    _validate_semantic_token_order(raw_snapshot, semantic_artifact)
    raw_tape = _build_raw_token_tape(raw_snapshot)
    raw_match_text, token_index_by_match_char, token_ranges = raw_tape
    lines_by_source: dict[tuple[int, str], list[Any]] = {}
    for line in sorted(script_snapshot.lines, key=lambda item: item.order_index):
        lines_by_source.setdefault(
            (line.source_order, line.source_hash),
            [],
        ).append(line)

    candidates_by_source: list[tuple[int, tuple[ExactScriptLineAlignment, ...]]] = []
    for (source_order, _source_hash), source_lines in sorted(
        lines_by_source.items(),
        key=lambda item: item[0],
    ):
        raw_cursor = 0
        source_alignments: list[ExactScriptLineAlignment] = []
        for line in sorted(
            source_lines,
            key=lambda item: item.source_line_order,
        ):
            script_view = build_match_text_view(line.text)
            script_match_text = str(line.match_text or '')
            if script_view is None:
                continue
            if script_match_text != script_view.match_text:
                raise ValueError('script line match_text disagrees with display text')
            if len(script_match_text) < min_match_chars:
                continue
            raw_match_start = raw_match_text.find(script_match_text, raw_cursor)
            if raw_match_start < 0:
                continue
            raw_match_end = raw_match_start + len(script_match_text)
            token_slices = _token_slices_for_range(
                token_ranges,
                raw_match_start=raw_match_start,
                raw_match_end=raw_match_end,
            )
            source_alignments.append(
                ExactScriptLineAlignment(
                    script_line_id=line.line_id,
                    script_line_order=line.order_index,
                    script_source_hash=line.source_hash,
                    script_source_order=line.source_order,
                    script_source_line_order=line.source_line_order,
                    script_line_number=line.line_number,
                    script_fragment_index=line.fragment_index,
                    ends_physical_line=line.ends_physical_line,
                    script_text=line.text,
                    script_match_text=script_match_text,
                    raw_match_start=raw_match_start,
                    raw_match_end=raw_match_end,
                    token_index_by_match_char=tuple(
                        token_index_by_match_char[raw_match_start:raw_match_end],
                    ),
                    token_slices=token_slices,
                    right_token_index_after_line=(
                        token_index_by_match_char[raw_match_end]
                        if raw_match_end < len(token_index_by_match_char)
                        else None
                    ),
                )
            )
            raw_cursor = raw_match_end
        candidates_by_source.append((source_order, tuple(source_alignments)))

    selected: list[ExactScriptLineAlignment] = []
    for _source_order, source_alignments in sorted(
        candidates_by_source,
        key=lambda item: item[0],
    ):
        for alignment in source_alignments:
            if any(
                alignment.raw_match_start < accepted.raw_match_end
                and accepted.raw_match_start < alignment.raw_match_end
                for accepted in selected
            ):
                continue
            selected.append(alignment)

    return tuple(sorted(
        selected,
        key=lambda item: (
            item.raw_match_start,
            item.raw_match_end,
            item.script_source_order,
            item.script_source_line_order,
        ),
    ))


def display_text_for_match_range(
    alignment: ExactScriptLineAlignment,
    start: int,
    end: int,
) -> str:
    view = build_match_text_view(alignment.script_text)
    if view is None or view.match_text != alignment.script_match_text:
        raise ValueError('script display view disagrees with its alignment')
    return view.display_slice(start, end)


def validate_exact_script_line_alignments(
    *,
    raw_snapshot: RawAsrSnapshot,
    script_snapshot: Any,
    alignments: Sequence[ExactScriptLineAlignment],
) -> None:
    raw_tape = _build_raw_token_tape(raw_snapshot)
    raw_match_text, token_index_by_match_char, token_ranges = raw_tape
    line_by_id = {line.line_id: line for line in script_snapshot.lines}
    previous_end = 0
    for alignment in alignments:
        start = alignment.raw_match_start
        end = alignment.raw_match_end
        line = line_by_id.get(alignment.script_line_id)
        if (
            line is None
            or start < previous_end
            or end <= start
            or end > len(raw_match_text)
        ):
            raise ValueError(
                'precomputed script alignment disagrees with current evidence'
            )
        expected_mapping = tuple(token_index_by_match_char[start:end])
        expected_right_token_index = (
            token_index_by_match_char[end]
            if end < len(token_index_by_match_char)
            else None
        )
        if (
            alignment.script_source_hash != line.source_hash
            or alignment.script_line_order != line.order_index
            or alignment.script_source_order != line.source_order
            or alignment.script_source_line_order != line.source_line_order
            or alignment.script_line_number != line.line_number
            or alignment.script_fragment_index != line.fragment_index
            or alignment.ends_physical_line != line.ends_physical_line
            or alignment.script_text != line.text
            or alignment.script_match_text != line.match_text
            or raw_match_text[start:end] != alignment.script_match_text
            or alignment.token_index_by_match_char != expected_mapping
            or alignment.token_slices != _token_slices_for_range(
                token_ranges,
                raw_match_start=start,
                raw_match_end=end,
            )
            or alignment.right_token_index_after_line
            != expected_right_token_index
        ):
            raise ValueError(
                'precomputed script alignment disagrees with current evidence'
            )
        previous_end = end


def _validate_semantic_token_order(
    raw_snapshot: RawAsrSnapshot,
    semantic_artifact: SemanticUtteranceArtifact,
) -> None:
    if semantic_artifact.raw_snapshot_hash != raw_snapshot.snapshot_hash:
        raise ValueError('semantic artifact references a different raw snapshot')
    semantic_token_ids = tuple(
        token_id
        for utterance in semantic_artifact.utterances
        for token_id in utterance.token_ids
    )
    raw_token_ids = tuple(token.token_id for token in raw_snapshot.tokens)
    if semantic_token_ids != raw_token_ids:
        raise ValueError('semantic artifact does not conserve the raw token tape')


def _build_raw_token_tape(
    raw_snapshot: RawAsrSnapshot,
) -> tuple[str, tuple[int, ...], tuple[tuple[int, int], ...]]:
    match_text_parts: list[str] = []
    token_index_by_match_char: list[int] = []
    token_ranges: list[tuple[int, int]] = []
    for token_index, token in enumerate(raw_snapshot.tokens):
        token_start = len(token_index_by_match_char)
        token_match_text = normalize_match_text(token.text)
        match_text_parts.append(token_match_text)
        token_index_by_match_char.extend([token_index] * len(token_match_text))
        token_ranges.append((token_start, len(token_index_by_match_char)))
    return (
        ''.join(match_text_parts),
        tuple(token_index_by_match_char),
        tuple(token_ranges),
    )


def _token_slices_for_range(
    token_ranges: tuple[tuple[int, int], ...],
    *,
    raw_match_start: int,
    raw_match_end: int,
) -> tuple[AlignedTokenSlice, ...]:
    return tuple(
        AlignedTokenSlice(
            token_index=token_index,
            script_match_start=token_start - raw_match_start,
            script_match_end=token_end - raw_match_start,
        )
        for token_index, (token_start, token_end) in enumerate(token_ranges)
        if token_start >= raw_match_start and token_end <= raw_match_end and token_start != token_end
    )
