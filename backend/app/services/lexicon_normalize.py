from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from app.services.chinese_text import normalize_to_simplified_chinese


DISPLAY_PUNCTUATION = set("，。、！？；：、“”‘’（）()【】[]《》〈〉…,.!?;:·\"'")


@dataclass(frozen=True)
class NormalizedTextMap:
    raw_text: str
    normalized_text: str
    raw_to_normalized: list[int | None]
    normalized_to_raw: list[int]
    normalization_version: str = "lexicon_normalize.v1"


def normalize_term(value: str) -> str:
    simplified = normalize_to_simplified_chinese(str(value or ""))
    stripped = "".join(char for char in simplified.strip() if char not in DISPLAY_PUNCTUATION)
    return re.sub(r"\s+", "", stripped)


def hash_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def stable_hash_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_normalized_text_with_offset_map(raw_text: str) -> NormalizedTextMap:
    raw = str(raw_text or "")
    normalized_chars: list[str] = []
    normalized_to_raw: list[int] = []
    raw_to_normalized: list[int | None] = [None for _ in raw]
    for raw_index, raw_char in enumerate(raw):
        normalized_value = normalize_to_simplified_chinese(raw_char)
        if not normalized_value:
            continue
        normalized_index = len(normalized_chars)
        for normalized_char in normalized_value:
            normalized_chars.append(normalized_char)
            normalized_to_raw.append(raw_index)
        raw_to_normalized[raw_index] = normalized_index
    return NormalizedTextMap(
        raw_text=raw,
        normalized_text="".join(normalized_chars),
        raw_to_normalized=raw_to_normalized,
        normalized_to_raw=normalized_to_raw,
    )
